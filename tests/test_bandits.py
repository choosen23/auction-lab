"""Two learners on one grid of bid multipliers, and the regret that separates them.

The market is the one `tests/test_pacing.py` uses, and for the same reason: B and C are a
standing pair of fixed bids clearing at 15, and A's values are drawn from [40, 100]. That
makes the arms cleanly rankable by hand, which is what turns "it converged" into a claim
about a *particular* multiplier rather than a claim about whatever it happened to settle
on:

* arm 0.4 bids 0.4 x value, which is 16 or more for every draw, so it wins every round
  and keeps 0.6 of the value. Expected utility about 42.
* arm 0.2 bids 0.2 x value, which clears 15 only when the draw is 75 or better — about
  five rounds in twelve — and keeps 0.8 when it does. Expected utility about 29.
* arm 0.6 wins everything but keeps only 0.4. Expected utility about 28.
* arm 0.8 keeps 0.2 (about 14) and arm 1.0 bids the value away for nothing.

So the best fixed arm is 0.4, the two next-best are close to each other and clearly below
it, and a learner that has not learned anything is easy to spot.
"""

import random

import pytest

from agt.bandits import grid
from agt.series import run_series
from agt.trace import Bidder
from agt.world import World

BIDDERS = [Bidder("A", 100, 100), Bidder("B", 100, 10), Bidder("C", 100, 15)]

# The arithmetic in the module docstring is for the default five-arm grid.
BEST_ARM = 0.4

LEARNERS = ["bandit_epsilon", "bandit_ucb"]


def day(
    name,
    params=None,
    rounds=40,
    seed=1,
    mechanism="first_price",
    low=40,
    high=100,
    budget=None,
    scale=1,
):
    """One day of impressions, A learning against a steady market.

    ``scale`` multiplies every number in the market — the fixed bids, the value range, the
    budget. It is a change of currency and nothing else, and the two tests that use it are
    the ones asking whether a learning rule notices what its money is called.
    """
    world = World(
        rounds=rounds,
        seed=seed,
        value_low=low * scale,
        value_high=high * scale,
        budgets={} if budget is None else {"A": budget * scale},
    )
    plan = {
        "A": {"name": name, "params": dict(params or {})},
        "B": {"name": "manual"},
        "C": {"name": "manual"},
    }
    table = [Bidder(b.id, b.value * scale, b.bid * scale) for b in BIDDERS]
    return run_series(mechanism, table, plan, world=world)


def arms_played(series, who="A"):
    """The multiplier the learner pulled each round, read off its control variable."""
    return [r.decisions[who].control["value"] for r in series.rounds]


def regret(series, who="A"):
    return series.summary["regret"][who]


def modal(values):
    return max(set(values), key=values.count)


def mean(xs):
    return sum(xs) / len(xs)


# ------------------------------------------------------------------------ the grid


def test_the_grid_is_evenly_spaced_multipliers_up_to_bidding_the_whole_value():
    assert grid(5) == (0.2, 0.4, 0.6, 0.8, 1.0)
    assert grid(2) == (0.5, 1.0)


def test_both_learners_choose_from_the_same_grid():
    """The comparison is only a comparison if the two are choosing between the same
    things, so this is asserted rather than assumed."""
    a = set(arms_played(day("bandit_epsilon", {"epsilon": 1})))
    b = set(arms_played(day("bandit_ucb")))
    assert a == b == set(grid(5))


# -------------------------------------------------------------- the opening sweep


@pytest.mark.parametrize("name", LEARNERS)
def test_both_learners_pull_every_arm_once_before_settling(name):
    """UCB needs it — an arm never played has no mean and an unbounded bonus — and giving
    epsilon-greedy the identical opening is what makes the regret comparison a comparison
    of exploration *rules* rather than of who got the luckier start."""
    arms = grid(5)
    opening = arms_played(day(name))[: len(arms)]
    assert sorted(opening) == sorted(arms)


@pytest.mark.parametrize("name", LEARNERS)
def test_the_opening_sweep_says_it_is_a_sweep(name):
    why = day(name).rounds[0].decisions["A"].why.lower()
    assert "once" in why or "never played" in why or "no observation" in why


# ------------------------------------------------------------------- convergence


@pytest.mark.parametrize("name", LEARNERS)
def test_both_converge_on_the_best_arm_in_a_stationary_market(name):
    """Judged on the last third of the day, after the opening sweep is long past."""
    series = day(name, rounds=45)
    tail = arms_played(series)[-15:]
    assert modal(tail) == BEST_ARM
    assert tail.count(BEST_ARM) / len(tail) > 0.6
    assert series.summary["regret_baseline"]["A"]["arm"] == BEST_ARM


