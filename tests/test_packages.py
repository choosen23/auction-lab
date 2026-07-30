"""Combinatorial auctions: XOR package bids, two winner-determination algorithms.

The test this file exists for is `test_greedy_leaves_welfare_on_the_table`. Finding the
best allocation is NP-hard, so a practical auction runs greedy and settles for less; that
test puts a number on exactly how much less, and every other test here guards it.
"""

import random

import pytest

from agt.packages import (
    MAX_BIDS,
    MAX_ITEMS,
    PACKAGE_REGISTRY,
    PackageBid,
    greedy_allocate,
    item_universe,
    optimal_allocate,
    run_packages,
    total_bid,
)

TOL = 1e-9

# Two spectrum licences. A wants them only as a pair; B and C each want one.
# Greedy takes A's 10 first and is then blocked; B + C together are worth 12.
PAIR = PackageBid("A", ("north", "south"), 10, 10)
NORTH = PackageBid("B", ("north",), 6, 6)
SOUTH = PackageBid("C", ("south",), 6, 6)
PINNED = [PAIR, NORTH, SOUTH]


def who(accepted):
    return [b.bidder for b in accepted]


# ------------------------------------------------------------------- the whole point


def test_greedy_leaves_welfare_on_the_table():
    """The hand-built case that justifies showing both algorithms.

    A bids 10 for {north, south}; B bids 6 for {north}; C bids 6 for {south}. Greedy
    takes the single highest bid and is then blocked on both items, ending at 10.
    Splitting the pair between B and C is worth 12. The approximation costs 2.
    """
    greedy, optimal = greedy_allocate(PINNED), optimal_allocate(PINNED)

    assert who(greedy) == ["A"]
    assert total_bid(greedy) == 10

    assert sorted(who(optimal)) == ["B", "C"]
    assert total_bid(optimal) == 12

    assert total_bid(optimal) - total_bid(greedy) == 2


def test_greedy_and_optimal_agree_when_nothing_conflicts():
    """With no shared items and no shared bidders, greedy is already optimal."""
    bids = [
        PackageBid("A", ("north",), 9, 9),
        PackageBid("B", ("south",), 4, 4),
        PackageBid("C", ("east",), 7, 7),
    ]
    assert greedy_allocate(bids) == optimal_allocate(bids)
    assert total_bid(greedy_allocate(bids)) == 20


def test_optimal_prefers_one_bundle_over_a_cheaper_pair_of_singles():
    """Complementarities: {north, south} together beats two bidders whose sum is lower."""
    bids = [
        PackageBid("A", ("north", "south"), 10, 10),
        PackageBid("B", ("north",), 4, 4),
        PackageBid("C", ("south",), 4, 4),
    ]
    assert who(optimal_allocate(bids)) == ["A"]
    assert total_bid(optimal_allocate(bids)) == 10
    assert greedy_allocate(bids) == optimal_allocate(bids)


# --------------------------------------------------------------- the two constraints


def test_a_bidder_never_wins_two_bundles():
    """XOR: several bids from one bidder are alternatives, not an order for both."""
    bids = [
        PackageBid("A", ("north",), 9, 9),
        PackageBid("A", ("south",), 8, 8),
        PackageBid("B", ("south",), 1, 1),
    ]
    for accepted in (greedy_allocate(bids), optimal_allocate(bids)):
        assert who(accepted).count("A") == 1
    assert who(greedy_allocate(bids)) == ["A", "B"]
    assert who(optimal_allocate(bids)) == ["A", "B"]


def test_an_item_is_never_sold_twice():
    bids = [
        PackageBid("A", ("north", "south"), 9, 9),
        PackageBid("B", ("south", "east"), 8, 8),
    ]
    for accepted in (greedy_allocate(bids), optimal_allocate(bids)):
        assert who(accepted) == ["A"]


