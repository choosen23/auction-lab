"""Two learners on one grid of bid multipliers, and the regret that separates them.

Every strategy before this one was *told* what to do — bid your value, shade by (n-1)/n,
steer mu at the budget. These two are told nothing. They are handed the same short list of
multipliers of their own value, they pull one each round, they see what it paid, and the
only thing that distinguishes them is **how they decide to try something they are not sure
about**:

* ``bandit_epsilon`` flips a coin. With probability epsilon it ignores everything it has
  learned and picks a multiplier uniformly at random; the rest of the time it takes the
  best average so far. The coin never stops flipping, so the exploration bill is the same
  on the last round of the day as on the first.
* ``bandit_ucb`` never flips anything. It adds ``sqrt(2 ln t / n)`` to each arm's average
  — large for an arm it has barely played, small for one it has played to death — and
  takes the best sum. That preference decays as the counts grow, so its exploration is
  spent early and then stops.

**Both open by playing every arm once.** UCB has to: an arm with ``n = 0`` has no average
and an unbounded bonus. Giving epsilon-greedy the identical opening is what makes the
regret comparison a comparison of exploration *rules* rather than of who got the luckier
start.

**The reward is realised utility**: ``value - price`` on a win, 0 otherwise. What that
learning cost is scored in :mod:`agt.regret`, in the same money.

**And that scale is not a neutral choice — it decides the whole result.** ``sqrt(2 ln t/n)``
is an *absolute* quantity, and UCB1's constant of 2 is derived for rewards on [0, 1].
Against utilities in the tens the bonus is worth about 1 where the arms' averages are 13
apart, so it is a rounding error and UCB is pure greedy wearing a bonus. Divide every
number in the market by 100 and the identical rule inverts: the bonus now dwarfs the gaps,
UCB round-robins for the whole day and never settles. Measured on the market in
``tests/test_bandits.py``, twenty seeds, 45 rounds, mean final regret in comparable units:

    values 40-100    UCB 119.3   epsilon-greedy 203.0   pure greedy 120.1
    values 0.4-1.0   UCB 713.8   epsilon-greedy 203.0   pure greedy 120.1

epsilon-greedy is identical in both rows — it only ever compares averages, so it does not
care what the numbers are called. UCB is not, and both tails of that are worth seeing:

* **Where it wins, it is not winning by exploring.** At the larger scale the bonus changes
  the chosen arm on 0.1 rounds out of 45. UCB beats epsilon-greedy because it *stops*
  paying for exploration, not because it explores cleverly. The coin never stops.
* **Where the bonus is on its intended scale, it loses.** UCB1's guarantee is asymptotic:
  it plays an arm until roughly ``2 ln t / gap^2`` pulls, which for a gap of 0.2 is about
  190. A 45-round day split five ways gives each arm nine. Forty-five rounds is not the
  long run, and a learner with an exploration schedule written for the long run spends the
  whole day still exploring.

ponytail: rewards are used at whatever scale the auction happens to be denominated in.
Ceiling — the exploration rate of ``bandit_ucb`` therefore depends on the *units* of the
bidders' values, which is a genuinely surprising property to hand somebody without saying
so, and the two rows above are what it costs. Upgrade: divide the bonus by the spread of
rewards seen so far, which is the same as normalizing to [0, 1] and is the textbook fix —
but note the second row is what that produces at this horizon, so it wants a longer day or
a tuned coefficient with it, and both are changes to the phase's compute budget rather
than to this module.

The strategies are stateless for the same reason the pacers in :mod:`agt.pacing` are:
:func:`agt.strategies.decide` calls a plain function and there is nowhere to keep a
counter. Every arm's count and mean is replayed from ``context.history`` and
``context.own_values`` on every round.
"""

import math
from typing import Any

from agt.stages import num

from agt.strategies import BidDecision, RoundView, StrategyContext, strategy
from agt.trace import Number

# `steering` is bound as a module and read at call time, rather than `from agt.steering
# import knob`. This module and `agt.pacing` are both imported from the bottom of
# `agt.strategies` to register themselves, so the package has a deliberate cycle through
# it, and `import agt.pacing` first walks that cycle round to here while `agt.steering` is
# still half built. Naming `knob` at import time would read a name that does not exist yet;
# naming the module and reaching for `knob` when a bid is actually decided always works,
# because by then every module in the cycle has finished.
from agt import steering