@pytest.mark.parametrize("name", LEARNERS)
def test_a_learner_beats_bidding_its_whole_value_away(name):
    """The floor a learner has to clear: arm 1.0 is on the grid, and under first-price
    rules bidding your value hands the entire surplus to the seller."""
    learned = day(name, rounds=45).summary["cumulative_utilities"]["A"]
    truthful = day("truthful", rounds=45).summary["cumulative_utilities"]["A"]
    assert learned > truthful


# ------------------------------------------------------------------------ regret


def test_regret_is_the_best_fixed_arm_in_hindsight_minus_what_was_earned():
    """The baseline has to be a number somebody can check, so check it: the final
    cumulative regret is exactly the hindsight arm's total minus the realised total."""
    series = day("bandit_ucb", rounds=30)
    baseline = series.summary["regret_baseline"]["A"]
    assert baseline["rule"] == "best fixed arm in hindsight"
    assert regret(series)[-1] == pytest.approx(baseline["utility"] - baseline["earned"])
    assert baseline["earned"] == pytest.approx(
        series.summary["cumulative_utilities"]["A"]
    )


def test_the_hindsight_arm_really_is_the_best_of_the_arms_on_offer():
    series = day("bandit_ucb", rounds=30)
    baseline = series.summary["regret_baseline"]["A"]
    assert baseline["arm"] in baseline["arms"]
    assert baseline["utility"] == pytest.approx(max(baseline["arm_utilities"]))


@pytest.mark.parametrize("name", LEARNERS)
def test_cumulative_regret_ends_non_negative(name):
    """A learner cannot beat the best fixed arm in hindsight over the whole day — that is
    what "in hindsight" means — so the curve may dip but has to end at or above zero."""
    assert regret(day(name, rounds=30))[-1] >= -1e-9


def test_a_table_of_non_learners_has_no_regret_series_at_all():
    """Regret is defined against a grid of arms, and a bidder without one has no regret
    to report. A chart appears because the summary carries a series, never because of a
    strategy name, so the honest answer for a table with no learner in it is nothing."""
    series = day("truthful")
    assert series.summary["regret"] == {}
    assert series.summary["regret_baseline"] == {}


def test_regret_is_reported_for_the_learner_and_nobody_else():
    series = day("bandit_ucb")
    assert set(series.summary["regret"]) == {"A"}
    assert len(regret(series)) == len(series.rounds)


# ---------------------------------------------- the comparison the task exists for


SEEDS = range(1, 11)


def final_regret(name, seed, rounds=45, params=None, scale=1):
    return regret(day(name, params, rounds=rounds, seed=seed, scale=scale))[-1] / scale


def test_ucb_carries_less_regret_than_epsilon_greedy_averaged_over_seeds():
    """Ten seeds, not one lucky run: UCB 127.9, epsilon-greedy 209.7.

    epsilon-greedy explores by flipping a coin forever, so it keeps paying the same
    exploration tax on the last round of the day that it paid on the first. UCB stops.

    Read the next two tests before believing the headline, though — they measure *why*
    it wins here, and the answer is not the one the formula advertises.
    """
    ucb = [final_regret("bandit_ucb", seed) for seed in SEEDS]
    greedy = [final_regret("bandit_epsilon", seed) for seed in SEEDS]
    assert mean(ucb) < mean(greedy), (
        f"UCB mean {mean(ucb):.1f} vs epsilon-greedy mean {mean(greedy):.1f}"
    )


def test_ucb_wins_here_by_not_exploring_rather_than_by_exploring_well():
    """The honest half of the headline, and the reason it is asserted rather than left in
    a docstring.

    On this market the confidence bonus is worth about 1 where the arms' averages are 13
    apart, so it changes the chosen arm on 0.1 rounds in 45, averaged over twenty seeds.
    Strip it out entirely — epsilon = 0, pure greedy — and the regret is the same to within
    a percent: 127.9 against 129.0 over ten seeds. UCB is beating epsilon-greedy because a
    fixed exploration rate never stops charging rent, not because optimism is finding it
    anything.
    """
    ucb = [final_regret("bandit_ucb", seed) for seed in SEEDS]
    greedy = [final_regret("bandit_epsilon", seed, params={"epsilon": 0}) for seed in SEEDS]
    assert abs(mean(ucb) - mean(greedy)) < 0.05 * mean(greedy), (
        f"UCB {mean(ucb):.1f} vs pure greedy {mean(greedy):.1f}"
    )


