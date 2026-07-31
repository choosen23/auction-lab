"""Tests for the HTTP layer and the request layer under it.

``validate`` and ``run_payload`` live in :mod:`agt.api` and are plain functions, so most
of this file needs no socket at all; they are reached through ``serve`` because that is
the front door every caller uses. The handler tests bind port 0 on the loopback
interface, because status codes and header handling are what they exist to check and
faking them proves nothing.
"""

import http.client
import json
import os
import threading
import time
from http.server import ThreadingHTTPServer

import pytest

from agt import serve
from agt.packages import MAX_BIDS, MAX_ITEMS, PackageBid
from agt.registry import REGISTRY, Mechanism
from agt.series import MAX_ROUNDS
from agt.strategies import STRATEGIES
from agt.trace import Bidder, step

GOOD = {
    "mechanism": "second_price",
    "bidders": [
        {"id": "A", "value": 100, "bid": 95},
        {"id": "B", "value": 72, "bid": 72},
    ],
    "params": {"reserve": 0},
}

SERIES = {
    "mechanism": "first_price",
    "bidders": [
        {"id": "A", "value": 100, "bid": 95},
        {"id": "B", "value": 72, "bid": 72},
    ],
    "strategies": {
        "A": {"name": "best_response", "params": {"tick": 1}},
        "B": {"name": "truthful"},
    },
    "rounds": 4,
    "params": {"reserve": 0},
}


# The pinned combinatorial case: A wants both licences or neither, B and C want one
# each, and splitting the pair is worth more than keeping it together.
PACKAGE = {
    "mechanism": "vcg_package",
    "packages": [
        {"bidder": "A", "items": ["north", "south"], "value": 10, "bid": 10},
        {"bidder": "B", "items": ["north"], "value": 6, "bid": 6},
        {"bidder": "C", "items": ["south"], "value": 6, "bid": 6},
    ],
}


def payload(**overrides):
    """A valid payload with the named keys replaced."""
    return {**GOOD, **overrides}


def package_payload(**overrides):
    """A valid package-auction payload with the named keys replaced."""
    return {**PACKAGE, **overrides}


def one_package(**overrides):
    """A payload carrying a single package bid, built from one overridden field."""
    return package_payload(
        packages=[{**PACKAGE["packages"][1], **overrides}],
    )


def series_payload(**overrides):
    """A valid ``/run_series`` payload with the named keys replaced."""
    return {**SERIES, **overrides}


def bidders(n):
    return [{"id": chr(65 + i), "value": 10 + i, "bid": 10 + i} for i in range(n)]


# --------------------------------------------------------------------- validate


def test_validate_accepts_a_good_payload():
    valid = serve.validate(GOOD)
    assert valid["mechanism"] == "second_price"
    assert valid["bidders"] == [Bidder("A", 100, 95), Bidder("B", 72, 72)]
    assert valid["params"] == {"reserve": 0}


def test_validate_defaults_missing_params_to_empty():
    assert serve.validate(payload(params=None))["params"] == {}
    assert serve.validate({k: v for k, v in GOOD.items() if k != "params"})["params"] == {}


def test_validate_rejects_a_non_object_payload():
    for junk in ([], "second_price", 3, None):
        with pytest.raises(ValueError, match="JSON object"):
            serve.validate(junk)


def test_validate_rejects_unknown_mechanism_and_lists_the_valid_ones():
    with pytest.raises(ValueError, match="unknown mechanism") as caught:
        serve.validate(payload(mechanism="vickrey_clarke_groves"))
    assert "second_price" in str(caught.value)


def test_validate_rejects_a_non_string_mechanism():
    with pytest.raises(ValueError, match="unknown mechanism"):
        serve.validate(payload(mechanism=7))


def test_validate_rejects_a_missing_mechanism():
    with pytest.raises(ValueError, match="unknown mechanism"):
        serve.validate({"bidders": bidders(2)})


def test_validate_rejects_an_empty_bidder_list():
    with pytest.raises(ValueError, match="between 1 and 12"):
        serve.validate(payload(bidders=[]))


def test_validate_rejects_more_than_twelve_bidders():
    with pytest.raises(ValueError, match="between 1 and 12"):
        serve.validate(payload(bidders=bidders(13)))


def test_validate_accepts_exactly_twelve_bidders():
    assert len(serve.validate(payload(bidders=bidders(12)))["bidders"]) == 12


def test_validate_rejects_missing_or_misshapen_bidders():
    for junk in (None, {}, "A,B", 3):
        with pytest.raises(ValueError, match="between 1 and 12"):
            serve.validate(payload(bidders=junk))


def test_validate_rejects_a_bidder_that_is_not_an_object():
    with pytest.raises(ValueError, match="bidder 0"):
        serve.validate(payload(bidders=["A"]))


def test_validate_rejects_a_missing_bid_without_leaking_a_keyerror():
    with pytest.raises(ValueError, match="bid"):
        serve.validate(payload(bidders=[{"id": "A", "value": 10}]))


