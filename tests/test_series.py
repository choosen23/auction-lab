import json

import pytest

from agt.mechanisms import run
from agt.series import RoundRecord, Series, run_series
from agt.trace import Bidder

# Phase 1's own bidders, so a one-round manual series can be compared against `run()`.
BIDDERS = [Bidder("A", 100, 95), Bidder("B", 72, 72), Bidder("C", 41, 41)]
VALUES = {b.id: b.value for b in BIDDERS}


def everyone(name, **params):
    return {b.id: {"name": name, "params": params} for b in BIDDERS}


def settled(series):
    return {i: path[-1] for i, path in series.summary["bid_paths"].items()}


# ------------------------------------------------------------------------ the seam


def test_one_manual_round_is_exactly_a_plain_run():
    """Phase 1 is genuinely untouched: same trace, step for step."""
    s = run_series("second_price", BIDDERS, everyone("manual"), rounds=1)
    assert s.rounds[0].trace.to_dict() == run("second_price", BIDDERS).to_dict()


def test_manual_bidders_never_move():
    s = run_series("first_price", BIDDERS, everyone("manual"), rounds=3)
    assert s.summary["bid_paths"] == {"A": [95, 95, 95], "B": [72, 72, 72], "C": [41, 41, 41]}
    assert s.summary["converged"] is True
    assert s.summary["converged_round"] == 0


def test_mechanism_params_are_resolved_once_and_applied_every_round():
    s = run_series("second_price", BIDDERS, everyone("manual"), rounds=2, params={"reserve": 80})
    assert s.params == {"reserve": 80}
    assert [r.trace.result["price"] for r in s.rounds] == [80, 80]


def test_values_stay_fixed_across_rounds_and_only_bids_move():
    s = run_series("first_price", BIDDERS, everyone("best_response"), rounds=4)
    for record in s.rounds:
        assert {b.id: b.value for b in record.trace.bidders} == VALUES


# ------------------------------------------------- the two dynamics that are the point


def test_second_price_best_response_settles_on_every_bidders_value():
    s = run_series("second_price", BIDDERS, everyone("best_response"), rounds=6)
    assert s.summary["converged"] is True
    assert s.summary["converged_round"] == 1
    assert settled(s) == VALUES
    assert s.summary["bid_paths"]["A"] == [95, 100, 100, 100, 100, 100]


def test_first_price_best_response_settles_strictly_below_value():
    s = run_series("first_price", BIDDERS, everyone("best_response"), rounds=6)
    assert s.summary["converged"] is True
    winner = s.rounds[-1].trace.result["winner"]
    assert 0 < settled(s)[winner] < VALUES[winner]
    assert settled(s)["A"] < s.summary["bid_paths"]["A"][0]  # it shaded downwards


def test_a_series_that_is_still_moving_says_so():
    """Best-response dynamics in a first-price auction can cycle rather than settle, and
    a cycle must never be reported as an equilibrium."""
    restless = [Bidder("A", 100, 95), Bidder("B", 72, 60)]
    s = run_series("first_price", restless, {i: {"name": "best_response"} for i in "AB"}, rounds=8)
    assert s.summary["converged"] is False
    assert s.summary["converged_round"] is None


# ---------------------------------------------------------------------- the summary


def test_cumulative_utilities_are_the_sum_of_the_rounds():
    s = run_series("first_price", BIDDERS, everyone("best_response"), rounds=5)
    for i, per_round in s.summary["utilities"].items():
        assert len(per_round) == 5
        assert s.summary["cumulative_utilities"][i] == sum(per_round)


def test_efficiency_rate_counts_rounds_that_reached_the_highest_value_bidder():
    """A bids 0 in round 0 and loses an item they value most; from round 1 they win it."""
    bidders = [Bidder("A", 100, 0), Bidder("B", 50, 50)]
    s = run_series(
        "second_price",
        bidders,
        {"A": {"name": "best_response"}, "B": {"name": "manual"}},
        rounds=4,
    )
    assert [r.trace.result["winner"] for r in s.rounds] == ["B", "A", "A", "A"]
    assert s.summary["efficiency_rate"] == 0.75
    assert s.summary["revenue"] == [0, 50, 50, 50]


def test_bid_paths_are_what_each_round_actually_ran():
    s = run_series("first_price", BIDDERS, everyone("best_response"), rounds=3)
    for index, record in enumerate(s.rounds):
        assert record.round == index
        for b in record.trace.bidders:
            assert s.summary["bid_paths"][b.id][index] == b.bid


def test_every_round_records_why_each_bidder_bid_what_they_bid():
    s = run_series("first_price", BIDDERS, everyone("best_response"), rounds=2)
    for record in s.rounds:
        assert isinstance(record, RoundRecord)
        assert set(record.decisions) == set(VALUES)
        for i, decision in record.decisions.items():
            assert decision.why.endswith(".")
            assert s.summary["bid_paths"][i][record.round] == decision.bid


