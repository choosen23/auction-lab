import random

import pytest

from agt.mechanisms import REGISTRY, mechanism, registry_schema, run
from agt.trace import Bidder

BIDDERS = [Bidder("A", 100, 95), Bidder("B", 72, 72), Bidder("C", 41, 41)]


def test_second_price_winner_pays_second_bid():
    t = run("second_price", BIDDERS)
    assert t.result["winner"] == "A"
    assert t.result["price"] == 72
    assert t.result["utilities"]["A"] == 28


def test_first_price_winner_pays_own_bid():
    t = run("first_price", BIDDERS)
    assert t.result["winner"] == "A"
    assert t.result["price"] == 95
    assert t.result["utilities"]["A"] == 5


def test_all_pay_everyone_pays():
    t = run("all_pay", BIDDERS)
    assert t.result["winner"] == "A"
    assert t.result["revenue"] == 95 + 72 + 41
    assert t.result["utilities"]["C"] == -41


def test_reserve_above_all_bids_blocks_sale():
    t = run("second_price", BIDDERS, {"reserve": 200})
    assert t.result["winner"] is None
    assert t.result["revenue"] == 0
    assert t.result["efficient"] is False


def test_reserve_above_all_bids_blocks_all_pay_too():
    t = run("all_pay", BIDDERS, {"reserve": 200})
    assert t.result["winner"] is None
    assert t.result["revenue"] == 0


def test_reserve_between_bids_raises_price():
    t = run("second_price", BIDDERS, {"reserve": 80})
    assert t.result["winner"] == "A"
    assert t.result["price"] == 80


def test_first_price_reserve_does_not_lower_own_bid():
    t = run("first_price", BIDDERS, {"reserve": 80})
    assert t.result["price"] == 95


def test_steps_are_ordered_and_labelled():
    t = run("second_price", BIDDERS)
    labels = [s.label for s in t.steps]
    assert labels[0] == "collect bids"
    assert "price rule" in labels
    assert t.steps[-1].state["winner"] == "A"


def test_ties_break_by_bidder_order_and_say_so():
    tied = [Bidder("A", 50, 50), Bidder("B", 90, 50)]
    t = run("first_price", tied)
    assert t.result["winner"] == "A"
    assert any("tie" in s.detail.lower() for s in t.steps)


def test_unknown_mechanism_is_rejected():
    with pytest.raises(ValueError, match="unknown mechanism"):
        run("nope", BIDDERS)


def test_unknown_param_is_rejected():
    with pytest.raises(ValueError, match="unknown parameter"):
        run("second_price", BIDDERS, {"bogus": 1})


def test_out_of_range_param_is_rejected():
    with pytest.raises(ValueError, match="reserve"):
        run("second_price", BIDDERS, {"reserve": -1})


def test_non_numeric_param_is_rejected():
    with pytest.raises(ValueError, match="reserve"):
        run("second_price", BIDDERS, {"reserve": "cheap"})


def test_duplicate_bidder_ids_are_rejected():
    """Duplicates would silently collapse in payments/utilities, so the engine refuses."""
    with pytest.raises(ValueError, match="unique"):
        run("second_price", [Bidder("A", 100, 95), Bidder("A", 72, 72)])


def test_empty_bidder_list_is_rejected():
    with pytest.raises(ValueError, match="at least one bidder"):
        run("second_price", [])


def test_defaults_are_applied_and_recorded():
    t = run("second_price", BIDDERS)
    assert t.params["reserve"] == 0


def test_registry_schema_is_json_safe_and_form_ready():
    import json

    payload = json.loads(json.dumps(registry_schema()))
    assert set(payload) == set(REGISTRY)
    entry = payload["second_price"]
    assert entry["name"] == "second_price"
    assert entry["label"] and entry["description"]
    reserve = entry["params"]["reserve"]
    assert reserve["type"] == "number"
    assert reserve["default"] == 0
    assert reserve["label"]


def test_every_mechanism_declares_whether_truth_is_dominant():
    """Whether honesty is dominant is a fact about the mechanism, so the mechanism owns
    it. Keeping it in a list inside the strategy module made every new mechanism default
    to 'not dominant' by silence, which is how a truthful one would be misdescribed."""
    for name, spec in REGISTRY.items():
        assert isinstance(spec.truthful_dominant, bool), f"{name} declares no flag"
    assert REGISTRY["second_price"].truthful_dominant is True
    assert REGISTRY["english"].truthful_dominant is True
    assert REGISTRY["first_price"].truthful_dominant is False
    assert REGISTRY["dutch"].truthful_dominant is False
    assert REGISTRY["all_pay"].truthful_dominant is False


