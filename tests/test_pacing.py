"""Budget pacing: four bidders with the same money, differing only in how they spread it.

A budget turns bidding into a *sequencing* problem, and these tests exist to make the
sequencing visible rather than to check arithmetic. Each one is a claim about a spend
curve: that `budget_blind` goes quiet with the day half unspent, that mu falls when the
budget binds and climbs to 1 when it does not, and that `throttle` reaches the same spend
as pacing by buying a worse basket.

The world every test runs in draws values in **[40, 100]**, not [0, 100], and the bound
away from zero is load-bearing for one claim: once a spent-out bidder has less than 40
left it cannot cover *any* draw, so its silent tail is genuinely sealed. With values that
can be tiny the tail leaks, and
:func:`test_budget_blind_still_picks_up_scraps_when_values_can_be_tiny` says so out loud
rather than letting the neater scenario imply something false.
"""

import pytest

from agt.series import run_series
from agt.trace import Bidder
from agt.world import World

# A is the bidder under test. B and C are *the market*: a standing pair of fixed bids that
# clears at 15, which is the exchange a pacing bidder actually buys from. A's values are
# drawn from [40, 100], so a bid of value x mu clears the market whenever value > 15/mu —
# which makes A's win rate a smooth, steadily increasing function of mu across the whole
# band from mu = 0.15 (it wins nothing) to mu = 0.375 (it wins everything). That smooth
# band is what makes the budget the thing being steered against.
#
# Rivals bidding their own fresh draws were tried first and are the wrong instrument. Under
# first-price rules a win against them costs more than their own top draw, so the win rate
# collapses from ~1 to ~0 over a few percent of mu: a bang-bang plant on which no gain
# converges, and one on which a loop's ringing would say nothing about pacing. (Raising the
# clamp on the correction was tried too, and changes nothing — the ratio rarely saturates.)
BIDDERS = [Bidder("A", 100, 100), Bidder("B", 100, 10), Bidder("C", 100, 15)]

# A budget that binds: A wins nearly every round at ~70 a time if it bids its value, so 400
# is about six impressions out of a thirty-round day.
BUDGET = 400


def day(
    name,
    params=None,
    budget=None,
    rounds=30,
    seed=1,
    mechanism="first_price",
    low=40,
    high=100,
):
    """One day of impressions, A running strategy ``name`` against a steady market.

    A's value each round comes from ``rng_for("value", "A", round)``, so two days run at
    the same seed hand A the *same* sequence of values whatever strategy it is playing.
    That is what makes `throttle` and `pace_multiplicative` comparable further down: the
    only difference between the two runs is the rule, not the luck.
    """
    world = World(
        rounds=rounds,
        seed=seed,
        value_low=low,
        value_high=high,
        budgets={} if budget is None else {"A": budget},
    )
    plan = {
        "A": {"name": name, "params": dict(params or {})},
        "B": {"name": "manual"},
        "C": {"name": "manual"},
    }
    return run_series(mechanism, BIDDERS, plan, world=world)


def abstentions(series, who="A"):
    return [r.decisions[who].abstain for r in series.rounds]


def wanted(series, who="A"):
    """What a bidder's strategy asked for each round, whether or not it entered."""
    return [r.decisions[who].bid for r in series.rounds]


def prices_paid(series, who="A"):
    return [
        r.trace.result["price"]
        for r in series.rounds
        if r.trace is not None and r.trace.result["winner"] == who
    ]


def spend(series, who="A"):
    return series.summary["spend"][who]


def control(series, who="A"):
    return [r.decisions[who].control for r in series.rounds]


def last_spending_round(series, who="A"):
    """The last round in which this bidder parted with any money."""
    path = spend(series, who)
    moved = [i for i, total in enumerate(path) if total > (path[i - 1] if i else 0)]
    return moved[-1] if moved else None


# ------------------------------------------------------------------- budget_blind
#
# The control case. Nothing it does in any single round is wrong; the mistake is that it
# never asks whether the day has more rounds in it.


def test_budget_blind_spends_out_before_the_last_round_and_then_goes_quiet():
    """The silent tail is the whole point, so assert that it exists and that it lasts."""
    s = day("budget_blind", budget=BUDGET)
    silent = abstentions(s)
    first = silent.index(True)
    assert first < len(silent) - 1, "a tail with nothing after it is not a tail"
    assert all(silent[first:]), "once the money cannot cover a draw it never bids again"
    assert not any(silent[:first]), "it bids on everything until the money is gone"


def test_budget_blind_stops_spending_with_most_of_the_day_still_to_run():
    s = day("budget_blind", budget=BUDGET)
    assert last_spending_round(s) < len(s.rounds) / 2


def test_budget_blind_sits_out_impressions_worth_more_than_ones_it_bought():
    """The expensive half of the mistake: the money went to whatever came first, and
    what came later was better."""
    s = day("budget_blind", budget=BUDGET)
    silent = abstentions(s)
    missed = [bid for bid, out in zip(wanted(s), silent) if out]
    bought = prices_paid(s)
    assert max(missed) > min(bought)


def test_budget_blind_never_abstains_when_there_is_no_budget_to_run_out_of():
    s = day("budget_blind")
    assert not any(abstentions(s))
    assert wanted(s) == [b.value for r in s.rounds for b in r.trace.bidders if b.id == "A"]


