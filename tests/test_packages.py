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
    PackageBid,
    greedy_allocate,
    item_universe,
    optimal_allocate,
    total_bid,
)

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
