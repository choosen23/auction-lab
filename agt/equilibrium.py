"""The mechanism as a game: which bid profiles are stable, and what a reply curve looks like.

Phases 1-4 all answer one shape of question — *given these bids, what happens?* A trace
runs one profile; a series runs the sequence of profiles that strategies happened to
choose. This module asks the question underneath: of all the profiles that *could* have
been played, which ones is nobody sorry about, and what does one bidder's payoff look like
across its whole range rather than at the single point it bid.

**Every payoff here is measured, never derived.** A cell of the payoff table is a real
:func:`agt.mechanisms.run` of the real mechanism, read out of ``result["utilities"]`` —
the same door :func:`agt.strategies.best_response` and :mod:`agt.regret` already use. That
is the constraint the module hangs off: restate a payment rule outside the mechanism that
owns it and the analysis becomes an analysis of a *model* of the auction, which is one
refactor away from teaching something false. It is also why every mechanism written later
gets equilibrium analysis for free, exactly as it gets a best-response bidder for free.

**A grid is a different game from the continuum, and every report here says so.** The
equilibria found are equilibria *of the discretized game*. Coarsen the grid and profiles
become stable that the continuous game would not hold still at; refine it and some of them
dissolve. So the grid travels with the answer, always — and every bidder's own value is
forced onto it by :func:`bid_grid`, because a search that cannot see a bidder's value
cannot answer "is bidding your value an equilibrium?" and would answer "no" rather than
"I could not look".
"""

import itertools
from typing import Any

from agt.mechanisms import REGISTRY, run
from agt.registry import _resolve_params
from agt.revenue import DEFAULT_DRAWS, revenue_equivalence
from agt.stages import num
from agt.trace import Bidder, Number

# The profile space is |grid| ** |bidders|, and every profile is one auction. At roughly
# 90us for the most expensive mechanism this is about 0.75s, matching the measured worst
# case of ``/run_series``, and it is what lets a five-bidder table be searched at all: five
# distinct values plus 0 already need 7776 profiles on the coarsest grid there is. The work
# is bounded before it starts rather than by a timeout, as the package solver's caps are.
MAX_PROFILES = 8192
DEFAULT_STEPS = 9
MIN_STEPS = 2
MAX_STEPS = 33

# Second-price rules alone produce dozens of pure equilibria on a modest grid — that is the
# lesson, not a defect — so the list is cut. What was cut is reported rather than dropped:
# a truncated list that looks complete would turn "Nash is a weak promise" into "here are
# all four of them".
MAX_REPORTED = 24

# ponytail: one absolute tolerance for "these two bids are the same" and for "this
# deviation actually pays", matching the one in :mod:`agt.series`. Ceiling — values in the
# millions make both comparisons effectively exact. Upgrade: scale it by the largest value
# in the table if a lesson ever needs numbers that big.
TOLERANCE = 1e-9


# --------------------------------------------------------------------------- the grid


def bid_grid(bidders: list[Bidder], steps: int = DEFAULT_STEPS) -> list[Number]:
    """Evenly spaced bids from 0 to the largest value, plus every bidder's own value.

    The even spacing is what makes the picture readable; the values are what make the
    search *able to answer the question*. Truthful bidding is the profile the whole phase
    is about, and a grid that stepped straight past a bidder's value would report it as
    not an equilibrium — a false negative indistinguishable, from the outside, from a real
    finding.

    Values are seeded first and the spaced points are only admitted where they do not
    collide with one, so an exact value is never the point that gets dropped as a
    near-duplicate. 0 is always present: not bidding is a strategy, and under all-pay
    rules it is frequently the right one.
    """
    high = max((b.value for b in bidders), default=0)
    spaced = [high * i / (steps - 1) for i in range(steps)] if steps > 1 else [high]
    kept = sorted({float(b.value) for b in bidders} | {0.0})
    for point in spaced:
        if all(abs(point - already) > TOLERANCE for already in kept):
            kept.append(point)
    return sorted(kept)


def _nearest(grid: list[Number], value: Number) -> int:
    """Where ``value`` sits on the grid. Nearest rather than exact: :func:`bid_grid` puts
    every bidder's value on the grid, and this must not become the one line that turns a
    float representation detail into a ``ValueError`` from inside an analysis."""
    return min(range(len(grid)), key=lambda i: abs(grid[i] - value))


