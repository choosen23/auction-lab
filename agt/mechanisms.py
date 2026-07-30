"""The mechanism registry and every single-item auction.

A mechanism is a generator: it ``yield``s one :class:`~agt.trace.Step` per algorithmic
stage and ``return``s the result dict from :func:`~agt.trace.outcome`. ``run()`` drives
the generator and wraps everything in a :class:`~agt.trace.Trace`.

Mechanism bodies read top-to-bottom on purpose — they *are* the teaching material, so
the steps they emit matter as much as the numbers they produce.

Adding a mechanism means adding one decorated generator here. The web UI builds its
whole setup form from :func:`registry_schema`, so it needs no changes.
"""

import copy
import math
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from typing import Any

from agt.trace import Bidder, Step, Trace, outcome, step

MechanismFn = Callable[..., Iterator[Step]]


@dataclass(frozen=True)
class Mechanism:
    """A registered mechanism: how to run it and how to build a form for it."""

    name: str
    label: str
    description: str
    params: dict[str, dict[str, Any]]
    fn: MechanismFn


REGISTRY: dict[str, Mechanism] = {}

# Every single-item mechanism here supports a reserve, so the schema is shared.
RESERVE = {
    "type": "number",
    "default": 0,
    "label": "Reserve price",
    "min": 0,
    "description": "Bids below the reserve are ineligible; the seller keeps the item.",
}


def mechanism(
    name: str,
    *,
    label: str,
    description: str,
    params: dict[str, dict[str, Any]] | None = None,
) -> Callable[[MechanismFn], MechanismFn]:
    """Register a mechanism generator under ``name`` with a form-ready params schema."""

    def decorate(fn: MechanismFn) -> MechanismFn:
        REGISTRY[name] = Mechanism(name, label, description, params or {}, fn)
        return fn

    return decorate


def registry_schema() -> dict[str, dict[str, Any]]:
    """Serialize REGISTRY to a JSON-safe dict. The UI generates its form from this."""
    return {
        name: {
            "name": m.name,
            "label": m.label,
            "description": m.description,
            "params": copy.deepcopy(m.params),
        }
        for name, m in REGISTRY.items()
    }


# --------------------------------------------------------------------------- run


def run(
    name: str,
    bidders: list[Bidder],
    params: dict[str, Any] | None = None,
) -> Trace:
    """Run mechanism ``name`` to completion and return its :class:`Trace`."""
    if name not in REGISTRY:
        raise ValueError(
            f"unknown mechanism {name!r}; expected one of {sorted(REGISTRY)}"
        )
    spec = REGISTRY[name]
    resolved = _resolve_params(spec, params or {})

    generator = spec.fn(list(bidders), **resolved)
    steps: list[Step] = []
    while True:
        try:
            steps.append(next(generator))
        except StopIteration as stop:  # the generator's ``return`` lands here
            result = stop.value
            break
    if result is None:
        raise ValueError(f"mechanism {name!r} finished without returning a result")
    return Trace(
        mechanism=name,
        params=resolved,
        bidders=list(bidders),
        steps=steps,
        result=result,
    )


def _resolve_params(spec: Mechanism, given: dict[str, Any]) -> dict[str, Any]:
    """Fill in schema defaults and reject anything the schema does not declare."""
    for key in given:
        if key not in spec.params:
            raise ValueError(
                f"unknown parameter {key!r} for {spec.name!r}; "
                f"expected one of {sorted(spec.params)}"
            )
    resolved = {}
    for key, schema in spec.params.items():
        value = given.get(key, schema["default"])
        resolved[key] = _validate_param(key, value, schema)
    return resolved


def _validate_param(key: str, value: Any, schema: dict[str, Any]) -> Any:
    """Validate one param against its schema entry. ``None`` means 'use the default'."""
    if value is None and schema["default"] is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"parameter {key!r} must be a number, got {value!r}")
    if not math.isfinite(value):
        raise ValueError(f"parameter {key!r} must be finite, got {value!r}")
    low, high = schema.get("min"), schema.get("max")
    if low is not None and value < low:
        raise ValueError(f"parameter {key!r} must be >= {low}, got {value!r}")
    if high is not None and value > high:
        raise ValueError(f"parameter {key!r} must be <= {high}, got {value!r}")
    return value


# ----------------------------------------------------------------- shared stages


