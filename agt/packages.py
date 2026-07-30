"""Combinatorial auctions: two ways to price the same bundles of items.

The bid language and the two winner-determination solvers live in
:mod:`agt.winner_determination` and are re-exported here, so ``agt.packages`` stays the
one place a package auction is reached from.

The pair of mechanisms below differ in a single decision — which allocation to charge for
— and everything else follows. ``greedy_package`` bills each winner their own bid on the
allocation a practical auction can actually afford to compute. ``vcg_package`` pays for
the exhaustive search first, and only because it did can it charge externalities and
promise that bidding your true value for a bundle is dominant. That promise is not a
property of the payment formula on its own: bolted onto the greedy allocation the very
same formula stops being truthful, which is a real trap in practice rather than a
footnote, and the step text says so out loud.
"""

from collections.abc import Callable, Iterator
from dataclasses import replace
from typing import Any

from agt.registry import Mechanism
from agt.stages import num
from agt.trace import Bidder, Number, Step, Trace, outcome_allocation, step
from agt.winner_determination import (
    MAX_BIDS,
    MAX_ITEMS,
    Allocation,
    Bids,
    PackageBid,
    greedy_allocate,
    item_universe,
    optimal_allocate,
    ranked,
    total_bid,
)

__all__ = [
    "MAX_BIDS",
    "MAX_ITEMS",
    "PACKAGE_REGISTRY",
    "PackageBid",
    "greedy_allocate",
    "greedy_package",
    "item_universe",
    "optimal_allocate",
    "run_packages",
    "total_bid",
    "vcg_package",
]

PackageFn = Callable[[list[PackageBid]], Iterator[Step]]

# =====================================================================================
# The two mechanisms.
#
# They differ in one decision — which allocation to charge for — and everything else
# follows from it. `greedy_package` takes the practical allocation and bills each winner
# their own bid. `vcg_package` pays for the exhaustive search and can then charge
# externalities, which is the only reason its truthfulness guarantee holds.
# =====================================================================================


def _participants(bids: Bids) -> list[Bidder]:
    """One :class:`~agt.trace.Bidder` per bidder id, so the shared scoring code has
    something to key its dicts by.

    ponytail: reusing the single-item record as a carrier rather than widening
    ``outcome_allocation`` to know about packages. Only ``id`` is ever read off these;
    ``value`` and ``bid`` are filled from that bidder's highest package bid purely so the
    record is not a lie. Ceiling: a package bidder has no one scalar value, so do not
    read those two fields. Upgrade: task 7 gives the trace a declared package input kind.
    """
    top: dict[str, PackageBid] = {}
    for b in bids:
        if b.bidder not in top or b.bid > top[b.bidder].bid:
            top[b.bidder] = b
    return [Bidder(name, b.value, b.bid) for name, b in top.items()]


def _best_value_welfare(bids: Bids) -> Number:
    """The most *value* any feasible allocation could have created.

    Efficiency is a question about values, not bids, so this re-solves the same packing
    problem with every bidder's private value in place of what they offered. It is what
    the auction would have achieved had everybody been honest.
    """
    honest = [replace(b, bid=b.value) for b in bids]
    return sum(b.value for b in optimal_allocate(honest))


def _bundle(b: PackageBid) -> str:
    return "{" + ", ".join(b.items) + "}"


def _offer(b: PackageBid) -> str:
    return f"{b.bidder} bids {num(b.bid)} for {_bundle(b)}"


def _names(bids: Bids) -> list[str]:
    return list(dict.fromkeys(b.bidder for b in bids))