def test_epsilon_greedy_does_not_care_what_its_money_is_called():
    """The control for the test below. epsilon-greedy only ever compares averages with each
    other, so dividing every number in the market by 100 divides its regret by 100 and
    changes nothing else about what it does."""
    big = [final_regret("bandit_epsilon", seed) for seed in SEEDS]
    small = [final_regret("bandit_epsilon", seed, scale=0.01) for seed in SEEDS]
    assert mean(small) == pytest.approx(mean(big), rel=1e-9)


def test_ucbs_confidence_bonus_is_denominated_in_the_units_of_the_reward():
    """The same rule, the same market, the money renamed — and the result inverts.

    ``sqrt(2 ln t / n)`` is an absolute number and UCB1's constant of 2 is derived for
    rewards on [0, 1]. Run the identical market with every number divided by 100 and the
    bonus, unchanged, now dwarfs the gaps between the arms: UCB round-robins the whole day
    and loses to the coin it beat a moment ago. Measured over ten seeds in comparable
    units, mean final regret:

        values 40-100   UCB 127.9   epsilon-greedy 209.7
        values 0.4-1.0  UCB 762.7   epsilon-greedy 209.7

    This is not a defect in the implementation, it is the assumption of the bound showing
    through, and it is the thing to know before trusting a UCB on a reward you have not
    normalized.
    """
    big = [final_regret("bandit_ucb", seed) for seed in SEEDS]
    small = [final_regret("bandit_ucb", seed, scale=0.01) for seed in SEEDS]
    coin = [final_regret("bandit_epsilon", seed, scale=0.01) for seed in SEEDS]
    assert mean(small) > 4 * mean(big), (
        f"rescaled UCB {mean(small):.1f} against {mean(big):.1f}"
    )
    assert mean(small) > mean(coin), "on rewards it was written for, UCB loses to the coin"


def test_ucb_stops_converging_once_its_bonus_outweighs_the_gaps():
    """The other face of the same measurement: a learner that never stops exploring never
    settles either. Over twenty seeds, UCB spends 0.32 of its last fifteen rounds on the
    best arm at the rescaled market against 1.00 at the original scale."""
    tail = arms_played(day("bandit_ucb", rounds=45, scale=0.01))[-15:]
    assert tail.count(BEST_ARM) / len(tail) < 0.6
    assert len(set(tail)) >= 4, "it is still sweeping the whole grid on the last day"


def test_a_coin_that_always_explores_is_the_worst_of_the_lot():
    """epsilon = 1 never uses anything it learned, which is the control case for the
    whole idea: it fixes the exploration rate at everything and never stops paying."""
    blind = [final_regret("bandit_epsilon", seed, params={"epsilon": 1}) for seed in SEEDS]
    tuned = [final_regret("bandit_epsilon", seed) for seed in SEEDS]
    assert mean(tuned) < mean(blind)


# ------------------------------------------------------------------- reproducibility


@pytest.mark.parametrize("name", LEARNERS)
def test_a_learner_replays_exactly_under_a_fixed_seed(name):
    assert arms_played(day(name, seed=3)) == arms_played(day(name, seed=3))
    assert arms_played(day(name, seed=3)) != arms_played(day(name, seed=4))


@pytest.mark.parametrize("name", LEARNERS)
def test_a_learner_never_touches_the_global_random_module(name):
    random.seed(99)
    before = random.getstate()
    day(name)
    assert random.getstate() == before


# ----------------------------------------------------- what a chart gets to read


@pytest.mark.parametrize("name", LEARNERS)
def test_the_chosen_arm_is_published_where_a_chart_can_read_it(name):
    knob = day(name).rounds[-1].decisions["A"].control
    assert knob["name"] == "arm"
    assert knob["label"]
    assert knob["value"] in grid(5)
    assert knob["direction"] in {"up", "down", "steady"}


@pytest.mark.parametrize("name", LEARNERS)
def test_every_arm_is_weighed_in_the_open_and_carries_its_own_count_and_mean(name):
    considered = day(name).rounds[-1].decisions["A"].considered
    assert [c["arm"] for c in considered] == list(grid(5))
    assert sum(c["pulls"] for c in considered) == len(day(name).rounds) - 1
    for entry in considered:
        assert entry["bid"] == pytest.approx(entry["arm"] * 100) or entry["bid"] >= 0
        assert isinstance(entry["mean"], float)