def test_both_solvers_always_return_a_feasible_allocation():
    """Property: over random instances neither solver ever double-sells an item or
    hands one bidder two bundles."""
    rng = random.Random(4)
    items = ["north", "south", "east", "west"]
    for _ in range(300):
        bids = [
            PackageBid(
                rng.choice("ABC"),
                tuple(sorted(rng.sample(items, rng.randint(1, 3)))),
                v := rng.randint(1, 40),
                v,
            )
            for _ in range(rng.randint(1, 8))
        ]
        for accepted in (greedy_allocate(bids), optimal_allocate(bids)):
            taken = [i for b in accepted for i in b.items]
            assert len(taken) == len(set(taken)), "an item was sold twice"
            winners = who(accepted)
            assert len(winners) == len(set(winners)), "a bidder won twice"
            assert all(b in bids for b in accepted)


def test_optimal_is_never_worse_than_greedy():
    rng = random.Random(5)
    items = ["north", "south", "east"]
    for _ in range(300):
        bids = [
            PackageBid(
                rng.choice("ABCD"),
                tuple(sorted(rng.sample(items, rng.randint(1, 2)))),
                v := rng.randint(1, 30),
                v,
            )
            for _ in range(rng.randint(1, 7))
        ]
        assert total_bid(optimal_allocate(bids)) >= total_bid(greedy_allocate(bids))


# ------------------------------------------------------------------ determinism


def test_ties_keep_the_listed_order():
    """Both solvers must be reproducible: the trace text quotes the answer."""
    bids = [
        PackageBid("A", ("north",), 5, 5),
        PackageBid("B", ("north",), 5, 5),
        PackageBid("C", ("north",), 5, 5),
    ]
    assert who(greedy_allocate(bids)) == ["A"]
    assert who(optimal_allocate(bids)) == ["A"]
    # the rule is the listed order, not the name: reverse the list and the tie moves
    assert who(greedy_allocate(list(reversed(bids)))) == ["C"]
    assert who(optimal_allocate(list(reversed(bids)))) == ["C"]


def test_winners_are_reported_highest_bid_first():
    bids = [
        PackageBid("A", ("north",), 3, 3),
        PackageBid("B", ("south",), 9, 9),
        PackageBid("C", ("east",), 6, 6),
    ]
    assert who(greedy_allocate(bids)) == ["B", "C", "A"]
    assert who(optimal_allocate(bids)) == ["B", "C", "A"]


# ------------------------------------------------------------------- edge cases


def test_no_bids_allocates_nothing():
    assert greedy_allocate([]) == ()
    assert optimal_allocate([]) == ()
    assert total_bid(()) == 0
    assert item_universe([]) == ()


def test_a_single_bid_wins_on_its_own():
    only = [PackageBid("A", ("north",), 7, 7)]
    assert greedy_allocate(only) == optimal_allocate(only) == (only[0],)
    assert total_bid(optimal_allocate(only)) == 7


def test_a_bid_of_zero_still_wins_what_nobody_else_wants():
    bids = [PackageBid("A", ("north",), 0, 0)]
    assert who(optimal_allocate(bids)) == ["A"]


def test_the_item_universe_is_the_union_of_every_bid():
    assert item_universe(PINNED) == ("north", "south")
    assert item_universe([PackageBid("A", ("west", "east"), 1, 1)]) == ("east", "west")


# ------------------------------------------------------------------- the size guard


def test_too_many_items_is_refused_by_name():
    bids = [
        PackageBid("A", tuple(f"item{i}" for i in range(MAX_ITEMS + 1)), 1, 1),
    ]
    with pytest.raises(ValueError) as caught:
        optimal_allocate(bids)
    message = str(caught.value)
    assert str(MAX_ITEMS) in message
    assert "13" in message, "the message must say how big the input actually was"
    assert "item" in message.lower()


def test_too_many_bids_is_refused_by_name():
    bids = [PackageBid(f"b{i}", ("north",), 1, 1) for i in range(MAX_BIDS + 1)]
    with pytest.raises(ValueError) as caught:
        optimal_allocate(bids)
    message = str(caught.value)
    assert str(MAX_BIDS) in message
    assert "21" in message
    assert "bid" in message.lower()


