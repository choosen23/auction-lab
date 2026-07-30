from agt.trace import Bidder, outcome, outcome_allocation, step


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


# -------------------------------------------------- allocation scoring (many winners)
#
# `outcome()` scores one winner who receives their whole value. An allocation scores
# several winners who each receive part of it — a slot's worth of clicks, one bundle out
# of many — so the gross value received has to be handed in rather than read off the
# bidder, which is why this cannot be folded into `outcome()`.

SLOTTED = [Bidder("A", 100, 95), Bidder("B", 72, 72), Bidder("C", 41, 41)]

# `outcome_allocation` is handed the gains rather than reading a `value` off anybody,
# so all it needs of the room is who is in it — which is also what lets a package
# mechanism, whose bidders have no single scalar record, use the same scorer.
SEATS = ["A", "B", "C"]


def test_outcome_allocation_scores_every_winner():
    r = outcome_allocation(
        SEATS,
        allocation={"A": 0, "B": 1},
        payments={"A": 72, "B": 20.5},
        gains={"A": 100, "B": 36},
        best_possible_welfare=136,
    )
    assert r["allocation"] == {"A": 0, "B": 1}
    assert r["utilities"] == {"A": 28, "B": 15.5, "C": 0}
    assert r["welfare"] == 136
    assert r["revenue"] == 92.5
    assert r["efficient"] is True


def test_outcome_allocation_gives_unallocated_bidders_no_gain():
    r = outcome_allocation(
        SEATS,
        allocation={"A": 0},
        payments={"A": 72},
        gains={"A": 100},
        best_possible_welfare=100,
    )
    assert r["gains"]["C"] == 0
    assert r["utilities"]["C"] == 0
    assert r["welfare"] == 100


def test_outcome_allocation_flags_a_welfare_shortfall():
    """Efficiency is the whole allocation's business: the same bidders in the wrong
    slots lose welfare even though every slot is filled."""
    r = outcome_allocation(
        SEATS,
        allocation={"B": 0, "A": 1},
        payments={"B": 95, "A": 0},
        gains={"B": 72, "A": 50},
        best_possible_welfare=136,
    )
    assert r["welfare"] == 122
    assert r["efficient"] is False


def test_outcome_allocation_names_the_top_winner_so_the_result_panel_still_works():
    """`winner` and `price` stay populated so phase 1's result panel needs no change."""
    r = outcome_allocation(
        SEATS,
        allocation={"A": 0, "B": 1},
        payments={"A": 72, "B": 20.5},
        gains={"A": 100, "B": 36},
        best_possible_welfare=136,
    )
    assert r["winner"] == "A"
    assert r["price"] == 72


def test_outcome_allocation_matches_outcome_when_only_one_bidder_is_allocated():
    """A single-winner allocation must score identically to `outcome()`, or the two
    scorers would quietly disagree about the same auction."""
    single = outcome(SLOTTED, winner="A", payments={"A": 72, "B": 0, "C": 0})
    allocated = outcome_allocation(
        SEATS,
        allocation={"A": 0},
        payments={"A": 72},
        gains={"A": 100},
        best_possible_welfare=100,
    )
    assert {key: allocated[key] for key in single} == single


def test_outcome_allocation_with_nothing_allocated_has_no_winner():
    r = outcome_allocation(
        SEATS, allocation={}, payments={}, gains={}, best_possible_welfare=100
    )
    assert r["winner"] is None
    assert r["price"] == 0
    assert r["revenue"] == 0
    assert r["welfare"] == 0
    assert r["efficient"] is False


def test_outcome_allocation_names_whoever_the_mechanism_ranked_first():
    """The mechanism lists its winners best first and the scorer does not second-guess
    it, so the top slot stays the top slot even when a lower slot is worth more to the
    bidder holding it."""
    r = outcome_allocation(
        SEATS,
        allocation={"B": 0, "A": 1},
        payments={"B": 40, "A": 10},
        gains={"B": 72, "A": 50},
        best_possible_welfare=136,
    )
    assert r["winner"] == "B"
    assert r["price"] == 40


def test_outcome_allocation_survives_float_dust_in_the_welfare_comparison():
    """Click-weighted welfare sums its terms in a different order than the best-possible
    sum does, so exact equality would report a false inefficiency."""
    gains = {"A": 0.1 * 3, "B": 0.2}
    r = outcome_allocation(
        ["A", "B"],
        allocation={"A": 0, "B": 1},
        payments={},
        gains=gains,
        best_possible_welfare=0.3 + 0.2,
    )
    assert r["efficient"] is True


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