def _open(bids: Bids, state: dict[str, Any]) -> Iterator[Step]:
    """collect package bids -> the item universe. Identical under both payment rules."""
    universe = item_universe(bids)
    yield step(
        "collect package bids",
        "Every bid is all-or-nothing: a bidder who wants two licences together is "
        "offering nothing at all for either one alone, which is exactly what separate "
        "single-item auctions cannot express. The offers are "
        f"{'; '.join(_offer(b) for b in bids)}. One bidder may submit several bids, but "
        "they are alternatives — XOR — so at most one of them can be accepted.",
        state,
        formula=" | ".join(f"{b.bidder}: {_bundle(b)} = {num(b.bid)}" for b in bids),
        stage="collect",
        bidders=_names(bids),
    )
    yield step(
        "item universe",
        f"Nothing was listed for sale in advance. What is on offer is simply the union "
        f"of everything anybody bid on: {', '.join(universe)}. An item nobody names is "
        "not in the auction, and an item several bidders name can still go to only one "
        "of them.",
        state,
        formula=f"items = {{{', '.join(universe)}}} ({len(universe)})",
        stage="items",
        bidders=_names(bids),
    )


def _gap(
    greedy: Allocation, optimal: Allocation, state: dict[str, Any]
) -> Iterator[Step]:
    """The step both mechanisms exist to make visible: what the shortcut costs.

    ponytail: this is why ``greedy_package`` inherits the exhaustive search's size guard
    even though greedy itself has no limit — you cannot show a gap without solving both
    sides of it. Ceiling: greedy alone would happily run on hundreds of items but the
    mechanism refuses them. Upgrade: report the gap only when the input is small enough
    and say so, rather than refusing the run.
    """
    lost = total_bid(optimal) - total_bid(greedy)
    verdict = (
        "Greedy happened to find the best allocation here, so it gave up nothing."
        if not lost
        else f"Greedy gave up {num(lost)} by taking the biggest bid first and being "
        "blocked afterwards, and no amount of care in the payment rule can win that back."
    )
    state["greedy_welfare"] = total_bid(greedy)
    state["optimal_welfare"] = total_bid(optimal)
    state["welfare_gap"] = lost
    yield step(
        "greedy vs optimal",
        "Choosing the best set of bids to accept is NP-hard, so a practical auction runs "
        f"the greedy rule and settles for what it finds. Greedy accepts "
        f"{', '.join(_names(greedy)) or 'nobody'} and totals {num(total_bid(greedy))}; "
        f"the exhaustive search accepts {', '.join(_names(optimal)) or 'nobody'} and "
        f"totals {num(total_bid(optimal))}. {verdict}",
        state,
        formula=(
            f"gap = {num(total_bid(optimal))} - {num(total_bid(greedy))} = {num(lost)}"
        ),
        stage="compare",
        bidders=sorted(set(_names(greedy)) ^ set(_names(optimal))),
    )


def _settle(
    bids: Bids,
    accepted: Allocation,
    greedy: Allocation,
    optimal: Allocation,
    payments: dict[str, Number],
    state: dict[str, Any],
    detail: str,
) -> Iterator[Step]:
    """Terminal stage: total the bill and score the allocation, gap included."""
    people = _participants(bids)
    state["payments"] = {p.id: payments.get(p.id, 0) for p in people}
    state["winner"] = accepted[0].bidder if accepted else None
    total = sum(payments.values())
    terms = [num(payments[b.bidder]) for b in accepted if payments.get(b.bidder, 0)]
    summed = " + ".join(terms) + f" = {num(total)}" if len(terms) > 1 else num(total)
    yield step(
        "payments",
        detail,
        state,
        formula=f"revenue = {summed}",
        stage="payments",
        bidders=[b.bidder for b in accepted if payments.get(b.bidder, 0)],
    )
    scored = outcome_allocation(
        people,
        {b.bidder: list(b.items) for b in accepted},
        payments,
        {b.bidder: b.value for b in accepted},
        _best_value_welfare(bids),
    )
    return scored | {
        "greedy_welfare": total_bid(greedy),
        "optimal_welfare": total_bid(optimal),
        "welfare_gap": total_bid(optimal) - total_bid(greedy),
    }


PACKAGE_REGISTRY: dict[str, Mechanism] = {}