def test_validate_rejects_a_missing_value_without_leaking_a_keyerror():
    with pytest.raises(ValueError, match="value"):
        serve.validate(payload(bidders=[{"id": "A", "bid": 10}]))


@pytest.mark.parametrize("bad_id", [None, "", "   ", 7, ["A"]])
def test_validate_rejects_a_bad_bidder_id(bad_id):
    with pytest.raises(ValueError, match="id"):
        serve.validate(payload(bidders=[{"id": bad_id, "value": 10, "bid": 5}]))


@pytest.mark.parametrize("field", ["value", "bid"])
def test_validate_rejects_negative_numbers(field):
    entry = {"id": "A", "value": 10, "bid": 5, field: -1}
    with pytest.raises(ValueError, match="non-negative"):
        serve.validate(payload(bidders=[entry]))


@pytest.mark.parametrize("field", ["value", "bid"])
@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf")])
def test_validate_rejects_non_finite_numbers(field, bad):
    entry = {"id": "A", "value": 10, "bid": 5, field: bad}
    with pytest.raises(ValueError, match="finite"):
        serve.validate(payload(bidders=[entry]))


def test_validate_rejects_non_finite_numbers_arriving_as_json():
    """Python's json module parses the NaN and Infinity literals, so they do arrive."""
    body = json.loads('{"mechanism": "second_price", "bidders": '
                      '[{"id": "A", "value": NaN, "bid": 5}]}')
    with pytest.raises(ValueError, match="finite"):
        serve.validate(body)


@pytest.mark.parametrize("field", ["value", "bid"])
def test_validate_rejects_an_integer_too_large_for_a_float(field):
    """JSON has unbounded integers; math.isfinite raises OverflowError on them."""
    entry = {"id": "A", "value": 10, "bid": 5, field: 10**400}
    with pytest.raises(ValueError, match="finite"):
        serve.validate(payload(bidders=[entry]))


@pytest.mark.parametrize("field", ["value", "bid"])
def test_validate_rejects_booleans_even_though_bool_is_an_int(field):
    entry = {"id": "A", "value": 10, "bid": 5, field: True}
    with pytest.raises(ValueError, match="number"):
        serve.validate(payload(bidders=[entry]))


def test_validate_rejects_duplicate_bidder_ids():
    duplicated = [
        {"id": "A", "value": 100, "bid": 95},
        {"id": "A", "value": 72, "bid": 72},
    ]
    with pytest.raises(ValueError, match="unique"):
        serve.validate(payload(bidders=duplicated))


def test_validate_rejects_an_unknown_param_key():
    with pytest.raises(ValueError, match="unknown parameter") as caught:
        serve.validate(payload(params={"bogus": 1}))
    assert "reserve" in str(caught.value)


def test_validate_rejects_a_param_the_mechanism_does_not_declare():
    """`start` belongs to dutch only, so second_price must refuse it."""
    with pytest.raises(ValueError, match="unknown parameter"):
        serve.validate(payload(params={"start": 300}))
    assert serve.validate(payload(mechanism="dutch", params={"start": 300}))


@pytest.mark.parametrize("junk", [[], "reserve", 3, [("reserve", 0)]])
def test_validate_rejects_non_dict_params(junk):
    with pytest.raises(ValueError, match="params"):
        serve.validate(payload(params=junk))


# ------------------------------------------------------------------ run_payload


def test_validated_payload_runs_and_serializes_to_json():
    valid = serve.validate(GOOD)
    from agt.mechanisms import run

    trace = run(valid["mechanism"], valid["bidders"], valid["params"])
    round_tripped = json.loads(json.dumps(trace.to_dict()))
    assert round_tripped["result"]["winner"] == "A"
    assert round_tripped["result"]["price"] == 72


def test_run_payload_returns_a_trace():
    status, body = serve.run_payload(GOOD)
    assert status == 200
    assert body["result"]["winner"] == "A"
    assert body["steps"][0]["label"] == "collect bids"
    json.dumps(body)


def test_run_payload_reports_validation_errors_as_400():
    status, body = serve.run_payload(payload(mechanism="nope"))
    assert status == 400
    assert "unknown mechanism" in body["error"]
    assert set(body) == {"error"}


def test_run_payload_reports_engine_param_errors_as_400():
    """Range checking stays in the engine; the server only has to relay the message."""
    status, body = serve.run_payload(payload(params={"reserve": -5}))
    assert status == 400
    assert "reserve" in body["error"]


def test_run_payload_never_raises_on_hostile_input():
    """Whatever json.loads can produce, the client gets a status and a JSON body."""
    hostile = [
        None,
        [],
        {"mechanism": "second_price", "bidders": [{"id": "A", "value": 10**400, "bid": 1}]},
        {"mechanism": "second_price", "bidders": [{"id": "A"}]},
        {"mechanism": None, "bidders": None, "params": None},
        {"mechanism": "second_price", "bidders": bidders(2), "params": {"reserve": None}},
    ]
    for body in hostile:
        status, answer = serve.run_payload(body)
        assert status in (400, 500), body
        assert isinstance(answer["error"], str)
        json.dumps(answer)


