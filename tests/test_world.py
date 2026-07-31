"""The world model: value draws, budgets, spend, abstention and seeded randomness.

The load-bearing test in this file is the first one. Phase 4 adds one optional argument
to ``run_series``, and the whole design rests on its default reproducing phase 2 exactly
— not approximately, and not because a test-only branch keeps the old path alive.
"""

import json
import random

import pytest

from agt.series import run_series
from agt.strategies import STRATEGIES, BidDecision, strategy
from agt.trace import Bidder
from agt.world import World

BIDDERS = [Bidder("A", 100, 95), Bidder("B", 72, 72), Bidder("C", 41, 41)]


def everyone(name, **params):
    return {b.id: {"name": name, "params": params} for b in BIDDERS}


@pytest.fixture
def temp_strategy():
    """Register a throwaway strategy and take it out again.

    STRATEGIES is a process-wide registry and other test modules iterate it, so a
    strategy registered at import time here would show up in their assertions.
    """
    added = []

    def register(name, fn, **schema):
        strategy(name, label=name, description=name, params=schema or None)(fn)
        added.append(name)
        return {"name": name}

    yield register
    for name in added:
        STRATEGIES.pop(name, None)


def sits_out(context):
    return BidDecision(0, "Sits this round out on purpose, entering nothing.", abstain=True)


# ------------------------------------------------------------------------- the seam


def test_a_series_with_no_world_is_the_phase_two_series_exactly():
    """The default world is not *like* phase 2, it is phase 2 — asserted against a run
    that passes no world at all rather than against a copied-out expectation."""
    plain = run_series("first_price", BIDDERS, everyone("best_response"), rounds=5)
    defaulted = run_series(
        "first_price", BIDDERS, everyone("best_response"), rounds=5, world=World(rounds=5)
    )
    assert defaulted.to_dict() == plain.to_dict()


def test_a_world_without_a_range_keeps_values_fixed():
    s = run_series("second_price", BIDDERS, everyone("truthful"), world=World(rounds=3, seed=9))
    for record in s.rounds:
        assert {b.id: b.value for b in record.trace.bidders} == {"A": 100, "B": 72, "C": 41}


def test_the_world_owns_the_round_count():
    s = run_series("first_price", BIDDERS, everyone("manual"), rounds=2, world=World(rounds=6))
    assert len(s.rounds) == 6


@pytest.mark.parametrize("rounds", [0, -1, 51, 1.5, True, "8"])
def test_a_world_with_a_bad_round_count_is_rejected(rounds):
    with pytest.raises(ValueError, match="rounds"):
        run_series("first_price", BIDDERS, everyone("manual"), world=World(rounds=rounds))


# ------------------------------------------------------------------- value draws


def test_drawn_values_land_inside_the_range():
    s = run_series(
        "first_price",
        BIDDERS,
        everyone("truthful"),
        world=World(rounds=10, seed=3, value_low=10, value_high=20),
    )
    drawn = [b.value for r in s.rounds for b in r.trace.bidders]
    assert len(drawn) == 30
    assert all(10 <= v <= 20 for v in drawn)
    assert len(set(drawn)) > 1, "a fresh draw every round, not one value repeated"


def test_a_degenerate_range_draws_the_same_value_every_round():
    s = run_series(
        "first_price",
        BIDDERS,
        everyone("truthful"),
        world=World(rounds=3, value_low=50, value_high=50),
    )
    assert {b.value for r in s.rounds for b in r.trace.bidders} == {50}


def test_half_a_range_is_rejected():
    with pytest.raises(ValueError, match="value_low"):
        World(rounds=3, value_low=10)


def test_a_range_that_runs_backwards_is_rejected():
    with pytest.raises(ValueError, match="value_low"):
        World(rounds=3, value_low=90, value_high=10)


def test_a_negative_budget_is_rejected():
    with pytest.raises(ValueError, match="budget"):
        World(rounds=3, budgets={"A": -1})


# ------------------------------------------------------------- seeded randomness


def test_the_same_seed_reproduces_the_series_exactly():
    world = World(rounds=6, seed=7, value_low=0, value_high=100)
    a = run_series("first_price", BIDDERS, everyone("truthful"), world=world)
    b = run_series("first_price", BIDDERS, everyone("truthful"), world=world)
    assert a.to_dict() == b.to_dict()


def test_a_different_seed_produces_a_different_series():
    def series(seed):
        return run_series(
            "first_price",
            BIDDERS,
            everyone("truthful"),
            world=World(rounds=6, seed=seed, value_low=0, value_high=100),
        )

    assert series(7).summary["bid_paths"] != series(8).summary["bid_paths"]


