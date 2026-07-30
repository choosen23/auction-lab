"""Repeated rounds: drive the strategies, thread the history, and summarize the path.

One round is one *unchanged* phase 1 auction. All this module adds is the loop around it:
ask every bidder's strategy for a bid, run the mechanism on those bids, narrow the result
into history that the next round's strategies are allowed to see, and afterwards report
what moved and whether it stopped moving.

Values are fixed for the whole series; only bids move. Redrawing values per round is a
later phase, and mixing the two would make a bid path impossible to read.
"""

import copy
from dataclasses import dataclass
from typing import Any

from agt.mechanisms import REGISTRY, run
from agt.registry import _resolve_params
from agt.strategies import (
    STRATEGIES,
    BidDecision,
    StrategyContext,
    decide,
    observe,
)
from agt.trace import Bidder, Trace

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
    """One round: why everybody bid what they bid, and the auction that followed."""

    round: int
    decisions: dict[str, BidDecision]
    trace: Trace

    def to_dict(self) -> dict[str, Any]:
        return {
            "round": self.round,
            "decisions": {i: d.to_dict() for i, d in self.decisions.items()},
            "trace": self.trace.to_dict(),
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
) -> Series:
    """Play ``mechanism`` ``rounds`` times, letting each bidder's strategy choose its bid."""
    if mechanism not in REGISTRY:
        raise ValueError(
            f"unknown mechanism {mechanism!r}; expected one of {sorted(REGISTRY)}"
        )
    if isinstance(rounds, bool) or not isinstance(rounds, int):
        raise ValueError(f"rounds must be a whole number, got {rounds!r}")
    if not 1 <= rounds <= MAX_ROUNDS:
        raise ValueError(f"rounds must be between 1 and {MAX_ROUNDS}, got {rounds}")
    plan = _plan(bidders, strategies)
    resolved = _resolve_params(REGISTRY[mechanism], params or {})

    history = []
    records: list[RoundRecord] = []
    for index in range(rounds):
        decisions = {}
        for seat, bidder in enumerate(bidders):
            spec = plan[bidder.id]
            context = StrategyContext(
                bidder=bidder,
                rival_ids=[other.id for other in bidders if other.id != bidder.id],
                seat=seat,
                n=len(bidders),
                round=index,
                history=list(history),
                mechanism=mechanism,
                params=dict(resolved),
            )
            decisions[bidder.id] = decide(spec["name"], context, spec["params"])
        trace = run(
            mechanism,
            [Bidder(b.id, b.value, decisions[b.id].bid) for b in bidders],
            dict(resolved),
        )
        records.append(RoundRecord(index, decisions, trace))
        # observe() is the privacy seam: the next round's strategies see public bids and
        # the public outcome, never the values sitting in the trace.
        history.append(observe(index, trace))

    return Series(mechanism, resolved, plan, records, _summarize(records, tolerance))


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


def _summarize(records: list[RoundRecord], tolerance: float) -> dict[str, Any]:
    """Everything the timeline, the bid chart and the convergence label are drawn from."""
    ids = [b.id for b in records[0].trace.bidders]
    bids = [{b.id: b.bid for b in r.trace.bidders} for r in records]
    utilities = {i: [r.trace.result["utilities"][i] for r in records] for i in ids}
    reached = _settled_round(bids, tolerance)
    return {
        "bid_paths": {i: [round_bids[i] for round_bids in bids] for i in ids},
        "utilities": utilities,
        "cumulative_utilities": {i: sum(u) for i, u in utilities.items()},
        "revenue": [r.trace.result["revenue"] for r in records],
        "efficiency_rate": sum(r.trace.result["efficient"] for r in records)
        / len(records),
        "converged": reached is not None,
        "converged_round": reached,
    }


def _settled_round(bids: list[dict[str, float]], tolerance: float) -> int | None:
    """The first round nothing after it disturbs, or None if the series is still moving.

    Honesty matters more than a tidy label here: a series that ends mid-move returns
    None, and a single round returns None too, because one observation cannot show that
    anything has settled.
    """
    moved = [
        max(abs(after[i] - before[i]) for i in after)
        for before, after in zip(bids, bids[1:])
    ]
    last = len(bids) - 1
    settled = last
    while settled > 0 and moved[settled - 1] < tolerance:
        settled -= 1
    return settled if settled < last else None