def _package_mechanism(
    name: str, *, label: str, description: str, truthful_dominant: bool
) -> Any:
    """Register a package mechanism, declaring the same facts ``@mechanism`` demands.

    ponytail: a second registry rather than an entry in the shared one, because the
    cross-mechanism invariant tests are parametrized over every name in ``REGISTRY`` and
    feed it scalar bidders and a reserve — a package mechanism registered there today
    would fail four of them for the right reason. Ceiling: the UI cannot see these yet.
    Upgrade: task 7 adds ``input_kind`` to ``Mechanism``, merges this dict into
    ``REGISTRY``, and teaches those invariants to build the input each kind expects.
    """

    def decorate(fn: PackageFn) -> PackageFn:
        PACKAGE_REGISTRY[name] = Mechanism(
            name, label, description, {}, fn, truthful_dominant
        )
        return fn

    return decorate


@_package_mechanism(
    "greedy_package",
    label="Greedy combinatorial auction",
    description="Bids are accepted in descending order, skipping conflicts; "
    "each winner pays their own bid. Practical, not truthful.",
    truthful_dominant=False,
)
def greedy_package(bids: list[PackageBid]) -> Iterator[Step]:
    state: dict[str, Any] = {
        "bids": [b.to_dict() for b in bids],
        "items": list(item_universe(bids)),
    }
    yield from _open(bids, state)

    accepted = greedy_allocate(bids)
    pending = list(accepted)
    taken: dict[str, str] = {}
    won: set[str] = set()
    for b in ranked(bids):
        if pending and pending[0] is b:
            pending.pop(0)
            won.add(b.bidder)
            taken.update({i: b.bidder for i in b.items})
            state["allocation"] = {
                w.bidder: list(w.items) for w in accepted if w.bidder in won
            }
            detail = (
                f"{b.bidder}'s {num(b.bid)} for {_bundle(b)} is accepted: none of those "
                "items is spoken for and this bidder has not already won something else."
            )
        else:
            clash = [i for i in b.items if i in taken]
            reason = (
                f"{b.bidder} has already won a bundle, and a bidder's bids are "
                "alternatives"
                if b.bidder in won
                else f"{', '.join(clash)} already went to "
                f"{', '.join(dict.fromkeys(taken[i] for i in clash))}"
            )
            detail = (
                f"{b.bidder}'s {num(b.bid)} for {_bundle(b)} is skipped: {reason}. Greedy "
                "never reconsiders a bid it has already accepted, which is precisely how "
                "it loses welfare."
            )
        yield step(
            "consider bid",
            detail,
            state,
            formula=f"{b.bidder}: {_bundle(b)} at {num(b.bid)}",
            stage="allocate",
            bidders=[b.bidder],
        )

    optimal = optimal_allocate(bids)
    yield from _gap(accepted, optimal, state)

    payments = {b.bidder: b.bid for b in accepted}
    state["payments"] = dict(payments)
    yield step(
        "price rule",
        "Every winner simply pays their own bid — first price, one bundle at a time. "
        "Nothing here charges a bidder for the harm they do to anybody else, and nothing "
        "protects a winner from having offered more than they had to, so wherever there "
        "is room to shade a bid down and still be accepted, doing so is pure profit and "
        "bidding your true value for a bundle is not a best reply. On top of that the "
        "allocation being charged for is greedy's, not the optimal one.",
        state,
        formula="p_i = b_i",
        stage="price",
        bidders=[b.bidder for b in accepted],
    )

    return (
        yield from _settle(
            bids,
            accepted,
            accepted,
            optimal,
            payments,
            state,
            "The winners hand over exactly what they offered: "
            f"{'; '.join(f'{b.bidder} pays {num(b.bid)}' for b in accepted)}. Everybody "
            "whose bid was skipped pays nothing and gets nothing.",
        )
    )


