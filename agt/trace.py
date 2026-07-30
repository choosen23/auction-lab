"""Trace data model: the wire format between the auction engine and any renderer.

This module knows nothing about specific mechanisms. It defines what a bidder is,
what a single algorithm step looks like, how an outcome is scored, and how the whole
thing serializes to JSON.

Everything here is immutable: a ``Step`` is a record of something that already
happened, so nothing may edit it afterwards.
"""

import copy
from dataclasses import dataclass, field
from typing import Any

Number = float  # ints are accepted and preserved verbatim


@dataclass(frozen=True)
class Bidder:
    """One participant: a private ``value`` for the item and the ``bid`` they submit."""

    id: str
    value: Number
    bid: Number

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.id, "value": self.value, "bid": self.bid}


# ponytail: `frozen=True` freezes the fields, not the dicts inside them. Ceiling — a
# caller holding a reference could still edit `state` in place. `step()` deep-copies on
# the way in, which closes the only path that actually happens (mechanisms reuse one
# working dict). Upgrade: wrap in MappingProxyType if a mutation bug ever shows up.
@dataclass(frozen=True)
class Step:
    """One algorithmic step, carrying a *full* state snapshot rather than a diff.

    ponytail: full snapshots, not diffs — the renderer then needs no replay logic.
    Ceiling: trace size grows with steps x bidders. Upgrade: switch to diffs only if a
    trace ever exceeds ~1MB.

    ``label``     short stage name, shown in the stage list.
    ``detail``    one plain-English sentence naming the rule that just fired.
    ``state``     complete snapshot the renderer draws directly (no replay logic).
    ``formula``   plain string with the real numbers substituted, or None.
    ``highlight`` renderer hints, always including ``stage`` and ``bidders``.
    """

    label: str
    detail: str
    state: dict[str, Any]
    formula: str | None = None
    highlight: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "detail": self.detail,
            "formula": self.formula,
            "highlight": copy.deepcopy(self.highlight),
            "state": copy.deepcopy(self.state),
        }


def step(
    label: str,
    detail: str,
    state: dict[str, Any],
    formula: str | None = None,
    **highlight: Any,
) -> Step:
    """Build a :class:`Step`, deep-copying the snapshot so later mutation cannot reach it.

    Mechanisms keep one live working dict and hand it to ``step()`` repeatedly; without
    the copy every recorded step would show the final state.
    """
    return Step(
        label=label,
        detail=detail,
        state=copy.deepcopy(state),
        formula=formula,
        highlight=copy.deepcopy(highlight),
    )


def outcome(
    bidders: list[Bidder],
    winner: str | None,
    payments: dict[str, Number],
) -> dict[str, Any]:
    """Score an allocation: payments, utilities, revenue, welfare, efficiency.

    Losers can pay (all-pay auctions), so their utility can be negative.
    ``efficient`` asks whether the item reached the highest-value bidder — a reserve
    that blocks every sale is therefore inefficient whenever somebody valued the item,
    which is exactly the lesson the flag exists to teach.
    """
    settled = {b.id: payments.get(b.id, 0) for b in bidders}
    utilities = {
        b.id: (b.value if b.id == winner else 0) - settled[b.id] for b in bidders
    }
    welfare = next((b.value for b in bidders if b.id == winner), 0)
    best_possible = max((b.value for b in bidders), default=0)
    return {
        "winner": winner,
        "price": settled.get(winner, 0),
        "payments": settled,
        "utilities": utilities,
        "revenue": sum(settled.values()),
        "welfare": welfare,
        "efficient": bool(welfare == best_possible),
    }


# ponytail: an absolute tolerance on the efficiency comparison. Click-weighted welfare
# adds its terms in slot order while the best-possible welfare adds them in value order,
# so two totals that are equal on paper can differ in the last bit and an exact `==`
# would report a false inefficiency. Ceiling: values above roughly 1e9 make this
# effectively exact equality again. Upgrade: switch to `math.isclose` if a lesson ever
# needs numbers that large.
WELFARE_TOLERANCE = 1e-9


def outcome_allocation(
    bidders: list[Bidder],
    allocation: dict[str, Any],
    payments: dict[str, Number],
    gains: dict[str, Number],
    best_possible_welfare: Number,
) -> dict[str, Any]:
    """Score an allocation of several things at once: the sibling of :func:`outcome`.

    ``allocation`` maps a bidder id to whatever they received — a slot index, a bundle of
    items — and simply omits anybody who received nothing. It is ordered **best first**:
    the mechanism lists its winners in the order it ranked them. ``gains`` is the *gross
    value* each winner actually got, which for a position auction is their value per
    click times the slot's click-through rate, so it is not the bidder's ``value`` and
    cannot be read off the bidder the way :func:`outcome` reads it.
    ``best_possible_welfare`` is passed in because only the mechanism knows which
    allocations were available.

    ``winner`` and ``price`` stay populated with that first winner and what they paid, so
    a renderer written against :func:`outcome` keeps working unchanged. Reading the top
    off the ordering rather than off the largest gain matters: the holder of the most
    clicked slot is the winner even when a bidder further down values their smaller slot
    more.
    """
    settled = {b.id: payments.get(b.id, 0) for b in bidders}
    earned = {b.id: gains.get(b.id, 0) for b in bidders}
    utilities = {b.id: earned[b.id] - settled[b.id] for b in bidders}
    welfare = sum(earned.values())
    winner = next(iter(allocation), None)
    return {
        "winner": winner,
        "price": settled.get(winner, 0),
        "payments": settled,
        "utilities": utilities,
        "revenue": sum(settled.values()),
        "welfare": welfare,
        "efficient": bool(abs(welfare - best_possible_welfare) < WELFARE_TOLERANCE),
        "allocation": dict(allocation),
        "gains": earned,
        "best_possible_welfare": best_possible_welfare,
    }


@dataclass(frozen=True)
class Trace:
    """A complete run: what was asked for, every step taken, and the final scoring."""

    mechanism: str
    params: dict[str, Any]
    bidders: list[Bidder]
    steps: list[Step]
    result: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        """Plain dicts and lists only — ``json.dumps`` must accept the result as-is."""
        return {
            "mechanism": self.mechanism,
            "params": dict(self.params),
            "bidders": [b.to_dict() for b in self.bidders],
            "steps": [s.to_dict() for s in self.steps],
            "result": copy.deepcopy(self.result),
        }
