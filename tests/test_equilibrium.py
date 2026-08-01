import json
import random

import pytest

from agt.equilibrium import (
    MAX_PROFILES,
    MAX_REPORTED,
    analyse,
    best_response_curve,
    bid_grid,
    dominance,
    payoff_table,
    pure_nash,
    refuse_grid_search,
)
from agt.mechanisms import REGISTRY, run
from agt.revenue import SYMMETRIC_BNE, compare, revenue_equivalence
from agt.trace import Bidder

# Two bidders keeps every profile space small enough to reason about by hand, which is what
# a test asserting "these are the equilibria" needs.
PAIR = [Bidder("A", 100, 60), Bidder("B", 80, 50)]
SCALAR = [name for name, spec in REGISTRY.items() if spec.input_kind == "single"]


def game(mechanism, bidders=PAIR, draws=20, **kwargs):
    return analyse(mechanism, bidders, draws=draws, **kwargs)["game"]


def bids_at(equilibrium):
    return equilibrium["bids"]


# --------------------------------------------------------------------------- the grid


def test_the_grid_carries_every_bidders_own_value():
    """Without this the search cannot answer the one question the phase exists to ask."""
    grid = bid_grid(PAIR, steps=9)
    assert 100 in grid and 80 in grid
    assert grid[0] == 0, "not bidding is a strategy, so 0 is always on the grid"
    assert grid == sorted(grid) and len(grid) == len(set(grid))


def test_a_value_is_never_dropped_as_a_near_duplicate_of_a_spaced_point():
    """A value landing next to an even step must survive; the step is what gives way."""
    nearly = [Bidder("A", 100, 10), Bidder("B", 50.0000000001, 10)]
    grid = bid_grid(nearly, steps=3)  # even steps would be 0, 50, 100
    assert 50.0000000001 in grid
    assert grid.count(50) == 0


def test_a_table_of_zero_values_still_produces_a_grid():
    assert bid_grid([Bidder("A", 0, 0)], steps=9) == [0.0]


# -------------------------------------------------------------------- the payoff table


def test_a_payoff_cell_is_a_real_run_of_the_real_mechanism():
    """The whole module rests on this: payoffs are measured, never derived."""
    grid = bid_grid(PAIR, steps=3)
    table = payoff_table("first_price", PAIR, grid, {})
    for profile, utilities in table.items():
        lineup = [Bidder(b.id, b.value, grid[i]) for b, i in zip(PAIR, profile)]
        assert utilities == run("first_price", lineup, {}).result["utilities"]


def test_the_table_covers_the_whole_profile_space():
    grid = bid_grid(PAIR, steps=4)
    assert len(payoff_table("second_price", PAIR, grid, {})) == len(grid) ** 2


# ------------------------------------------------------------------ best-response curve


def test_second_price_reply_curve_is_a_plateau_up_to_value():
    """Your bid does not set your price, so every winning bid pays the same."""
    curve = best_response_curve("second_price", PAIR, 0, bid_grid(PAIR, 9), {})
    assert curve["plateau"] == [50, 100], "flat from the rival's bid to A's value"
    assert curve["truthful_is_best"] is True
    assert curve["best"] == 100
    winning = [p["utility"] for p in curve["points"] if p["bid"] >= 50]
    assert len(set(winning)) == 1, "a genuine plateau, not a slope"


def test_first_price_reply_curve_has_one_strict_peak_below_value():
    """Bid too little and you lose; bid too much and you overpay. Only one bid is right."""
    curve = best_response_curve("first_price", PAIR, 0, bid_grid(PAIR, 9), {})
    assert curve["best"] == 50
    assert curve["best"] < 100, "the peak is strictly below A's value"
    assert curve["truthful_is_best"] is False
    assert curve["plateau"] == [50, 50], "a single point, not a flat top"
    peak = curve["best_utility"]
    assert all(p["utility"] < peak for p in curve["points"] if p["bid"] != 50)


def test_the_reply_curve_holds_rivals_at_the_bids_actually_typed():
    curve = best_response_curve("first_price", PAIR, 0, bid_grid(PAIR, 5), {})
    assert curve["rivals"] == {"B": 50}