@pytest.fixture
def broken_mechanism():
    """Register a mechanism that emits one step and then explodes."""

    def boom(bidders, **_):
        yield step(
            "collect bids",
            "Bids arrive.",
            {"bids": {b.id: b.bid for b in bidders}},
            stage="collect",
            bidders=[b.id for b in bidders],
        )
        raise RuntimeError("mechanism exploded")

    REGISTRY["broken"] = Mechanism("broken", "Broken", "Explodes on purpose.", {}, boom)
    try:
        yield "broken"
    finally:
        del REGISTRY["broken"]


def test_run_payload_returns_500_with_the_partial_trace(broken_mechanism):
    status, body = serve.run_payload(payload(mechanism="broken", params={}))
    assert status == 500
    assert "mechanism exploded" in body["error"]
    assert [s["label"] for s in body["partial_trace"]["steps"]] == ["collect bids"]
    assert body["partial_trace"]["result"] is None
    assert body["partial_trace"]["bidders"][0]["id"] == "A"
    assert "RuntimeError" in body["traceback"]
    json.dumps(body)


# ------------------------------------------------------------ the package input kind
#
# `/run` takes `packages` instead of `bidders` when the mechanism declares
# `input_kind: "package"`. The wrong kind is refused in *both* directions and by name:
# guessing what a caller meant would run an auction nobody asked for, and a message that
# only said "invalid" would leave a learner nowhere.


def test_validate_accepts_a_package_payload():
    valid = serve.validate(PACKAGE)
    assert valid["mechanism"] == "vcg_package"
    assert valid["bidders"] == [
        PackageBid("A", ("north", "south"), 10, 10),
        PackageBid("B", ("north",), 6, 6),
        PackageBid("C", ("south",), 6, 6),
    ]
    assert valid["params"] == {}


def test_run_payload_runs_a_package_mechanism_end_to_end():
    status, body = serve.run_payload(PACKAGE)
    assert status == 200
    assert body["result"]["allocation"] == {"B": ["north"], "C": ["south"]}
    assert body["result"]["welfare_gap"] == 2
    assert body["bidders"][0] == {
        "bidder": "A",
        "items": ["north", "south"],
        "value": 10,
        "bid": 10,
    }
    json.dumps(body)


def test_a_package_mechanism_refuses_scalar_bidders_and_names_the_kind_it_wants():
    status, body = serve.run_payload(
        {"mechanism": "vcg_package", "bidders": GOOD["bidders"]}
    )
    assert status == 400
    assert "packages" in body["error"]
    assert "vcg_package" in body["error"]


def test_a_single_item_mechanism_refuses_packages_and_names_the_kind_it_wants():
    status, body = serve.run_payload(
        {"mechanism": "second_price", "packages": PACKAGE["packages"]}
    )
    assert status == 400
    assert "bidders" in body["error"]
    assert "second_price" in body["error"]


def test_a_package_mechanism_with_no_packages_key_says_what_it_wanted():
    status, body = serve.run_payload({"mechanism": "greedy_package"})
    assert status == 400
    assert "packages" in body["error"]


@pytest.mark.parametrize("junk", [None, "A", 5, [], {}])
def test_validate_rejects_a_non_list_package_field(junk):
    with pytest.raises(ValueError, match="packages must be a list"):
        serve.validate(package_payload(packages=junk))


@pytest.mark.parametrize("bad_id", [None, "", "   ", 5, ["A"]])
def test_validate_rejects_a_bad_package_bidder_id(bad_id):
    with pytest.raises(ValueError, match="non-empty string bidder"):
        serve.validate(one_package(bidder=bad_id))


@pytest.mark.parametrize("junk", [None, "north", 5, {}, []])
def test_validate_rejects_a_bad_item_list(junk):
    with pytest.raises(ValueError, match="items must be a list"):
        serve.validate(one_package(items=junk))


@pytest.mark.parametrize("bad_item", [None, "", "   ", 5, ["north"]])
def test_validate_rejects_a_bad_item_name(bad_item):
    with pytest.raises(ValueError, match="non-empty string item names"):
        serve.validate(one_package(items=[bad_item]))


def test_validate_rejects_a_bundle_naming_the_same_item_twice():
    with pytest.raises(ValueError, match="same item twice"):
        serve.validate(one_package(items=["north", "north"]))


@pytest.mark.parametrize("field", ["value", "bid"])
@pytest.mark.parametrize("bad", [None, "10", True, -1, float("inf"), float("nan")])
def test_validate_rejects_bad_package_numbers(field, bad):
    """The same finite/non-negative rules the scalar bidders get, not a second copy."""
    with pytest.raises(ValueError, match=field):
        serve.validate(one_package(**{field: bad}))


def test_validate_rejects_a_package_entry_that_is_not_an_object():
    with pytest.raises(ValueError, match="package bid 0"):
        serve.validate(package_payload(packages=["north"]))


def test_validate_rejects_an_empty_package_list():
    with pytest.raises(ValueError, match="packages must be a list"):
        serve.validate(package_payload(packages=[]))


