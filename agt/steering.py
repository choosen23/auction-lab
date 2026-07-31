"""The feedback law the budget pacers steer with, apart from the bidders that use it.

One rule, three actuators. `pace_multiplicative` moves how much it bids, `throttle` moves
how often it enters, and both drive the mover the same way: compare the money that should
have gone out by now against the money that actually did, and nudge. Keeping the law here
rather than inside either bidder is what makes that claim checkable — the two strategies
demonstrably share a controller, so a difference between them is a difference of actuator
and cannot be a difference of tuning hidden in a copy.

Everything here is a pure function of a :class:`~agt.strategies.StrategyContext`. Nothing
is remembered between rounds; see :mod:`agt.pacing` for why that is deliberate.
"""

from typing import Any

from agt.strategies import StrategyContext
from agt.trace import Number

# The most one round's news may multiply a control variable by, in either direction. A
# bidder that won nothing all day has an infinite target-over-actual ratio, and without a
# bound the first nudge would slam the knob to its stop and the loop would learn nothing
# from every round after it.
#
# ponytail: one shared cap for every steered variable. Ceiling — pacing and throttling
# want the same caution here, but a controller tuned for a fast market might not. Upgrade:
# make it a param the first time a lesson needs a strategy to move faster than doubling.
CAP = 2.0

# The default is 0.2 because it was measured, not guessed. Over a 30-round day against a
# steady market, across ten seeds:
#
#   step 0.1  under-damped. mu falls monotonically and never overshoots, but too slowly to
#             stop the early overspending: 84-99% of the budget was gone by half time and
#             the bidder was then barred for the rest of the day, 3 to 16 rounds of it.
#   step 0.2  converged. mu dips to about 0.15, settles near 0.26, and the bidder was
#             barred on *no* round in any of the ten seeds.
#   step 0.3  over-damped. mu over-corrects downward, only 38-48% of the budget is spent by
#             half time, and the day ends in a catch-up scramble.
#
# The well-tuned gain leaves a little on the table — 0.2 finished on 66-93% of the budget
# where the badly tuned ones burned 94-100% of it — and that is the trade, not a defect: a
# loop tuned to spend every last unit is a loop tuned to spend it early.
STEP = {
    "type": "number",
    "default": 0.2,
    "label": "Step size",
    "min": 0,
    "max": 1,
    "description": "How much of each round's correction to actually apply. 0 never steers.",
}


def clamp(value: float, low: float, high: float) -> float:
    return min(max(value, low), high)


def horizon(context: StrategyContext) -> int:
    """How many rounds the day has in total, counted from where this bidder stands.

    ``rounds_left`` includes the round about to be played, so this is the world's own
    round count — and pacing has to divide by it, which is why the world owns it rather
    than the loop.
    """
    return context.round + context.rounds_left


def spend_path(context: StrategyContext) -> list[Number]:
    """Cumulative spend after each finished round, read off the public record.

    ponytail: a :class:`~agt.strategies.RoundView` records who won and what they paid, so
    this reconstructs spend from wins. Ceiling — under ``all_pay`` rules the losers pay
    too, and this path will understate what they handed over; ``context.spent`` still
    holds the true running total, so only the *shape* is approximate and only there.
    Upgrade: carry each bidder's realised payment on ``RoundView``. Payments are public,
    so that is a legal widening of the privacy seam in :func:`agt.strategies.observe`,
    not a breach of it.
    """
    total: Number = 0
    path = []
    for view in context.history:
        if view.winner == context.bidder.id:
            total += view.price
        path.append(total)
    return path


def ratio(target: Number | None, actual: Number) -> float:
    """Target spend over actual spend: above 1 means underspending, below 1 overspending.

    ``None`` is no budget at all, which is a budget that can never bind — the same answer
    a limitless one would give, arrived at by the same feedback rather than by a branch
    that skips the loop.
    """
    if target is None:
        return CAP
    if target <= 0:
        return 1 / CAP
    if actual <= 0:
        return CAP
    return clamp(target / actual, 1 / CAP, CAP)


def steer(
    context: StrategyContext, start: float, step: float
) -> tuple[float, float, Number | None]:
    """Replay a control variable forward: one nudge per finished round, clamped to [0, 1].

    Returns where the variable stands now, where it stood before the last nudge, and the
    straight-line spend target it was last steered against. The target is a flat line
    because it is the honest null: a bidder with no forecast of what the rest of the day
    holds has no reason to expect the afternoon to be worth more than the morning.
    """
    rounds, budget = horizon(context), context.budget
    value = previous = start
    target: Number | None = None
    for index, spent in enumerate(spend_path(context)):
        target = None if budget is None else budget * (index + 1) / rounds
        previous = value
        value = clamp(value * (1 + step * (ratio(target, spent) - 1)), 0, 1)
    return value, previous, target


def direction(value: float, previous: float) -> str:
    if value > previous:
        return "up"
    return "down" if value < previous else "steady"


def knob(name: str, label: str, value: float, previous: float) -> dict[str, Any]:
    """The control variable, in the shape a chart reads. See ``BidDecision.control``."""
    return {
        "name": name,
        "label": label,
        "value": value,
        "previous": previous,
        "direction": direction(value, previous),
    }
