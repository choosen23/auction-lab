"""Position auctions: the same slots to the same bidders, two different bills.

Every test here exists to make one comparison concrete — GSP is not truthful, VCG is —
so the pair `test_same_allocation_different_payments` and
`test_gsp_rewards_dropping_a_slot_on_purpose` carry the lesson and the rest guard it.
"""

import pytest

from agt.mechanisms import run
from agt.trace import Bidder

BIDDERS = [Bidder("A", 100, 95), Bidder("B", 72, 72), Bidder("C", 41, 41)]

# Three slots at ctr 1, 0.5, 0.25 and the bid ladder 95 > 72 > 41.
GSP_PAYMENTS = {"A": 72, "B": 20.5, "C": 0}
VCG_PAYMENTS = {"A": 46.25, "B": 10.25, "C": 0}
SLOTS = {"A": 0, "B": 1, "C": 2}


# --------------------------------------------------------------- the whole point


def test_same_allocation_different_payments():
    """The one comparison phase 3a exists for: identical bidders, identical slots,
    identical winners, and a different bill purely because the payment rule differs."""
    gsp, vcg = run("gsp", BIDDERS), run("vcg_positions", BIDDERS)

    assert gsp.result["allocation"] == vcg.result["allocation"] == SLOTS

    assert gsp.result["payments"] == GSP_PAYMENTS
    assert vcg.result["payments"] == VCG_PAYMENTS
    assert gsp.result["revenue"] == 92.5
    assert vcg.result["revenue"] == 56.5


def test_gsp_rewards_dropping_a_slot_on_purpose():
    """A pinned profitable deviation, because a GSP that were silently truthful would
    otherwise pass every other test in this file.

    A values clicks at 10, B bids 8, C bids 2, two slots at ctr 1 and 0.5. Bidding
    honestly buys the top slot at B's price; bidding 3 loses the top slot on purpose and
    buys half the clicks at C's price instead, which is worth more.
    """
    rivals = [Bidder("B", 8, 8), Bidder("C", 2, 2)]
    params = {"slots": 2}

    honest = run("gsp", [Bidder("A", 10, 10)] + rivals, params)
    deviant = run("gsp", [Bidder("A", 10, 3)] + rivals, params)

    assert honest.result["allocation"]["A"] == 0
    assert honest.result["payments"]["A"] == 8
    assert honest.result["utilities"]["A"] == 2

    assert deviant.result["allocation"]["A"] == 1
    assert deviant.result["payments"]["A"] == 1
    assert deviant.result["utilities"]["A"] == 4

    assert deviant.result["utilities"]["A"] > honest.result["utilities"]["A"]


def test_vcg_closes_the_deviation_gsp_leaves_open():
    """The same lie, the same bidders, the same slots — and under VCG it loses money."""
    rivals = [Bidder("B", 8, 8), Bidder("C", 2, 2)]
    params = {"slots": 2}

    honest = run("vcg_positions", [Bidder("A", 10, 10)] + rivals, params)
    deviant = run("vcg_positions", [Bidder("A", 10, 3)] + rivals, params)

    assert honest.result["utilities"]["A"] == 5
    assert deviant.result["utilities"]["A"] == 4
    assert deviant.result["utilities"]["A"] < honest.result["utilities"]["A"]


@pytest.mark.parametrize("name", ["gsp", "vcg_positions"])
def test_one_slot_collapses_to_a_second_price_auction(name):
    """A free equivalence check in the spirit of phase 1's English == second-price: with
    a single slot both payment rules are the highest losing bid."""
    single = run(name, BIDDERS, {"slots": 1}).result
    sealed = run("second_price", BIDDERS).result
    assert {key: single[key] for key in sealed} == sealed


# ----------------------------------------------------------------- the allocation


@pytest.mark.parametrize("name", ["gsp", "vcg_positions"])
def test_more_bidders_than_slots_leaves_the_lowest_bids_out(name):
    bidders = BIDDERS + [Bidder("D", 30, 30)]
    r = run(name, bidders, {"slots": 2}).result
    assert r["allocation"] == {"A": 0, "B": 1}
    assert r["gains"]["C"] == 0 and r["gains"]["D"] == 0
    assert r["payments"]["C"] == 0 and r["payments"]["D"] == 0


@pytest.mark.parametrize("name", ["gsp", "vcg_positions"])
def test_fewer_bidders_than_slots_leaves_slots_empty(name):
    r = run(name, BIDDERS[:2], {"slots": 3}).result
    assert r["allocation"] == {"A": 0, "B": 1}
    # Nothing sits below B, so B's price is the reserve, which is zero by default.
    assert r["payments"]["B"] == 0


@pytest.mark.parametrize("name", ["gsp", "vcg_positions"])
def test_equal_bids_take_slots_in_the_order_they_were_listed(name):
    tied = [Bidder("A", 50, 50), Bidder("B", 90, 50)]
    r = run(name, tied, {"slots": 2}).result
    assert r["allocation"] == {"A": 0, "B": 1}