def test_a_strategy_draws_from_a_seeded_stream_on_its_context(temp_strategy):
    def coin(context):
        return BidDecision(context.rng.random() * 10, "Bids a draw from its seeded stream.")

    plan = {b.id: temp_strategy("coin", coin) for b in BIDDERS}
    a = run_series("first_price", BIDDERS, plan, world=World(rounds=4, seed=5))
    b = run_series("first_price", BIDDERS, plan, world=World(rounds=4, seed=5))
    c = run_series("first_price", BIDDERS, plan, world=World(rounds=4, seed=6))
    assert a.summary["bid_paths"] == b.summary["bid_paths"]
    assert a.summary["bid_paths"] != c.summary["bid_paths"]


def test_each_bidder_and_round_gets_its_own_stream(temp_strategy):
    def coin(context):
        return BidDecision(context.rng.random() * 10, "Bids a draw from its seeded stream.")

    plan = {b.id: temp_strategy("coin", coin) for b in BIDDERS}
    s = run_series("first_price", BIDDERS, plan, world=World(rounds=4, seed=5))
    paths = s.summary["bid_paths"]
    assert paths["A"] != paths["B"], "two bidders in one round must not share a stream"
    assert len(set(paths["A"])) == 4, "one bidder must not repeat a round's draw"


def test_the_series_never_touches_the_global_random_module(temp_strategy):
    def coin(context):
        return BidDecision(context.rng.random() * 10, "Bids a draw from its seeded stream.")

    plan = {b.id: temp_strategy("coin", coin) for b in BIDDERS}
    random.seed(1234)
    before = random.getstate()
    run_series(
        "first_price",
        BIDDERS,
        plan,
        world=World(rounds=5, seed=2, value_low=0, value_high=50),
    )
    assert random.getstate() == before


# ---------------------------------------------------------------- budget and spend


def test_the_context_carries_budget_spent_and_rounds_left(temp_strategy):
    seen = []

    def spy(context):
        seen.append((context.round, context.budget, context.spent, context.rounds_left))
        return BidDecision(context.bidder.value, "Notes the world it is in, then bids its value.")

    plan = {"A": temp_strategy("spy", spy)}
    run_series("second_price", BIDDERS, plan, world=World(rounds=3, budgets={"A": 500}))
    assert [(r, b, left) for r, b, _, left in seen] == [(0, 500, 3), (1, 500, 2), (2, 500, 1)]
    assert [spent for _, _, spent, _ in seen] == [0, 72, 144]


def test_a_bidder_without_a_budget_has_none_on_its_context(temp_strategy):
    seen = []

    def spy(context):
        seen.append(context.budget)
        return BidDecision(context.bidder.value, "Notes the world it is in, then bids its value.")

    run_series("second_price", BIDDERS, {"A": temp_strategy("spy", spy)}, rounds=2)
    assert seen == [None, None]


def test_spend_accumulates_from_the_payments_each_round_realised():
    s = run_series("second_price", BIDDERS, everyone("truthful"), rounds=4)
    running = {"A": 0, "B": 0, "C": 0}
    for index, record in enumerate(s.rounds):
        for who, amount in record.trace.result["payments"].items():
            running[who] += amount
        for who, total in running.items():
            assert s.summary["spend"][who][index] == total
    assert s.summary["spend"]["A"] == [72, 144, 216, 288]


def test_a_bidder_is_barred_once_its_budget_cannot_cover_its_bid():
    """A wins the first round for 72 and has 28 left, which will not cover the 100 it
    wants to bid, so it stops entering — and the series says so out loud."""
    s = run_series(
        "second_price", BIDDERS, everyone("truthful"), world=World(rounds=4, budgets={"A": 100})
    )
    assert [r.decisions["A"].abstain for r in s.rounds] == [False, True, True, True]
    assert [b.id for b in s.rounds[1].trace.bidders] == ["B", "C"]
    assert s.rounds[1].trace.result["winner"] == "B"
    why = s.rounds[1].decisions["A"].why.lower()
    assert "budget" in why and "28" in why


def test_a_budget_that_never_binds_bars_nobody():
    s = run_series(
        "second_price", BIDDERS, everyone("truthful"), world=World(rounds=3, budgets={"A": 10_000})
    )
    assert not any(r.decisions["A"].abstain for r in s.rounds)


def test_the_budget_rule_is_visible_in_the_series_data():
    s = run_series(
        "second_price", BIDDERS, everyone("truthful"), world=World(rounds=2, budgets={"A": 100})
    )
    assert s.summary["budgets"] == {"A": 100}
    assert s.summary["spend"]["A"] == [72, 72]