def refuse_grid_search(mechanism: str) -> None:
    """Refuse a mechanism whose strategy is not one number.

    The sibling of :func:`agt.series.refuse_repeated_rounds`, and refused for the same
    reason with a different consequence: a bidder submitting XOR bundles has no scalar bid
    to put on an axis, so it has no grid, no reply curve, and no profile space. Its
    strategy space is which bundles to name and what to offer for each — a different game
    with different equilibria, not this one at lower resolution.
    """
    spec = REGISTRY[mechanism]
    if spec.input_kind == "single":
        return
    raise ValueError(
        f"{spec.label} ({mechanism}) takes package bids, and equilibrium analysis needs "
        "one bid per bidder to search over: a bidder offering all-or-nothing bundles has "
        "no single number to vary, so there is no grid to put them on. Its strategy space "
        "is which bundles to name and what to offer for each, which is a different game."
    )


# -------------------------------------------------------------------- the payoff table


def payoff_table(
    mechanism: str,
    bidders: list[Bidder],
    grid: list[Number],
    params: dict[str, Any],
) -> dict[tuple[int, ...], dict[str, Number] | None]:
    """Every bidder's utility at every profile, keyed by the profile's grid indices.

    Utilities only, not whole results: the table is the hot object — up to
    :data:`MAX_PROFILES` entries — and everything a stability check needs is in it. The
    handful of profiles that end up being *reported* are re-run for their revenue and
    welfare, which is cheaper than carrying that for all of them.

    ``None`` records a profile the mechanism refused outright, which a Dutch clock does
    when its start price would open below the highest bid. Such a profile is not a
    strategy anyone could have played, so it can neither be an equilibrium nor be deviated
    to, and both loops below skip it.
    """
    table: dict[tuple[int, ...], dict[str, Number] | None] = {}
    for profile in itertools.product(range(len(grid)), repeat=len(bidders)):
        lineup = [Bidder(b.id, b.value, grid[i]) for b, i in zip(bidders, profile)]
        try:
            table[profile] = dict(run(mechanism, lineup, dict(params)).result["utilities"])
        except ValueError:
            table[profile] = None
    return table


def _swap(profile: tuple[int, ...], seat: int, index: int) -> tuple[int, ...]:
    return (*profile[:seat], index, *profile[seat + 1 :])


# ------------------------------------------------------------------ best-response curve


def best_response_curve(
    mechanism: str,
    bidders: list[Bidder],
    seat: int,
    grid: list[Number],
    params: dict[str, Any],
) -> dict[str, Any]:
    """One bidder's utility across the whole grid, with every rival held at its own bid.

    This is phase 2's ``best_response`` with the conclusion left in and the working shown.
    That strategy reports its argmax; this reports the *shape*, which is where the lesson
    is. Under second-price rules the curve is a plateau — every bid from the highest rival
    bid up to your value earns identically, because your bid does not set your price — and
    under first-price rules it is a single peak strictly below value, where being wrong in
    either direction costs money.

    Rivals sit at the bids typed into the table, not at grid points, and this bidder is
    reseated in its own chair: ties break by list order, so a curve computed with the
    bidder moved would score ties it would not really have won.
    """
    me = bidders[seat]
    rivals = {b.id: b.bid for b in bidders if b.id != me.id}
    points = []
    for candidate in grid:
        lineup = [
            Bidder(b.id, b.value, candidate if i == seat else b.bid)
            for i, b in enumerate(bidders)
        ]
        try:
            utility = run(mechanism, lineup, dict(params)).result["utilities"][me.id]
        except ValueError:
            continue  # a bid this mechanism will not accept is not a candidate
        points.append({"bid": candidate, "utility": utility})

    if not points:
        return {
            "bidder": me.id,
            "value": me.value,
            "rivals": rivals,
            "points": [],
            "best": None,
            "why": (
                f"{me.id} has no bid this mechanism would accept against the rival bids "
                "on the table, so there is no reply curve to draw."
            ),
        }

    peak = max(p["utility"] for p in points)
    plateau = [p for p in points if p["utility"] > peak - TOLERANCE]
    truthful = next(
        (p for p in plateau if abs(p["bid"] - me.value) <= TOLERANCE), None
    )
    # Ties resolve toward the truthful bid first, matching ``best_response``: under
    # second-price rules the whole top of the curve is a tie, and preferring the cheapest
    # of them would answer "shade to just above the rival" and bury the lesson.
    best = truthful or plateau[0]
    return {
        "bidder": me.id,
        "value": me.value,
        "rivals": rivals,
        "points": points,
        "best": best["bid"],
        "best_utility": best["utility"],
        "plateau": [plateau[0]["bid"], plateau[-1]["bid"]],
        "truthful_is_best": truthful is not None,
        "why": _curve_sentence(me, rivals, plateau, best, truthful is not None),
    }