def test_a_reply_curve_is_a_slice_and_says_so():
    curve = best_response_curve("second_price", PAIR, 0, bid_grid(PAIR, 5), {})
    assert "not an equilibrium" in curve["why"]


# -------------------------------------------------------------- what Nash does not promise


def test_truthful_is_a_nash_equilibrium_of_second_price():
    nash = game("second_price")["nash"]
    assert nash["truthful_is_nash"] is True
    truthful = [e for e in nash["shown"] if e["truthful"]]
    assert bids_at(truthful[0]) == {"A": 100, "B": 80}


def test_truthful_is_not_the_only_nash_equilibrium_of_second_price():
    """The lesson: Nash rules out unilateral regret and nothing else.

    An equilibrium in which the bidder who values the item least takes it, propped up by a
    bid nobody would really make, survives the definition intact. Somebody who has only
    ever heard "second-price is truthful" needs to see this to learn that the sentence is
    about dominance.
    """
    nash = game("second_price")["nash"]
    assert nash["count"] > 1
    assert any(not e["truthful"] for e in nash["shown"])
    assert "rules out unilateral regret and nothing else" in nash["why"]


def test_second_price_has_a_nash_equilibrium_the_low_value_bidder_wins():
    """B bids above A's value, A cannot profitably outbid it, and B takes the item."""
    nash = game("second_price")["nash"]
    stolen = [
        e for e in nash["shown"] if e["winner"] == "B" and not e["efficient"]
    ]
    assert stolen, "the famous embarrassing equilibrium must not be filtered out"
    assert stolen[0]["bids"]["B"] >= 100 > stolen[0]["bids"]["A"]


def test_all_pay_has_no_pure_equilibrium_and_says_why():
    """Not a gap in the search: its equilibrium is in mixed strategies, which is out of scope."""
    nash = game("all_pay")["nash"]
    assert nash["count"] == 0
    assert nash["shown"] == []
    assert "mixed strategies" in nash["why"]


def test_first_price_equilibria_are_bid_ties_not_truthful_bids():
    nash = game("first_price")["nash"]
    assert nash["truthful_is_nash"] is False
    assert nash["count"] > 0
    for equilibrium in nash["shown"]:
        assert equilibrium["bids"]["A"] == equilibrium["bids"]["B"]


def test_a_deviation_that_only_ties_does_not_break_an_equilibrium():
    """Strictly better, or it is not an improvement — which is what keeps weakly dominated
    equilibria visible instead of quietly filtered out."""
    grid = bid_grid(PAIR, 9)
    table = payoff_table("second_price", PAIR, grid, {})
    found = pure_nash(PAIR, grid, table)
    truthful = (grid.index(100), grid.index(80))
    assert truthful in found


def test_the_equilibrium_list_is_cut_but_the_count_is_not():
    """A truncated list that looked complete would turn the lesson into its opposite."""
    nash = game("second_price", [Bidder(chr(65 + i), 100 - 5 * i, 50) for i in range(3)])[
        "nash"
    ]
    assert nash["count"] > MAX_REPORTED
    assert len(nash["shown"]) == MAX_REPORTED
    assert str(nash["count"]) in nash["why"]


# ------------------------------------------------------------------------- dominance


def test_second_price_truthfulness_is_dominant_on_the_grid():
    report = game("second_price")["dominance"]
    assert all(d["truthful_dominant"] for d in report.values())
    assert all(d["counterexample"] is None for d in report.values())
    assert "honesty earns at least as much as anything else" in report["A"]["why"]


def test_first_price_truthfulness_is_refuted_by_an_asserted_deviation():
    """Phase 3's rule: an untruthful mechanism is pinned by a constructed profitable
    deviation, never by being left off a list. A mechanism that was silently truthful
    would be wrong, and only this catches it."""
    report = game("first_price")["dominance"]
    assert report["A"]["truthful_dominant"] is False
    counter = report["A"]["counterexample"]
    assert counter["gain"] > 0
    assert counter["deviation_utility"] > counter["honest_utility"]

    # and the deviation is real: replay it through the mechanism itself.
    lineup = [Bidder("A", 100, counter["bid"]), Bidder("B", 80, counter["rivals"]["B"])]
    honest = [Bidder("A", 100, 100), Bidder("B", 80, counter["rivals"]["B"])]
    got = run("first_price", lineup, {}).result["utilities"]["A"]
    instead = run("first_price", honest, {}).result["utilities"]["A"]
    assert got - instead == pytest.approx(counter["gain"])