def test_greedy_has_no_size_limit():
    """Greedy is linearithmic, so only the exhaustive search needs a bound."""
    bids = [PackageBid(f"b{i}", (f"item{i}",), 1, 1) for i in range(MAX_BIDS + 50)]
    assert len(greedy_allocate(bids)) == MAX_BIDS + 50


def test_the_bound_is_reachable_in_a_web_request():
    """The stated bound has to be one the solver can actually finish. This is the
    measured worst shape: 20 near-equal singleton bids from 20 bidders over 12 items,
    which maximises the number of feasible allocations the search must walk."""
    import time

    bids = [
        PackageBid(f"b{i}", (f"item{i % MAX_ITEMS}",), 100 - i * 1e-9, 100 - i * 1e-9)
        for i in range(MAX_BIDS)
    ]
    started = time.perf_counter()
    accepted = optimal_allocate(bids)
    elapsed = time.perf_counter() - started
    assert len(accepted) == MAX_ITEMS
    assert elapsed < 1.0, f"exhaustive search took {elapsed:.3f}s at the stated bound"


def test_a_bid_for_no_items_is_refused():
    """A bundle of nothing conflicts with nothing and would win for free."""
    with pytest.raises(ValueError, match="at least one item"):
        greedy_allocate([PackageBid("A", (), 5, 5)])
    with pytest.raises(ValueError, match="at least one item"):
        optimal_allocate([PackageBid("A", (), 5, 5)])


# =====================================================================================
# Task 6: the two mechanisms.
#
# `vcg_package` is truthful and `greedy_package` is not, and the pair of tests
# `test_greedy_package_rewards_shading` / `test_vcg_package_closes_that_deviation`
# is what says so. The welfare-gap tests carry the other half of the lesson: the
# practical algorithm is cheap because it is wrong, and here is by how much.
# =====================================================================================

# Complements: A wants the pair, and this time the two singles are worth less apart.
COMPLEMENTS = [
    PackageBid("A", ("north", "south"), 10, 10),
    PackageBid("B", ("north",), 4, 4),
    PackageBid("C", ("south",), 4, 4),
]

# One item, one rival: the smallest setting in which pay-your-own-bid is exploitable.
SHADEABLE = [PackageBid("A", ("north",), 10, 10), PackageBid("B", ("north",), 3, 3)]


def shaded(bids, bidder, factor):
    """The same bidders, with one of them scaling every bid they submitted."""
    return [
        PackageBid(b.bidder, b.items, b.value, b.bid * factor)
        if b.bidder == bidder
        else b
        for b in bids
    ]


# ------------------------------------------------------------- the whole point, again


def test_greedy_package_rewards_shading():
    """A pinned profitable deviation, because a `greedy_package` that were silently
    truthful would otherwise pass every other test in this file.

    A values the northern licence at 10 and B bids 3 for it. Bidding honestly wins the
    licence and hands the entire surplus to the seller. Bidding 4 wins exactly the same
    licence — greedy still ranks A first — and keeps 6, because under this rule the
    winner pays their own bid and nothing else in the auction constrains it.
    """
    honest = run_packages("greedy_package", SHADEABLE)
    deviant = run_packages("greedy_package", shaded(SHADEABLE, "A", 0.4))

    assert honest.result["allocation"]["A"] == ["north"]
    assert honest.result["payments"]["A"] == 10
    assert honest.result["utilities"]["A"] == 0

    assert deviant.result["allocation"]["A"] == ["north"]
    assert deviant.result["payments"]["A"] == 4
    assert deviant.result["utilities"]["A"] == 6

    assert deviant.result["utilities"]["A"] > honest.result["utilities"]["A"]