def _curve_sentence(
    me: Bidder,
    rivals: dict[str, Number],
    plateau: list[dict[str, Any]],
    best: dict[str, Any],
    truthful_is_best: bool,
) -> str:
    seen = ", ".join(f"{who} {num(bid)}" for who, bid in rivals.items()) or "nobody"
    shape = (
        f"every bid from {num(plateau[0]['bid'])} to {num(plateau[-1]['bid'])} earns the "
        f"same {num(best['utility'])}, so the top of the curve is flat and the exact bid "
        "does not matter"
        if len(plateau) > 1
        else f"one bid is best and both directions off it earn less"
    )
    honest = (
        "Bidding the true value is among the best replies here."
        if truthful_is_best
        else f"Bidding the true value of {num(me.value)} is *not* a best reply here."
    )
    return (
        f"Against {seen}, {me.id} earns most at {num(best['bid'])}, worth "
        f"{num(best['utility'])}: {shape}. {honest} This is a reply to these rival bids "
        "and to no others — it is a slice through the game, not an equilibrium."
    )


# ------------------------------------------------------------------------- pure Nash


def pure_nash(
    bidders: list[Bidder],
    grid: list[Number],
    table: dict[tuple[int, ...], dict[str, Number] | None],
) -> list[tuple[int, ...]]:
    """Profiles no single bidder can strictly improve on by moving alone.

    Strictly: a deviation that earns exactly the same is not an improvement, which is what
    makes weakly dominated equilibria — the famous embarrassing ones — show up rather than
    get filtered out. That is deliberate. "Nobody can do better by deviating alone" is the
    whole definition, and an equilibrium sustained by a threat nobody would carry out is
    still an equilibrium; seeing one is how a learner finds out what Nash does not promise.
    """
    return [
        profile
        for profile, utilities in table.items()
        if utilities is not None
        and not any(
            _can_improve(bidders, grid, table, profile, seat, utilities)
            for seat in range(len(bidders))
        )
    ]


def _can_improve(
    bidders: list[Bidder],
    grid: list[Number],
    table: dict[tuple[int, ...], dict[str, Number] | None],
    profile: tuple[int, ...],
    seat: int,
    utilities: dict[str, Number],
) -> bool:
    who = bidders[seat].id
    mine = utilities[who]
    for index in range(len(grid)):
        other = table[_swap(profile, seat, index)]
        if other is not None and other[who] > mine + TOLERANCE:
            return True
    return False


def _describe(
    mechanism: str,
    bidders: list[Bidder],
    grid: list[Number],
    params: dict[str, Any],
    profile: tuple[int, ...],
) -> dict[str, Any]:
    """One equilibrium, re-run for the revenue and welfare the table did not keep."""
    lineup = [Bidder(b.id, b.value, grid[i]) for b, i in zip(bidders, profile)]
    result = run(mechanism, lineup, dict(params)).result
    bids = {b.id: grid[i] for b, i in zip(bidders, profile)}
    return {
        "bids": bids,
        "utilities": dict(result["utilities"]),
        "revenue": result["revenue"],
        "welfare": result["welfare"],
        "efficient": result["efficient"],
        "winner": result["winner"],
        "truthful": all(abs(bids[b.id] - b.value) <= TOLERANCE for b in bidders),
        "distance_from_truthful": sum(abs(bids[b.id] - b.value) for b in bidders),
    }


# ------------------------------------------------------------------------- dominance