def test_a_question_that_could_not_be_asked_is_not_answered_yes():
    """The one way this search can assert something false.

    A Dutch clock started below a bidder's value forbids that bidder from ever naming it,
    so every profile in which it bids honestly is refused. "Nothing beat honesty" is then
    vacuously true and means nothing — it was never on the board. The verdict has to be
    ``None``, because the alternative is a mechanism that declares itself untruthful being
    reported as dominant.
    """
    table = game("dutch", params={"start": 70})
    assert REGISTRY["dutch"].truthful_dominant is False
    for who, verdict in table["dominance"].items():
        assert verdict["tested"] == 0, who
        assert verdict["truthful_dominant"] is None, who
        assert "not a move this mechanism will accept" in verdict["why"]


def test_a_verdict_says_how_many_profiles_it_actually_rests_on():
    verdict = game("second_price")["dominance"]["A"]
    grid = game("second_price")["grid"]
    assert verdict["tested"] == len(grid), "one per rival bid, all of them legal here"
    assert str(verdict["tested"]) in verdict["why"]


@pytest.mark.parametrize("name", SCALAR)
def test_the_grid_verdict_agrees_with_the_declared_truthful_dominant_flag(name):
    """The cross-check that makes the whole registry honest.

    ``truthful_dominant`` is a claim each mechanism makes about itself, and the strategy
    explanations a learner reads are built from it. Here the claim is tested against an
    exhaustive search of the mechanism's own payoffs. A mechanism that declared the wrong
    answer fails here rather than quietly teaching the wrong lesson.
    """
    report = game(name)["dominance"]
    measured = all(d["truthful_dominant"] for d in report.values())
    assert measured is REGISTRY[name].truthful_dominant


def test_gsp_untruthfulness_is_a_slot_downgrade_that_pays():
    """The phase 3 lesson, rediscovered by search rather than restated."""
    counter = game("gsp")["dominance"]["A"]["counterexample"]
    assert counter is not None and counter["gain"] > 0
    assert counter["bid"] < counter["honest_bid"], "A wins a cheaper slot on purpose"


# ------------------------------------------------------------------ bounded before it starts


def test_the_search_is_capped_and_never_exceeds_the_cap():
    for n in (2, 3, 4, 5):
        bidders = [Bidder(chr(65 + i), 100 - 7 * i, 50) for i in range(n)]
        table = game("second_price", bidders)
        assert table["profiles"] <= MAX_PROFILES


def test_a_grid_too_big_to_search_coarsens_and_says_so():
    bidders = [Bidder(chr(65 + i), 100 - 7 * i, 50) for i in range(4)]
    result = analyse("second_price", bidders)
    assert result["game"]["searched"] is True
    assert result["game"]["steps"] < result["best_response"]["steps"]
    assert "coarsened" in result["game"]["note"]
    # the curves keep the fine grid they can afford
    assert len(result["best_response"]["grid"]) > len(result["game"]["grid"])


def test_a_table_too_big_to_search_at_all_still_returns_the_other_two_analyses():
    """Silence would be the real error: a missing equilibrium table reads as "there are none"."""
    bidders = [Bidder(chr(65 + i), 100 - 7 * i, 50) for i in range(7)]
    result = analyse("second_price", bidders, draws=20)
    assert result["game"]["searched"] is False
    assert result["game"]["nash"] is None
    assert "No equilibrium search" in result["game"]["note"]
    assert result["best_response"]["curves"]["A"]["points"], "curves are unaffected"
    assert result["revenue_equivalence"]["symmetric"]["mechanisms"]


# ------------------------------------------------------------------- revenue equivalence