def test_a_series_with_no_budgets_reports_none():
    s = run_series("second_price", BIDDERS, everyone("truthful"), rounds=2)
    assert s.summary["budgets"] == {}
    assert s.summary["no_auction_rounds"] == []


# -------------------------------------------------------------------- abstention


def test_abstaining_removes_a_bidder_from_the_round(temp_strategy):
    """A bid of 0 would clear a reserve of 0 and could win the item for nothing; an
    abstainer is not in the auction at all."""
    plan = {"A": temp_strategy("sits_out", sits_out)}
    s = run_series("second_price", BIDDERS, plan, rounds=1)
    record = s.rounds[0]
    assert [b.id for b in record.trace.bidders] == ["B", "C"]
    assert "A" not in record.trace.result["utilities"]
    assert "A" not in record.trace.result["payments"]
    assert record.decisions["A"].abstain is True


def test_an_abstainer_has_no_bid_and_no_utility_that_round(temp_strategy):
    plan = {"A": temp_strategy("sits_out", sits_out)}
    s = run_series("second_price", BIDDERS, plan, rounds=2)
    assert s.summary["bid_paths"]["A"] == [None, None]
    assert s.summary["utilities"]["A"] == [0, 0]
    assert s.summary["cumulative_utilities"]["A"] == 0


def test_a_round_nobody_enters_records_no_auction(temp_strategy):
    plan = {b.id: temp_strategy("sits_out", sits_out) for b in BIDDERS}
    s = run_series("first_price", BIDDERS, plan, rounds=2)
    assert [r.trace for r in s.rounds] == [None, None]
    assert s.summary["no_auction_rounds"] == [0, 1]
    assert s.summary["revenue"] == [0, 0]
    assert s.summary["efficiency_rate"] == 0
    assert s.summary["bid_paths"] == {"A": [None, None], "B": [None, None], "C": [None, None]}


def test_an_all_abstain_round_still_records_why_everybody_stayed_out(temp_strategy):
    plan = {b.id: temp_strategy("sits_out", sits_out) for b in BIDDERS}
    s = run_series("first_price", BIDDERS, plan, rounds=1)
    assert set(s.rounds[0].decisions) == {"A", "B", "C"}
    assert all(d.why.endswith(".") for d in s.rounds[0].decisions.values())


def test_a_no_auction_round_is_json_safe_and_reads_as_null(temp_strategy):
    plan = {b.id: temp_strategy("sits_out", sits_out) for b in BIDDERS}
    payload = json.loads(json.dumps(run_series("first_price", BIDDERS, plan, rounds=1).to_dict()))
    assert payload["rounds"][0]["trace"] is None
    assert payload["rounds"][0]["decisions"]["A"]["abstain"] is True
    assert payload["summary"]["no_auction_rounds"] == [0]


def test_the_history_a_no_auction_round_leaves_behind_is_empty(temp_strategy):
    """The next round's strategies must see the round happened and that nobody bid,
    rather than the round vanishing and history sliding out of step with the timeline."""
    seen = []

    def watcher(context):
        seen.append([(v.round, dict(v.bids), v.winner) for v in context.history])
        return BidDecision(0, "Watches the history and bids nothing worth mentioning.", abstain=True)

    plan = {b.id: temp_strategy("watcher", watcher) for b in BIDDERS}
    run_series("first_price", BIDDERS, plan, rounds=3)
    assert seen[-1] == [(0, {}, None), (1, {}, None)]


def test_a_series_that_goes_quiet_and_stays_quiet_has_settled(temp_strategy):
    """Everybody spends out and the bids stop moving because there are none left to
    move. That really is a fixed point, and the summary is allowed to say so."""
    s = run_series(
        "second_price", BIDDERS, everyone("truthful"), world=World(rounds=5, budgets={"A": 100})
    )
    assert s.summary["bid_paths"]["A"] == [100, None, None, None, None]
    assert s.summary["converged"] is True


def test_entering_after_sitting_out_counts_as_movement(temp_strategy):
    """A bidder that re-enters has changed what it is doing, so the series is not
    settled even though the numbers on either side of the gap match."""

    def every_other(context):
        if context.round % 2:
            return BidDecision(0, "Sits out on the odd rounds to save its money.", abstain=True)
        return BidDecision(context.bidder.value, "Enters on the even rounds and bids its value.")

    plan = {"A": temp_strategy("every_other", every_other)}
    s = run_series("second_price", BIDDERS, plan, rounds=4)
    assert s.summary["bid_paths"]["A"] == [100, None, 100, None]
    assert s.summary["converged"] is False
