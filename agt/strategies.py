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
    description="Bid your private value; dominant under second-price and clock-ascending rules.",
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
    description="Bid value x (n-1)/n: the first-price equilibrium for i.i.d. uniform values.",
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


TICK = {
    "type": "number",
    "default": 1,
    "label": "Bid increment",
    "min": 0,
    "description": "How far above a rival's last bid the search is willing to step.",
}


@strategy(
    "best_response",
    label="Best response to last round",
    description="Score candidate bids against last round's bids; keep the most profitable.",
    params={"tick": TICK},
)
def best_response(context: StrategyContext, tick: Number = 1) -> BidDecision:
    """Pick the bid that would have paid best against last round's bids.

    Nothing here knows what mechanism it is playing: every candidate is scored by
    *running* the mechanism and reading this bidder's utility out of the result. That is
    why the same code bids truthfully under second-price rules and shades under
    first-price rules, and why every mechanism added later gets a best-response bidder
    for free.
    """
    me = context.bidder
    if not context.history:
        return BidDecision(
            me.bid,
            f"{me.id} has no previous round to react to, so it opens with the "
            f"{num(me.bid)} typed into the form; from the next round on it bids the best "
            "reply to what rivals actually did.",
        )

    rival_bids = {rid: context.history[-1].bids.get(rid, 0) for rid in context.rival_ids}
    guesses = [0, me.value, *rival_bids.values(), *(b + tick for b in rival_bids.values())]
    candidates = sorted({min(max(g, 0), me.value) for g in guesses})

    considered = []
    for candidate in candidates:
        utility = _utility_if(context, candidate, rival_bids)
        # A mechanism can refuse a bid outright (a Dutch clock cannot open below one), and
        # a bid that cannot be submitted is not a candidate.
        if utility is not None:
            considered.append({"bid": candidate, "utility": utility})
    if not considered:
        return BidDecision(
            me.bid,
            f"{me.id} could not find a single bid this mechanism would accept against "
            f"last round's bids, so it repeats the {num(me.bid)} typed into the form.",
        )

    # ponytail: ties go to the truthful bid first and only then to the lower bid. Under
    # second-price rules the top-utility set is *always* a tie — bidding 51, 100 or
    # anything between wins at the same price — so preferring the lower bid there would
    # answer "shade to just above the rival" and bury the one lesson the mechanism exists
    # to teach. Preferring the truthful bid costs nothing elsewhere: under first-price
    # rules the maximum is unique whenever there is surplus to win, and among untruthful
    # candidates the lower bid still wins, so an indifferent bidder is never shown bidding
    # aggressively for no reason. Ceiling: a bidder indifferent between its value and a
    # cheaper bid is shown the honest one. Upgrade: expose the tie rule as a param if a
    # lesson ever needs the other order.
    best = min(considered, key=lambda c: (-c["utility"], c["bid"] != me.value, c["bid"]))
    seen = ", ".join(f"{rid} {num(b)}" for rid, b in rival_bids.items()) or "nobody"
    return BidDecision(
        best["bid"],
        f"{me.id} scored {len(considered)} candidate bids against last round's bids "
        f"({seen}) and kept {num(best['bid'])}, worth {num(best['utility'])} under "
        f"{context.mechanism} — the most profitable reply *if* every rival repeats "
        "exactly what they bid last round, which is the assumption this strategy makes "
        "and the first one to fail once rivals start moving too.",
        considered=considered,
    )


def _lineup(
    context: StrategyContext, my_bid: Number, rival_bids: dict[str, Number]
) -> list[Bidder]:
    """Rebuild the table with a candidate bid in this bidder's own chair.

    Seating matters because ties break by list order, so a what-if run that reseated the
    bidder would score ties it would not actually win.
    """
    # ponytail: rivals are probed with value = bid, since the strategy cannot see their
    # real values. Ceiling — welfare and efficiency inside a probe trace are meaningless,
    # so nothing reads them; this bidder's own utility depends only on who wins and what
    # they pay, which the bids alone decide. Upgrade: none while the privacy rule stands.
    rivals = [Bidder(rid, bid, bid) for rid, bid in rival_bids.items()]
    me = Bidder(context.bidder.id, context.bidder.value, my_bid)
    return [*rivals[: context.seat], me, *rivals[context.seat :]]


def _utility_if(
    context: StrategyContext, my_bid: Number, rival_bids: dict[str, Number]
) -> Number | None:
    """What this bidder would have earned bidding ``my_bid``, or None if it is illegal."""
    try:
        trace = run(
            context.mechanism, _lineup(context, my_bid, rival_bids), dict(context.params)
        )
    except ValueError:
        return None
    return trace.result["utilities"][context.bidder.id]
