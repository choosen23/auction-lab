"""Position auctions: several ad slots sold at once to today's scalar bids.

Nothing about the *input* changes here. A :class:`~agt.trace.Bidder` still carries one
value and one bid; they are simply read as a price **per click** rather than for a whole
item. What changes is the output: several bidders win at once, so the run is scored by
:func:`~agt.trace.outcome_allocation` instead of :func:`~agt.trace.outcome`.

The two mechanisms in this module differ in exactly one line of arithmetic and in nothing
else — same bidders, same ranking, same slots, same click-through rates. That is the
point. ``gsp`` charges the next bidder's bid and is not truthful; ``vcg_positions``
charges the externality the winner imposes and is. Sponsored search runs GSP anyway.
"""

from collections.abc import Iterator
from typing import Any

from agt.registry import mechanism
from agt.stages import collect_and_sort, ids, num
from agt.trace import Bidder, Number, Step, outcome_allocation, step

RESERVE = {
    "type": "number",
    "default": 0,
    "label": "Reserve price per click",
    "min": 0,
    "description": "Bids below the reserve win no slot, and the lowest winner pays it.",
}

SLOTS = {
    "type": "number",
    "default": 3,
    "label": "Slots",
    "min": 1,
    "max": 10,
    "description": "How many positions are for sale. Slot 0 is the most clicked.",
}

# ponytail: geometric click-through rates out of two number params, because the params
# schema validates numbers only and this buys a legible slot ladder for one multiply.
# Ceiling: real CTR curves are measured, not geometric, and no real slot ladder is a
# clean power of anything. Upgrade: widen `_validate_param` to accept a list of numbers
# and take the explicit CTRs the way an ad server would.
CTR_DECAY = {
    "type": "number",
    "default": 0.5,
    "label": "Click-through decay",
    "min": 0,
    "max": 1,
    "description": "Slot i receives ctr_decay^i of the clicks slot 0 receives.",
}


# --------------------------------------------------------------------- the slot ladder


def _whole(key: str, value: Number) -> int:
    """Slot counts index a list, so half a slot is a configuration error, not a rounding."""
    if value != int(value):
        raise ValueError(
            f"parameter {key!r} must be a whole number of slots, got {value!r}"
        )
    return int(value)


def _rates(slots: int, decay: Number) -> list[float]:
    """Click-through rate per slot: slot ``i`` gets ``decay ** i`` of slot 0's clicks."""
    return [decay**i for i in range(slots)]


def _rate(rates: list[float], slot: int) -> float:
    """The rate of a slot, or 0 for the imaginary slot below the last real one."""
    return rates[slot] if slot < len(rates) else 0


def _ladder(eligible: list[Bidder], slots: int, reserve: Number) -> list[Number]:
    """The bid sitting on each rung, padded with the reserve where nobody is standing.

    ``ladder[i]`` is what the holder of slot ``i`` bid, and ``ladder[i + 1]`` is what the
    bidder immediately below them bid — the number both payment rules are built out of.
    Padding with the reserve is not a convenience: it *is* how a reserve works here. The
    seller is the bidder of last resort, willing to hold a slot back rather than sell a
    click below the reserve, so both rules treat an empty rung as a bid of exactly that.
    """
    bids = [b.bid for b in eligible[: slots + 1]]
    return bids + [reserve] * (slots + 1 - len(bids))


def _best_welfare(bidders: list[Bidder], rates: list[float]) -> Number:
    """The most click-weighted value any allocation of these slots could have produced."""
    values = sorted((b.value for b in bidders), reverse=True)
    return sum(value * rate for value, rate in zip(values, rates))


def _setter(eligible: list[Bidder], slot: int) -> str:
    """What fixes the price of ``slot``: the bid one rung down, or the reserve."""
    below = slot + 1
    return f"{eligible[below].id}'s bid" if below < len(eligible) else "the reserve"


def _moves_up(
    eligible: list[Bidder], into: int, reserve: Number, lost: float, bid: Number
) -> str:
    """One term of a VCG bill: who would take ``into`` if the winner above were absent.

    When nobody is left to move up, the slot simply goes unsold and the seller keeps its
    clicks at the reserve — the same outside option the bid ladder is padded with.
    """
    below = into + 1
    if below < len(eligible):
        return (
            f"{eligible[below].id} would move up into slot {into}, gaining {num(lost)} "
            f"clicks they value at {num(bid)} each"
        )
    return (
        f"slot {into} would go unsold and the seller would keep its {num(lost)} clicks, "
        f"valued at the reserve of {num(reserve)}"
    )


# ------------------------------------------------------------------- shared stages


