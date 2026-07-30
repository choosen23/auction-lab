"""How a bidder chooses a bid, and the registry the UI builds its dropdowns from.

This mirrors :mod:`agt.registry` deliberately: a strategy is a function with a declared
parameter schema, registered by a decorator, serialized for the form by
:func:`strategy_schema`, and driven by one entry point (:func:`decide`, the sibling of
``run``). Adding a strategy means adding one decorated function here and nothing else.

**The privacy rule is load-bearing.** A strategy sees its own bidder's private value,
its rivals' *ids*, and its rivals' *past bids*. It never sees a rival's current private
value. That is why :class:`StrategyContext` carries ``rival_ids`` rather than rival
:class:`~agt.trace.Bidder` objects, and why history is a list of :class:`RoundView` —
:func:`observe` strips values off a finished round on the way in. A strategy that could
read rival values would quietly teach that an auction is a full-information game, which
is the opposite of what this tool exists to show.
"""

import copy
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from agt.mechanisms import run  # also registers every mechanism
from agt.registry import _resolve_params
from agt.stages import num
from agt.trace import Bidder, Number, Trace

StrategyFn = Callable[..., "BidDecision"]

# Mechanisms whose strategic problem is choosing a first-price bid. Dutch is the same
# decision as a sealed first-price bid, which phase 1's own trace says out loud.
FIRST_PRICE_LIKE = ("first_price", "dutch")
# Mechanisms where bidding your value is dominant.
SECOND_PRICE_LIKE = ("second_price", "english")


@dataclass(frozen=True)
class RoundView:
    """One finished round as a strategy is allowed to remember it.

    Bids are public once a round is over, so rivals' past bids are fair game; values
    never were public and never appear here. See :func:`observe`.
    """

    round: int
    bids: dict[str, Number]
    winner: str | None
    price: Number

    def to_dict(self) -> dict[str, Any]:
        return {
            "round": self.round,
            "bids": dict(self.bids),
            "winner": self.winner,
            "price": self.price,
        }


@dataclass(frozen=True)
class StrategyContext:
    """Everything a strategy is allowed to know when it picks this round's bid.

    ``bidder``    this bidder: id, private value, and the bid typed into the form.
    ``rival_ids`` the other bidders, in table order — ids only, never their values.
    ``seat``      this bidder's index in the table. Ties break by list order, so a
                  faithful what-if has to sit the bidder back in the same chair.
    ``n``         total number of bidders.
    ``round``     0-based index of the round about to be run.
    ``history``   every finished round, oldest first, narrowed by :func:`observe`.
    ``mechanism`` the mechanism name, so a strategy can say when it is the wrong tool.
    ``params``    the mechanism's resolved params (reserve, and so on).
    """

    bidder: Bidder
    rival_ids: list[str]
    seat: int
    n: int
    round: int
    history: list[RoundView]
    mechanism: str
    params: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class BidDecision:
    """A bid plus the one plain sentence a learner reads to understand it.

    ``considered`` records the candidates a searching strategy weighed and what each was
    worth, so the UI can show the deliberation rather than only the conclusion.
    """

    bid: Number
    why: str
    considered: list[dict[str, Any]] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "bid": self.bid,
            "why": self.why,
            "considered": copy.deepcopy(self.considered),
        }


@dataclass(frozen=True)
class Strategy:
    """A registered strategy: how to run it and how to build a form for it."""

    name: str
    label: str
    description: str
    params: dict[str, dict[str, Any]]
    fn: StrategyFn


STRATEGIES: dict[str, Strategy] = {}


def strategy(
    name: str,
    *,
    label: str,
    description: str,
    params: dict[str, dict[str, Any]] | None = None,
) -> Callable[[StrategyFn], StrategyFn]:
    """Register a strategy under ``name`` with a form-ready params schema."""

    def decorate(fn: StrategyFn) -> StrategyFn:
        STRATEGIES[name] = Strategy(name, label, description, params or {}, fn)
        return fn

    return decorate


