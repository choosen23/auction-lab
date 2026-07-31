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

The feedback law itself lives in :mod:`agt.steering`, because ``pace_multiplicative`` and
``throttle`` genuinely share one — they differ in what they move, not in how they decide
to move it, and keeping the law in one place is what makes that claim checkable rather
than merely asserted.
"""

from agt.stages import num
from agt.steering import STEP, clamp, direction, horizon, knob, steer
from agt.strategies import BidDecision, StrategyContext, strategy
from agt.trace import Number


def _pacing_note(context: StrategyContext, target: Number | None) -> str:
    """Where this bidder stands against a budget it is trying to land exactly on."""
    if context.budget is None:
        return (
            "There is no budget to run out of, so there is nothing to hold back for and "
            "the feedback has no reason to shade at all"
        )
    return (
        f"It has spent {num(context.spent)} of its {num(context.budget)} with "
        f"{context.rounds_left} of {horizon(context)} rounds still to play, against a "
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
    mu, previous, target = steer(context, mu_start, step)
    me = context.bidder
    bid = me.value * mu
    moving = {
        "down": "and heading down, because spend is running ahead of the straight line",
        "up": "and heading up, because the money is not going out fast enough",
        "steady": "and steady, which is what a loop with nothing left to correct looks like",
    }[direction(mu, previous)]
    return BidDecision(
        bid,
        f"{me.id} bids value x mu = {num(me.value)} x {mu:.3f} = {num(bid)}. mu is at "
        f"{mu:.3f} {moving}. {_pacing_note(context, target)}. Once mu settles it has "
        "stopped being a knob and become a shadow price on the budget: the share of "
        "surplus this bidder has to hand back on every single impression to make the "
        "money reach the end of the day.",
        control=knob("mu", "Pacing multiplier (mu)", mu, previous),
    )


# ------------------------------------------------------- p, the same money spent worse


P_START = {
    "type": "number",
    "default": 0.5,
    "label": "Starting entry probability (p)",
    "min": 0,
    "max": 1,
    "description": "How often to enter an auction before any feedback has arrived.",
}


@strategy(
    "throttle",
    label="Probabilistic throttling",
    description="Bid your full value, but enter only a fraction p of auctions; steer p to the budget.",
    params={"p_start": P_START, "step": STEP},
)
def throttle(
    context: StrategyContext, p_start: float = 0.5, step: float = 0.2
) -> BidDecision:
    """Enter with probability *p* and bid the full value; sit out the rest.

    This is ``pace_multiplicative``'s steering law driving a different actuator. Pacing
    changes *how much it bids*; throttling changes *how often it bids at all*. Both land
    on the budget, and comparing them is the point of having both.

    They do not buy the same things. Pacing stays in every auction with a shaded bid, so
    it wins precisely the impressions its shading can still afford — the ones going cheap
    relative to what they are worth to it. Throttling picks its rounds with a coin that
    knows nothing about the auction, pays full price for whatever it lands on, and skips
    bargains for no reason other than the coin. Same budget, same spend, worse basket.

    That is the general shape of the thing: a bidder that reduces spend by *withdrawing*
    loses the ability to be selective, and a bidder that reduces spend by *shading* keeps
    it. The coin is not the problem — bidding full price on a random subset is.
    """
    p, previous, target = steer(context, p_start, step)
    me = context.bidder
    # One draw per round from this bidder's own stream. Never the global `random` module:
    # a strategy that reached for it would make the whole series irreproducible from its
    # seed, and there is a test in `agt.world` standing over exactly that.
    entering = context.rng.random() < p
    moving = {
        "down": "and heading down, because spend is running ahead of the straight line",
        "up": "and heading up, because the money is not going out fast enough",
        "steady": "and steady, with nothing left to correct",
    }[direction(p, previous)]
    verdict = (
        f"the coin came up entering, so it bids its full {num(me.value)}"
        if entering
        else "the coin came up sitting out, so it does not enter at all"
    )
    return BidDecision(
        me.value,
        f"{me.id} enters with probability p = {p:.3f} {moving}; this round {verdict}. "
        f"{_pacing_note(context, target)}. Throttling reaches the budget by buying less "
        "often at full price, where pacing reaches it by shading and buying often — so "
        "the coin decides which impressions it gets rather than the value of them, and "
        "the bargains it skips are skipped for no reason at all.",
        abstain=not entering,
        control=knob("p", "Entry probability (p)", p, previous),
    )


# -------------------------------------------------- a loop that rings, on purpose


TARGET_RATE = {
    "type": "number",
    "default": 0.3,
    "label": "Target win rate",
    "min": 0,
    "max": 1,
    "description": "The share of rounds this bidder is trying to win.",
}

KP = {
    "type": "number",
    "default": 1,
    "label": "Proportional gain (kp)",
    "min": 0,
    "description": "How hard to react to the current win-rate error. Too high and it rings.",
}

KI = {
    "type": "number",
    "default": 0.2,
    "label": "Integral gain (ki)",
    "min": 0,
    "description": "How hard to react to accumulated error. Removes a standing offset.",
}

# ponytail: the multiplier is clamped to [0, 3] rather than left free. Ceiling — a loop
# wound up hard enough to want 10x value is pinned at 3 and the chart shows a flat line
# where the truth is "far past the top of the scale". Upgrade: publish the unclamped
# demand alongside the applied one if a lesson ever needs to show saturation as such.
# The upper bound is deliberately above 1: overbidding is a real thing a badly tuned
# controller does, and capping at 1 would hide the most instructive half of the failure.
MAX_MULTIPLIER = 3.0


@strategy(
    "pid_winrate",
    label="PID win-rate control",
    description="Hold a target win rate with proportional and integral terms on a bid multiplier.",
    params={"target_rate": TARGET_RATE, "kp": KP, "ki": KI},
)
def pid_winrate(
    context: StrategyContext,
    target_rate: float = 0.3,
    kp: float = 1,
    ki: float = 0.2,
) -> BidDecision:
    """Steer a *win rate* rather than a spend rate, with a textbook PI controller.

    ``multiplier = 1 + kp x error + ki x accumulated error``, where the error is the
    target win rate minus the win rate achieved so far. Winning too much drives the error
    negative, which pulls the multiplier down; winning too little pushes it back up.

    **It will overshoot and oscillate when the gains are wrong, and that is the lesson.**
    A high ``kp`` reacts to the current error so hard that it sails past the target, then
    reacts to *that* error just as hard coming back, and the win rate rings instead of
    settling. Nothing here damps that, hides it, or quietly clamps it into looking calm:
    a control loop is only as good as its gains, and seeing a badly tuned one ring is
    worth more than seeing a well tuned one succeed.

    The proportional term alone would leave a standing offset — it needs an error to
    produce any correction, so it settles *near* the target rather than on it. The
    integral term is what removes that, by growing for as long as the error persists. It
    is also what makes a badly tuned loop wind up and take a long time to come back.

    Two things about the failure are worth knowing before reading a chart of it, both
    measured over ten seeds in ``tests/test_pacing.py``:

    * **The opening round is the expensive one.** Having won nothing yet, the bidder opens
      on the full error, so the multiplier starts at ``1 + kp x target`` — above 1 for any
      positive gain. It therefore opens by *overbidding*, and at kp = 8 it opens at triple
      value and buys its first impression at a loss of 98 to 156.
    * **The ringing does not show up in the win rate.** A loop banging between its two
      stops integrates to a very accurate average: at kp = 8 the realised win rate sits on
      0.33 against a target of 0.30, as close as the calm loop gets, and the win rate
      actually overshoots *further* at low gain. Anyone reading the output alone would
      call the ringing loop the better tuned one. The control variable is where to look.
    """
    me = context.bidder
    error, accumulated, rate = _winrate_error(context, target_rate)
    multiplier = clamp(1 + kp * error + ki * accumulated, 0, MAX_MULTIPLIER)
    previous = clamp(1 + kp * _prior_error(context, target_rate), 0, MAX_MULTIPLIER)
    bid = me.value * multiplier
    standing = (
        f"has won {rate:.0%} of {len(context.history)} rounds so far against a target of "
        f"{target_rate:.0%}"
        if context.history
        else "has no rounds behind it yet, so it starts from the full error"
    )
    return BidDecision(
        bid,
        f"{me.id} bids value x {multiplier:.3f} = {num(bid)}. It {standing}, an error of "
        f"{error:+.3f} with {accumulated:+.3f} accumulated, and the multiplier is "
        f"{direction(multiplier, previous)}. This loop steers how often it wins rather "
        "than how much it spends, and it is only as good as its gains: raise kp and it "
        "will sail past the target and ring back and forth across it instead of settling.",
        control=knob("multiplier", "Bid multiplier (PID)", multiplier, previous),
    )


def _winrate_error(
    context: StrategyContext, target_rate: float
) -> tuple[float, float, float]:
    """This round's error, the accumulated error, and the win rate achieved so far.

    The accumulated term sums one error per finished round, which is the discrete integral
    a PI controller needs. It is replayed from history for the same reason mu is — the
    strategy has nowhere to keep it between rounds.
    """
    wins = 0
    accumulated = 0.0
    rate = 0.0
    for index, view in enumerate(context.history):
        wins += view.winner == context.bidder.id
        rate = wins / (index + 1)
        accumulated += target_rate - rate
    return target_rate - rate, accumulated, rate


def _prior_error(context: StrategyContext, target_rate: float) -> float:
    """Where the proportional term stood a round ago, so ``why`` can say which way it moved.

    ponytail: the previous multiplier is reconstructed from the proportional term alone
    rather than by replaying the whole controller a second time. Ceiling — on a round
    where the integral term moved and the proportional one did not, the reported direction
    is the proportional story rather than the whole one. Upgrade: have `_winrate_error`
    return the running pair if the direction label ever needs to be exact.
    """
    if not context.history:
        return target_rate
    wins = sum(v.winner == context.bidder.id for v in context.history[:-1])
    played = len(context.history) - 1
    return target_rate - (wins / played if played else 0.0)
