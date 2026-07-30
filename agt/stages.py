"""Stages shared by more than one mechanism.

Each helper here is itself a generator: it yields the steps for one stage and returns
whatever the mechanism needs next, so a mechanism body can read as a sequence of
``yield from`` lines and still be the clearest description of the rules.
"""

from collections.abc import Iterator
from typing import Any

from agt.trace import Bidder, Step, outcome, step

TIE_RULE = (
    "Equal bids keep the order the bidders were listed in, "
    "so a tie goes to the first-listed bidder."
)


def num(x: float) -> str:
    """Format a number for a formula string: 95 not 95.0, 41.5 kept as 41.5."""
    return f"{x:g}"


def ids(bidders: list[Bidder]) -> list[str]:
    return [b.id for b in bidders]


def rank(bidders: list[Bidder]) -> list[Bidder]:
    """Highest bid first. ``sorted`` is stable, so ties keep the listed order."""
    return sorted(bidders, key=lambda b: -b.bid)


def collect_and_sort(
    bidders: list[Bidder], reserve: float, state: dict[str, Any]
) -> Iterator[Step]:
    """collect bids -> sort -> (apply reserve). Returns the eligible bidders, ranked."""
    yield step(
        "collect bids",
        "Each bidder submits one sealed bid; private values are never revealed.",
        state,
        stage="collect",
        bidders=ids(bidders),
    )

    ranked = rank(bidders)
    state["ranked"] = ids(ranked)
    yield step(
        "sort",
        f"Bids are ranked from highest to lowest. {TIE_RULE}",
        state,
        formula=" >= ".join(num(b.bid) for b in ranked),
        stage="sort",
        bidders=state["ranked"],
    )

    eligible = [b for b in ranked if b.bid >= reserve]
    if reserve:
        dropped = [b for b in ranked if b.bid < reserve]
        state["eligible"] = ids(eligible)
        named = ", ".join(f"{b.id} ({num(b.bid)})" for b in dropped) or "nobody"
        yield step(
            "apply reserve",
            f"The seller refuses to sell below the reserve of {num(reserve)}, "
            f"so these bids are struck out: {named}.",
            state,
            formula=f"eligible: b_i >= r = {num(reserve)}",
            stage="reserve",
            bidders=ids(dropped),
        )
    return eligible


def no_sale(
    bidders: list[Bidder], state: dict[str, Any], detail: str, formula: str | None
) -> Iterator[Step]:
    """Terminal stage when nothing clears the reserve: nobody wins and nobody pays."""
    state["winner"] = None
    state["price"] = 0
    state["payments"] = {b.id: 0 for b in bidders}
    yield step("no sale", detail, state, formula=formula, stage="result", bidders=[])
    return outcome(bidders, winner=None, payments=state["payments"])


def pick_winner(
    eligible: list[Bidder], state: dict[str, Any], detail: str
) -> Iterator[Step]:
    """The highest eligible bid takes the item; ties fall to the first-listed bidder."""
    winner = eligible[0]
    tied = [b for b in eligible if b.bid == winner.bid]
    note = (
        f" {len(tied)} bidders tied at {num(winner.bid)}; the tie goes to {winner.id} "
        "because they were listed first."
        if len(tied) > 1
        else ""
    )
    state["winner"] = winner.id
    yield step(
        "pick winner",
        detail + note,
        state,
        formula=f"argmax b_i = {winner.id} at {num(winner.bid)}",
        stage="winner",
        bidders=[b.id for b in tied],
    )
    return winner


def settle(
    bidders: list[Bidder],
    state: dict[str, Any],
    payments: dict[str, float],
    detail: str,
) -> Iterator[Step]:
    """Terminal stage: record who actually hands over money, and how much."""
    state["payments"] = dict(payments)
    paying = [b.id for b in bidders if payments.get(b.id, 0)]
    total = sum(payments.values())
    terms = [num(payments[b.id]) for b in bidders if payments.get(b.id, 0)]
    summed = " + ".join(terms) + f" = {num(total)}" if len(terms) > 1 else num(total)
    yield step(
        "payments",
        detail,
        state,
        formula=f"revenue = {summed}",
        stage="payments",
        bidders=paying,
    )
    return outcome(bidders, winner=state["winner"], payments=payments)