ARMS = {
    "type": "number",
    "default": 5,
    "label": "Number of bid multipliers",
    "min": 2,
    "max": 20,
    "description": "How many evenly spaced fractions of value the learner chooses between.",
}

EPSILON = {
    "type": "number",
    "default": 0.1,
    "label": "Exploration rate (epsilon)",
    "min": 0,
    "max": 1,
    "description": "How often to ignore what it has learned and try a multiplier at random.",
}

# How close a replayed bid has to sit to a grid point to count as that arm. Bids are
# rebuilt as `arm x value` and read back as `bid / value`, so the only gap is float dust.
SNAP = 1e-9


def grid(arms: float) -> tuple[float, ...]:
    """The multipliers on offer: ``k`` even steps from ``1/k`` up to bidding the whole value.

    ponytail: an evenly spaced grid with no arm at 0. Ceiling — the best answer against a
    market this bidder can never afford is to bid nothing, and that answer is not on the
    grid; the learner will sit on its lowest arm losing every round instead. Upgrade: add
    a 0 arm, but note that a 0 is a *bid*, not an abstention — it clears a reserve of 0 and
    can win the item for nothing — so the arm and the sitting-out are two different things
    and adding one does not give you the other.
    """
    k = max(2, int(arms))
    return tuple((i + 1) / k for i in range(k))


LEARNERS = ("bandit_epsilon", "bandit_ucb")


def arm_grid(name: str, params: dict[str, Any]) -> tuple[float, ...] | None:
    """The arms strategy ``name`` is choosing between, or ``None`` if it is not a learner.

    This is the seam the regret series hangs on. :mod:`agt.series` asks the question rather
    than keeping a list of learner names of its own, so a learner added here gets a regret
    curve without the runner being told about it, and a bidder with no arms gets no regret
    curve rather than one measured against a grid it never had.
    """
    if name not in LEARNERS:
        return None
    return grid(params.get("arms") or ARMS["default"])


# --------------------------------------------------------------- reading the past back


def _reward(view: RoundView, me: str, value: Number) -> float:
    """Realised utility: what the bidder kept after paying for what it won.

    ponytail: computed from the public winner and price, so it is the winner's surplus and
    0 for everybody else. Ceiling — under ``all_pay`` rules the losers pay their bids too,
    and this scores their loss as a flat 0, so a learner would under-punish overbidding on
    exactly the mechanism where overbidding hurts most. Upgrade: carry each bidder's
    realised payment on :class:`~agt.strategies.RoundView`; payments are public, so that is
    a legal widening of the privacy seam rather than a breach of it, and
    :func:`agt.steering.spend_path` wants the same field.
    """
    return (value - view.price) if view.winner == me else 0.0


def _which_arm(multiplier: float, arms: tuple[float, ...]) -> int | None:
    """Which grid point a replayed bid came from, or ``None`` if it came from none of them."""
    closest = min(range(len(arms)), key=lambda i: abs(arms[i] - multiplier))
    return closest if abs(arms[closest] - multiplier) < SNAP else None


def _pulls(
    context: StrategyContext, arms: tuple[float, ...]
) -> tuple[list[int], list[float], int | None]:
    """Replay this bidder's own record: how often each arm played, its total reward, and
    the arm pulled most recently.

    A round the bidder did not enter — it sat out, or the runner barred it — has no bid in
    the public record and therefore no observation. It is skipped rather than booked as a
    zero, because a zero would be a claim about an arm that was never actually played.
    """
    counts = [0] * len(arms)
    totals = [0.0] * len(arms)
    last: int | None = None
    for index, view in enumerate(context.history):
        bid = view.bids.get(context.bidder.id)
        value = context.own_values[index] if index < len(context.own_values) else 0
        if bid is None or not value:
            continue
        arm = _which_arm(bid / value, arms)
        if arm is None:
            continue
        counts[arm] += 1
        totals[arm] += _reward(view, context.bidder.id, value)
        last = arm
    return counts, totals, last


def _means(counts: list[int], totals: list[float]) -> list[float]:
    return [total / count if count else 0.0 for total, count in zip(totals, counts)]


def _gap(means: list[float]) -> float:
    """How far the best average stands above the second best — what a bonus has to cover."""
    ranked = sorted(means, reverse=True)
    return ranked[0] - ranked[1]


