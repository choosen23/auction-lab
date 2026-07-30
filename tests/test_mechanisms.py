import pytest

from agt.mechanisms import REGISTRY, registry_schema, run
from agt.trace import Bidder

BIDDERS = [Bidder("A", 100, 95), Bidder("B", 72, 72), Bidder("C", 41, 41)]


def test_second_price_winner_pays_second_bid():
    t = run("second_price", BIDDERS)
    assert t.result["winner"] == "A"
    assert t.result["price"] == 72
    assert t.result["utilities"]["A"] == 28


def test_first_price_winner_pays_own_bid():
    t = run("first_price", BIDDERS)
    assert t.result["winner"] == "A"
    assert t.result["price"] == 95
    assert t.result["utilities"]["A"] == 5


def test_all_pay_everyone_pays():
    t = run("all_pay", BIDDERS)
    assert t.result["winner"] == "A"
    assert t.result["revenue"] == 95 + 72 + 41
    assert t.result["utilities"]["C"] == -41


def test_reserve_above_all_bids_blocks_sale():
    t = run("second_price", BIDDERS, {"reserve": 200})
    assert t.result["winner"] is None
    assert t.result["revenue"] == 0
    assert t.result["efficient"] is False


def test_reserve_above_all_bids_blocks_all_pay_too():
    t = run("all_pay", BIDDERS, {"reserve": 200})
    assert t.result["winner"] is None
    assert t.result["revenue"] == 0


def test_reserve_between_bids_raises_price():
    t = run("second_price", BIDDERS, {"reserve": 80})
    assert t.result["winner"] == "A"
    assert t.result["price"] == 80


def test_first_price_reserve_does_not_lower_own_bid():
    t = run("first_price", BIDDERS, {"reserve": 80})
    assert t.result["price"] == 95


def test_steps_are_ordered_and_labelled():
    t = run("second_price", BIDDERS)
    labels = [s.label for s in t.steps]
    assert labels[0] == "collect bids"
    assert "price rule" in labels
    assert t.steps[-1].state["winner"] == "A"


def test_ties_break_by_bidder_order_and_say_so():
    tied = [Bidder("A", 50, 50), Bidder("B", 90, 50)]
    t = run("first_price", tied)
    assert t.result["winner"] == "A"
    assert any("tie" in s.detail.lower() for s in t.steps)


def test_unknown_mechanism_is_rejected():
    with pytest.raises(ValueError, match="unknown mechanism"):
        run("nope", BIDDERS)


def test_unknown_param_is_rejected():
    with pytest.raises(ValueError, match="unknown parameter"):
        run("second_price", BIDDERS, {"bogus": 1})


def test_out_of_range_param_is_rejected():
    with pytest.raises(ValueError, match="reserve"):
        run("second_price", BIDDERS, {"reserve": -1})


def test_non_numeric_param_is_rejected():
    with pytest.raises(ValueError, match="reserve"):
        run("second_price", BIDDERS, {"reserve": "cheap"})


def test_defaults_are_applied_and_recorded():
    t = run("second_price", BIDDERS)
    assert t.params["reserve"] == 0


def test_registry_schema_is_json_safe_and_form_ready():
    import json

    payload = json.loads(json.dumps(registry_schema()))
    assert set(payload) == set(REGISTRY)
    entry = payload["second_price"]
    assert entry["name"] == "second_price"
    assert entry["label"] and entry["description"]
    reserve = entry["params"]["reserve"]
    assert reserve["type"] == "number"
    assert reserve["default"] == 0
    assert reserve["label"]


def test_trace_is_json_serializable_for_every_mechanism():
    import json

    for name in REGISTRY:
        json.dumps(run(name, BIDDERS).to_dict())


@pytest.mark.parametrize("name", ["first_price", "second_price", "all_pay"])
def test_sealed_bid_steps_are_teaching_material(name):
    t = run(name, BIDDERS, {"reserve": 50})
    labels = [s.label for s in t.steps]
    assert labels[0] == "collect bids"
    assert "sort" in labels
    assert "apply reserve" in labels
    assert "pick winner" in labels
    assert "price rule" in labels
    assert labels[-1] == "payments"
    for s in t.steps:
        assert s.detail.endswith("."), f"{name}/{s.label}: detail must be a sentence"
        assert s.highlight["stage"]
        assert isinstance(s.highlight["bidders"], list)
        assert "bids" in s.state
    assert any(s.formula for s in t.steps), "at least one step must show a formula"
