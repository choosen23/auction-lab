import random

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


# ------------------------------------------------------------- clock auctions


def test_english_equals_second_price():
    t = run("english", BIDDERS)
    assert t.result["winner"] == "A"
    assert t.result["price"] == 72
    assert t.result == run("second_price", BIDDERS).result


def test_english_steps_show_each_dropout():
    t = run("english", BIDDERS)
    dropouts = [s for s in t.steps if s.highlight.get("stage") == "dropout"]
    assert len(dropouts) == 2
    assert [s.state["clock"] for s in dropouts] == [41, 72]


def test_english_clock_starts_at_the_reserve():
    t = run("english", BIDDERS, {"reserve": 80})
    assert t.result["winner"] == "A"
    assert t.result["price"] == 80
    assert t.result == run("second_price", BIDDERS, {"reserve": 80}).result


def test_english_reserve_above_all_bids_blocks_sale():
    t = run("english", BIDDERS, {"reserve": 200})
    assert t.result["winner"] is None
    assert t.result["revenue"] == 0


def test_dutch_equals_first_price():
    t = run("dutch", BIDDERS)
    assert t.result["winner"] == "A"
    assert t.result["price"] == 95
    assert t.result == run("first_price", BIDDERS).result


def test_dutch_clock_starts_above_every_bid():
    t = run("dutch", BIDDERS)
    start = t.steps[1].state["clock"]
    assert start > max(b.bid for b in BIDDERS)


def test_dutch_accepts_an_explicit_start_price():
    t = run("dutch", BIDDERS, {"start": 300})
    assert t.steps[1].state["clock"] == 300
    assert t.result["price"] == 95


def test_dutch_reserve_blocks_sale():
    t = run("dutch", BIDDERS, {"reserve": 200})
    assert t.result["winner"] is None
    assert t.result["revenue"] == 0


@pytest.mark.parametrize(
    "name,equivalence", [("english", "second-price"), ("dutch", "first-price")]
)
def test_clock_steps_teach_the_equivalence(name, equivalence):
    t = run(name, BIDDERS)
    labels = [s.label for s in t.steps]
    assert labels[0] == "collect bids"
    assert "price rule" in labels
    assert labels[-1] == "payments"
    assert t.steps[-1].state["winner"] == "A"
    for s in t.steps:
        assert s.detail.endswith("."), f"{name}/{s.label}: detail must be a sentence"
        assert s.highlight["stage"]
        assert isinstance(s.highlight["bidders"], list)
        assert "bids" in s.state
    priced = next(s for s in t.steps if s.label == "price rule")
    assert equivalence in priced.detail.lower()


# ------------------------------------------- invariants every mechanism must hold
#
# Parametrized over the registry, so a mechanism added in a later phase inherits these
# checks the moment it is registered.

TOL = 1e-9


@pytest.mark.parametrize("name", sorted(REGISTRY))
def test_invariants_hold(name):
    t = run(name, BIDDERS)
    r = t.result
    assert r["revenue"] == pytest.approx(sum(r["payments"].values()))
    for b in BIDDERS:
        gain = b.value if b.id == r["winner"] else 0
        assert r["utilities"][b.id] == pytest.approx(gain - r["payments"][b.id])
    assert t.steps, "a mechanism must emit at least one step"
    assert t.steps[-1].state["winner"] == r["winner"]


@pytest.mark.parametrize("name", sorted(REGISTRY))
def test_invariants_hold_under_a_blocking_reserve(name):
    t = run(name, BIDDERS, {"reserve": 200})
    r = t.result
    assert r["winner"] is None
    assert r["revenue"] == 0
    assert r["efficient"] is False
    assert t.steps[-1].state["winner"] is None


@pytest.mark.parametrize("name", sorted(REGISTRY))
def test_winner_never_pays_more_than_their_bid(name):
    t = run(name, BIDDERS)
    winner = t.result["winner"]
    bid = next(b.bid for b in BIDDERS if b.id == winner)
    assert t.result["price"] <= bid + TOL


@pytest.mark.parametrize("name", ["second_price", "english"])
def test_truthful_bidding_is_dominant(name):
    """No deviation from bidding your value beats it, over random value profiles."""
    rng = random.Random(0)
    for _ in range(200):
        values = [rng.randint(1, 100) for _ in range(3)]
        others = [Bidder(i, v, v) for i, v in zip("BC", values[1:])]
        truthful = run(name, [Bidder("A", values[0], values[0])] + others)
        honest_utility = truthful.result["utilities"]["A"]
        for lie in (values[0] // 2, values[0] * 2):
            deviant = run(name, [Bidder("A", values[0], lie)] + others)
            assert deviant.result["utilities"]["A"] <= honest_utility + TOL, (
                f"{name}: values={values} bid={lie} beat truthful bidding"
            )


@pytest.mark.parametrize("name", ["second_price", "english"])
def test_truthful_bidding_is_dominant_under_a_reserve(name):
    """The reserve must not open a profitable lie either."""
    rng = random.Random(1)
    for _ in range(200):
        values = [rng.randint(1, 100) for _ in range(3)]
        reserve = rng.randint(0, 100)
        others = [Bidder(i, v, v) for i, v in zip("BC", values[1:])]
        truthful = run(
            name, [Bidder("A", values[0], values[0])] + others, {"reserve": reserve}
        )
        honest_utility = truthful.result["utilities"]["A"]
        for lie in (values[0] // 2, values[0] * 2):
            deviant = run(name, [Bidder("A", values[0], lie)] + others, {"reserve": reserve})
            assert deviant.result["utilities"]["A"] <= honest_utility + TOL, (
                f"{name}: values={values} reserve={reserve} bid={lie} beat truthful bidding"
            )