def _n(x: float) -> str:
    """Format a number for a formula string: 95 not 95.0, 41.5 kept as 41.5."""
    return f"{x:g}"


def _ids(bidders: list[Bidder]) -> list[str]:
    return [b.id for b in bidders]


def _rank(bidders: list[Bidder]) -> list[Bidder]:
    """Highest bid first. ``sorted`` is stable, so ties keep the listed order."""
    return sorted(bidders, key=lambda b: -b.bid)


TIE_RULE = "Equal bids keep the order the bidders were listed in, so a tie goes to the first-listed bidder."


def _sealed_prologue(
    bidders: list[Bidder], reserve: float, state: dict[str, Any]
) -> Iterator[Step]:
    """collect bids -> sort -> (apply reserve). Returns the eligible bidders, ranked."""
    yield step(
        "collect bids",
        "Each bidder submits one sealed bid; private values are never revealed.",
        state,
        stage="collect",
        bidders=_ids(bidders),
    )

    ranked = _rank(bidders)
    state["ranked"] = _ids(ranked)
    yield step(
        "sort",
        f"Bids are ranked from highest to lowest. {TIE_RULE}",
        state,
        formula=" >= ".join(_n(b.bid) for b in ranked),
        stage="sort",
        bidders=state["ranked"],
    )

    eligible = [b for b in ranked if b.bid >= reserve]
    if reserve:
        dropped = [b for b in ranked if b.bid < reserve]
        state["eligible"] = _ids(eligible)
        named = ", ".join(f"{b.id} ({_n(b.bid)})" for b in dropped) or "nobody"
        yield step(
            "apply reserve",
            f"The seller refuses to sell below the reserve of {_n(reserve)}, "
            f"so these bids are struck out: {named}.",
            state,
            formula=f"eligible: b_i >= r = {_n(reserve)}",
            stage="reserve",
            bidders=_ids(dropped),
        )
    return eligible


def _no_sale(
    bidders: list[Bidder], state: dict[str, Any], detail: str, formula: str | None
) -> Iterator[Step]:
    """Terminal stage when nothing clears the reserve: nobody wins and nobody pays."""
    state["winner"] = None
    state["price"] = 0
    state["payments"] = {b.id: 0 for b in bidders}
    yield step("no sale", detail, state, formula=formula, stage="result", bidders=[])
    return outcome(bidders, winner=None, payments=state["payments"])


def _pick_winner(
    eligible: list[Bidder], state: dict[str, Any], detail: str
) -> Iterator[Step]:
    """The highest eligible bid takes the item; ties fall to the first-listed bidder."""
    winner = eligible[0]
    tied = [b for b in eligible if b.bid == winner.bid]
    note = (
        f" {len(tied)} bidders tied at {_n(winner.bid)}; the tie goes to {winner.id} "
        "because they were listed first."
        if len(tied) > 1
        else ""
    )
    state["winner"] = winner.id
    yield step(
        "pick winner",
        detail + note,
        state,
        formula=f"argmax b_i = {winner.id} at {_n(winner.bid)}",
        stage="winner",
        bidders=[b.id for b in tied],
    )
    return winner


def _settle(
    bidders: list[Bidder],
    state: dict[str, Any],
    payments: dict[str, float],
    detail: str,
) -> Iterator[Step]:
    """Terminal stage: record who actually hands over money, and how much."""
    state["payments"] = dict(payments)
    paying = [b.id for b in bidders if payments.get(b.id, 0)]
    total = sum(payments.values())
    terms = [_n(payments[b.id]) for b in bidders if payments.get(b.id, 0)]
    summed = " + ".join(terms) + f" = {_n(total)}" if len(terms) > 1 else _n(total)
    yield step(
        "payments",
        detail,
        state,
        formula=f"revenue = {summed}",
        stage="payments",
        bidders=paying,
    )
    return outcome(bidders, winner=state["winner"], payments=payments)


# ------------------------------------------------------------ sealed-bid auctions


