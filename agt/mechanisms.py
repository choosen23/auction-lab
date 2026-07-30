"""Every single-item auction, and the public entry points for running one.

A mechanism is a generator: it ``yield``s one :class:`~agt.trace.Step` per algorithmic
stage and ``return``s the result dict from :func:`~agt.trace.outcome`. :func:`run` drives
the generator and wraps everything in a :class:`~agt.trace.Trace`.

Mechanism bodies read top-to-bottom on purpose — they *are* the teaching material, so
the steps they emit matter as much as the numbers they produce.

Adding a mechanism means adding one decorated generator here. The web UI builds its
whole setup form from :func:`registry_schema`, so it needs no changes.
"""

from collections.abc import Iterator
from typing import Any

from agt.registry import REGISTRY, Mechanism, mechanism, registry_schema, run
from agt.stages import (
    collect_and_sort,
    ids,
    no_sale,
    num,
    pick_winner,
    rank,
    settle,
)
from agt.trace import Bidder, Step, step

__all__ = ["REGISTRY", "Mechanism", "mechanism", "registry_schema", "run"]

# Every single-item mechanism here supports a reserve, so the schema is shared.
RESERVE = {
    "type": "number",
    "default": 0,
    "label": "Reserve price",
    "min": 0,
    "description": "Bids below the reserve are ineligible; the seller keeps the item.",
}

START = {
    "type": "number",
    "default": None,
    "label": "Clock start price",
    "min": 0,
    "description": "Leave blank to start at twice the highest bid.",
}


# ------------------------------------------------------------ sealed-bid auctions


@mechanism(
    "second_price",
    label="Second-price (Vickrey)",
    description="Highest bid wins and pays the highest losing bid. Truthful bidding is dominant.",
    truthful_dominant=True,
    params={"reserve": RESERVE},
)
def second_price(bidders: list[Bidder], reserve: float = 0) -> Iterator[Step]:
    state: dict[str, Any] = {"bids": {b.id: b.bid for b in bidders}, "reserve": reserve}
    eligible = yield from collect_and_sort(bidders, reserve, state)
    if not eligible:
        return (
            yield from no_sale(
                bidders,
                state,
                f"No bid reached the reserve of {num(reserve)}, so the item is not sold "
                "even though the bidders valued it.",
                f"max(b) < r = {num(reserve)}",
            )
        )

    winner = yield from pick_winner(
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
        formula=f"p = max(max(b_-i), r) = max({num(best_loser)}, {num(reserve)}) = {num(price)}",
        stage="price",
        bidders=[b.id for b in eligible[1:] if b.bid == best_loser],
    )

    return (
        yield from settle(
            bidders,
            state,
            {b.id: (price if b.id == winner.id else 0) for b in bidders},
            f"{winner.id} pays {num(price)}; the losers pay nothing.",
        )
    )


@mechanism(
    "first_price",
    label="First-price sealed bid",
    description="Highest bid wins and pays its own bid, which rewards shading below your value.",
    truthful_dominant=False,
    params={"reserve": RESERVE},
)
def first_price(bidders: list[Bidder], reserve: float = 0) -> Iterator[Step]:
    state: dict[str, Any] = {"bids": {b.id: b.bid for b in bidders}, "reserve": reserve}
    eligible = yield from collect_and_sort(bidders, reserve, state)
    if not eligible:
        return (
            yield from no_sale(
                bidders,
                state,
                f"No bid reached the reserve of {num(reserve)}, so the item is not sold.",
                f"max(b) < r = {num(reserve)}",
            )
        )

    winner = yield from pick_winner(
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
        formula=f"p = b_i = {num(price)}",
        stage="price",
        bidders=[winner.id],
    )

    return (
        yield from settle(
            bidders,
            state,
            {b.id: (price if b.id == winner.id else 0) for b in bidders},
            f"{winner.id} pays their own bid of {num(price)}; the losers pay nothing.",
        )
    )