def test_series_is_json_safe():
    s = run_series("first_price", BIDDERS, everyone("best_response"), rounds=2)
    assert isinstance(s, Series)
    payload = json.loads(json.dumps(s.to_dict()))
    assert payload["mechanism"] == "first_price"
    assert len(payload["rounds"]) == 2
    assert payload["rounds"][1]["decisions"]["A"]["considered"]
    assert payload["summary"]["converged"] in (True, False)


# ---------------------------------------------------------------------- validation


def test_unknown_strategy_is_rejected():
    with pytest.raises(ValueError, match="unknown strategy"):
        run_series("first_price", BIDDERS, everyone("psychic"))


@pytest.mark.parametrize("rounds", [0, -1, 51, 1.5, True, "8"])
def test_rounds_out_of_range_is_rejected(rounds):
    with pytest.raises(ValueError, match="rounds"):
        run_series("first_price", BIDDERS, everyone("manual"), rounds=rounds)


def test_a_strategy_for_a_bidder_who_is_not_playing_is_rejected():
    with pytest.raises(ValueError, match="Z"):
        run_series("first_price", BIDDERS, {"Z": {"name": "truthful"}})


def test_a_bidder_without_a_strategy_falls_back_to_their_typed_bid():
    s = run_series("first_price", BIDDERS, {"A": {"name": "truthful"}}, rounds=1)
    assert settled(s) == {"A": 100, "B": 72, "C": 41}


def test_strategy_params_are_validated_against_the_registry():
    with pytest.raises(ValueError, match="unknown parameter"):
        run_series("first_price", BIDDERS, everyone("best_response", nudge=3))


# ------------------------------------------------ the privacy rule, end to end


def test_a_best_responder_ignores_a_rival_value_it_cannot_see():
    """Same rival bids, different rival value: the decisions must be identical."""

    def series(rival_value):
        players = [Bidder("A", 100, 95), Bidder("B", rival_value, 50)]
        return run_series(
            "first_price",
            players,
            {"A": {"name": "best_response"}, "B": {"name": "manual"}},
            rounds=4,
        )

    rich, poor = series(777), series(5)
    assert rich.summary["bid_paths"] == poor.summary["bid_paths"]
    assert [r.decisions["A"].why for r in rich.rounds] == [
        r.decisions["A"].why for r in poor.rounds
    ]


# --------------------------------------- GSP best-response dynamics (a real cycle)
#
# `best_response` scores candidates by *running* the mechanism, so GSP got a
# best-responding bidder the moment it was registered, with no new strategy code. What
# came out is the behaviour GSP is known for: with as many slots as bidders there is no
# bid profile everybody is happy to repeat, and the series oscillates forever. These
# tests pin the direction of that — that it cycles rather than settles or stalls — and
# deliberately not the exact numbers, which are an artefact of one bid profile.


def positions(mechanism, slots, rounds=10, bidders=BIDDERS):
    plan = {b.id: {"name": "best_response"} for b in bidders}
    return run_series(mechanism, bidders, plan, rounds=rounds, params={"slots": slots})


def profiles(series):
    """One tuple per round: who bid what, so repeats are comparable."""
    paths = series.summary["bid_paths"]
    return [
        tuple(sorted((who, path[r]) for who, path in paths.items()))
        for r in range(len(series.rounds))
    ]


def test_best_response_actually_drives_gsp():
    """The seam first: every bidder from round 1 on really did score candidate bids under
    GSP. A strategy that silently fell back on the typed bid would produce a flat, stable
    series that looks like convergence and means nothing."""
    series = positions("gsp", slots=3)
    for record in series.rounds[1:]:
        for who, decision in record.decisions.items():
            assert decision.considered, f"round {record.round}: {who} scored nothing"


def test_gsp_best_response_cycles_instead_of_settling():
    """Three bidders, three slots: dropping a slot on purpose is a profitable reply to
    honest rivals, and bidding honestly is a profitable reply to that, so the series
    walks back into a profile it has already played and never leaves."""
    series = positions("gsp", slots=3)
    seen = profiles(series)

    assert series.summary["converged"] is False
    assert series.summary["converged_round"] is None
    assert seen[-1] in seen[:-1], "a cycle revisits a profile; a drift does not"
    assert len(set(seen)) < len(seen) - 2, "it is a short cycle, not a long wander"

    top = series.summary["bid_paths"]["A"]
    assert min(top) < max(top), "the top bidder moves"
    assert any(a > b for a, b in zip(top, top[1:])), "and moves down as well as up"
    assert any(a < b for a, b in zip(top, top[1:]))


def test_vcg_best_response_settles_on_bidding_your_value():
    """The same bidders, the same slots, the same strategy: the truthful payment rule
    turns the cycle into a fixed point at the values themselves."""
    series = positions("vcg_positions", slots=3)
    assert series.summary["converged"] is True
    assert settled(series) == VALUES


def test_gsp_best_response_can_settle_below_your_value():
    """GSP does not always cycle. With fewer slots than bidders this profile settles —
    but it settles with the highest-value bidder sitting *under* a rival on purpose,
    paying less for fewer clicks, which is the untruthfulness made visible."""
    series = positions("gsp", slots=2)
    assert series.summary["converged"] is True
    assert settled(series)["A"] < VALUES["A"]
    assert series.rounds[-1].trace.result["allocation"]["A"] == 1