def dominance(
    bidders: list[Bidder],
    grid: list[Number],
    table: dict[tuple[int, ...], dict[str, Number] | None],
) -> dict[str, dict[str, Any]]:
    """Is bidding your value a best reply to *every* rival profile, not merely to one?

    This is the sharper question, and the same table already answers it. Nash asks whether
    a bidder regrets its bid **given what everyone else did**; dominance asks whether it
    could ever regret it **whatever anyone else does**. A second-price bidder never can,
    which is why "second-price is truthful" is a sentence about dominance — and a
    first-price bidder can, which the report proves rather than asserts, by naming the
    rival profile and the deviation that beats honesty on it.

    The counterexample kept is the *worst* one found, not the first: the largest amount
    honesty leaves on the table is a more useful number than an arbitrary one, and it makes
    the verdict independent of the order the profiles happen to be enumerated in.

    ``truthful_dominant`` is ``None`` when the honest bid was refused against *every* rival
    profile and the question could therefore never be put. A Dutch clock started below a
    bidder's value does exactly that: bidding your value is not a move you are allowed to
    make, so no profile ever tests it. Reporting "dominant" there would be a vacuous truth
    dressed as a finding — "nothing beat honesty" is not a fact about honesty when honesty
    was never on the board — and it is the one way this search can assert something false.
    """
    report = {}
    seats = len(bidders)
    for seat, bidder in enumerate(bidders):
        honest_index = _nearest(grid, bidder.value)
        worst: dict[str, Any] | None = None
        tested = 0
        for rivals in itertools.product(range(len(grid)), repeat=seats - 1):
            base = table[_splice(rivals, seat, honest_index)]
            if base is None:
                continue
            tested += 1
            honest_utility = base[bidder.id]
            for index in range(len(grid)):
                other = table[_splice(rivals, seat, index)]
                if other is None:
                    continue
                gain = other[bidder.id] - honest_utility
                if gain > TOLERANCE and (worst is None or gain > worst["gain"]):
                    worst = {
                        "gain": gain,
                        "bid": grid[index],
                        "honest_bid": bidder.value,
                        "honest_utility": honest_utility,
                        "deviation_utility": other[bidder.id],
                        "rivals": {
                            other_bidder.id: grid[rivals[i]]
                            for i, other_bidder in enumerate(
                                b for j, b in enumerate(bidders) if j != seat
                            )
                        },
                    }
        report[bidder.id] = {
            "truthful_dominant": None if tested == 0 else worst is None,
            "tested": tested,
            "counterexample": worst,
            "why": _dominance_sentence(bidder, worst, tested),
        }
    return report


def _splice(rivals: tuple[int, ...], seat: int, index: int) -> tuple[int, ...]:
    return (*rivals[:seat], index, *rivals[seat:])


def _dominance_sentence(
    bidder: Bidder, worst: dict[str, Any] | None, tested: int
) -> str:
    if tested == 0:
        return (
            f"Unanswerable for {bidder.id} on this setup: bidding its value of "
            f"{num(bidder.value)} is not a move this mechanism will accept against any "
            "profile of rival bids, so there is no honest bid to test anything against. "
            "That is a fact about the parameters, not about honesty — a descending clock "
            "started below a bidder's value forbids that bidder from naming it."
        )
    if worst is None:
        return (
            f"On this grid {bidder.id} can never beat bidding its value of "
            f"{num(bidder.value)}: against all {tested} profiles of rival bids this "
            "mechanism would accept, honesty earns at least as much as anything else. "
            "That is dominance, and it is a much stronger promise than being one "
            "equilibrium among several."
        )
    seen = ", ".join(f"{who} {num(bid)}" for who, bid in worst["rivals"].items())
    return (
        f"Bidding its value is not dominant for {bidder.id}. Against rivals at {seen}, "
        f"honesty at {num(worst['honest_bid'])} earns {num(worst['honest_utility'])} while "
        f"bidding {num(worst['bid'])} earns {num(worst['deviation_utility'])} — "
        f"{num(worst['gain'])} more. One profile is enough: dominance is a claim about all "
        "of them."
    )


# --------------------------------------------------------------------- the whole answer


def analyse(
    mechanism: str,
    bidders: list[Bidder],
    params: dict[str, Any] | None = None,
    steps: int = DEFAULT_STEPS,
    draws: int = DEFAULT_DRAWS,
    seed: int = 0,
) -> dict[str, Any]:
    """Best-response curves, the pure equilibria of the grid game, and revenue equivalence.

    Three analyses of one setup rather than three endpoints, because they are three answers
    to one question and a learner reads them together: the curve is one bidder's slice, the
    equilibria are where every slice agrees at once, and the revenue check is what that
    buys the seller across mechanisms.

    The curves are drawn on a ``steps`` grid always; the profile search runs on the largest
    grid at or below it whose profile space fits :data:`MAX_PROFILES`. Two grids, because
    the two costs are nothing alike — a curve is ``bidders x grid`` auctions and a search is
    ``grid ** bidders`` — and squeezing the plot to fit a search it does not pay for would
    make the chart worse for no reason. When they differ, the report says so.
    """
    if mechanism not in REGISTRY:
        raise ValueError(
            f"unknown mechanism {mechanism!r}; expected one of {sorted(REGISTRY)}"
        )
    refuse_grid_search(mechanism)
    if not bidders:
        raise ValueError("at least one bidder is required")
    resolved = _resolve_params(REGISTRY[mechanism], params or {})

    grid = bid_grid(bidders, steps)
    curves = {
        b.id: best_response_curve(mechanism, bidders, seat, grid, resolved)
        for seat, b in enumerate(bidders)
    }
    return {
        "mechanism": mechanism,
        "params": resolved,
        "bidders": [b.to_dict() for b in bidders],
        "best_response": {"grid": grid, "steps": steps, "curves": curves},
        "game": _game(mechanism, bidders, resolved, steps),
        "revenue_equivalence": revenue_equivalence(bidders, draws=draws, seed=seed),
    }