def test_vcg_package_closes_that_deviation():
    """The same bidders, the same lie, and under VCG it buys nothing at all."""
    honest = run_packages("vcg_package", SHADEABLE)
    deviant = run_packages("vcg_package", shaded(SHADEABLE, "A", 0.4))
    underbid = run_packages("vcg_package", shaded(SHADEABLE, "A", 0.2))

    assert honest.result["payments"]["A"] == 3, "A pays what B would have got, no more"
    assert honest.result["utilities"]["A"] == 7

    assert deviant.result["utilities"]["A"] == 7, "shading changes the bill by nothing"
    assert underbid.result["allocation"] == {"B": ["north"]}
    assert underbid.result["utilities"]["A"] == 0, "underbidding only loses the licence"


def test_vcg_package_is_truthful_on_small_instances():
    """Property test: over random value profiles no deviation beats bidding value.

    Each bidder submits up to two XOR bundles; the deviation scales *every* bid that
    bidder submitted, which is the whole strategy space an XOR bidder has.
    """
    rng = random.Random(17)
    items = ["north", "south", "east"]
    for _ in range(120):
        bids = []
        for who_ in "ABC":
            for _ in range(rng.randint(1, 2)):
                bundle = tuple(sorted(rng.sample(items, rng.randint(1, 2))))
                v = rng.randint(1, 20)
                bids.append(PackageBid(who_, bundle, v, v))
        honest = run_packages("vcg_package", bids).result["utilities"]["A"]
        for factor in (0, 0.5, 1.5, 3):
            deviant = run_packages("vcg_package", shaded(bids, "A", factor))
            assert deviant.result["utilities"]["A"] <= honest + TOL, (
                f"bids={bids} factor={factor} beat truthful bidding"
            )


def test_vcg_package_never_charges_more_than_the_winning_bid():
    """Individual rationality: a VCG bill is an externality, so it sits between nothing
    and what the winner offered."""
    rng = random.Random(23)
    items = ["north", "south", "east"]
    for _ in range(120):
        bids = [
            PackageBid(
                rng.choice("ABCD"),
                tuple(sorted(rng.sample(items, rng.randint(1, 2)))),
                v := rng.randint(1, 25),
                v,
            )
            for _ in range(rng.randint(1, 6))
        ]
        result = run_packages("vcg_package", bids).result
        won = {b.bidder: b.bid for b in optimal_allocate(bids)}
        for bidder, paid in result["payments"].items():
            assert paid >= -TOL
            assert paid <= won.get(bidder, 0) + TOL


# ------------------------------------------------------------------- the welfare gap


def test_both_mechanisms_report_what_greedy_costs():
    """The gap is the point of running two solvers, so both mechanisms carry it."""
    for name in ("greedy_package", "vcg_package"):
        result = run_packages(name, PINNED).result
        assert result["greedy_welfare"] == 10
        assert result["optimal_welfare"] == 12
        assert result["welfare_gap"] == 2, f"{name} must report the 2 greedy gave up"


def test_the_gap_is_zero_when_greedy_finds_the_optimum():
    for name in ("greedy_package", "vcg_package"):
        result = run_packages(name, COMPLEMENTS).result
        assert result["welfare_gap"] == 0
        assert result["greedy_welfare"] == result["optimal_welfare"] == 10


def test_a_bundle_beats_two_singles_worth_less_apart():
    """Complementarities: the pair is worth 10 and the halves 4 each, so selling them
    separately would destroy value — which only a package bid can express."""
    result = run_packages("vcg_package", COMPLEMENTS).result
    assert result["allocation"] == {"A": ["north", "south"]}
    assert result["payments"]["A"] == 8, "A pays what B and C together would have got"
    assert result["utilities"]["A"] == 2
    assert result["efficient"] is True