def test_validate_rejects_a_param_a_package_mechanism_does_not_declare():
    """Combinatorial reserve prices do not exist yet, so a reserve must be refused
    rather than accepted and quietly dropped."""
    with pytest.raises(ValueError, match="unknown parameter 'reserve'"):
        serve.validate(package_payload(params={"reserve": 5}))


# --------------------------------------------- the solver's size guard, as a 400


def test_more_packages_than_the_search_can_take_is_a_400_not_a_500():
    """`greedy_package` inherits the exhaustive search's bound because it reports the
    greedy-vs-optimal gap, so both mechanisms have to refuse an oversized input, and a
    learner has to meet that refusal as a bad request rather than as a crash."""
    oversized = [
        {"bidder": f"b{i}", "items": [f"item{i}"], "value": 1, "bid": 1}
        for i in range(MAX_BIDS + 1)
    ]
    for name in ("greedy_package", "vcg_package"):
        status, body = serve.run_payload(
            package_payload(mechanism=name, packages=oversized)
        )
        assert status == 400, name
        assert str(MAX_BIDS) in body["error"]


def test_more_items_than_the_search_can_take_is_a_400_not_a_500():
    crowded = [
        {"bidder": f"b{i}", "items": [f"item{i}"], "value": 1, "bid": 1}
        for i in range(MAX_ITEMS + 1)
    ]
    status, body = serve.run_payload(package_payload(packages=crowded))
    assert status == 400
    assert str(MAX_ITEMS) in body["error"]
    assert str(MAX_ITEMS + 1) in body["error"], "say how big the input actually was"


def test_a_payload_at_exactly_the_bound_still_runs():
    """The stated limits have to be usable, not just stated."""
    at_bound = [
        {"bidder": f"b{i}", "items": [f"item{i % MAX_ITEMS}"], "value": 100 - i, "bid": 100 - i}
        for i in range(MAX_BIDS)
    ]
    status, body = serve.run_payload(package_payload(packages=at_bound))
    assert status == 200
    assert body["result"]["welfare_gap"] >= 0


def test_run_payload_never_raises_on_hostile_package_input():
    hostile = [
        {"mechanism": "vcg_package", "packages": [{"bidder": "A"}]},
        {"mechanism": "vcg_package", "packages": [{"bidder": "A", "items": []}]},
        {"mechanism": "vcg_package", "packages": [{"bidder": "A", "items": ["n"], "value": 10**400, "bid": 1}]},
        {"mechanism": "greedy_package", "packages": None, "params": None},
        {"mechanism": "greedy_package", "packages": [{}] * 3},
    ]
    for body in hostile:
        status, answer = serve.run_payload(body)
        assert status in (400, 500), body
        assert isinstance(answer["error"], str)
        json.dumps(answer)


# -------------------------------------------------------------- validate_series


def test_validate_series_accepts_a_good_payload():
    valid = serve.validate_series(SERIES)
    assert valid["mechanism"] == "first_price"
    assert valid["bidders"] == [Bidder("A", 100, 95), Bidder("B", 72, 72)]
    assert valid["params"] == {"reserve": 0}
    assert valid["rounds"] == 4
    assert valid["strategies"] == {
        "A": {"name": "best_response", "params": {"tick": 1}},
        "B": {"name": "truthful", "params": {}},
    }


def test_validate_series_reuses_the_bidder_rules():
    """The extra endpoint must not grow a second, weaker copy of the bidder checks."""
    with pytest.raises(ValueError, match="between 1 and 12"):
        serve.validate_series(series_payload(bidders=bidders(13), strategies={}))
    with pytest.raises(ValueError, match="non-negative"):
        serve.validate_series(
            series_payload(
                bidders=[{"id": "A", "value": 10, "bid": -1}],
                strategies={"A": {"name": "manual"}},
            )
        )
    with pytest.raises(ValueError, match="unknown mechanism"):
        serve.validate_series(series_payload(mechanism="nope"))
    with pytest.raises(ValueError, match="unknown parameter"):
        serve.validate_series(series_payload(params={"bogus": 1}))


@pytest.mark.parametrize("name", ["greedy_package", "vcg_package"])
def test_validate_series_refuses_a_package_mechanism(name):
    """A phase 2 strategy answers one question — what number should I bid — and a bidder
    submitting bundles has no such number. Faking one would draw a bid path that means
    nothing, so the endpoint says no and says why."""
    with pytest.raises(ValueError, match="repeated rounds"):
        serve.validate_series(series_payload(mechanism=name))


def test_the_series_refusal_names_the_mechanism_and_the_reason():
    with pytest.raises(ValueError) as caught:
        serve.validate_series(series_payload(mechanism="vcg_package"))
    message = str(caught.value)
    assert "vcg_package" in message
    assert "package" in message
    assert "single" in message


def test_run_series_payload_refuses_a_package_mechanism_with_a_400():
    status, body = serve.run_series_payload(series_payload(mechanism="greedy_package"))
    assert status == 400
    assert "greedy_package" in body["error"]
    assert set(body) == {"error"}


