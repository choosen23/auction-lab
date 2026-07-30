from agt.trace import Bidder, outcome, step


def test_outcome_second_price_style():
    bidders = [Bidder("A", 100, 95), Bidder("B", 72, 72), Bidder("C", 41, 41)]
    r = outcome(bidders, winner="A", payments={"A": 72, "B": 0, "C": 0})
    assert r["winner"] == "A"
    assert r["price"] == 72
    assert r["utilities"] == {"A": 28, "B": 0, "C": 0}
    assert r["revenue"] == 72
    assert r["welfare"] == 100
    assert r["efficient"] is True


def test_outcome_all_pay_losers_pay_too():
    bidders = [Bidder("A", 100, 60), Bidder("B", 72, 50)]
    r = outcome(bidders, winner="A", payments={"A": 60, "B": 50})
    assert r["utilities"] == {"A": 40, "B": -50}
    assert r["revenue"] == 110


def test_outcome_no_winner_is_inefficient_when_value_exists():
    bidders = [Bidder("A", 100, 10)]
    r = outcome(bidders, winner=None, payments={"A": 0})
    assert r["welfare"] == 0
    assert r["efficient"] is False


def test_outcome_flags_inefficient_allocation():
    bidders = [Bidder("A", 100, 10), Bidder("B", 50, 40)]
    r = outcome(bidders, winner="B", payments={"A": 0, "B": 40})
    assert r["welfare"] == 50
    assert r["efficient"] is False


def test_step_carries_full_state_snapshot():
    s = step("sort", "Rank bids high to low.", {"bids": {"A": 95}}, formula="b_(1) = 95", stage="sort")
    assert s.state == {"bids": {"A": 95}}
    assert s.highlight == {"stage": "sort"}
    assert s.formula == "b_(1) = 95"


def test_step_snapshot_is_decoupled_from_caller_state():
    live = {"bids": {"A": 95}}
    s = step("collect", "Bids arrive.", live)
    live["bids"]["A"] = 999
    assert s.state["bids"]["A"] == 95


def test_trace_serializes_to_json_shape():
    import json

    from agt.trace import Trace

    bidders = [Bidder("A", 100, 95)]
    t = Trace(
        mechanism="second_price",
        params={"reserve": 0},
        bidders=bidders,
        steps=[step("collect bids", "Bids arrive.", {"bids": {"A": 95}})],
        result=outcome(bidders, winner="A", payments={"A": 0}),
    )
    payload = json.loads(json.dumps(t.to_dict()))
    assert payload["mechanism"] == "second_price"
    assert payload["bidders"][0] == {"id": "A", "value": 100, "bid": 95}
    assert payload["steps"][0]["label"] == "collect bids"
    assert payload["result"]["winner"] == "A"
