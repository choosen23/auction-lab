import dataclasses
import json

import pytest

from agt.mechanisms import REGISTRY, run
from agt.strategies import (
    STRATEGIES,
    BidDecision,
    RoundView,
    StrategyContext,
    decide,
    observe,
    strategy_schema,
)
from agt.trace import Bidder

ME = Bidder("A", 100, 95)


def context(
    bidder=ME,
    rivals=("B", "C"),
    seat=0,
    round=0,
    history=(),
    mechanism="first_price",
    params=None,
):
    """A context of the shape ``agt.series`` builds, for testing one decision alone."""
    return StrategyContext(
        bidder=bidder,
        rival_ids=list(rivals),
        seat=seat,
        n=1 + len(rivals),
        round=round,
        history=list(history),
        mechanism=mechanism,
        params=dict(params or {"reserve": 0}),
    )


def scalars(obj):
    """Every scalar reachable from ``obj`` through dataclass fields, dicts and sequences."""
    found, stack = [], [obj]
    while stack:
        item = stack.pop()
        if dataclasses.is_dataclass(item) and not isinstance(item, type):
            stack.extend(getattr(item, f.name) for f in dataclasses.fields(item))
        elif isinstance(item, dict):
            stack.extend(item.keys())
            stack.extend(item.values())
        elif isinstance(item, (list, tuple, set)):
            stack.extend(item)
        else:
            found.append(item)
    return found


# --------------------------------------------------------------- the simple rules


def test_manual_returns_the_typed_bid_verbatim():
    assert decide("manual", context()).bid == 95


def test_manual_does_not_clamp_a_bid_above_value():
    """Phase 1 lets a human type an overbid, and `manual` must reproduce phase 1 exactly."""
    assert decide("manual", context(bidder=Bidder("A", 100, 120))).bid == 120


def test_truthful_bids_the_private_value():
    assert decide("truthful", context()).bid == 100


def test_shade_bne_divides_by_the_number_of_bidders():
    d = decide("shade_bne", context(bidder=Bidder("A", 90, 90), rivals=("B", "C")))
    assert d.bid == 60


def test_shade_bne_with_two_bidders_halves_the_value():
    assert decide("shade_bne", context(rivals=("B",))).bid == 50


def test_shade_bne_names_the_assumptions_that_make_it_an_equilibrium():
    why = decide("shade_bne", context()).why.lower()
    assert "first-price" in why
    assert "uniform" in why
    assert "independent" in why


def test_shade_bne_says_it_is_the_wrong_tool_under_second_price():
    why = decide("shade_bne", context(mechanism="second_price")).why.lower()
    assert "not" in why
    assert "second-price" in why
    assert "dominant" in why


@pytest.mark.parametrize("name", sorted(REGISTRY))
def test_truthful_reads_dominance_off_the_mechanism(name):
    """The caveat follows the mechanism's own declaration, not a list of names kept here,
    so a mechanism where truth *is* dominant can never be told it is not."""
    why = decide("truthful", context(mechanism=name)).why.lower()
    assert "dominant" in why
    assert ("not a dominant" in why) != REGISTRY[name].truthful_dominant


@pytest.mark.parametrize("name", sorted(REGISTRY))
def test_shade_bne_warns_only_where_truth_is_dominant(name):
    """Shading is the wrong answer exactly where honesty is dominant; the word only
    appears when the mechanism says so."""
    why = decide("shade_bne", context(mechanism=name)).why.lower()
    assert ("dominant" in why) == REGISTRY[name].truthful_dominant


def test_every_strategy_explains_itself_in_plain_words():
    for name in STRATEGIES:
        d = decide(name, context())
        assert isinstance(d, BidDecision)
        assert isinstance(d.bid, (int, float))
        assert d.why.endswith(".") and len(d.why.split()) >= 8


# ------------------------------------------------------------------- the registry


def test_unknown_strategy_is_rejected():
    with pytest.raises(ValueError, match="unknown strategy"):
        decide("nope", context())


def test_unknown_strategy_param_is_rejected():
    with pytest.raises(ValueError, match="unknown parameter"):
        decide("truthful", context(), {"bogus": 1})


def test_strategy_schema_is_json_safe_and_form_ready():
    payload = json.loads(json.dumps(strategy_schema()))
    assert set(payload) == set(STRATEGIES)
    for name, spec in payload.items():
        assert spec["name"] == name
        assert spec["label"] and spec["description"]
        for param in spec["params"].values():
            assert "default" in param and param["label"] and param["type"]


def test_schema_is_a_copy_so_callers_cannot_edit_the_registry():
    strategy_schema()["manual"]["label"] = "hacked"
    assert STRATEGIES["manual"].label != "hacked"