def strategy_schema() -> dict[str, dict[str, Any]]:
    """Serialize STRATEGIES to a JSON-safe dict. The UI generates its dropdowns from this."""
    return {
        name: {
            "name": s.name,
            "label": s.label,
            "description": s.description,
            "params": copy.deepcopy(s.params),
        }
        for name, s in STRATEGIES.items()
    }


def decide(
    name: str,
    context: StrategyContext,
    params: dict[str, Any] | None = None,
) -> BidDecision:
    """Ask strategy ``name`` for this round's bid. The sibling of ``run``."""
    if name not in STRATEGIES:
        raise ValueError(f"unknown strategy {name!r}; expected one of {sorted(STRATEGIES)}")
    spec = STRATEGIES[name]
    # ponytail: reuse the mechanism param resolver — it only needs `.name` and `.params`,
    # which a Strategy also has. Ceiling: it validates numbers only, so a strategy cannot
    # declare a string or enum param. Upgrade: widen `_validate_param` with a type switch
    # the first time a strategy actually needs one.
    resolved = _resolve_params(spec, params or {})  # type: ignore[arg-type]
    return spec.fn(context, **resolved)


def observe(round_index: int, trace: Trace) -> RoundView:
    """Narrow a finished round to what a strategy may remember: public bids and outcome.

    This function is the privacy seam. ``trace.bidders`` carries private values; the
    view it returns does not, so no amount of history digging can reach one.
    """
    return RoundView(
        round=round_index,
        bids={b.id: b.bid for b in trace.bidders},
        winner=trace.result["winner"],
        price=trace.result["price"],
    )


# ------------------------------------------------------------------- the strategies


@strategy(
    "manual",
    label="Manual",
    description="Bid exactly what was typed into the form, every round.",
)
def manual(context: StrategyContext) -> BidDecision:
    bid = context.bidder.bid
    return BidDecision(
        bid,
        f"{context.bidder.id} bids the {num(bid)} typed into the form, unchanged — this "
        "strategy makes no decision of its own, so the round is exactly the auction a "
        "human would have set up by hand.",
    )


@strategy(
    "truthful",
    label="Truthful",
    description="Bid your private value. Dominant under second-price and ascending-clock rules.",
)
def truthful(context: StrategyContext) -> BidDecision:
    value = context.bidder.value
    if context.mechanism in SECOND_PRICE_LIKE:
        note = (
            "Here that is a dominant strategy: the price is set by somebody else's bid, "
            "so honesty can neither raise what you pay nor lose you a good deal."
        )
    else:
        note = (
            f"Here that is not a dominant strategy: under {context.mechanism} the winner's "
            "own bid drives what they pay, so bidding your value hands the whole surplus "
            "to the seller."
        )
    return BidDecision(
        value,
        f"{context.bidder.id} bids their true value of {num(value)}. {note}",
    )


@strategy(
    "shade_bne",
    label="BNE shading (uniform values)",
    description="Bid value x (n-1)/n, the symmetric equilibrium of a first-price auction with i.i.d. uniform values.",
)
def shade_bne(context: StrategyContext) -> BidDecision:
    value, n = context.bidder.value, context.n
    bid = value * (n - 1) / n
    derivation = (
        f"{context.bidder.id} shades to value x (n-1)/n = {num(value)} x {n - 1}/{n} = "
        f"{num(bid)}, the symmetric Bayes-Nash equilibrium of a first-price auction when "
        "all n values are drawn independently from the same uniform distribution."
    )
    if context.mechanism in FIRST_PRICE_LIKE:
        caveat = (
            "Those two assumptions are the whole reason this number is an equilibrium: "
            "change the distribution, or let one bidder draw from a different one, and it "
            "stops being one."
        )
    else:
        caveat = (
            f"It is not the equilibrium of {context.mechanism}, nor of any other "
            "mechanism or value distribution than the one it was derived for, so it is "
            "the wrong tool here"
        )
        caveat += (
            ": under second-price rules bidding your value is dominant, and shading only "
            "risks losing at a price you would have been glad to pay."
            if context.mechanism in SECOND_PRICE_LIKE
            else "."
        )
    return BidDecision(bid, f"{derivation} {caveat}")
