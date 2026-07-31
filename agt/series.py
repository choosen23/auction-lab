"""Repeated rounds: drive the strategies, thread the history, and summarize the path.

One round is one *unchanged* phase 1 auction. All this module adds is the loop around it:
ask every bidder's strategy for a bid, run the mechanism on the bids of everyone who
entered, narrow the result into history that the next round's strategies are allowed to
see, and afterwards report what moved and whether it stopped moving.

What varies between rounds is decided by the :class:`~agt.world.World` — how many rounds
there are, whether values are redrawn, who has a budget, and where randomness comes from.
There is one world, always: a caller who supplies none gets ``World(rounds=rounds)``,
whose every default is phase 2's behaviour. That single line is the entire seam, and it
is why nothing below it asks whether a world was given.
"""

import copy
import dataclasses
import math
from dataclasses import dataclass
from typing import Any

from agt.mechanisms import REGISTRY, run
from agt.registry import _resolve_params
from agt.regret import regret_summary
from agt.stages import num
from agt.strategies import (
    STRATEGIES,
    BidDecision,
    StrategyContext,
    decide,
    observe,
)
from agt.trace import Bidder, Number, Trace
from agt.world import World

# A round is cheap, but `best_response` runs the whole mechanism once per candidate, so
# the cost is rounds x bidders^2 auctions. 50 keeps the worst case in the low thousands.
MAX_ROUNDS = 50

# ponytail: an absolute tolerance, because a bid that moves by less than a hundredth of a
# currency unit is not moving in any way a learner can see. Ceiling — values in the
# millions would make this effectively exact equality. Upgrade: scale it by the largest
# value in the auction if that ever bites.
TOLERANCE = 1e-9


@dataclass(frozen=True)
class RoundRecord:
    """One round: why everybody bid what they bid, and the auction that followed.

    ``decisions`` always covers every bidder, including the ones that abstained — a
    bidder that sat out still explains itself. ``trace`` is ``None`` when *nobody*
    entered and there was therefore no auction to run. "No fill" is a real outcome and
    inventing an empty auction to avoid the ``None`` would be a lie about what happened.
    """

    round: int
    decisions: dict[str, BidDecision]
    trace: Trace | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "round": self.round,
            "decisions": {i: d.to_dict() for i, d in self.decisions.items()},
            "trace": None if self.trace is None else self.trace.to_dict(),
        }


@dataclass(frozen=True)
class Series:
    """A run of repeated rounds and the summary the timeline and bid chart draw."""

    mechanism: str
    params: dict[str, Any]
    strategies: dict[str, dict[str, Any]]
    rounds: list[RoundRecord]
    summary: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "mechanism": self.mechanism,
            "params": dict(self.params),
            "strategies": copy.deepcopy(self.strategies),
            "rounds": [r.to_dict() for r in self.rounds],
            "summary": copy.deepcopy(self.summary),
        }


