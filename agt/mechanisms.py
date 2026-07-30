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


# ---------------------------------------------------------------- clock auctions
#
# The clock jumps straight to the next price at which something happens instead of
# ticking by a fixed increment. Nothing is lost — no bidder acts between two dropouts —
# and the trace stays short enough to see the equivalences in a handful of steps.


@mechanism(
    "english",
    label="English (ascending clock)",
    description="Price rises until one bidder is left; equivalent to a second-price auction.",
    params={"reserve": RESERVE},
)
def english(bidders: list[Bidder], reserve: float = 0) -> Iterator[Step]:
    state: dict[str, Any] = {"bids": {b.id: b.bid for b in bidders}, "reserve": reserve}
    yield step(
        "collect bids",
        "Nothing is submitted on paper. Each bidder privately knows the highest price "
        "they will keep their hand up to, and the clock is what reveals it.",
        state,
        stage="collect",
        bidders=_ids(bidders),
    )

    active = [b for b in bidders if b.bid >= reserve]
    state["clock"] = reserve
    state["active"] = _ids(active)
    yield step(
        "clock start",
        f"The clock opens at the reserve of {_n(reserve)} and only ever rises. "
        + (
            f"{len(active)} bidders are willing to pay that much and keep their hands up."
            if active
            else "No bidder is willing to pay that much."
        ),
        state,
        formula=f"clock = r = {_n(reserve)}",
        stage="clock",
        bidders=_ids(active),
    )
    if not active:
        return (
            yield from _no_sale(
                bidders,
                state,
                f"Every hand is down at the opening price of {_n(reserve)}, so the "
                "auction ends before it starts and the item stays with the seller.",
                f"max(b) < r = {_n(reserve)}",
            )
        )

    ranked = _rank(active)
    winner, losers = ranked[0], ranked[1:]
    clock = reserve
    gone: list[str] = []
    for leaver in sorted(losers, key=lambda b: b.bid):
        clock = leaver.bid
        gone.append(leaver.id)
        left = [b.id for b in active if b.id not in gone]
        state["clock"] = clock
        state["out"] = list(gone)
        state["active"] = left
        yield step(
            "dropout",
            f"The clock reaches {_n(clock)}, which is exactly {leaver.id}'s limit, so "
            f"{leaver.id} lowers their hand. "
            + (
                f"{len(left)} bidders are still in."
                if len(left) > 1
                else f"Only {left[0]} is still in."
            ),
            state,
            formula=f"clock = {_n(clock)} = b_{leaver.id}",
            stage="dropout",
            bidders=[leaver.id],
        )

    state["winner"] = winner.id
    yield step(
        "pick winner",
        f"The clock stops: {winner.id} is the last bidder with a hand up and takes "
        f"the item. {TIE_RULE}",
        state,
        formula=f"last standing = {winner.id}",
        stage="winner",
        bidders=[winner.id],
    )

    price = max(clock, reserve)
    state["price"] = price
    yield step(
        "price rule",
        f"{winner.id} pays the price at which the last rival dropped out — the "
        f"second-highest limit in the room — and never their own limit of "
        f"{_n(winner.bid)}. That is why an ascending clock is the same mechanism as a "
        "sealed-bid second-price auction: same winner, same price, and the same reason "
        "to be honest about what the item is worth to you.",
        state,
        formula=f"p = clock at final dropout = {_n(price)}",
        stage="price",
        bidders=[b.id for b in losers if b.bid == clock],
    )

    return (
        yield from _settle(
            bidders,
            state,
            {b.id: (price if b.id == winner.id else 0) for b in bidders},
            f"{winner.id} pays {_n(price)}; everybody who dropped out pays nothing.",
        )
    )


START = {
    "type": "number",
    "default": None,
    "label": "Clock start price",
    "min": 0,
    "description": "Leave blank to start at twice the highest bid.",
}


@mechanism(
    "dutch",
    label="Dutch (descending clock)",
    description="Price falls until someone claims the item; equivalent to a first-price auction.",
    params={"reserve": RESERVE, "start": START},
)
def dutch(
    bidders: list[Bidder], reserve: float = 0, start: float | None = None
) -> Iterator[Step]:
    state: dict[str, Any] = {"bids": {b.id: b.bid for b in bidders}, "reserve": reserve}
    yield step(
        "collect bids",
        "Nothing is submitted on paper. Each bidder privately knows the highest price "
        "at which they would still take the item, and says nothing until they claim it.",
        state,
        stage="collect",
        bidders=_ids(bidders),
    )

    top = max((b.bid for b in bidders), default=0)
    # ponytail: default start = twice the top bid. Ceiling — it peeks at private bids to
    # pick a number a real seller could not see. Upgrade: pass `start` explicitly, or
    # derive it from a declared value distribution once phase 2 adds strategies.
    if start is None:
        start = max(2 * top, reserve)
    state["clock"] = start
    state["start"] = start
    yield step(
        "clock start",
        f"The clock opens at {_n(start)}, above anything anyone would pay, and falls "
        "from there. The first bidder to speak gets the item.",
        state,
        formula=f"clock = {_n(start)}, falling",
        stage="clock",
        bidders=_ids(bidders),
    )

    contenders = [b for b in bidders if b.bid >= reserve]
    if not contenders:
        state["clock"] = reserve
        return (
            yield from _no_sale(
                bidders,
                state,
                f"The clock falls all the way to the reserve of {_n(reserve)} without a "
                "single hand going up, so the seller keeps the item.",
                f"max(b) < r = {_n(reserve)}",
            )
        )

    accept = min(start, max(b.bid for b in contenders))
    state["clock"] = accept
    yield step(
        "clock falls",
        (
            f"Nobody speaks at {_n(start)}, so the clock ticks down. The first price any "
            f"bidder is willing to pay is {_n(accept)}."
            if accept < start
            else f"The clock does not have to fall: somebody is already willing to pay "
            f"the opening price of {_n(accept)}."
        ),
        state,
        formula=f"clock: {_n(start)} -> {_n(accept)}",
        stage="clock",
        bidders=[b.id for b in contenders if b.bid >= accept],
    )

    claimants = [b for b in contenders if b.bid >= accept]
    winner = claimants[0]
    state["winner"] = winner.id
    yield step(
        "pick winner",
        f"{winner.id} accepts at {_n(accept)} and the clock stops. "
        + (
            f"{len(claimants)} bidders would have accepted here; the one listed first "
            "reaches the button first."
            if len(claimants) > 1
            else "Nobody else ever gets a chance to say anything."
        ),
        state,
        formula=f"first to accept = {winner.id}",
        stage="accept",
        bidders=[b.id for b in claimants],
    )

    price = accept
    state["price"] = price
    yield step(
        "price rule",
        f"{winner.id} pays the price they chose to stop the clock at, which is their "
        "own bid — no rival's limit is ever revealed, let alone used. Deciding when to "
        "stop the clock is exactly the problem of choosing a sealed first-price bid, so "
        "a descending clock is the same mechanism as a first-price auction, shading and "
        "all.",
        state,
        formula=f"p = clock at acceptance = b_{winner.id} = {_n(price)}",
        stage="price",
        bidders=[winner.id],
    )

    return (
        yield from _settle(
            bidders,
            state,
            {b.id: (price if b.id == winner.id else 0) for b in bidders},
            f"{winner.id} pays {_n(price)}; nobody else pays anything.",
        )
    )