@_package_mechanism(
    "vcg_package",
    label="VCG combinatorial auction",
    description="The best allocation is solved for exactly, then each winner is charged "
    "the welfare the others lose by their presence. Truthful.",
    truthful_dominant=True,
)
def vcg_package(bids: list[PackageBid]) -> Iterator[Step]:
    state: dict[str, Any] = {
        "bids": [b.to_dict() for b in bids],
        "items": list(item_universe(bids)),
    }
    yield from _open(bids, state)

    greedy = greedy_allocate(bids)
    accepted = optimal_allocate(bids)
    state["allocation"] = {b.bidder: list(b.items) for b in accepted}
    state["winner"] = accepted[0].bidder if accepted else None
    yield step(
        "solve for the best allocation",
        "Every feasible combination of bids is searched, not just the ones a single pass "
        "down the list would reach, and the best of them is "
        f"{'; '.join(_offer(b) for b in accepted)} — worth {num(total_bid(accepted))} in "
        "total. This exhaustive step is what the payment rule below depends on, and it "
        f"is why the auction refuses inputs above {MAX_ITEMS} items or {MAX_BIDS} bids.",
        state,
        formula=" + ".join(num(b.bid) for b in accepted)
        + f" = {num(total_bid(accepted))}",
        stage="allocate",
        bidders=_names(accepted),
    )

    yield from _gap(greedy, accepted, state)

    yield step(
        "price rule",
        "Each winner pays the harm their presence does to everybody else: what the other "
        "bidders could have collected had this one never shown up, minus what they "
        "actually collected alongside them. A winner's own bid never appears in their "
        "own bill — it only decides whether they win — so raising it can never raise "
        "their price and lowering it can only lose them the bundle, which is why bidding "
        "your true value for a bundle is dominant here. That guarantee rests entirely on "
        "the allocation being the optimal one: bolt these same formulas onto the greedy "
        "allocation and they stop being truthful, because a bidder can then shade a bid "
        "to change which bundles greedy happens to accept. It is a real trap in "
        "practice, not a footnote.",
        state,
        formula="p_i = W(without i) - (W(all) - b_i)",
        stage="price",
        bidders=_names(accepted),
    )

    total = total_bid(accepted)
    payments: dict[str, Number] = {}
    for b in accepted:
        others = [x for x in bids if x.bidder != b.bidder]
        without = total_bid(optimal_allocate(others))
        payments[b.bidder] = without - (total - b.bid)
        state["payments"] = dict(payments)
        yield step(
            "bundle price",
            f"{b.bidder} takes {_bundle(b)}. Without {b.bidder} in the room the remaining "
            f"bids could have been worth {num(without)}; alongside {b.bidder} they are "
            f"worth {num(total - b.bid)}. The difference, {num(payments[b.bidder])}, is "
            f"what {b.bidder} took away from everybody else, and it is the whole bill.",
            state,
            formula=(
                f"p_{b.bidder} = {num(without)} - ({num(total)} - {num(b.bid)}) "
                f"= {num(payments[b.bidder])}"
            ),
            stage="price",
            bidders=[b.bidder, *(x.bidder for x in others)],
        )

    return (
        yield from _settle(
            bids,
            accepted,
            greedy,
            accepted,
            payments,
            state,
            f"{accepted[0].bidder} tops the allocation and pays "
            f"{num(payments[accepted[0].bidder])}. Nobody is ever charged for value they "
            "did not take away from somebody else, so a winner who displaced nothing "
            "pays nothing at all.",
        )
    )


def run_packages(name: str, bids: list[PackageBid]) -> Trace:
    """Run a package mechanism to completion and return its :class:`~agt.trace.Trace`.

    ponytail: a second driver beside ``registry.run`` rather than a widened one, because
    ``run`` validates scalar bidder ids and resolves a params schema, and neither applies
    here yet. Ceiling: the ``Trace.bidders`` slot carries :class:`PackageBid` records,
    which serialize fine but are not the declared type. Upgrade: task 7 folds this into
    ``run`` behind the declared input kind.
    """
    if name not in PACKAGE_REGISTRY:
        raise ValueError(
            f"unknown package mechanism {name!r}; "
            f"expected one of {sorted(PACKAGE_REGISTRY)}"
        )
    if not bids:
        raise ValueError("at least one package bid is required")

    generator = PACKAGE_REGISTRY[name].fn(list(bids))
    steps: list[Step] = []
    while True:
        try:
            steps.append(next(generator))
        except StopIteration as stop:
            result = stop.value
            break
    if result is None:
        raise ValueError(f"mechanism {name!r} finished without returning a result")
    return Trace(name, {}, list(bids), steps, result)
