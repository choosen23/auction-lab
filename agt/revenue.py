"""Revenue equivalence: a theorem about *equilibrium*, checked against the real engine.

The revenue equivalence theorem says that two auctions which allocate the item the same
way and leave the lowest possible type with the same surplus raise the same expected
revenue — however different their payment rules look. First-price collects from the winner
only, at a shaded bid. All-pay collects from *everybody*, including bidders who go home
with nothing. Second-price collects from the winner at a price they did not choose. Under
the theorem's hypotheses the seller cannot tell them apart.

That is a startling claim, and it is worth checking rather than repeating, because the
hypotheses are doing all the work and they are easy to skim past. This module checks it by
drawing values, having every bidder play the **known symmetric equilibrium** of the
mechanism in question, running the real auction, and comparing what the seller actually
collected.

**The equilibrium bid functions here are theory, and that is the point.** Everywhere else
in this package a payoff is measured rather than derived — see :mod:`agt.equilibrium`. Here
the derived object is the thing under test: the theorem is a statement about what happens
*when bidders play equilibrium*, so the check has to supply one. What is measured is the
revenue, by running the same mechanisms every other phase runs.

**Common random numbers, and a real test.** Every mechanism is scored on the identical
value draws, so "do these two agree?" is a question about a paired difference rather than
about two independent means, and its standard error is far smaller than either. Two means
differing by 0.24 on 400 draws is not evidence of anything; the paired difference says so
with a number instead of a shrug.
"""

import math
import random
import statistics
from collections.abc import Callable
from typing import Any

from agt.mechanisms import REGISTRY, run
from agt.stages import num
from agt.trace import Bidder, Number

DEFAULT_DRAWS = 400
MIN_DRAWS = 2
# 5 mechanisms x 2 comparisons x draws auctions, at roughly 35us each. 5000 is about 1.8s
# and is the point at which this endpoint would start to feel slow rather than instant.
MAX_DRAWS = 5000

# How many standard errors of the *paired difference* two revenues may differ by and still
# be called agreement. 3 is the usual line; the point of naming it is that the verdict is a
# statistical test with a stated threshold rather than an impression of two numbers.
AGREEMENT_SIGMAS = 3

# Every difference is measured against this one. Second-price is the right ruler because it
# is the mechanism whose equilibrium revenue is known exactly without any shading algebra —
# truthful bidders make its revenue the second-highest value by definition — so a
# disagreement is evidence about the other mechanism rather than about the comparison.
BASELINE = "second_price"


def _first_price(value: Number, n: int, high: Number) -> Number:
    """b(v) = v(n-1)/n — the expected highest rival value, conditional on winning."""
    return value * (n - 1) / n


def _all_pay(value: Number, n: int, high: Number) -> Number:
    """b(v) = (n-1)/n . v^n / H^(n-1) — pay always, so bid the *probability-weighted* price."""
    if high <= 0:
        return 0.0
    return (n - 1) / n * value**n / high ** (n - 1)


# The symmetric Bayes-Nash equilibrium of each mechanism when all n values are drawn
# independently from the same uniform distribution on [0, high]. English is an ascending
# clock and Dutch a descending one, so they are not separate theory — they are the same two
# auctions in a different costume, which is exactly what makes them worth running: their
# columns must come out identical draw for draw, not merely equal on average.
#
# ponytail: a hard-coded table keyed by mechanism name, in a package that otherwise refuses
# to keep lists of mechanism names outside the registry. Ceiling — a mechanism added later
# is silently absent from the comparison. Upgrade: none available; a closed-form equilibrium
# is not something a mechanism can declare the way it declares `truthful_dominant`, because
# it depends on the value distribution as much as on the payment rule. What the report can
# do, and does, is name every registered mechanism it had to leave out and why.
SYMMETRIC_BNE: dict[str, Callable[[Number, int, Number], Number]] = {
    "second_price": lambda value, n, high: value,
    "english": lambda value, n, high: value,
    "first_price": _first_price,
    "dutch": _first_price,
    "all_pay": _all_pay,
}


def compare(highs: list[Number], draws: int, seed: int) -> dict[str, Any]:
    """Mean revenue of each mechanism over ``draws`` seeded value profiles.

    ``highs`` is one value ceiling per bidder. All equal is the symmetric world the theorem
    is about. Unequal is the counterexample: every bidder still plays the symmetric rule —
    which is what a bidder who believes the market is symmetric would do — and that rule is
    no longer anybody's equilibrium, so the theorem's conclusion is not owed to us.

    Randomness comes from a local :class:`random.Random`, never the global module, for the
    same reason it does in :class:`~agt.world.World`: a check whose answer depends on what
    else ran first is not a check.
    """
    n = len(highs)
    high = max(highs) if highs else 0
    rng = random.Random(seed)
    names = [name for name in REGISTRY if name in SYMMETRIC_BNE]

    revenues: dict[str, list[Number]] = {name: [] for name in names}
    for _ in range(draws):
        values = [rng.uniform(0, ceiling) for ceiling in highs]
        for name in names:
            lineup = [
                Bidder(f"b{i}", value, SYMMETRIC_BNE[name](value, n, high))
                for i, value in enumerate(values)
            ]
            revenues[name].append(run(name, lineup, {}).result["revenue"])

    baseline = revenues.get(BASELINE)
    symmetric = all(abs(ceiling - high) <= 1e-9 for ceiling in highs)
    return {
        "n": n,
        "highs": list(highs),
        "symmetric": symmetric,
        "draws": draws,
        "seed": seed,
        # E[second-highest of n i.i.d. uniform draws] — what a second-price auction
        # collects from truthful bidders, and therefore what every mechanism obeying the
        # theorem collects. Only defined when the draws really are identically distributed.
        "theory": high * (n - 1) / (n + 1) if symmetric else None,
        "mechanisms": {
            name: _score(name, series, baseline, is_baseline=name == BASELINE)
            for name, series in revenues.items()
        },
        "excluded": _excluded(),
        "why": _sentence(symmetric, high, n),
    }