@mechanism(
    "all_pay",
    label="All-pay auction",
    description="Highest bid wins, but every eligible bidder pays their bid — losing bids are sunk.",
    truthful_dominant=False,
    params={"reserve": RESERVE},
)
def all_pay(bidders: list[Bidder], reserve: float = 0) -> Iterator[Step]:
    state: dict[str, Any] = {"bids": {b.id: b.bid for b in bidders}, "reserve": reserve}
    eligible = yield from collect_and_sort(bidders, reserve, state)
    if not eligible:
        return (
            yield from no_sale(
                bidders,
                state,
                f"No bid reached the reserve of {num(reserve)}, so the auction does not "
                "happen and nobody pays — not even the losers.",
                f"max(b) < r = {num(reserve)}",
            )
        )

    winner = yield from pick_winner(
        eligible, state, "The highest eligible bid wins the item."
    )

    price = winner.bid
    state["price"] = price
    yield step(
        "price rule",
        "Every bidder who cleared the reserve pays their own bid, win or lose. A losing "
        "bid buys nothing, which is why this models lobbying, contests and R&D races.",
        state,
        formula=f"p_i = b_i for every eligible i; winner pays {num(price)}",
        stage="price",
        bidders=ids(eligible),
    )

    eligible_ids = {b.id for b in eligible}
    payments = {b.id: (b.bid if b.id in eligible_ids else 0) for b in bidders}
    losses = ", ".join(
        f"{b.id} loses {num(b.bid)}" for b in eligible if b.id != winner.id
    )
    return (
        yield from settle(
            bidders,
            state,
            payments,
            f"{winner.id} pays {num(price)} and takes the item"
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
    truthful_dominant=True,
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
        bidders=ids(bidders),
    )

    active = [b for b in bidders if b.bid >= reserve]
    state["clock"] = reserve
    state["active"] = ids(active)
    yield step(
        "clock start",
        f"The clock opens at the reserve of {num(reserve)} and only ever rises. "
        + (
            f"{len(active)} bidders are willing to pay that much and keep their hands up."
            if active
            else "No bidder is willing to pay that much."
        ),
        state,
        formula=f"clock = r = {num(reserve)}",
        stage="clock",
        bidders=ids(active),
    )
    if not active:
        return (
            yield from no_sale(
                bidders,
                state,
                f"Every hand is down at the opening price of {num(reserve)}, so the "
                "auction ends before it starts and the item stays with the seller.",
                f"max(b) < r = {num(reserve)}",
            )
        )

    ranked = rank(active)
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
            f"The clock reaches {num(clock)}, which is exactly {leaver.id}'s limit, so "
            f"{leaver.id} lowers their hand. "
            + (
                f"{len(left)} bidders are still in."
                if len(left) > 1
                else f"Only {left[0]} is still in."
            ),
            state,
            formula=f"clock = {num(clock)} = b_{leaver.id}",
            stage="dropout",
            bidders=[leaver.id],
        )

    state["winner"] = winner.id
    tied = [b for b in active if b.bid == winner.bid]
    note = (
        f" {len(tied)} bidders held out to the same limit of {num(winner.bid)}, so the "
        f"tie goes to {winner.id} for being listed first."
        if len(tied) > 1
        else ""
    )
    yield step(
        "pick winner",
        f"The clock stops: {winner.id} is the last bidder with a hand up and takes "
        "the item." + note,
        state,
        formula=f"last standing = {winner.id}",
        stage="winner",
        bidders=[b.id for b in tied],
    )

    price = max(clock, reserve)
    state["price"] = price
    yield step(
        "price rule",
        f"{winner.id} pays the price at which the last rival dropped out — the "
        f"second-highest limit in the room — and never their own limit of "
        f"{num(winner.bid)}. That is why an ascending clock is the same mechanism as a "
        "sealed-bid second-price auction: same winner, same price, and the same reason "
        "to be honest about what the item is worth to you.",
        state,
        formula=f"p = clock at final dropout = {num(price)}",
        stage="price",
        bidders=[b.id for b in losers if b.bid == clock],
    )

    return (
        yield from settle(
            bidders,
            state,
            {b.id: (price if b.id == winner.id else 0) for b in bidders},
            f"{winner.id} pays {num(price)}; everybody who dropped out pays nothing.",
        )
    )