def test_the_engine_refuses_a_package_series_too():
    """The rejection lives in the engine as well as at the door, because the engine runs
    under Pyodide with no door in front of it."""
    from agt.series import run_series

    with pytest.raises(ValueError, match="repeated rounds"):
        run_series("vcg_package", [Bidder("A", 10, 10)], {"A": {"name": "manual"}}, 2)


def test_validate_series_defaults_missing_rounds():
    assert serve.validate_series(series_payload(rounds=None))["rounds"] > 0
    stripped = {k: v for k, v in SERIES.items() if k != "rounds"}
    assert serve.validate_series(stripped)["rounds"] > 0


def test_validate_series_accepts_the_round_bounds():
    assert serve.validate_series(series_payload(rounds=1))["rounds"] == 1
    assert serve.validate_series(series_payload(rounds=MAX_ROUNDS))["rounds"] == MAX_ROUNDS


def test_validate_series_accepts_an_integral_float_for_rounds():
    """JSON round-trips make 3 into 3.0 often enough that refusing it is just rude."""
    assert serve.validate_series(series_payload(rounds=3.0))["rounds"] == 3


@pytest.mark.parametrize("bad", [0, -1, MAX_ROUNDS + 1, 10**400])
def test_validate_series_rejects_rounds_out_of_range(bad):
    with pytest.raises(ValueError, match=f"between 1 and {MAX_ROUNDS}"):
        serve.validate_series(series_payload(rounds=bad))


@pytest.mark.parametrize("bad", [True, False, 1.5, "3", "many", [4], {}, float("inf")])
def test_validate_series_rejects_rounds_that_are_not_whole_numbers(bad):
    with pytest.raises(ValueError, match="whole number"):
        serve.validate_series(series_payload(rounds=bad))


def test_validate_series_rejects_an_unknown_strategy_and_lists_the_valid_ones():
    with pytest.raises(ValueError, match="unknown strategy") as caught:
        serve.validate_series(
            series_payload(strategies={"A": {"name": "collude"}, "B": {"name": "truthful"}})
        )
    assert "truthful" in str(caught.value)
    assert "'A'" in str(caught.value)


@pytest.mark.parametrize("bad", [None, 7, ["truthful"]])
def test_validate_series_rejects_a_non_string_strategy_name(bad):
    with pytest.raises(ValueError, match="unknown strategy"):
        serve.validate_series(
            series_payload(strategies={"A": {"name": bad}, "B": {"name": "truthful"}})
        )


def test_validate_series_rejects_strategies_missing_a_bidder():
    with pytest.raises(ValueError, match="strategies") as caught:
        serve.validate_series(series_payload(strategies={"A": {"name": "truthful"}}))
    assert "'B'" in str(caught.value)


def test_validate_series_rejects_strategies_for_a_bidder_not_in_the_auction():
    strangers = {**SERIES["strategies"], "Z": {"name": "truthful"}}
    with pytest.raises(ValueError, match="strategies") as caught:
        serve.validate_series(series_payload(strategies=strangers))
    assert "'Z'" in str(caught.value)


@pytest.mark.parametrize("junk", [None, [], "truthful", 3])
def test_validate_series_rejects_non_dict_strategies(junk):
    with pytest.raises(ValueError, match="strategies"):
        serve.validate_series(series_payload(strategies=junk))


@pytest.mark.parametrize("junk", ["truthful", 3, ["truthful"], None])
def test_validate_series_rejects_a_strategy_entry_that_is_not_an_object(junk):
    with pytest.raises(ValueError, match="A"):
        serve.validate_series(
            series_payload(strategies={"A": junk, "B": {"name": "truthful"}})
        )


def test_validate_series_rejects_an_unknown_strategy_param():
    entry = {"name": "best_response", "params": {"bogus": 1}}
    with pytest.raises(ValueError, match="unknown parameter") as caught:
        serve.validate_series(
            series_payload(strategies={"A": entry, "B": {"name": "truthful"}})
        )
    assert "tick" in str(caught.value)


def test_validate_series_rejects_a_param_another_strategy_declares():
    """`tick` belongs to best_response only, so truthful must refuse it."""
    entry = {"name": "truthful", "params": {"tick": 1}}
    with pytest.raises(ValueError, match="unknown parameter"):
        serve.validate_series(
            series_payload(strategies={"A": entry, "B": {"name": "truthful"}})
        )


@pytest.mark.parametrize("junk", [[], "tick", 3])
def test_validate_series_rejects_non_dict_strategy_params(junk):
    entry = {"name": "best_response", "params": junk}
    with pytest.raises(ValueError, match="params"):
        serve.validate_series(
            series_payload(strategies={"A": entry, "B": {"name": "truthful"}})
        )


def test_validate_series_defaults_missing_strategy_params_to_empty():
    entry = {"name": "best_response", "params": None}
    valid = serve.validate_series(
        series_payload(strategies={"A": entry, "B": {"name": "truthful"}})
    )
    assert valid["strategies"]["A"] == {"name": "best_response", "params": {}}


# ----------------------------------------------------------- run_series_payload