def _decision(
    context: StrategyContext,
    arms: tuple[float, ...],
    chosen: int,
    last: int | None,
    considered: list[dict[str, Any]],
    why: str,
) -> BidDecision:
    """Turn a chosen arm into a bid, sitting out if the budget cannot cover it.

    Sitting out is ``abstain=True`` and never a bid of 0: a 0 is a real bid that clears a
    reserve of 0 and can win the item for nothing, which would hand the learner a reward
    for an impression that never happened.
    """
    bid = arms[chosen] * context.bidder.value
    remaining = context.remaining
    control = steering.knob(
        "arm",
        "Bid multiplier (arm)",
        arms[chosen],
        arms[last] if last is not None else arms[chosen],
    )
    if remaining is not None and bid > remaining:
        return BidDecision(
            bid,
            f"{context.bidder.id} chose the {arms[chosen]:g} arm, which asks for a bid of "
            f"{num(bid)}, and has only {num(remaining)} left of its "
            f"{num(context.budget or 0)} budget — so it does not enter at all. It does not "
            "bid 0 instead: a 0 is a real bid that could win the item for nothing. Sitting "
            "out costs it the round and teaches it nothing, because an arm it never played "
            "returns no observation to learn from.",
            considered=considered,
            abstain=True,
            control=control,
        )
    return BidDecision(bid, why, considered=considered, control=control)


# ------------------------------------------------------------------ epsilon-greedy


@strategy(
    "bandit_epsilon",
    label="Epsilon-greedy learner",
    description="Pull a bid multiplier at random with probability epsilon; otherwise the best average so far.",
    params={"arms": ARMS, "epsilon": EPSILON},
)
def bandit_epsilon(
    context: StrategyContext, arms: float = 5, epsilon: float = 0.1
) -> BidDecision:
    """Explore by flipping a coin, exploit by taking the best mean.

    The rule is one line and its weakness is the same line: the coin has no memory. On the
    fortieth round, with every arm measured a dozen times and one of them clearly ahead, it
    still spends epsilon of its rounds on a multiplier it already knows is worse. That is
    not a bug in the tuning, it is what a *fixed* exploration rate means, and it is why the
    regret curve of an epsilon-greedy learner keeps climbing at a steady slope long after a
    learner that explores by uncertainty has flattened out.
    """
    grid_ = grid(arms)
    counts, totals, last = _pulls(context, grid_)
    means = _means(counts, totals)
    considered = [
        {"arm": arm, "bid": arm * context.bidder.value, "pulls": n, "mean": m}
        for arm, n, m in zip(grid_, counts, means)
    ]
    unplayed = [i for i, n in enumerate(counts) if n == 0]
    best = max(range(len(grid_)), key=lambda i: means[i])

    if unplayed:
        chosen = unplayed[0]
        why = (
            f"{context.bidder.id} has never played the {grid_[chosen]:g} arm, so it plays "
            f"it once before judging anything: {len(unplayed)} of {len(grid_)} multipliers "
            "are still unmeasured. Every arm gets one opening pull, because an average over "
            "no observations is not an average."
        )
    elif context.rng.random() < epsilon:
        chosen = context.rng.randrange(len(grid_))
        why = (
            f"{context.bidder.id} flipped its exploration coin, which came up explore with "
            f"probability {epsilon:g}, and so picked the {grid_[chosen]:g} arm at random "
            f"rather than the {grid_[best]:g} arm with the best average of "
            f"{means[best]:.3f}. The coin has no memory: it will explore just as often on "
            "the last round of the day as it did on the first, whatever it has learned by "
            "then."
        )
    else:
        chosen = best
        why = (
            f"{context.bidder.id} took the {grid_[chosen]:g} arm, which holds the best "
            f"average so far at {means[chosen]:.3f} over {counts[chosen]} pulls. The coin "
            f"came up exploit, which it does {1 - epsilon:.0%} of the time; the other "
            f"{epsilon:.0%} it will throw this away and try a multiplier at random."
        )
    return _decision(
        context,
        grid_,
        chosen,
        last,
        considered,
        f"{why} It bids {grid_[chosen]:g} x {num(context.bidder.value)} = "
        f"{num(grid_[chosen] * context.bidder.value)}.",
    )


# ------------------------------------------------------------------------------ UCB