# ----------------------------------------------------------------- best response
#
# The point of the phase: one mechanism-agnostic strategy, opposite behaviour under
# second-price and first-price rules, purely because the mechanism differs.


def past(bids, round=0, winner=None, price=0):
    return RoundView(round=round, bids=dict(bids), winner=winner, price=price)


def respond(mechanism, value, rival_bids, seat=0, typed=95, params=None, **overrides):
    ctx = context(
        bidder=Bidder("A", value, typed),
        rivals=tuple(rival_bids),
        seat=seat,
        round=1,
        history=[past({"A": typed, **rival_bids})],
        mechanism=mechanism,
        params=params,
    )
    return decide("best_response", ctx, overrides or None)


@pytest.mark.parametrize(
    "rival_bids", [{"B": 50}, {"B": 120}, {"B": 100}, {"B": 0}, {"B": 50, "C": 80}]
)
@pytest.mark.parametrize("seat", [0, 1])
def test_best_response_is_truthful_under_second_price(rival_bids, seat):
    """Truthfulness is the best reply to anything, and the strategy finds that by
    running the mechanism, not by knowing it."""
    assert respond("second_price", 100, rival_bids, seat=seat).bid == 100


def test_best_response_is_truthful_under_an_ascending_clock_too():
    assert respond("english", 100, {"B": 50}, seat=1).bid == 100


@pytest.mark.parametrize("seat,expected", [(0, 50), (1, 51)])
def test_best_response_shades_below_value_under_first_price(seat, expected):
    """Same code, opposite conclusion: buy the item as cheaply as the rival allows."""
    d = respond("first_price", 100, {"B": 50}, seat=seat)
    assert d.bid == expected
    assert d.bid < 100


def test_best_response_is_mechanism_agnostic_under_all_pay():
    d = respond("all_pay", 100, {"B": 50}, seat=1)
    assert d.bid == 51
    assert max(c["utility"] for c in d.considered) == 49


def test_best_response_never_considers_a_bid_above_value():
    d = respond("first_price", 100, {"B": 500}, seat=1, typed=95)
    assert d.bid <= 100
    assert all(0 <= c["bid"] <= 100 for c in d.considered)


def test_tick_sets_how_far_above_a_rival_to_look():
    assert respond("first_price", 100, {"B": 50}, seat=1, tick=10).bid == 60


def test_best_response_records_every_candidate_and_what_it_was_worth():
    d = respond("first_price", 100, {"B": 50}, seat=1)
    assert [c["bid"] for c in d.considered] == [0, 50, 51, 100]
    assert all("utility" in c for c in d.considered)
    chosen = [c for c in d.considered if c["bid"] == d.bid][0]
    assert chosen["utility"] == max(c["utility"] for c in d.considered)


def test_best_response_opens_with_the_typed_bid_when_there_is_no_history():
    d = decide("best_response", context(round=0, history=[]))
    assert d.bid == 95
    assert d.considered is None


def test_best_response_states_that_it_assumes_rivals_repeat_their_bids():
    why = respond("first_price", 100, {"B": 50}, seat=1).why.lower()
    assert "repeat" in why
    assert "last round" in why or "previous" in why


# ---------------------------------------------------- the privacy rule (load-bearing)


def test_history_keeps_public_bids_and_drops_private_values():
    trace = run("first_price", [Bidder("A", 100, 95), Bidder("B", 777, 50)])
    view = observe(0, trace)
    assert isinstance(view, RoundView)
    assert view.bids == {"A": 95, "B": 50}
    assert view.winner == "A"
    assert 777 not in scalars(view)


def test_no_rival_value_is_reachable_from_a_context():
    """A strategy that could read rival values would teach that auctions are a
    full-information game, which is the opposite of the point."""
    trace = run("first_price", [Bidder("A", 100, 95), Bidder("B", 777, 50)])
    ctx = context(rivals=("B",), history=[observe(0, trace)])
    reachable = scalars(ctx)
    assert 777 not in reachable, "a rival's private value leaked into the context"
    assert 50 in reachable, "the rival's past bid should still be visible"
    assert not [
        x for x in reachable if isinstance(x, Bidder) and x.id != ctx.bidder.id
    ]


def test_no_decision_changes_when_a_rival_value_changes_but_their_bid_does_not():
    rich = observe(0, run("first_price", [Bidder("A", 100, 95), Bidder("B", 777, 50)]))
    poor = observe(0, run("first_price", [Bidder("A", 100, 95), Bidder("B", 5, 50)]))
    for name in STRATEGIES:
        a = decide(name, context(rivals=("B",), round=1, history=[rich]))
        b = decide(name, context(rivals=("B",), round=1, history=[poor]))
        assert (a.bid, a.why) == (b.bid, b.why), f"{name} reacted to a rival's value"