def run_series(
    mechanism: str,
    bidders: list[Bidder],
    strategies: dict[str, dict[str, Any]],
    rounds: int = 8,
    params: dict[str, Any] | None = None,
    tolerance: float = TOLERANCE,
    world: World | None = None,
) -> Series:
    """Play ``mechanism`` for a whole world, letting each bidder's strategy choose its bid.

    ``world`` is the one addition phase 4 makes, and the line that defaults it is the only
    place anything here knows a world might not have been supplied. A caller that passes
    none gets ``World(rounds=rounds)``, which draws no values, hands out no budgets and
    bars nobody — phase 2 exactly. A caller that passes one owns the round count with it,
    because a bidder pacing a budget steers on how much of the day is left.
    """
    if mechanism not in REGISTRY:
        raise ValueError(
            f"unknown mechanism {mechanism!r}; expected one of {sorted(REGISTRY)}"
        )
    refuse_repeated_rounds(mechanism)
    world = World(rounds=rounds) if world is None else world
    if isinstance(world.rounds, bool) or not isinstance(world.rounds, int):
        raise ValueError(f"rounds must be a whole number, got {world.rounds!r}")
    if not 1 <= world.rounds <= MAX_ROUNDS:
        raise ValueError(f"rounds must be between 1 and {MAX_ROUNDS}, got {world.rounds}")
    plan = _plan(bidders, strategies)
    resolved = _resolve_params(REGISTRY[mechanism], params or {})

    history = []
    records: list[RoundRecord] = []
    spent: dict[str, Number] = {b.id: 0 for b in bidders}
    # Each bidder's *own* past value draws, kept per bidder rather than on the shared
    # history for the same reason `observe` exists: a value is private, and a list one
    # bidder is handed cannot leak into another's context by being widened later.
    drawn: dict[str, list[Number]] = {b.id: [] for b in bidders}
    for index in range(world.rounds):
        playing = [Bidder(b.id, world.value_for(b, index), b.bid) for b in bidders]
        decisions = {}
        for seat, bidder in enumerate(playing):
            spec = plan[bidder.id]
            context = StrategyContext(
                bidder=bidder,
                rival_ids=[other.id for other in playing if other.id != bidder.id],
                seat=seat,
                n=len(playing),
                round=index,
                history=list(history),
                mechanism=mechanism,
                params=dict(resolved),
                budget=world.budget_for(bidder.id),
                spent=spent[bidder.id],
                rounds_left=world.rounds - index,
                rng=world.rng_for("bid", bidder.id, index),
                own_values=list(drawn[bidder.id]),
            )
            decisions[bidder.id] = _afford(decide(spec["name"], context, spec["params"]), context)
        # Abstainers are removed rather than entered at 0: a 0 clears a reserve of 0 and
        # could win the item for nothing, fabricating an impression nobody bid on.
        entrants = [b for b in playing if not decisions[b.id].abstain]
        trace = (
            run(
                mechanism,
                [Bidder(b.id, b.value, decisions[b.id].bid) for b in entrants],
                dict(resolved),
            )
            if entrants
            else None
        )
        records.append(RoundRecord(index, decisions, trace))
        for who, paid in _payments(trace).items():
            spent[who] += paid
        # observe() is the privacy seam: the next round's strategies see public bids and
        # the public outcome, never the values sitting in the trace.
        history.append(observe(index, trace))
        for bidder in playing:
            drawn[bidder.id].append(bidder.value)

    summary = _summarize(records, world, tolerance)
    # Regret is asked for rather than assembled here: only a strategy that chooses between
    # a declared grid of arms has a hindsight comparator, and `arm_grid` is what knows
    # which strategies those are. A table with no learner in it gets no regret series at
    # all, which is how the chart stays a consequence of the data rather than of a name.
    summary.update(regret_summary(records, plan, bidders, mechanism, resolved, world))
    return Series(mechanism, resolved, plan, records, summary)


def _payments(trace: Trace | None) -> dict[str, Number]:
    """What each bidder actually handed over. A round nobody entered cost nobody anything."""
    return {} if trace is None else trace.result["payments"]


def _afford(decision: BidDecision, context: StrategyContext) -> BidDecision:
    """Bar a bidder whose remaining budget will not cover the bid it just chose.

    ponytail: the *bid* is treated as the most a bidder could owe, so the budget check is
    made before the auction rather than after it. That is exact under first-price, Dutch
    and all-pay rules, and conservative under the rest — a second-price bidder pays
    somebody else's lower bid, so this bars it from wins it could in fact have afforded.
    Ceiling: a bidder with 28 left and a value of 100 sits out a round it might have won
    for 5, and no mechanism here charges above the bid, so nobody is ever let in and then
    found short. Upgrade: ask the mechanism for a per-bidder payment bound, or let a bid
    clear and settle the overspend afterwards the way a real exchange does — either is a
    change to the mechanism protocol, not to this function.
    """
    remaining = context.remaining
    if decision.abstain or remaining is None or decision.bid <= remaining:
        return decision
    # `replace` rather than a fresh BidDecision: barring a bidder changes whether it
    # entered and why, and nothing else. Everything the strategy published about the
    # decision — what it weighed, what it is steering — describes a decision it really
    # made, and a control variable that vanished on the rounds a bidder was barred would
    # leave a hole in the one chart drawn to show the loop settling.
    return dataclasses.replace(
        decision,
        why=(
            f"{context.bidder.id} wanted to bid {num(decision.bid)} but has only "
            f"{num(remaining)} left of its {num(context.budget)} budget, so it does not "
            "enter this round at all: a bidder may not submit a bid it could not pay in "
            "full. It does not bid 0 instead — a 0 is a real bid that could win the item "
            "for nothing."
        ),
        abstain=True,
    )


def refuse_repeated_rounds(mechanism: str) -> None:
    """Refuse a mechanism whose input is not one scalar bid per bidder per round.

    Every strategy in :mod:`agt.strategies` answers a single question — what number
    should I bid — and a bidder submitting XOR bundles has no such number: their strategy
    space is which bundles to name and what to offer for each, which is a different
    problem with different equilibria. Faking a scalar out of a bundle would draw a bid
    path and a convergence verdict that mean nothing, so the answer is no and why.

    Lives here rather than only in :mod:`agt.api` because the engine is meant to run
    under Pyodide with no server in front of it, and :func:`agt.api.validate_series`
    calls it so the reason arrives before a complaint about the wrong payload key.
    """
    spec = REGISTRY[mechanism]
    if spec.input_kind == "single":
        return
    raise ValueError(
        f"{spec.label} ({mechanism}) takes package bids, and repeated rounds are only "
        "defined for single-item mechanisms: a strategy picks one number per round, and "
        "a bidder offering all-or-nothing bundles has no one number to pick. Run it as a "
        "single auction instead."
    )