def test_registering_a_mechanism_without_the_flag_is_refused():
    """Correct by construction: a new mechanism cannot forget to answer the question."""
    with pytest.raises(TypeError):
        mechanism("nameless", label="X", description="X.")


def test_registry_schema_publishes_the_truthfulness_flag():
    import json

    payload = json.loads(json.dumps(registry_schema()))
    assert payload["second_price"]["truthful_dominant"] is True
    assert payload["first_price"]["truthful_dominant"] is False


def test_trace_is_json_serializable_for_every_mechanism():
    import json

    for name in REGISTRY:
        json.dumps(run(name, BIDDERS).to_dict())


@pytest.mark.parametrize("name", ["first_price", "second_price", "all_pay"])
def test_sealed_bid_steps_are_teaching_material(name):
    t = run(name, BIDDERS, {"reserve": 50})
    labels = [s.label for s in t.steps]
    assert labels[0] == "collect bids"
    assert "sort" in labels
    assert "apply reserve" in labels
    assert "pick winner" in labels
    assert "price rule" in labels
    assert labels[-1] == "payments"
    for s in t.steps:
        assert s.detail.endswith("."), f"{name}/{s.label}: detail must be a sentence"
        assert s.highlight["stage"]
        assert isinstance(s.highlight["bidders"], list)
        assert "bids" in s.state
    assert any(s.formula for s in t.steps), "at least one step must show a formula"


# ------------------------------------------------------------- clock auctions


def test_english_equals_second_price():
    t = run("english", BIDDERS)
    assert t.result["winner"] == "A"
    assert t.result["price"] == 72
    assert t.result == run("second_price", BIDDERS).result


def test_english_steps_show_each_dropout():
    t = run("english", BIDDERS)
    dropouts = [s for s in t.steps if s.highlight.get("stage") == "dropout"]
    assert len(dropouts) == 2
    assert [s.state["clock"] for s in dropouts] == [41, 72]


def test_english_clock_starts_at_the_reserve():
    t = run("english", BIDDERS, {"reserve": 80})
    assert t.result["winner"] == "A"
    assert t.result["price"] == 80
    assert t.result == run("second_price", BIDDERS, {"reserve": 80}).result


def test_english_reserve_above_all_bids_blocks_sale():
    t = run("english", BIDDERS, {"reserve": 200})
    assert t.result["winner"] is None
    assert t.result["revenue"] == 0


def test_dutch_equals_first_price():
    t = run("dutch", BIDDERS)
    assert t.result["winner"] == "A"
    assert t.result["price"] == 95
    assert t.result == run("first_price", BIDDERS).result


def test_english_does_not_narrate_a_tiebreak_that_did_not_happen():
    """`detail` is read as the literal account of what happened, so it must be true."""
    t = run("english", BIDDERS)
    detail = next(s.detail for s in t.steps if s.label == "pick winner")
    assert "tie" not in detail.lower()
    assert "listed first" not in detail.lower()


def test_english_names_the_tiebreak_when_limits_actually_tie():
    t = run("english", [Bidder("A", 50, 50), Bidder("B", 90, 50)])
    detail = next(s.detail for s in t.steps if s.label == "pick winner")
    assert "tie" in detail.lower()
    assert "listed first" in detail.lower()
    assert t.result["winner"] == "A"


def test_dutch_clock_starts_above_every_bid():
    t = run("dutch", BIDDERS)
    start = t.steps[1].state["clock"]
    assert start > max(b.bid for b in BIDDERS)


def test_dutch_accepts_an_explicit_start_price():
    t = run("dutch", BIDDERS, {"start": 300})
    assert t.steps[1].state["clock"] == 300
    assert t.result["price"] == 95


def test_dutch_start_below_the_top_bid_is_rejected():
    """A clock opening under a bidder's limit hands the item to whoever is listed first
    rather than to the highest bidder, so the configuration is refused, not modelled."""
    bidders = [Bidder("B", 72, 72), Bidder("A", 100, 95)]
    with pytest.raises(ValueError, match="start"):
        run("dutch", bidders, {"start": 50})


def test_dutch_start_below_the_reserve_is_rejected():
    """The reserve is an unconditional floor; the clock cannot open beneath it."""
    with pytest.raises(ValueError, match="start"):
        run("dutch", [Bidder("A", 100, 20)], {"reserve": 50, "start": 30})


def test_dutch_start_can_never_undercut_the_reserve():
    """Regression: a low `start` used to sell under the reserve price."""
    with pytest.raises(ValueError, match="start"):
        run("dutch", [Bidder("A", 100, 95)], {"reserve": 50, "start": 10})


def test_dutch_rejection_message_names_the_minimum_start():
    """The message goes straight to the user in the setup form, so it must be actionable."""
    with pytest.raises(ValueError, match="95"):
        run("dutch", BIDDERS, {"start": 50})