@pytest.mark.parametrize("name", ["gsp", "vcg_positions"])
def test_a_reserve_strikes_out_the_bids_below_it(name):
    r = run(name, BIDDERS, {"reserve": 50}).result
    assert r["allocation"] == {"A": 0, "B": 1}
    assert r["payments"]["C"] == 0
    # C valued clicks and got none: the reserve bought revenue with welfare.
    assert r["welfare"] == 136
    assert r["efficient"] is False


def test_a_reserve_becomes_the_price_of_the_last_slot():
    """With nobody below them, the bottom winner pays the reserve rather than nothing."""
    assert run("gsp", BIDDERS, {"reserve": 50}).result["payments"]["B"] == 25
    assert run("vcg_positions", BIDDERS, {"reserve": 50}).result["payments"]["A"] == 61


@pytest.mark.parametrize("name", ["gsp", "vcg_positions"])
def test_a_reserve_above_every_bid_leaves_every_slot_unsold(name):
    r = run(name, BIDDERS, {"reserve": 200}).result
    assert r["allocation"] == {}
    assert r["winner"] is None
    assert r["revenue"] == 0
    assert r["efficient"] is False


# ------------------------------------------------------------------- the numbers


@pytest.mark.parametrize("name", ["gsp", "vcg_positions"])
def test_both_allocate_assortatively_and_efficiently(name):
    """The highest value on the most clicked slot: both rules reach the best allocation
    available, so the only thing left to compare is the bill."""
    r = run(name, BIDDERS).result
    assert r["gains"] == {"A": 100, "B": 36, "C": 10.25}
    assert r["welfare"] == 146.25
    assert r["best_possible_welfare"] == 146.25
    assert r["efficient"] is True


@pytest.mark.parametrize("name", ["gsp", "vcg_positions"])
def test_click_through_rates_decay_geometrically(name):
    r = run(name, BIDDERS, {"ctr_decay": 0.1}).result
    assert r["gains"] == pytest.approx({"A": 100, "B": 7.2, "C": 0.41})


@pytest.mark.parametrize("name", ["gsp", "vcg_positions"])
def test_a_fractional_slot_count_is_refused(name):
    with pytest.raises(ValueError, match="slots"):
        run(name, BIDDERS, {"slots": 2.5})


@pytest.mark.parametrize("name", ["gsp", "vcg_positions"])
def test_vcg_never_charges_more_than_gsp(name):
    """The externality a winner imposes is bounded by the next bid on every click below
    them, so the truthful rule is also the cheaper one — the trade-off in one line."""
    import random

    rng = random.Random(5)
    for _ in range(200):
        bidders = [
            Bidder(chr(65 + j), rng.randint(0, 100), rng.randint(0, 100))
            for j in range(rng.randint(1, 5))
        ]
        params = {"slots": rng.randint(1, 4), "ctr_decay": rng.choice([0.3, 0.5, 0.9])}
        gsp = run("gsp", bidders, params).result
        vcg = run("vcg_positions", bidders, params).result
        assert gsp["allocation"] == vcg["allocation"]
        assert vcg["revenue"] <= gsp["revenue"] + 1e-9


# ------------------------------------------------------------- teaching material


def test_gsp_says_the_next_bidders_bid_is_what_breaks_truthfulness():
    """The step text is the lesson; a learner who only reads it must still learn why
    sponsored search runs a mechanism that can be gamed."""
    detail = next(
        s.detail for s in run("gsp", BIDDERS).steps if s.label == "price rule"
    ).lower()
    assert "next bidder" in detail
    assert "truthful" in detail


def test_vcg_says_a_winners_own_bid_never_enters_their_bill():
    detail = next(
        s.detail for s in run("vcg_positions", BIDDERS).steps if s.label == "price rule"
    ).lower()
    assert "externality" in detail
    assert "dominant" in detail


@pytest.mark.parametrize("name", ["gsp", "vcg_positions"])
def test_steps_show_the_ladder_and_the_arithmetic(name):
    t = run(name, BIDDERS)
    labels = [s.label for s in t.steps]
    assert labels[0] == "collect bids"
    assert "click-through rates" in labels
    assert "assign slots" in labels
    assert "price rule" in labels
    assert labels[-1] == "payments"
    # one payment step per winner, each showing its own arithmetic and leading with the
    # bidder being charged (VCG also highlights everybody that bidder pushed down)
    priced = [s for s in t.steps if s.label == "slot price"]
    assert [s.highlight["bidders"][0] for s in priced] == ["A", "B", "C"]
    assert all(s.formula for s in priced)
    for s in t.steps:
        assert s.detail.endswith("."), f"{name}/{s.label}: detail must be a sentence"
        assert s.highlight["stage"]
        assert isinstance(s.highlight["bidders"], list)
        assert "bids" in s.state
    assert t.steps[-1].state["allocation"] == SLOTS