def _open(
    bidders: list[Bidder],
    reserve: Number,
    slots: int,
    ctr_decay: Number,
    state: dict[str, Any],
) -> Iterator[Step]:
    """collect -> sort -> reserve -> click-through rates -> slot assignment.

    Returns the eligible bidders ranked best first, the click-through rates and the bid
    ladder. Everything up to here is identical under both payment rules, which is what
    makes the comparison between them a comparison of one thing.
    """
    eligible = yield from collect_and_sort(bidders, reserve, state)

    rates = _rates(slots, ctr_decay)
    state["ctrs"] = rates
    yield step(
        "click-through rates",
        f"A bid here is a price per click, and the {slots} slots differ only in how often "
        f"they are clicked: slot i receives {num(ctr_decay)}^i of the clicks slot 0 gets. "
        "A slot is therefore worth its holder's value per click times that rate, so the "
        "same bidder is worth less further down the page.",
        state,
        formula=", ".join(f"ctr_{i} = {num(r)}" for i, r in enumerate(rates)),
        stage="slots",
        bidders=ids(bidders),
    )

    winners = eligible[:slots]
    state["allocation"] = {b.id: i for i, b in enumerate(winners)}
    state["winner"] = winners[0].id if winners else None
    if winners:
        taken = ", ".join(
            f"{b.id} takes slot {i} at ctr {num(rates[i])}" for i, b in enumerate(winners)
        )
        yield step(
            "assign slots",
            f"The highest bid takes the most clicked slot, the next bid the one below it, "
            f"and so on down: {taken}. Both payment rules allocate exactly this way — "
            "only the bill that follows differs.",
            state,
            formula=" | ".join(
                f"slot {i} -> {b.id} ({num(b.bid)}/click)" for i, b in enumerate(winners)
            ),
            stage="allocate",
            bidders=ids(winners),
        )
    return eligible, rates, _ladder(eligible, slots, reserve)


def _unsold(
    bidders: list[Bidder],
    state: dict[str, Any],
    reserve: Number,
    rates: list[float],
) -> Iterator[Step]:
    """Terminal stage when no bid clears the reserve: every slot stays empty."""
    state["allocation"] = {}
    state["winner"] = None
    state["payments"] = {b.id: 0 for b in bidders}
    yield step(
        "no sale",
        f"No bid reached the reserve of {num(reserve)} per click, so every slot goes "
        "unsold: the clicks are shown to nobody and nobody pays, even though bidders "
        "valued them.",
        state,
        formula=f"max(b) < r = {num(reserve)}",
        stage="result",
        bidders=[],
    )
    return outcome_allocation(
        bidders, {}, state["payments"], {}, _best_welfare(bidders, rates)
    )


def _settle(
    bidders: list[Bidder],
    winners: list[Bidder],
    rates: list[float],
    payments: dict[str, Number],
    state: dict[str, Any],
    detail: str,
) -> Iterator[Step]:
    """Terminal stage: total the bill and score the whole allocation, not one winner."""
    state["payments"] = {b.id: payments.get(b.id, 0) for b in bidders}
    total = sum(payments.values())
    terms = [num(payments[b.id]) for b in winners if payments.get(b.id, 0)]
    summed = " + ".join(terms) + f" = {num(total)}" if len(terms) > 1 else num(total)
    yield step(
        "payments",
        detail,
        state,
        formula=f"revenue = {summed}",
        stage="payments",
        bidders=[b.id for b in winners if payments.get(b.id, 0)],
    )
    allocation = {b.id: i for i, b in enumerate(winners)}
    gains = {b.id: b.value * rates[i] for i, b in enumerate(winners)}
    return outcome_allocation(
        bidders, allocation, payments, gains, _best_welfare(bidders, rates)
    )


# ------------------------------------------------------------------ the mechanisms