def _score(
    name: str,
    series: list[Number],
    baseline: list[Number] | None,
    is_baseline: bool = False,
) -> dict[str, Any]:
    """One mechanism's mean revenue, and whether it agrees with the baseline when paired.

    ``baseline`` is carried on the row rather than left for the reader to work out from a
    difference of zero, because two other rows also sit at exactly zero — the clock
    auctions are the same auctions — and a renderer distinguishing them would have to
    hard-code a mechanism name to do it.
    """
    mean = statistics.fmean(series)
    stderr = _stderr(series)
    if baseline is None:
        return {"label": REGISTRY[name].label, "mean": mean, "stderr": stderr}
    paired = [mine - theirs for mine, theirs in zip(series, baseline)]
    difference = statistics.fmean(paired)
    difference_stderr = _stderr(paired)
    return {
        "label": REGISTRY[name].label,
        "baseline": is_baseline,
        "mean": mean,
        "stderr": stderr,
        "difference": difference,
        "difference_stderr": difference_stderr,
        # A tie at zero — English against second-price — is exact agreement rather than a
        # test that squeaked through, and reads as such: the standard error is 0 too.
        "agrees": abs(difference) <= AGREEMENT_SIGMAS * difference_stderr + 1e-9,
        "identical": all(abs(d) <= 1e-9 for d in paired),
    }


def _stderr(series: list[Number]) -> float:
    """Standard error of the mean. A single draw has no spread to estimate, so it is 0."""
    if len(series) < 2:
        return 0.0
    return statistics.stdev(series) / math.sqrt(len(series))


def _excluded() -> dict[str, str]:
    """Registered mechanisms with no entry in :data:`SYMMETRIC_BNE`, and why not.

    Named rather than omitted. A comparison table showing five of seven mechanisms with no
    note reads as a claim about all of them.
    """
    reasons = {}
    for name, spec in REGISTRY.items():
        if name in SYMMETRIC_BNE:
            continue
        reasons[name] = (
            f"{spec.label} sells bundles, and the theorem here is about one item and one "
            "scalar bid per bidder."
            if spec.input_kind == "package"
            else f"{spec.label} sells several slots at once; this package has no "
            "closed-form symmetric equilibrium for it to play, and guessing one would "
            "make the comparison a check of the guess."
        )
    return reasons


def _sentence(symmetric: bool, high: Number, n: int) -> str:
    if symmetric:
        return (
            f"All {n} bidders draw values independently and uniformly from 0 to "
            f"{num(high)}, and each plays the symmetric equilibrium of the mechanism it is "
            "in. Those are the theorem's hypotheses, so the mechanisms should all collect "
            f"the same expected revenue — namely the expected second-highest value, "
            f"{num(high * (n - 1) / (n + 1))} — despite charging by completely different "
            "rules. Agreement is judged on the paired difference against second-price, "
            f"within {AGREEMENT_SIGMAS} standard errors."
        )
    return (
        "Bidders now draw from *different* ranges, and every one of them still plays the "
        "symmetric rule — which is what a bidder who believes the market is symmetric "
        "would do. That rule is no longer an equilibrium of this market, so the theorem's "
        "conclusion is not owed to us and any agreement below is luck rather than law. "
        "How far the columns actually part depends on how unequal the ranges are: values "
        "of 100, 93 and 86 barely move first-price, while 100, 60 and 30 move it by ten "
        "currency units on a hundred-unit item. All-pay parts company soonest, because its "
        "equilibrium bid divides by the shared ceiling and so leans on the distribution "
        "harder than the other two. Whatever the numbers do, nothing that broke here is a "
        "payment rule — it is the assumption that everyone drew from the same "
        "distribution. Second-price still collects the second-highest value no matter how "
        "lopsided the market is, because truthful bidding stays dominant regardless of "
        "anybody's values, and that is the practical reason to prefer it."
    )


def revenue_equivalence(
    bidders: list[Bidder], draws: int = DEFAULT_DRAWS, seed: int = 0
) -> dict[str, Any]:
    """The check both ways round: the symmetric world, and this table's own asymmetry.

    The symmetric run uses the largest value on the table as the common ceiling. The
    asymmetric run gives each bidder its own value as its ceiling, which is the counterpart
    the setup on screen already implies — and it is skipped, with a reason, when every
    bidder's value is the same, because then it *is* the symmetric run and reporting it
    twice would suggest a comparison that was never made.
    """
    highs = [b.value for b in bidders]
    high = max(highs, default=0)
    report = {
        "symmetric": compare([high] * len(highs), draws=draws, seed=seed),
        "asymmetric": None,
        "skipped": None,
    }
    if all(abs(ceiling - high) <= 1e-9 for ceiling in highs):
        report["skipped"] = (
            "Every bidder on the table has the same value, so the asymmetric comparison "
            "would be the symmetric one run twice. Give the bidders different values to "
            "see revenue equivalence break."
        )
    else:
        report["asymmetric"] = compare(highs, draws=draws, seed=seed)
    return report