def test_symmetric_values_make_every_mechanism_raise_the_same_revenue():
    """Three payment rules that look nothing alike, one expected revenue."""
    report = compare([100, 100, 100], draws=600, seed=0)
    assert report["symmetric"] is True
    assert report["theory"] == pytest.approx(50)  # H(n-1)/(n+1)
    for name, scored in report["mechanisms"].items():
        assert scored["agrees"] is True, name
        assert scored["mean"] == pytest.approx(50, abs=2)


def test_exactly_one_row_is_marked_as_the_baseline():
    """The renderer cannot work it out from a difference of zero — the clock auctions sit
    at exactly zero too — and working it out would mean hard-coding a mechanism name."""
    scored = compare([100, 100], draws=20, seed=0)["mechanisms"]
    assert [name for name, row in scored.items() if row["baseline"]] == ["second_price"]
    assert scored["english"]["difference"] == 0, "identical, but still not the baseline"


def test_english_is_second_price_and_dutch_is_first_price_draw_for_draw():
    """Not equal on average — *identical*. They are the same two auctions in a costume."""
    report = compare([100, 100, 100], draws=200, seed=3)["mechanisms"]
    assert report["english"]["identical"] is True
    assert report["dutch"]["difference"] == pytest.approx(
        report["first_price"]["difference"]
    )


def test_asymmetric_values_break_revenue_equivalence():
    """The hypothesis failed, not the theorem — and the report says which one."""
    report = compare([100, 60, 30], draws=600, seed=0)
    assert report["symmetric"] is False
    assert report["theory"] is None, "the closed form assumes identical distributions"
    first = report["mechanisms"]["first_price"]
    assert first["agrees"] is False
    assert abs(first["difference"]) > 5 * first["difference_stderr"]
    assert "no longer an equilibrium of this market" in report["why"]


def test_second_price_collects_the_second_highest_value_however_lopsided_the_market():
    """Truthful bidding stays dominant whatever anybody's values look like."""
    for highs in ([100, 100, 100], [100, 60, 30]):
        report = compare(highs, draws=400, seed=1)
        assert report["mechanisms"]["second_price"]["difference"] == 0


def test_the_comparison_names_the_mechanisms_it_had_to_leave_out():
    """A table showing five of nine with no note reads as a claim about all nine."""
    excluded = compare([100, 100], draws=10, seed=0)["excluded"]
    assert set(excluded) == set(REGISTRY) - set(SYMMETRIC_BNE)
    assert all(reason for reason in excluded.values())


def test_an_all_equal_table_skips_the_asymmetric_run_rather_than_repeating_itself():
    report = revenue_equivalence([Bidder("A", 50, 10), Bidder("B", 50, 10)], draws=20)
    assert report["asymmetric"] is None
    assert "different values" in report["skipped"]


def test_the_check_is_seeded_and_never_touches_the_global_random_module():
    """A series is reproducible from its seed only if nothing reaches for global state."""
    before = random.getstate()
    first = compare([100, 60], draws=50, seed=11)
    assert random.getstate() == before
    assert compare([100, 60], draws=50, seed=11) == first
    assert compare([100, 60], draws=50, seed=12) != first


# ------------------------------------------------------------------------ the whole answer


def test_package_mechanisms_are_refused_by_name_and_reason():
    with pytest.raises(ValueError, match="no single number to vary"):
        refuse_grid_search("vcg_package")
    with pytest.raises(ValueError, match="different game"):
        analyse("greedy_package", PAIR)


def test_an_unknown_mechanism_names_the_ones_that_exist():
    with pytest.raises(ValueError, match="unknown mechanism"):
        analyse("third_price", PAIR)


def test_mechanism_params_reach_the_analysis():
    result = analyse("second_price", PAIR, params={"reserve": 90}, draws=20)
    assert result["params"] == {"reserve": 90}
    # A reserve of 90 prices B out entirely, so B can never win and never pays.
    assert all(p["utility"] <= 0 for p in result["best_response"]["curves"]["B"]["points"])


def test_the_analysis_is_json_serializable():
    """It is a wire format before it is anything else."""
    json.dumps(analyse("first_price", PAIR, draws=20))


def test_the_whole_analysis_is_reproducible():
    assert analyse("first_price", PAIR, draws=50) == analyse("first_price", PAIR, draws=50)