@mechanism(
    "gsp",
    label="Generalized second-price (GSP)",
    description="Slots go to the highest bids; each winner pays the next bidder's bid per click. Not truthful.",
    truthful_dominant=False,
    params={"reserve": RESERVE, "slots": SLOTS, "ctr_decay": CTR_DECAY},
)
def gsp(
    bidders: list[Bidder],
    reserve: Number = 0,
    slots: Number = 3,
    ctr_decay: Number = 0.5,
) -> Iterator[Step]:
    slots = _whole("slots", slots)
    state: dict[str, Any] = {
        "bids": {b.id: b.bid for b in bidders},
        "reserve": reserve,
        "slots": slots,
        "ctr_decay": ctr_decay,
    }
    eligible, rates, ladder = yield from _open(bidders, reserve, slots, ctr_decay, state)
    winners = eligible[:slots]
    if not winners:
        return (yield from _unsold(bidders, state, reserve, rates))

    state["prices"] = {b.id: ladder[i + 1] for i, b in enumerate(winners)}
    yield step(
        "price rule",
        "Each winner pays the next bidder's bid for every click their slot gets, never "
        "their own bid. That is the rule that breaks truthfulness: your own bid decides "
        "which slot you land in, but the bidder below you decides what that slot costs, "
        "so deliberately bidding low enough to drop a slot can buy cheaper clicks and "
        "leave you better off than bidding your value.",
        state,
        formula="p_i = ctr_i x b_(i+1)",
        stage="price",
        bidders=ids(winners),
    )

    payments: dict[str, Number] = {}
    for i, winner in enumerate(winners):
        payments[winner.id] = rates[i] * ladder[i + 1]
        state["payments"] = dict(payments)
        yield step(
            "slot price",
            f"{winner.id} holds slot {i} and is charged {_setter(eligible, i)} of "
            f"{num(ladder[i + 1])} on every one of that slot's clicks.",
            state,
            formula=(
                f"p_{winner.id} = ctr_{i} x b_{i + 1} = {num(rates[i])} x "
                f"{num(ladder[i + 1])} = {num(payments[winner.id])}"
            ),
            stage="price",
            bidders=[winner.id],
        )

    return (
        yield from _settle(
            bidders,
            winners,
            rates,
            payments,
            state,
            f"{winners[0].id} tops the ladder and pays {num(payments[winners[0].id])}. "
            "Every winner is billed at whatever sits one rung below them — the next "
            "bidder's bid, or the reserve once the ladder runs out — and anybody who won "
            "no slot pays nothing.",
        )
    )


@mechanism(
    "vcg_positions",
    label="VCG position auction",
    description="The same slots to the same bidders, but each winner pays the externality they impose. Truthful.",
    truthful_dominant=True,
    params={"reserve": RESERVE, "slots": SLOTS, "ctr_decay": CTR_DECAY},
)
def vcg_positions(
    bidders: list[Bidder],
    reserve: Number = 0,
    slots: Number = 3,
    ctr_decay: Number = 0.5,
) -> Iterator[Step]:
    slots = _whole("slots", slots)
    state: dict[str, Any] = {
        "bids": {b.id: b.bid for b in bidders},
        "reserve": reserve,
        "slots": slots,
        "ctr_decay": ctr_decay,
    }
    eligible, rates, ladder = yield from _open(bidders, reserve, slots, ctr_decay, state)
    winners = eligible[:slots]
    if not winners:
        return (yield from _unsold(bidders, state, reserve, rates))

    yield step(
        "price rule",
        "Each winner pays the externality they impose on everybody else: for every slot "
        "below them, the clicks that slot's holder loses by being pushed one place down, "
        "valued at that holder's own bid. A winner's own bid never appears anywhere in "
        "their own bill — it only decides which slot they get — so raising it can never "
        "raise their price and lowering it can only cost them clicks. That is why "
        "bidding your true value per click is dominant here.",
        state,
        formula="p_i = sum over j > i of (ctr_(j-1) - ctr_j) x b_j",
        stage="price",
        bidders=ids(winners),
    )

    payments: dict[str, Number] = {}
    for i, winner in enumerate(winners):
        terms = [
            ((_rate(rates, j - 1) - _rate(rates, j)), ladder[j])
            for j in range(i + 1, slots + 1)
        ]
        payments[winner.id] = sum(lost * bid for lost, bid in terms)
        state["payments"] = dict(payments)
        moves = "; ".join(
            _moves_up(eligible, j - 1, reserve, lost, bid)
            for j, (lost, bid) in enumerate(terms, start=i + 1)
        )
        yield step(
            "slot price",
            f"{winner.id} holds slot {i}. Without {winner.id}: {moves}. The total of what "
            f"everybody else would have gained is exactly what {winner.id} is charged.",
            state,
            formula=(
                f"p_{winner.id} = "
                + " + ".join(f"{num(lost)} x {num(bid)}" for lost, bid in terms)
                + f" = {num(payments[winner.id])}"
            ),
            stage="price",
            bidders=[winner.id, *(b.id for b in eligible[i + 1 : slots + 1])],
        )

    return (
        yield from _settle(
            bidders,
            winners,
            rates,
            payments,
            state,
            f"{winners[0].id} tops the ladder and pays {num(payments[winners[0].id])}, "
            "the harm their presence does to the bidders below them; nobody is ever "
            "charged for value they did not take away from somebody else.",
        )
    )
