"""Four bidders with the same money, differing only in how they spread it over the day.

Phase 2's strategies answer "what is this item worth to me". These answer a different
question — "how much of my budget should this item get" — and a budget is what makes the
second question exist at all. Spend now and the day's later, better impressions find you
broke; spend never and the money expires unused. Everything here is a rule for landing
between those two mistakes, and the spend curve is where the rule shows up.

**The strategies are stateless, so the control variable is replayed, not remembered.**
:func:`agt.strategies.decide` calls a plain function with a
:class:`~agt.strategies.StrategyContext` and nothing else; there is no object to hang a
mu on between rounds. So mu, *p* and the PID multiplier are all recomputed from round 0
every round, by walking ``context.history`` and applying the same nudge once per finished
round. That costs a few dozen multiplications and buys a large property: the same series
replays identically, a strategy cannot smuggle state past the privacy seam, and what a
bidder is steering is a pure function of what it was allowed to see.

Every one of them publishes that variable on :attr:`agt.strategies.BidDecision.control`,
so the chart that shows the loop settling reads a number the bidder actually acted on
rather than one inferred from its bids.
"""

from typing import Any

from agt.stages import num
from agt.strategies import BidDecision, StrategyContext, strategy
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


def _clamp(value: float, low: float, high: float) -> float:
    return min(max(value, low), high)


def _horizon(context: StrategyContext) -> int:
    """How many rounds the day has in total, counted from where this bidder stands.

    ``rounds_left`` includes the round about to be played, so this is the world's own
    round count — and pacing has to divide by it, which is why the world owns it rather
    than the loop.
    """
    return context.round + context.rounds_left


def _spend_path(context: StrategyContext) -> list[Number]:
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


def _ratio(target: Number | None, actual: Number) -> float:
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
    return _clamp(target / actual, 1 / CAP, CAP)


def _steer(
    context: StrategyContext, start: float, step: float
) -> tuple[float, float, Number | None]:
    """Replay a control variable forward: one nudge per finished round, clamped to [0, 1].

    Returns where the variable stands now, where it stood before the last nudge, and the
    straight-line spend target it was last steered against. The target is a flat line
    because it is the honest null: a bidder with no forecast of what the rest of the day
    holds has no reason to expect the afternoon to be worth more than the morning.
    """
    rounds, budget = _horizon(context), context.budget
    value = previous = start
    target: Number | None = None
    for index, spent in enumerate(_spend_path(context)):
        target = None if budget is None else budget * (index + 1) / rounds
        previous = value
        value = _clamp(value * (1 + step * (_ratio(target, spent) - 1)), 0, 1)
    return value, previous, target


def _direction(value: float, previous: float) -> str:
    if value > previous:
        return "up"
    return "down" if value < previous else "steady"


def _knob(name: str, label: str, value: float, previous: float) -> dict[str, Any]:
    """The control variable, in the shape a chart reads. See ``BidDecision.control``."""
    return {
        "name": name,
        "label": label,
        "value": value,
        "previous": previous,
        "direction": _direction(value, previous),
    }


def _pacing_note(context: StrategyContext, target: Number | None) -> str:
    """Where this bidder stands against a budget it is trying to land exactly on."""
    if context.budget is None:
        return (
            "There is no budget to run out of, so there is nothing to hold back for and "
            "the feedback has no reason to shade at all"
        )
    return (
        f"It has spent {num(context.spent)} of its {num(context.budget)} with "
        f"{context.rounds_left} of {_horizon(context)} rounds still to play, against a "
        f"straight-line target of {num(target or 0)} by now"
    )


# ---------------------------------------------------------------------- the control


@strategy(
    "budget_blind",
    label="Budget-blind (the control case)",
    description="Bid your value every round until the money runs out, then go quiet.",
)
def budget_blind(context: StrategyContext) -> BidDecision:
    """Bid the value; sit out once the budget cannot cover a win.

    This is the strategy every other one here is measured against, and it is worth being
    clear about what is wrong with it: *nothing, round by round*. Every bid it makes is
    the bid a textbook second-price analysis would call correct. The mistake is not in
    any bid, it is in the sequence — it treats each round as the only round, so the
    budget goes to whichever impressions happen to come first, and the ones that come
    later, however good, arrive to find the money gone.
    """
    me = context.bidder
    remaining = context.remaining
    if remaining is None or me.value <= remaining:
        return BidDecision(
            me.value,
            f"{me.id} bids its full value of {num(me.value)}, the same answer it would "
            "give if this were the only round of the day. It never asks how much of the "
            "day is left or what the budget has to cover, so the money goes to whatever "
            "shows up first rather than to what is worth most.",
        )

    won = [view.price for view in context.history if view.winner == me.id]
    bought = (
        f"It bought {len(won)} impression{'' if len(won) == 1 else 's'} at an average of "
        f"{num(sum(won) / len(won))}, and it is sitting out this one worth {num(me.value)}"
        if won
        else f"It never won anything, and is now sitting out a round worth {num(me.value)}"
    )
    return BidDecision(
        me.value,
        f"{me.id} has {num(remaining)} left of its {num(context.budget)} budget, which "
        f"will not cover the {num(me.value)} this round is worth, so it does not enter. "
        f"{bought}. Nothing was wrong with any single bid it made: the mistake was made "
        "rounds ago, by spending as though the day had no rest to it, and pace is the "
        "only thing that would have fixed it.",
        abstain=True,
    )


# ---------------------------------------------------------------------- mu, the shadow price


MU_START = {
    "type": "number",
    "default": 0.5,
    "label": "Starting multiplier (mu)",
    "min": 0,
    "max": 1,
    "description": "What fraction of value to bid before any feedback has arrived.",
}


@strategy(
    "pace_multiplicative",
    label="Multiplicative pacing",
    description="Bid value x mu, and steer mu so the budget lands empty at the final round.",
    params={"mu_start": MU_START, "step": STEP},
)
def pace_multiplicative(
    context: StrategyContext, mu_start: float = 0.5, step: float = 0.2
) -> BidDecision:
    """Bid ``value x mu``, nudging mu by target spend over actual spend after each round.

    **mu is the lesson.** It starts as an arbitrary knob and ends as a measurement: the
    fraction of surplus this bidder must give up on every impression for the money to
    last the day. That is a shadow price on the budget — the marginal value of one more
    unit of money — and nobody computed it. It appeared because the loop kept pushing mu
    down when spend ran hot and up when it ran cold, until it stopped needing to push.

    Multiplying the value is not the only way to spend less; ``throttle`` spends less by
    entering fewer auctions, and the two make very different purchases at the same total
    spend. Scaling the bid keeps the bidder in *every* auction and lets the mechanism
    decide which ones it wins, which turns out to select the cheap ones.
    """
    mu, previous, target = _steer(context, mu_start, step)
    me = context.bidder
    bid = me.value * mu
    moving = {
        "down": "and heading down, because spend is running ahead of the straight line",
        "up": "and heading up, because the money is not going out fast enough",
        "steady": "and steady, which is what a loop with nothing left to correct looks like",
    }[_direction(mu, previous)]
    return BidDecision(
        bid,
        f"{me.id} bids value x mu = {num(me.value)} x {mu:.3f} = {num(bid)}. mu is at "
        f"{mu:.3f} {moving}. {_pacing_note(context, target)}. Once mu settles it has "
        "stopped being a knob and become a shadow price on the budget: the share of "
        "surplus this bidder has to hand back on every single impression to make the "
        "money reach the end of the day.",
        control=_knob("mu", "Pacing multiplier (mu)", mu, previous),
    )