def test_run_series_payload_returns_a_series():
    status, body = serve.run_series_payload(SERIES)
    assert status == 200
    assert len(body["rounds"]) == 4
    assert body["rounds"][0]["trace"]["steps"][0]["label"] == "collect bids"
    assert set(body["summary"]["bid_paths"]) == {"A", "B"}
    assert body["strategies"]["A"]["name"] == "best_response"
    json.dumps(body)


def test_run_series_payload_reports_validation_errors_as_400():
    status, body = serve.run_series_payload(series_payload(rounds=0))
    assert status == 400
    assert set(body) == {"error"}
    assert f"between 1 and {MAX_ROUNDS}" in body["error"]


def test_run_series_payload_reports_engine_errors_as_400():
    status, body = serve.run_series_payload(series_payload(params={"reserve": -5}))
    assert status == 400
    assert "reserve" in body["error"]


def test_run_series_payload_returns_500_when_a_mechanism_explodes(broken_mechanism):
    status, body = serve.run_series_payload(series_payload(mechanism="broken", params={}))
    assert status == 500
    assert "mechanism exploded" in body["error"]
    assert "RuntimeError" in body["traceback"]
    json.dumps(body)


def test_run_series_payload_never_raises_on_hostile_input():
    hostile = [
        None,
        [],
        "rounds",
        series_payload(rounds={"n": 5}),
        series_payload(strategies={"A": {"name": "best_response", "params": {"tick": -1}}}),
        {"mechanism": "first_price", "bidders": bidders(2), "strategies": None},
        {**SERIES, "bidders": [{"id": "A", "value": 10**400, "bid": 1}]},
    ]
    for body in hostile:
        status, answer = serve.run_series_payload(body)
        assert status in (400, 500), body
        assert isinstance(answer["error"], str)
        json.dumps(answer)


def test_worst_case_series_finishes_before_a_learner_gives_up():
    """50 rounds x 12 best-response bidders, each re-running the mechanism per candidate.

    This is the compute ceiling of the endpoint and the reason the round cap exists.
    ``english`` is the slowest of the four: its clock emits a step per bidder per rung,
    and best_response pays that cost once per candidate bid. Measured at 0.66s against
    0.24s for the sealed-bid mechanisms.
    """
    ids = [entry["id"] for entry in bidders(serve.MAX_BIDDERS)]
    worst = {
        "mechanism": "english",
        "bidders": bidders(serve.MAX_BIDDERS),
        "strategies": {i: {"name": "best_response"} for i in ids},
        "rounds": MAX_ROUNDS,
    }
    started = time.perf_counter()
    status, body = serve.run_series_payload(worst)
    elapsed = time.perf_counter() - started
    assert status == 200, body.get("error")
    assert len(body["rounds"]) == MAX_ROUNDS
    # Generous by design: the claim is "no learner thinks the page hung", not a benchmark,
    # and CI machines are slower than the one this was measured on.
    assert elapsed < 5, f"worst case took {elapsed:.2f}s"


# --------------------------------------------------------------- static serving


@pytest.fixture
def web_root(tmp_path):
    root = tmp_path / "web"
    (root / "sub").mkdir(parents=True)
    (root / "index.html").write_text("<h1>hello</h1>")
    (root / "sub" / "app.js").write_text("console.log(1)")
    (tmp_path / "secret.txt").write_text("private")
    return root


def test_resolve_static_serves_the_index_at_the_root(web_root):
    assert serve.resolve_static("/", web_root) == web_root / "index.html"


def test_resolve_static_serves_nested_files(web_root):
    assert serve.resolve_static("/sub/app.js", web_root) == web_root / "sub" / "app.js"


def test_resolve_static_ignores_the_query_string(web_root):
    assert serve.resolve_static("/index.html?v=2", web_root) == web_root / "index.html"


def test_resolve_static_returns_none_for_a_missing_file(web_root):
    assert serve.resolve_static("/nope.js", web_root) is None


def test_resolve_static_returns_none_for_a_directory(web_root):
    assert serve.resolve_static("/sub", web_root) is None


@pytest.mark.parametrize(
    "attack",
    [
        "/../secret.txt",
        "/sub/../../secret.txt",
        "/%2e%2e/secret.txt",
        "/..%2fsecret.txt",
        "/%2e%2e%2fsecret.txt",
        "/sub/%2e%2e/%2e%2e/secret.txt",
        "//etc/passwd",
        "/../../../../../../etc/passwd",
    ],
)
def test_resolve_static_rejects_path_traversal(web_root, attack):
    assert serve.resolve_static(attack, web_root) is None


def test_resolve_static_rejects_a_symlink_escaping_the_root(web_root):
    (web_root / "leak.txt").symlink_to(web_root.parent / "secret.txt")
    assert serve.resolve_static("/leak.txt", web_root) is None


def test_resolve_static_survives_a_nul_byte(web_root):
    assert serve.resolve_static("/index.html\x00.png", web_root) is None