def test_budget_blind_still_picks_up_scraps_when_values_can_be_tiny():
    """Honesty about the model: `budget_blind` abstains when it cannot cover *this*
    round's value, not when some latch flips. With draws that reach down to zero, a
    bidder with 3 left still enters the round worth 2. The tail is quiet, not sealed."""
    s = day("budget_blind", budget=BUDGET, low=0, high=100, rounds=30, seed=4)
    silent = abstentions(s)
    first = silent.index(True)
    tail = silent[first:]
    assert not all(tail), "with tiny draws available, a spent-out bidder still nibbles"
    assert sum(tail) / len(tail) > 0.5, "but it is out far more often than it is in"


def test_budget_blind_explains_the_mistake_in_hindsight():
    s = day("budget_blind", budget=BUDGET)
    why = s.rounds[-1].decisions["A"].why.lower()
    assert "budget" in why
    assert "left" in why
    assert "pace" in why or "day" in why


def test_budget_blind_steers_nothing_and_publishes_no_control_variable():
    """It has no knob. A chart that drew one would be inventing a decision it never made."""
    s = day("budget_blind", budget=BUDGET)
    assert control(s) == [None] * len(s.rounds)


# ------------------------------------------------------------ pace_multiplicative
#
# mu is the lesson: a shadow price on the budget, appearing by feedback.


def test_pace_multiplicative_lowers_mu_when_the_budget_binds():
    s = day("pace_multiplicative", budget=BUDGET)
    path = [c["value"] for c in control(s)]
    assert path[0] == 0.5, "the first round has nothing to steer on yet"
    assert path[-1] < path[0], "a budget that binds pushes the shadow price up, mu down"


def test_pace_multiplicative_lets_mu_climb_to_its_ceiling_when_money_is_no_object():
    s = day("pace_multiplicative", budget=1_000_000)
    path = [c["value"] for c in control(s)]
    assert path[-1] == 1
    me = [b for b in s.rounds[-1].trace.bidders if b.id == "A"][0]
    assert me.bid == pytest.approx(me.value), "at mu = 1 pacing is plain truthful bidding"


def test_pace_multiplicative_with_no_budget_at_all_ends_up_truthful():
    """No budget is a budget that can never bind, so the same feedback answers the same
    way rather than by a special case."""
    s = day("pace_multiplicative")
    assert control(s)[-1]["value"] == 1


def test_pace_multiplicative_publishes_mu_where_a_chart_can_read_it():
    s = day("pace_multiplicative", budget=BUDGET)
    knob = s.rounds[-1].decisions["A"].control
    assert knob["name"] == "mu"
    assert knob["label"]
    assert 0 <= knob["value"] <= 1
    assert knob["direction"] in {"up", "down", "steady"}


def test_pace_multiplicative_says_what_mu_is_and_which_way_it_is_going():
    """mu *is* the lesson, so the sentence has to carry the number, not just the idea.

    Only the rounds the bidder actually entered: on a round the runner bars, the operative
    explanation is the runner's — that the budget would not cover the bid — and pasting
    mu's prose over that would bury the reason the bidder is sitting there silent.
    """
    entered = [r.decisions["A"] for r in day("pace_multiplicative", budget=BUDGET).rounds
               if not r.decisions["A"].abstain]
    assert entered, "the scenario has to leave some rounds actually bid in"
    for decision in entered:
        assert f"{decision.control['value']:.3f}" in decision.why
        assert decision.control["direction"] in decision.why


def test_pace_multiplicative_keeps_mu_visible_even_on_a_round_it_is_barred_from():
    """The runner may bar a bid the budget cannot cover. The control variable is what a
    chart plots, so it must not vanish on the rounds the bidder sat out."""
    s = day("pace_multiplicative", budget=60, rounds=12)
    assert any(abstentions(s)), "a 60 budget against 40-plus values has to bind"
    assert all(c is not None for c in control(s))


def test_a_bigger_step_moves_mu_further_in_one_round():
    """Measured on the size of a single nudge rather than on where mu ends up. A large
    step does not settle somewhere further away, it *hunts* — it swings past the mark and
    comes back, so its final value is a sample of the swing and says nothing on its own."""

    def biggest_jump(step):
        path = [c["value"] for c in control(day("pace_multiplicative", {"step": step}, budget=BUDGET))]
        return max(abs(b - a) for a, b in zip(path, path[1:]))

    assert biggest_jump(0.9) > biggest_jump(0.1)


def test_starting_mu_is_where_mu_starts():
    s = day("pace_multiplicative", {"mu_start": 0.8}, budget=BUDGET)
    assert control(s)[0]["value"] == 0.8


# --------------------------------------------------------- pacing against the control
#
# The picture the phase exists for: one spend curve that cliffs, one that ramps.


def test_pacing_is_still_bidding_after_budget_blind_has_gone_quiet():
    blind = day("budget_blind", budget=BUDGET)
    paced = day("pace_multiplicative", budget=BUDGET)
    assert abstentions(blind)[-1] is True, "the control case ends the day silent"
    assert last_spending_round(paced) > last_spending_round(blind)
    assert sum(abstentions(paced)) < sum(abstentions(blind)), "the pacer enters far more"


def test_pacing_spreads_the_same_money_across_the_whole_day():
    """Same budget, same values, same rivals — only the sequencing differs."""
    blind = day("budget_blind", budget=BUDGET)
    paced = day("pace_multiplicative", budget=BUDGET)
    half = len(blind.rounds) // 2
    assert spend(blind)[half] == spend(blind)[-1], "the blind bidder is done by half time"
    assert spend(paced)[half] < spend(paced)[-1], "the pacer is not"