def test_ucb_publishes_the_confidence_bonus_it_decided_on():
    considered = day("bandit_ucb").rounds[-1].decisions["A"].considered
    assert all("bonus" in c and "index" in c for c in considered)
    for entry in considered:
        assert entry["index"] == pytest.approx(entry["mean"] + entry["bonus"])


def test_epsilon_greedy_has_no_bonus_because_it_has_no_such_idea():
    considered = day("bandit_epsilon").rounds[-1].decisions["A"].considered
    assert all("bonus" not in c for c in considered)


# ------------------------------------------------------------------ the explanations


def bonus_decided(series):
    """Rounds where the arm with the best index is not the arm with the best average."""
    return [
        r.decisions["A"]
        for r in series.rounds
        if r.decisions["A"].considered
        and max(r.decisions["A"].considered, key=lambda c: c["index"])["arm"]
        != max(r.decisions["A"].considered, key=lambda c: c["mean"])["arm"]
    ]


def test_ucb_shows_the_bonus_doing_its_job_on_the_rounds_it_does_it():
    """The point of UCB is not which arm won, it is *why*: on some rounds the arm with the
    best average loses to one the loop knows less about, and those rounds have to be
    readable as such or the chart shows a learner changing its mind for no stated reason.

    Measured on the rescaled market, because that is where the bonus is on the scale its
    constant assumes and therefore where it actually decides anything — 27.1 rounds in 45
    against 0.1 in 45 at the original scale, averaged over twenty seeds.
    """
    persuaded = bonus_decided(day("bandit_ucb", rounds=45, scale=0.01))
    assert persuaded, "a run where the bonus never decides anything proves nothing"
    for decision in persuaded:
        why = decision.why.lower()
        assert "bonus is what decided this round" in why
        assert "average" in why
        assert f"{decision.control['value']:g} arm" in why


def test_ucb_says_so_on_the_rounds_the_bonus_decided_nothing():
    """The common case at this scale, and the one a reader is most likely to misread: the
    sentence has to say the bonus lost rather than quietly implying it won."""
    series = day("bandit_ucb", rounds=45)
    settled = [
        r.decisions["A"] for r in series.rounds if r.decisions["A"] not in bonus_decided(series)
    ]
    quiet = [d for d in settled if d.considered and any(c["pulls"] for c in d.considered)]
    assert quiet
    for decision in quiet[1:]:  # the opening sweep has no averages to compare yet
        if "sweep" in decision.why or "never played" in decision.why:
            continue
        assert "decided nothing" in decision.why
        assert "absolute number" in decision.why


def test_epsilon_greedy_says_which_way_the_coin_fell():
    whys = [r.decisions["A"].why.lower() for r in day("bandit_epsilon", rounds=45).rounds]
    assert any("at random" in why for why in whys), "it has to explore sometimes"
    assert any("best average" in why for why in whys), "and exploit the rest of the time"


@pytest.mark.parametrize("name", LEARNERS)
def test_a_learner_names_the_arm_it_pulled_and_the_bid_it_became(name):
    decision = day(name).rounds[-1].decisions["A"]
    assert f"{decision.control['value']:g}" in decision.why
    assert decision.why.endswith(".")


# ------------------------------------------------------------------ money running out


@pytest.mark.parametrize("name", LEARNERS)
def test_a_learner_with_no_money_left_sits_out_rather_than_bidding_zero(name):
    """A 0 is a real bid: it clears a reserve of 0 and can win the item for nothing. A
    learner that cannot afford its arm did not enter, and says so."""
    series = day(name, budget=120, rounds=20)
    silent = [r.decisions["A"] for r in series.rounds if r.decisions["A"].abstain]
    assert silent, "a 120 budget against 40-plus values has to bind"
    assert all(d.bid > 0 for d in silent), "it still records what it wanted to bid"
    assert all(d.control is not None for d in silent), "and the arm stays plottable"


@pytest.mark.parametrize("name", LEARNERS)
def test_a_round_it_sat_out_teaches_it_nothing(name):
    """No bid, no outcome, no observation. The counts have to reflect that rather than
    booking a zero reward against an arm that was never actually played."""
    series = day(name, budget=120, rounds=20)
    played = sum(not r.decisions["A"].abstain for r in series.rounds[:-1])
    considered = series.rounds[-1].decisions["A"].considered
    assert sum(c["pulls"] for c in considered) == played