@pytest.mark.parametrize(
    "name,expected",
    [
        ("index.html", "text/html; charset=utf-8"),
        ("app.js", "text/javascript; charset=utf-8"),
        ("style.CSS", "text/css; charset=utf-8"),
        ("trace.json", "application/json"),
        ("chart.svg", "image/svg+xml"),
        ("evil.php", "application/octet-stream"),
        ("noextension", "application/octet-stream"),
    ],
)
def test_content_type_is_an_allow_list(tmp_path, name, expected):
    assert serve.content_type(tmp_path / name) == expected


# ----------------------------------------------------------------- the handler


@pytest.fixture
def client(web_root, monkeypatch):
    monkeypatch.setattr(serve, "WEB_ROOT", web_root)
    monkeypatch.setattr(serve.Handler, "log_message", lambda *a, **k: None)
    httpd = ThreadingHTTPServer((serve.HOST, 0), serve.Handler)
    # poll_interval is how long shutdown() takes; the default 0.5s per test adds up.
    thread = threading.Thread(target=httpd.serve_forever, args=(0.01,), daemon=True)
    thread.start()
    conn = http.client.HTTPConnection(*httpd.server_address, timeout=5)
    try:
        yield conn
    finally:
        conn.close()
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)


def fetch(conn, method, path, body=None, headers=None):
    conn.request(method, path, body, headers or {})
    response = conn.getresponse()
    return response.status, response.headers, response.read()


def as_json(raw):
    return json.loads(raw.decode())


def test_server_binds_the_loopback_interface_only():
    assert serve.HOST == "127.0.0.1"


def test_get_mechanisms_returns_the_registry_schema(client):
    status, headers, raw = fetch(client, "GET", "/mechanisms")
    assert status == 200
    assert headers["Content-Type"] == "application/json"
    assert set(as_json(raw)) == set(REGISTRY)


def test_get_strategies_returns_the_strategy_schema(client):
    status, headers, raw = fetch(client, "GET", "/strategies")
    assert status == 200
    assert headers["Content-Type"] == "application/json"
    body = as_json(raw)
    assert set(body) == set(STRATEGIES)
    assert set(body["best_response"]) == {"name", "label", "description", "params"}
    assert "tick" in body["best_response"]["params"]


def test_get_root_serves_the_index(client):
    status, headers, raw = fetch(client, "GET", "/")
    assert status == 200
    assert headers["Content-Type"] == "text/html; charset=utf-8"
    assert headers["X-Content-Type-Options"] == "nosniff"
    assert raw == b"<h1>hello</h1>"


def test_get_static_file(client):
    status, headers, raw = fetch(client, "GET", "/sub/app.js")
    assert status == 200
    assert headers["Content-Type"] == "text/javascript; charset=utf-8"
    assert raw == b"console.log(1)"


def test_get_traversal_over_http_is_a_404(client):
    status, _, raw = fetch(client, "GET", "/../secret.txt")
    assert status == 404
    assert "error" in as_json(raw)


def test_get_an_unreadable_file_answers_instead_of_dropping_the_connection(
    client, web_root
):
    unreadable = web_root / "locked.txt"
    unreadable.write_text("secret")
    unreadable.chmod(0o000)
    if os.access(unreadable, os.R_OK):  # running as root; the mode means nothing
        pytest.skip("cannot make a file unreadable as this user")
    status, headers, raw = fetch(client, "GET", "/locked.txt")
    assert status == 404
    assert headers["Content-Type"] == "application/json"
    assert "secret" not in raw.decode()


def test_get_unknown_route_is_a_json_404(client):
    status, headers, raw = fetch(client, "GET", "/does/not/exist")
    assert status == 404
    assert headers["Content-Type"] == "application/json"
    assert "error" in as_json(raw)


def test_post_run_returns_a_trace(client):
    status, headers, raw = fetch(client, "POST", "/run", json.dumps(GOOD))
    assert status == 200
    assert headers["Content-Type"] == "application/json"
    assert as_json(raw)["result"]["price"] == 72


def test_post_run_rejects_an_invalid_payload_with_400(client):
    status, _, raw = fetch(client, "POST", "/run", json.dumps(payload(bidders=[])))
    assert status == 400
    assert "between 1 and 12" in as_json(raw)["error"]


def test_post_run_rejects_malformed_json_without_a_stack_trace(client):
    status, _, raw = fetch(client, "POST", "/run", "{not json")
    assert status == 400
    body = as_json(raw)
    assert "JSON" in body["error"]
    assert "Traceback" not in body["error"]


def test_post_run_rejects_an_oversized_body(client):
    huge = json.dumps({"mechanism": "second_price", "pad": "x" * (serve.MAX_BODY + 10)})
    status, _, raw = fetch(client, "POST", "/run", huge)
    assert status == 413
    assert "error" in as_json(raw)


def test_post_run_rejects_a_lying_content_length(client):
    """The declared size is the only thing we trust, and only up to the cap."""
    status, _, raw = fetch(
        client,
        "POST",
        "/run",
        json.dumps(GOOD),
        {"Content-Length": str(serve.MAX_BODY * 100)},
    )
    assert status == 413