def test_greedy_allocation_is_inefficient_on_the_pinned_case():
    """The gap is not bookkeeping: greedy really does misallocate the licences."""
    greedy = run_packages("greedy_package", PINNED).result
    vcg = run_packages("vcg_package", PINNED).result

    assert greedy["allocation"] == {"A": ["north", "south"]}
    assert greedy["welfare"] == 10
    assert greedy["efficient"] is False

    assert vcg["allocation"] == {"B": ["north"], "C": ["south"]}
    assert vcg["welfare"] == 12
    assert vcg["efficient"] is True
    assert vcg["payments"] == {"A": 0, "B": 4, "C": 4}
    assert vcg["utilities"] == {"A": 0, "B": 2, "C": 2}
    assert vcg["revenue"] == 8


# ------------------------------------------------------------------- the teaching text


def test_vcg_says_out_loud_that_truthfulness_needs_the_optimal_allocation():
    """The trap this mechanism exists to name: VCG payments are only truthful on top of
    an optimal allocation, so running them over the greedy one silently breaks it."""
    details = " ".join(s.detail for s in run_packages("vcg_package", PINNED).steps)
    assert "optimal" in details and "truthful" in details
    assert "greedy" in details.lower()


def test_greedy_says_out_loud_that_it_is_an_approximation():
    details = " ".join(s.detail for s in run_packages("greedy_package", PINNED).steps)
    assert "optimal" in details
    assert "own bid" in details


# ------------------------------------------------------------------- engine contract


def test_every_package_mechanism_declares_whether_truth_is_dominant():
    assert PACKAGE_REGISTRY["vcg_package"].truthful_dominant is True
    assert PACKAGE_REGISTRY["greedy_package"].truthful_dominant is False


def test_traces_are_wellformed_and_json_safe():
    """The same contract every single-item mechanism is held to, so these two are ready
    to be moved into the shared registry without a surprise."""
    import json

    rng = random.Random(29)
    items = ["north", "south", "east", "west"]
    for name in sorted(PACKAGE_REGISTRY):
        for _ in range(60):
            bids = [
                PackageBid(
                    rng.choice("ABC"),
                    tuple(sorted(rng.sample(items, rng.randint(1, 3)))),
                    v := rng.randint(0, 30),
                    v,
                )
                for _ in range(rng.randint(1, 7))
            ]
            trace = run_packages(name, bids)
            json.dumps(trace.to_dict())
            r = trace.result
            assert trace.steps
            assert trace.steps[-1].state["winner"] == r["winner"]
            for s in trace.steps:
                assert s.detail.endswith(".")
                assert s.highlight["stage"] and isinstance(s.highlight["bidders"], list)
                assert "bids" in s.state
            assert r["revenue"] == pytest.approx(sum(r["payments"].values()))
            assert r["welfare"] == pytest.approx(sum(r["gains"].values()))
            assert r["welfare_gap"] >= -TOL
            for bidder, paid in r["payments"].items():
                assert paid >= -TOL
            assert list(r["allocation"])[:1] == ([r["winner"]] if r["winner"] else [])


def test_an_unknown_package_mechanism_is_refused_by_name():
    with pytest.raises(ValueError, match="unknown package mechanism"):
        run_packages("nope", PINNED)


def test_running_with_no_bids_is_refused():
    with pytest.raises(ValueError, match="at least one package bid"):
        run_packages("greedy_package", [])


def test_greedy_package_inherits_the_size_guard_because_it_reports_the_gap():
    """Greedy alone has no limit, but the mechanism shows the gap, and showing a gap
    means solving the optimal side too. Task 7 has to surface this as a 400, not a 500."""
    bids = [PackageBid(f"b{i}", (f"item{i}",), 1, 1) for i in range(MAX_BIDS + 1)]
    for name in sorted(PACKAGE_REGISTRY):
        with pytest.raises(ValueError, match="NP-hard"):
            run_packages(name, bids)


def test_a_run_at_exactly_the_bound_still_succeeds():
    bids = [
        PackageBid(f"b{i}", (f"item{i % MAX_ITEMS}",), 100 - i, 100 - i)
        for i in range(MAX_BIDS)
    ]
    for name in sorted(PACKAGE_REGISTRY):
        assert run_packages(name, bids).result["welfare_gap"] >= 0