@mechanism(
    "second_price",
    label="Second-price (Vickrey)",
    description="Highest bid wins and pays the highest losing bid. Truthful bidding is dominant.",
    params={"reserve": RESERVE},
)
def second_price(bidders: list[Bidder], reserve: float = 0) -> Iterator[Step]:
    state: dict[str, Any] = {"bids": {b.id: b.bid for b in bidders}, "reserve": reserve}
    eligible = yield from _sealed_prologue(bidders, reserve, state)
    if not eligible:
        return (
            yield from _no_sale(
                bidders,
                state,
                f"No bid reached the reserve of {_n(reserve)}, so the item is not sold "
                "even though the bidders valued it.",
                f"max(b) < r = {_n(reserve)}",
            )
        )

    winner = yield from _pick_winner(
        eligible, state, "The highest eligible bid wins the item."
    )

    best_loser = max((b.bid for b in eligible[1:]), default=reserve)
    price = max(best_loser, reserve)
    state["price"] = price
    yield step(
        "price rule",
        "The winner pays the highest competing bid, not their own. Bidding above your "
        "value can only make you overpay, and shading below it can only lose the item "
        "at a price you would have been happy with, so honesty is the dominant strategy.",
        state,
        formula=f"p = max(max(b_-i), r) = max({_n(best_loser)}, {_n(reserve)}) = {_n(price)}",
        stage="price",
        bidders=[b.id for b in eligible[1:] if b.bid == best_loser],
    )

    return (
        yield from _settle(
            bidders,
            state,
            {b.id: (price if b.id == winner.id else 0) for b in bidders},
            f"{winner.id} pays {_n(price)}; the losers pay nothing.",
        )
    )


@mechanism(
    "first_price",
    label="First-price sealed bid",
    description="Highest bid wins and pays its own bid, which rewards shading below your value.",
    params={"reserve": RESERVE},
)
def first_price(bidders: list[Bidder], reserve: float = 0) -> Iterator[Step]:
    state: dict[str, Any] = {"bids": {b.id: b.bid for b in bidders}, "reserve": reserve}
    eligible = yield from _sealed_prologue(bidders, reserve, state)
    if not eligible:
        return (
            yield from _no_sale(
                bidders,
                state,
                f"No bid reached the reserve of {_n(reserve)}, so the item is not sold.",
                f"max(b) < r = {_n(reserve)}",
            )
        )

    winner = yield from _pick_winner(
        eligible, state, "The highest eligible bid wins the item."
    )

    price = winner.bid
    state["price"] = price
    yield step(
        "price rule",
        "The winner pays exactly what they bid, so every unit of bid above the "
        "runner-up is money left on the table. That is why bidders shade their bids "
        "below their true value here, and why the bids you see are not values.",
        state,
        formula=f"p = b_i = {_n(price)}",
        stage="price",
        bidders=[winner.id],
    )

    return (
        yield from _settle(
            bidders,
            state,
            {b.id: (price if b.id == winner.id else 0) for b in bidders},
            f"{winner.id} pays their own bid of {_n(price)}; the losers pay nothing.",
        )
    )


@mechanism(
    "all_pay",
    label="All-pay auction",
    description="Highest bid wins, but every eligible bidder pays their bid — losing bids are sunk.",
    params={"reserve": RESERVE},
)
def all_pay(bidders: list[Bidder], reserve: float = 0) -> Iterator[Step]:
    state: dict[str, Any] = {"bids": {b.id: b.bid for b in bidders}, "reserve": reserve}
    eligible = yield from _sealed_prologue(bidders, reserve, state)
    if not eligible:
        return (
            yield from _no_sale(
                bidders,
                state,
                f"No bid reached the reserve of {_n(reserve)}, so the auction does not "
                "happen and nobody pays — not even the losers.",
                f"max(b) < r = {_n(reserve)}",
            )
        )

    winner = yield from _pick_winner(
        eligible, state, "The highest eligible bid wins the item."
    )

    price = winner.bid
    state["price"] = price
    yield step(
        "price rule",
        "Every bidder who cleared the reserve pays their own bid, win or lose. A losing "
        "bid buys nothing, which is why this models lobbying, contests and R&D races.",
        state,
        formula=f"p_i = b_i for every eligible i; winner pays {_n(price)}",
        stage="price",
        bidders=_ids(eligible),
    )

    eligible_ids = {b.id for b in eligible}
    payments = {b.id: (b.bid if b.id in eligible_ids else 0) for b in bidders}
    losses = ", ".join(
        f"{b.id} loses {_n(b.bid)}" for b in eligible if b.id != winner.id
    )
    return (
        yield from _settle(
            bidders,
            state,
            payments,
            f"{winner.id} pays {_n(price)} and takes the item"
            + (
                f"; the losers pay their bids and get nothing: {losses}."
                if losses
                else "."
            ),
        )
    )