def test_post_without_content_length_is_refused(client):
    client.putrequest("POST", "/run")
    client.endheaders()
    response = client.getresponse()
    assert response.status == 411
    assert "error" in json.loads(response.read().decode())


def test_post_run_series_returns_a_series(client):
    status, headers, raw = fetch(client, "POST", "/run_series", json.dumps(SERIES))
    assert status == 200
    assert headers["Content-Type"] == "application/json"
    body = as_json(raw)
    assert len(body["rounds"]) == 4
    assert body["summary"]["bid_paths"]["A"][0] == 95


def test_post_run_series_rejects_an_invalid_payload_with_400(client):
    body = json.dumps(series_payload(rounds=99))
    status, _, raw = fetch(client, "POST", "/run_series", body)
    assert status == 400
    assert f"between 1 and {MAX_ROUNDS}" in as_json(raw)["error"]


def test_post_run_series_rejects_malformed_json_without_a_stack_trace(client):
    status, _, raw = fetch(client, "POST", "/run_series", "{not json")
    assert status == 400
    assert "JSON" in as_json(raw)["error"]
    assert "Traceback" not in as_json(raw)["error"]


def test_post_unknown_route_is_a_json_404(client):
    status, _, raw = fetch(client, "POST", "/nope", json.dumps(GOOD))
    assert status == 404
    assert "error" in as_json(raw)


# ------------------------------------------------------------------ the world (phase 4)
#
# `/run_series` grows one optional `world` key: value draws, budgets, and a seed. Its
# absence has to stay exactly the phase 2 request, because that is what the whole seam
# was built to guarantee.

WORLD_BODY = {
    "mechanism": "second_price",
    "bidders": [
        {"id": "A", "value": 100, "bid": 95},
        {"id": "B", "value": 72, "bid": 72},
    ],
    "strategies": {
        "A": {"name": "pace_multiplicative", "params": {}},
        "B": {"name": "truthful", "params": {}},
    },
    "rounds": 6,
}


def test_a_body_with_no_world_is_still_accepted():
    assert serve.validate_series(WORLD_BODY)["world"] is None


def test_a_world_is_read_back_with_its_rounds_from_the_request():
    valid = serve.validate_series({**WORLD_BODY, "world": {
        "seed": 3, "value_low": 10, "value_high": 90, "budgets": {"A": 200},
    }})
    world = valid["world"]
    assert (world.seed, world.value_low, world.value_high) == (3, 10, 90)
    assert world.budgets == {"A": 200}
    assert world.rounds == 6      # the request's `rounds`, not a second source of truth


@pytest.mark.parametrize("world,message", [
    ({"value_low": 90, "value_high": 10}, "value_low"),
    ({"value_low": 10}, "both"),
    ({"value_high": 10}, "both"),
    ({"value_low": -5, "value_high": 10}, "non-negative"),
    ({"value_low": 1, "value_high": float("inf")}, "finite"),
    ({"seed": 1.5}, "whole number"),
    ({"seed": True}, "whole number"),
    ({"budgets": {"Z": 10}}, "not a bidder"),
    ({"budgets": {"A": -1}}, "non-negative"),
    ({"budgets": []}, "object"),
    ("nope", "object"),
])
def test_a_bad_world_is_refused_with_a_readable_reason(world, message):
    with pytest.raises(ValueError, match=message):
        serve.validate_series({**WORLD_BODY, "world": world})


def test_a_package_mechanism_is_still_refused_even_with_a_world():
    """The phase 3 refusal comes first: a strategy produces one number, which means
    nothing over bundles, whatever world it would have run in."""
    with pytest.raises(ValueError, match="round"):
        serve.validate_series({
            "mechanism": "vcg_package",
            "packages": [{"bidder": "A", "items": ["x"], "value": 5, "bid": 5}],
            "strategies": {"A": {"name": "truthful", "params": {}}},
            "world": {"seed": 1},
        })


def test_running_a_world_series_end_to_end_reports_spend():
    status, body = serve.run_series_payload({**WORLD_BODY, "world": {
        "seed": 1, "value_low": 20, "value_high": 80, "budgets": {"A": 60},
    }})
    assert status == 200
    assert body["summary"]["budgets"] == {"A": 60}
    assert body["summary"]["spend"]["A"][-1] <= 60
    json.dumps(body)     # the browser has to be able to parse whatever we send


def test_the_worst_case_world_series_finishes_quickly():
    """Learners re-run the mechanism per arm, so the expensive path is bounded and
    measured rather than assumed."""
    body = {
        "mechanism": "second_price",
        "bidders": [{"id": f"B{i}", "value": 100, "bid": 50} for i in range(12)],
        "strategies": {f"B{i}": {"name": "bandit_ucb", "params": {}} for i in range(12)},
        "rounds": 50,
        "world": {"seed": 1, "value_low": 10, "value_high": 100},
    }
    start = time.perf_counter()
    status, _ = serve.run_series_payload(body)
    elapsed = time.perf_counter() - start
    assert status == 200
    assert elapsed < 5, f"worst-case world series took {elapsed:.2f}s"