@mechanism(
    "dutch",
    label="Dutch (descending clock)",
    description="Price falls until someone claims the item; equivalent to a first-price auction.",
    truthful_dominant=False,
    params={"reserve": RESERVE, "start": START},
)
def dutch(
    bidders: list[Bidder], reserve: float = 0, start: float | None = None
) -> Iterator[Step]:
    top = max((b.bid for b in bidders), default=0)
    floor = max(top, reserve)
    # A clock opening below the top bid is not a cheaper auction: several bidders would
    # accept the instant it starts, so the item goes to whoever reacts first rather than
    # to the highest bidder, and a start below the reserve sells under the floor the
    # reserve is supposed to guarantee. Neither is a Dutch auction, so refuse to run one.
    if start is not None and start < floor:
        raise ValueError(
            f"parameter 'start' must be at least {num(floor)}: a descending clock "
            f"cannot open below the highest bid ({num(top)}) or below the reserve "
            f"({num(reserve)}) — it would sell to whoever reacts first and ignore the "
            "reserve entirely"
        )
    # ponytail: default start = twice the top bid. Ceiling — it peeks at private bids to
    # pick a number a real seller could not see. Upgrade: pass `start` explicitly, or
    # derive it from a declared value distribution once phase 2 adds strategies.
    if start is None:
        start = max(2 * top, reserve)

    state: dict[str, Any] = {"bids": {b.id: b.bid for b in bidders}, "reserve": reserve}
    yield step(
        "collect bids",
        "Nothing is submitted on paper. Each bidder privately knows the highest price "
        "at which they would still take the item, and says nothing until they claim it.",
        state,
        stage="collect",
        bidders=ids(bidders),
    )

    state["clock"] = start
    state["start"] = start
    yield step(
        "clock start",
        f"The clock opens at {num(start)}, above anything anyone would pay, and falls "
        "from there. The first bidder to speak gets the item.",
        state,
        formula=f"clock = {num(start)}, falling",
        stage="clock",
        bidders=ids(bidders),
    )

    contenders = [b for b in bidders if b.bid >= reserve]
    if not contenders:
        state["clock"] = reserve
        return (
            yield from no_sale(
                bidders,
                state,
                f"The clock falls all the way to the reserve of {num(reserve)} without a "
                "single hand going up, so the seller keeps the item.",
                f"max(b) < r = {num(reserve)}",
            )
        )

    # The clock is guaranteed to open at or above every bid, so the first price anyone
    # accepts at is simply the highest bid still in the running.
    accept = max(b.bid for b in contenders)
    state["clock"] = accept
    yield step(
        "clock falls",
        (
            f"Nobody speaks at {num(start)}, so the clock ticks down. The first price any "
            f"bidder is willing to pay is {num(accept)}."
            if accept < start
            else f"The clock does not have to fall: somebody is already willing to pay "
            f"the opening price of {num(accept)}."
        ),
        state,
        formula=f"clock: {num(start)} -> {num(accept)}",
        stage="clock",
        bidders=[b.id for b in contenders if b.bid >= accept],
    )

    claimants = [b for b in contenders if b.bid >= accept]
    winner = claimants[0]
    state["winner"] = winner.id
    yield step(
        "pick winner",
        f"{winner.id} accepts at {num(accept)} and the clock stops. "
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
        formula=f"p = clock at acceptance = b_{winner.id} = {num(price)}",
        stage="price",
        bidders=[winner.id],
    )

    return (
        yield from settle(
            bidders,
            state,
            {b.id: (price if b.id == winner.id else 0) for b in bidders},
            f"{winner.id} pays {num(price)}; nobody else pays anything.",
        )
    )


# Registration is an import side effect, so the multi-item mechanisms have to be pulled
# in from here: everything else in the project reaches the registry through this module.
# The imports sit at the bottom because REGISTRY is an ordered dict and the setup form
# should offer the one-item auctions before the many-item ones, and the auctions taking
# a scalar bid before the ones taking bundles.
from agt import positions as _positions  # noqa: E402,F401  (registers gsp, vcg_positions)
from agt import packages as _packages  # noqa: E402,F401  (registers the package auctions)