def _game(
    mechanism: str,
    bidders: list[Bidder],
    params: dict[str, Any],
    steps: int,
) -> dict[str, Any]:
    """The exhaustive part, on the finest grid that fits — or an explanation of why not.

    A table too big to search does not fail the request. The reply curves and the revenue
    check are unaffected by it, they are the two thirds of the answer that still hold, and
    returning them with a sentence saying what was skipped beats a 400 that returns none of
    them. Silence would be the actual error: a missing equilibrium table reads as "there
    are none".
    """
    searchable = _searchable(bidders, steps)
    if searchable is None:
        smallest = len(bid_grid(bidders, MIN_STEPS)) ** len(bidders)
        return {
            "grid": [],
            "searched": False,
            "profiles": 0,
            "nash": None,
            "dominance": None,
            "note": (
                f"No equilibrium search: {len(bidders)} bidders need at least {smallest} "
                f"profiles even on the coarsest grid, and the search is capped at "
                f"{MAX_PROFILES} auctions. The reply curves and the revenue check below "
                "are unaffected. Remove a bidder to search this setup."
            ),
        }

    grid, used = searchable
    table = payoff_table(mechanism, bidders, grid, params)
    found = pure_nash(bidders, grid, table)
    described = sorted(
        (_describe(mechanism, bidders, grid, params, profile) for profile in found),
        key=lambda e: (e["distance_from_truthful"], -e["revenue"]),
    )
    coarsened = (
        ""
        if used == steps
        else (
            f" The reply curves above use a {steps}-step grid; searching every profile on "
            f"that grid would take more than {MAX_PROFILES} auctions, so the search below "
            f"was coarsened to {used} steps."
        )
    )
    return {
        "grid": grid,
        "steps": used,
        "searched": True,
        "profiles": len(table),
        "nash": {
            "count": len(found),
            "shown": described[:MAX_REPORTED],
            "truthful_is_nash": any(e["truthful"] for e in described),
            "why": _nash_sentence(len(found), described, grid) + coarsened,
        },
        "dominance": dominance(bidders, grid, table),
        "note": coarsened.strip(),
    }


def _searchable(bidders: list[Bidder], steps: int) -> tuple[list[Number], int] | None:
    """The finest grid at or below ``steps`` whose profile space fits the cap, if any."""
    for candidate in range(steps, MIN_STEPS - 1, -1):
        grid = bid_grid(bidders, candidate)
        if len(grid) ** len(bidders) <= MAX_PROFILES:
            return grid, candidate
    return None


def _nash_sentence(
    count: int, described: list[dict[str, Any]], grid: list[Number]
) -> str:
    where = (
        f"on a grid of {len(grid)} bids from {num(grid[0])} to {num(grid[-1])}, which "
        "includes every bidder's own value"
    )
    if count == 0:
        return (
            f"No profile on this grid is stable {where}: whatever everyone bids, somebody "
            "can do better by moving alone. That is not a gap in the search — some "
            "auctions genuinely have no pure equilibrium, and an all-pay auction is the "
            "standard example. Its equilibrium is in mixed strategies, which this search "
            "does not look for."
        )
    truthful = any(e["truthful"] for e in described)
    honest = (
        "Bidding your value is one of them."
        if truthful
        else "Bidding your value is not among them."
    )
    if count == 1:
        return f"Exactly one profile is stable {where}. {honest}"
    revenues = [e["revenue"] for e in described]
    inefficient = sum(1 for e in described if not e["efficient"])
    misallocated = (
        f", and {inefficient} of them leave the item with somebody other than the bidder "
        "who values it most"
        if inefficient
        else ""
    )
    return (
        f"{count} profiles are stable {where}. {honest} This is what Nash does and does "
        "not promise: it rules out unilateral regret and nothing else, so an equilibrium "
        "propped up by a bid nobody would ever really make survives it. They are not "
        f"equally good for the seller either — revenue across them runs from "
        f"{num(min(revenues))} to {num(max(revenues))}{misallocated}."
    )