def test_dutch_start_equal_to_the_top_bid_still_works():
    t = run("dutch", BIDDERS, {"start": 95})
    assert t.steps[1].state["clock"] == 95
    assert t.result["winner"] == "A"
    assert t.result["price"] == 95
    assert t.result == run("first_price", BIDDERS).result


def test_dutch_reserve_blocks_sale():
    t = run("dutch", BIDDERS, {"reserve": 200})
    assert t.result["winner"] is None
    assert t.result["revenue"] == 0


@pytest.mark.parametrize(
    "name,equivalence", [("english", "second-price"), ("dutch", "first-price")]
)
def test_clock_steps_teach_the_equivalence(name, equivalence):
    t = run(name, BIDDERS)
    labels = [s.label for s in t.steps]
    assert labels[0] == "collect bids"
    assert "price rule" in labels
    assert labels[-1] == "payments"
    assert t.steps[-1].state["winner"] == "A"
    for s in t.steps:
        assert s.detail.endswith("."), f"{name}/{s.label}: detail must be a sentence"
        assert s.highlight["stage"]
        assert isinstance(s.highlight["bidders"], list)
        assert "bids" in s.state
    priced = next(s for s in t.steps if s.label == "price rule")
    assert equivalence in priced.detail.lower()


# ------------------------------------------- invariants every mechanism must hold
#
# Parametrized over the registry, so a mechanism added in a later phase inherits these
# checks the moment it is registered. They are written to hold for both result shapes:
# a single winner who takes their whole value, and an allocation where several bidders
# each take part of theirs. Narrowing the parametrization to dodge the second shape
# would leave those mechanisms untested, so the invariants generalize instead.

TOL = 1e-9


def gains_of(result, bidders):
    """Gross value each bidder received, whichever way the mechanism reported it."""
    if "gains" in result:
        return result["gains"]
    return {b.id: (b.value if b.id == result["winner"] else 0) for b in bidders}


def winners_of(result, bidders):
    """Everybody who received something, best first."""
    if "allocation" in result:
        return list(result["allocation"])
    return [result["winner"]] if result["winner"] is not None else []


@pytest.mark.parametrize("name", sorted(REGISTRY))
def test_invariants_hold(name):
    t = run(name, BIDDERS)
    r = t.result
    gains = gains_of(r, BIDDERS)
    assert r["revenue"] == pytest.approx(sum(r["payments"].values()))
    for b in BIDDERS:
        assert r["utilities"][b.id] == pytest.approx(gains[b.id] - r["payments"][b.id])
    assert r["welfare"] == pytest.approx(sum(gains.values()))
    assert sum(r["utilities"].values()) == pytest.approx(r["welfare"] - r["revenue"])
    assert t.steps, "a mechanism must emit at least one step"
    assert t.steps[-1].state["winner"] == r["winner"]
    assert winners_of(r, BIDDERS)[:1] == [r["winner"]], "the result names its own top winner"


@pytest.mark.parametrize("name", sorted(REGISTRY))
def test_invariants_hold_under_a_blocking_reserve(name):
    t = run(name, BIDDERS, {"reserve": 200})
    r = t.result
    assert r["winner"] is None
    assert r["revenue"] == 0
    assert r["efficient"] is False
    assert t.steps[-1].state["winner"] is None


@pytest.mark.parametrize("name", sorted(REGISTRY))
def test_nobody_ever_pays_more_than_they_bid(name):
    """A bid is a ceiling on what you can be charged — for the winner, for the losers an
    all-pay auction still bills, and for every holder of a slot in a multi-slot one."""
    t = run(name, BIDDERS)
    for b in BIDDERS:
        assert t.result["payments"][b.id] <= b.bid + TOL, f"{name}: {b.id} overcharged"
    winner_bid = next(b.bid for b in BIDDERS if b.id == t.result["winner"])
    assert t.result["price"] <= winner_bid + TOL