@strategy(
    "bandit_ucb",
    label="UCB learner",
    description="Pull the bid multiplier maximising mean + sqrt(2 ln t / n): explore what you know least.",
    params={"arms": ARMS},
)
def bandit_ucb(context: StrategyContext, arms: float = 5) -> BidDecision:
    """Explore by preferring the arm you are least sure about.

    ``sqrt(2 ln t / n)`` is a confidence radius: how far above its measured average an arm
    could plausibly be, given that it has been played ``n`` times out of ``t``. Adding it
    to the mean and taking the largest is *optimism in the face of uncertainty* — play the
    arm that could still be the best one, and either it is, or you learn that it is not.

    The shape of the two terms is the whole idea. ``n`` grows every time an arm is played,
    so its bonus shrinks like ``1/sqrt(n)``; ``ln t`` grows for *every* arm whether played
    or not, so an arm left alone slowly regains attention. An arm that keeps paying keeps
    getting played and its bonus vanishes; an arm that disappointed once keeps a large
    bonus until it has been given a fair hearing. Nothing here is random, which is why this
    learner replays identically without ever drawing from ``context.rng``.

    **What that bonus is worth depends on what the money is called.** It is an absolute
    number and the rewards are not, so the same rule on the same market explores hardly at
    all in one currency and does nothing but explore in another. The module docstring has
    the measurements; the ``why`` below reports the bonus against the gap it would have had
    to close, so a reader can see which regime they are in rather than being told.
    """
    grid_ = grid(arms)
    counts, totals, last = _pulls(context, grid_)
    means = _means(counts, totals)
    unplayed = [i for i, n in enumerate(counts) if n == 0]
    played = sum(counts)
    # An unplayed arm has an unbounded bonus, which is exactly the right answer and an
    # unplottable number, so the opening sweep is handled as its own case below and the
    # published bonus for an unplayed arm is left at 0 rather than at infinity.
    bonuses = [
        math.sqrt(2 * math.log(played) / n) if n and played > 1 else 0.0 for n in counts
    ]
    considered = [
        {
            "arm": arm,
            "bid": arm * context.bidder.value,
            "pulls": n,
            "mean": m,
            "bonus": b,
            "index": m + b,
        }
        for arm, n, m, b in zip(grid_, counts, means, bonuses)
    ]
    best_mean = max(range(len(grid_)), key=lambda i: means[i])

    if unplayed:
        chosen = unplayed[0]
        why = (
            f"{context.bidder.id} has never played the {grid_[chosen]:g} arm. An arm played "
            "no times has no average and an unbounded confidence bonus, so UCB would pick "
            f"it anyway; {len(unplayed)} of {len(grid_)} multipliers are still unmeasured "
            "and each gets one opening pull."
        )
    else:
        chosen = max(range(len(grid_)), key=lambda i: means[i] + bonuses[i])
        if chosen != best_mean:
            detail = (
                f"the {grid_[best_mean]:g} arm has the better average at "
                f"{means[best_mean]:.3f} but has been played {counts[best_mean]} times, so "
                f"its bonus has shrunk to {bonuses[best_mean]:.3f} and its index is only "
                f"{means[best_mean] + bonuses[best_mean]:.3f}. The bonus is what decided "
                "this round: UCB is buying information about an arm it is not yet sure "
                "about rather than chasing an average it already believes"
            )
        else:
            detail = (
                "it also holds the best average outright, so the bonus decided nothing "
                f"this round: it would have had to cover the {_gap(means):.3f} between the "
                "two best averages to change the answer. That comparison is worth watching "
                "— sqrt(2 ln t / n) is an absolute number while the averages are in "
                "whatever the auction is denominated in, so the same rule explores hardly "
                "at all on rewards in the tens and does nothing but explore on rewards "
                "in [0, 1], which is the scale its constant was written for"
            )
        why = (
            f"{context.bidder.id} took the {grid_[chosen]:g} arm on an index of "
            f"{means[chosen] + bonuses[chosen]:.3f} = average {means[chosen]:.3f} + bonus "
            f"{bonuses[chosen]:.3f}, where the bonus is sqrt(2 ln {played} / "
            f"{counts[chosen]}). {detail}."
        )
    return _decision(
        context,
        grid_,
        chosen,
        last,
        considered,
        f"{why} It bids {grid_[chosen]:g} x {num(context.bidder.value)} = "
        f"{num(grid_[chosen] * context.bidder.value)}.",
    )