def _plan(
    bidders: list[Bidder], strategies: dict[str, dict[str, Any]]
) -> dict[str, dict[str, Any]]:
    """Resolve one strategy per bidder, defaulting to ``manual`` — phase 1's behaviour."""
    playing = {b.id for b in bidders}
    strangers = sorted(set(strategies) - playing)
    if strangers:
        raise ValueError(
            f"strategies were given for bidders who are not in the auction: {strangers}"
        )
    plan = {}
    for bidder in bidders:
        spec = strategies.get(bidder.id) or {}
        name = spec.get("name", "manual")
        if name not in STRATEGIES:
            raise ValueError(
                f"unknown strategy {name!r} for bidder {bidder.id!r}; "
                f"expected one of {sorted(STRATEGIES)}"
            )
        plan[bidder.id] = {"name": name, "params": dict(spec.get("params") or {})}
    return plan


def _summarize(
    records: list[RoundRecord], world: World, tolerance: float
) -> dict[str, Any]:
    """Everything the timeline, the charts and the convergence label are drawn from.

    Read off ``decisions`` rather than off the traces, because a bidder that abstained
    is missing from its round's trace and a round nobody entered has no trace at all. A
    bidder that did not enter has no bid that round — ``None`` in ``bid_paths``, so the
    chart shows a gap rather than a fabricated number — and earns nothing.

    ponytail: ``efficiency_rate`` counts a round nobody entered as inefficient, and in a
    round with abstainers it is the mechanism's own verdict, which only compares the
    bidders who entered. Ceiling — a round where the highest-value bidder was barred and
    a lesser one won reads as efficient, and it is not: the item did not reach the
    bidder who valued it most, it reached the best-funded one. Upgrade: record each
    round's drawn values on the ``RoundRecord`` and score welfare against everybody,
    which is also what a regret series will need in task 4.
    """
    ids = list(records[0].decisions)
    bids = [
        {i: None if d.abstain else d.bid for i, d in r.decisions.items()} for r in records
    ]
    utilities = {
        i: [_result(r).get("utilities", {}).get(i, 0) for r in records] for i in ids
    }
    spend: dict[str, list[Number]] = {i: [] for i in ids}
    for record in records:
        for i in ids:
            running = spend[i][-1] if spend[i] else 0
            spend[i].append(running + _payments(record.trace).get(i, 0))
    reached = _settled_round(bids, tolerance)
    return {
        "bid_paths": {i: [round_bids[i] for round_bids in bids] for i in ids},
        "utilities": utilities,
        "cumulative_utilities": {i: sum(u) for i, u in utilities.items()},
        "revenue": [_result(r).get("revenue", 0) for r in records],
        "efficiency_rate": sum(bool(_result(r).get("efficient")) for r in records)
        / len(records),
        "converged": reached is not None,
        "converged_round": reached,
        "spend": spend,
        "budgets": dict(world.budgets),
        "no_auction_rounds": [r.round for r in records if r.trace is None],
    }


def _result(record: RoundRecord) -> dict[str, Any]:
    """A round's scoring, or an empty one for a round that had no auction to score."""
    return {} if record.trace is None else record.trace.result


def _settled_round(bids: list[dict[str, Number | None]], tolerance: float) -> int | None:
    """The first round nothing after it disturbs, or None if the series is still moving.

    Honesty matters more than a tidy label here: a series that ends mid-move returns
    None, and a single round returns None too, because one observation cannot show that
    anything has settled.

    Entering or leaving the auction counts as an infinite move, because a bidder that
    sat out and came back has changed what it is doing even when the two bids either
    side of the gap are equal. Two consecutive rounds a bidder sat out are no move at
    all: a bidder that has spent out and gone quiet really has stopped.
    """
    moved = [
        max(_gap(before[i], after[i]) for i in after)
        for before, after in zip(bids, bids[1:])
    ]
    last = len(bids) - 1
    settled = last
    while settled > 0 and moved[settled - 1] < tolerance:
        settled -= 1
    return settled if settled < last else None


def _gap(before: Number | None, after: Number | None) -> float:
    """How far one bidder's bid moved between two rounds; ``None`` means it did not bid."""
    if before is None and after is None:
        return 0.0
    if before is None or after is None:
        return math.inf
    return abs(after - before)