@pytest.mark.parametrize("name", ["second_price", "english", "vcg_positions"])
def test_truthful_bidding_is_dominant(name):
    """No deviation from bidding your value beats it, over random value profiles."""
    rng = random.Random(0)
    for _ in range(200):
        values = [rng.randint(1, 100) for _ in range(3)]
        others = [Bidder(i, v, v) for i, v in zip("BC", values[1:])]
        truthful = run(name, [Bidder("A", values[0], values[0])] + others)
        honest_utility = truthful.result["utilities"]["A"]
        for lie in (values[0] // 2, values[0] * 2):
            deviant = run(name, [Bidder("A", values[0], lie)] + others)
            assert deviant.result["utilities"]["A"] <= honest_utility + TOL, (
                f"{name}: values={values} bid={lie} beat truthful bidding"
            )


@pytest.mark.parametrize("name", ["second_price", "english", "vcg_positions"])
def test_truthful_bidding_is_dominant_under_a_reserve(name):
    """The reserve must not open a profitable lie either."""
    rng = random.Random(1)
    for _ in range(200):
        values = [rng.randint(1, 100) for _ in range(3)]
        reserve = rng.randint(0, 100)
        others = [Bidder(i, v, v) for i, v in zip("BC", values[1:])]
        truthful = run(
            name, [Bidder("A", values[0], values[0])] + others, {"reserve": reserve}
        )
        honest_utility = truthful.result["utilities"]["A"]
        for lie in (values[0] // 2, values[0] * 2):
            deviant = run(name, [Bidder("A", values[0], lie)] + others, {"reserve": reserve})
            assert deviant.result["utilities"]["A"] <= honest_utility + TOL, (
                f"{name}: values={values} reserve={reserve} bid={lie} beat truthful bidding"
            )


@pytest.mark.parametrize("name", sorted(REGISTRY))
def test_random_input_produces_a_wellformed_trace(name):
    """Registry contract: any registered mechanism survives arbitrary bidders."""
    import json

    rng = random.Random(7)
    for _ in range(100):
        bidders = [
            Bidder(chr(65 + j), rng.randint(0, 100), rng.randint(0, 100))
            for j in range(rng.randint(1, 6))
        ]
        reserve = rng.choice([0, rng.randint(0, 120)])
        params: dict = {"reserve": reserve}
        if "start" in REGISTRY[name].params:
            # A clock start is only coherent at or above both the top bid and the
            # reserve; anything lower must be rejected, which is asserted separately.
            floor = max(max(b.bid for b in bidders), reserve)
            params["start"] = rng.choice([None, floor, floor + rng.randint(0, 50)])
        if "slots" in REGISTRY[name].params:
            params["slots"] = rng.randint(1, 4)
            params["ctr_decay"] = rng.choice([0, 0.5, 0.9, 1])
        t = run(name, bidders, params)
        json.dumps(t.to_dict())
        r = t.result
        gains = gains_of(r, bidders)
        assert r["revenue"] == pytest.approx(sum(r["payments"].values()))
        assert r["welfare"] == pytest.approx(sum(gains.values()))
        assert t.steps and t.steps[-1].state["winner"] == r["winner"]
        for s in t.steps:
            assert s.detail.endswith(".")
            assert s.highlight["stage"] and isinstance(s.highlight["bidders"], list)
            assert "bids" in s.state
        if r["winner"] is None:
            assert max(b.bid for b in bidders) < reserve
            assert r["revenue"] == 0
            assert not winners_of(r, bidders)
        else:
            for who in winners_of(r, bidders):
                won = next(b for b in bidders if b.id == who)
                assert won.bid >= reserve
                assert r["payments"][who] <= won.bid + TOL
            assert r["price"] <= next(
                b.bid for b in bidders if b.id == r["winner"]
            ) + TOL


@pytest.mark.parametrize(
    "clock,sealed", [("english", "second_price"), ("dutch", "first_price")]
)
def test_clock_and_sealed_equivalence_holds_generally(clock, sealed):
    """The equivalences are not a fluke of one bid profile."""
    rng = random.Random(3)
    for _ in range(200):
        bidders = [
            Bidder(chr(65 + j), rng.randint(0, 100), rng.randint(0, 100))
            for j in range(rng.randint(1, 5))
        ]
        params = {"reserve": rng.choice([0, rng.randint(0, 110)])}
        sealed_params = dict(params)
        if "start" in REGISTRY[clock].params and rng.random() < 0.5:
            # every legal clock start must land on the same outcome, not just the default
            floor = max(max(b.bid for b in bidders), params["reserve"])
            params["start"] = floor + rng.randint(0, 40)
        assert (
            run(clock, bidders, params).result
            == run(sealed, bidders, sealed_params).result
        )


@pytest.mark.parametrize(
    "name", sorted(n for n, m in REGISTRY.items() if "start" in m.params)
)
def test_clock_start_below_its_floor_is_always_rejected(name):
    """A clock that opens below the top bid or below the reserve is not a lower price —
    it silently reallocates the item and defeats the reserve, so it must not run."""
    rng = random.Random(11)
    for _ in range(200):
        bidders = [
            Bidder(chr(65 + j), rng.randint(0, 100), rng.randint(1, 100))
            for j in range(rng.randint(1, 5))
        ]
        reserve = rng.choice([0, rng.randint(0, 120)])
        floor = max(max(b.bid for b in bidders), reserve)
        too_low = rng.uniform(0, floor)
        if too_low >= floor:
            continue
        with pytest.raises(ValueError, match="start"):
            run(name, bidders, {"reserve": reserve, "start": too_low})
