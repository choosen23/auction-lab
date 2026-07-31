"""What the learning cost: the day a learner could have had, minus the one it got.

A learner's cumulative utility on its own says almost nothing. High is not good if the
market was generous, and low is not bad if it was brutal. Regret is the number that fixes
that, by measuring the learner against a *comparator* rather than against zero — and the
whole weight of it sits on which comparator, so :func:`regret_summary` says exactly.

This lives apart from :mod:`agt.bandits` because it is a different kind of thing. The
learners there are strategies: pure functions of what one bidder was allowed to see, run
before the auction. This is scoring, run afterwards by the summary, with the whole series
in hand and no privacy seam to respect. Keeping them in one file would blur a line the
rest of the package works hard to hold.
"""

from typing import Any

from agt.bandits import arm_grid
from agt.mechanisms import run
from agt.trace import Bidder, Number
from agt.world import World


def regret_summary(
    records: list[Any],
    plan: dict[str, dict[str, Any]],
    bidders: list[Bidder],
    mechanism: str,
    params: dict[str, Any],
    world: World,
) -> dict[str, Any]:
    """Cumulative regret against the best fixed arm in hindsight, for every learner playing.

    **The baseline, stated exactly, because a regret number with a vague baseline teaches
    nothing.** For each arm ``a`` on the learner's grid, replay the whole day with that one
    multiplier held fixed: in round *t* the learner bids ``a x value_t`` into the auction
    the other bidders actually ran — their real submitted bids, unchanged — and earns
    whatever the mechanism gives it. The baseline is the single arm whose total over the
    day is largest, and cumulative regret after round *T* is that arm's running total minus
    what was actually earned. This is *external* regret against a fixed comparator, the
    standard bandit baseline, and it is the honest one here because a fixed multiplier is
    precisely the thing the learner is trying to find.

    Two properties worth knowing before reading a curve of it. The comparator is chosen
    with the whole day in hand, so an individual round's regret can be negative — the
    learner really did beat the best fixed arm on that round — and the curve can dip. The
    total cannot: over the whole day no arm beats the best arm, so the last point is never
    below zero.

    Per-round values are recomputed from the world rather than read back off the records,
    because a round nobody entered has no trace to read them from and an abstaining bidder
    is missing from the trace of the rounds that did run. :meth:`agt.world.World.value_for`
    is a pure function of the seed, the bidder and the round, so this reproduces the draws
    the series actually played rather than resembling them.

    ponytail: rivals' bids are held at what they actually submitted, so the replay is not
    an equilibrium — a learner that had really bid differently all day would have been bid
    against differently. Ceiling: against reactive rivals (``best_response``, another
    learner) the baseline is a fiction, and the number should be read as regret "against
    this recorded stream of rival bids", not "against this market". Upgrade: none that is
    cheap; regret against a responding market means solving the game, which is phase 5's
    problem and not this function's.

    ponytail: the replay costs one mechanism run per round per arm per learner, which at
    the largest series the runner allows — 50 rounds, 12 bidders, all of them learners on
    the full 20-arm grid — is 12,000 extra auctions and measures at 0.67s against 0.02s for
    the same table without learners. Ceiling: a table of nothing but wide-grid learners is
    roughly thirty times slower to summarize than to run. Upgrade: score the arms
    analytically for single-item mechanisms, which means duplicating each payment rule
    outside the mechanism that owns it — not a trade worth making until somebody is
    actually waiting on it.
    """
    regret: dict[str, list[float]] = {}
    baseline: dict[str, Any] = {}
    values = [
        {b.id: world.value_for(b, record.round) for b in bidders} for record in records
    ]
    for bidder in bidders:
        spec = plan[bidder.id]
        arms = arm_grid(spec["name"], spec["params"])
        if arms is None:
            continue
        earned = [_earned(record, bidder.id) for record in records]
        paths = [
            _replay(arm, records, values, bidders, bidder.id, mechanism, params, world)
            for arm in arms
        ]
        totals = [sum(path) for path in paths]
        best = max(range(len(arms)), key=lambda i: totals[i])
        running = 0.0
        curve = []
        for hindsight, actual in zip(paths[best], earned):
            running += hindsight - actual
            curve.append(running)
        regret[bidder.id] = curve
        baseline[bidder.id] = {
            "rule": "best fixed arm in hindsight",
            "arm": arms[best],
            "utility": totals[best],
            "earned": sum(earned),
            "arms": list(arms),
            "arm_utilities": totals,
        }
    return {"regret": regret, "regret_baseline": baseline}


def _earned(record: Any, who: str) -> Number:
    """What this bidder actually took home in a round, or 0 for a round with no auction."""
    if record.trace is None:
        return 0
    return record.trace.result["utilities"].get(who, 0)


def _replay(
    arm: float,
    records: list[Any],
    values: list[dict[str, Number]],
    bidders: list[Bidder],
    who: str,
    mechanism: str,
    params: dict[str, Any],
    world: World,
) -> list[Number]:
    """What one fixed multiplier would have earned in each round of the day.

    Bidders are rebuilt in their original table order so that ties break where they really
    broke; a mechanism that settles a tie by list position would otherwise hand this bidder
    wins it would not have had.

    The budget is enforced by the same rule the runner uses — a bidder may not submit a bid
    its remaining money could not pay in full — so a hindsight arm that would have spent out
    on round three sits out the rest of the day exactly as a real bidder would, and the
    baseline stays a day this bidder could actually have had.
    """
    budget = world.budget_for(who)
    spent: Number = 0
    path: list[Number] = []
    for record, drawn in zip(records, values):
        bid = arm * drawn[who]
        if budget is not None and bid > budget - spent:
            path.append(0)
            continue
        lineup = [
            Bidder(b.id, drawn[b.id], bid if b.id == who else record.decisions[b.id].bid)
            for b in bidders
            if b.id == who or not record.decisions[b.id].abstain
        ]
        try:
            result = run(mechanism, lineup, dict(params)).result
        except ValueError:
            # A mechanism may refuse a bid outright — a Dutch clock cannot open below its
            # reserve. An arm that could not be submitted earned nothing that round.
            path.append(0)
            continue
        spent += result["payments"].get(who, 0)
        path.append(result["utilities"].get(who, 0))
    return path
