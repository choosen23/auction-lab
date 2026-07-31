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

from agt.pacing import MAX_MULTIPLIER
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
        s = day("pace_multiplicative", {"step": step}, budget=BUDGET)
        path = [c["value"] for c in control(s)]
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


def win_flags(series, who="A"):
    """1 for each round this bidder won, 0 otherwise — the signal `pid_winrate` steers."""
    return [
        1 if (r.trace is not None and r.trace.result["winner"] == who) else 0
        for r in series.rounds
    ]


def trailing_rate(flags, window=6):
    """Win rate over the last ``window`` rounds. A cumulative rate is dominated by the
    startup transient and would hide exactly the ringing these tests are looking for."""
    return [
        sum(flags[max(0, i - window + 1) : i + 1]) / len(flags[max(0, i - window + 1) : i + 1])
        for i in range(len(flags))
    ]


def mean(xs):
    return sum(xs) / len(xs)


def swing(path):
    return max(path) - min(path)


def reversals(path):
    """How many times a path changes direction — the shape of a loop that rings."""
    steps = [b - a for a, b in zip(path, path[1:]) if b != a]
    return sum(1 for a, b in zip(steps, steps[1:]) if (a > 0) != (b > 0))


# ------------------------------------------------------------------------ throttle
#
# Same steering law as pacing, different actuator: it moves *how often it enters* instead
# of *how much it bids*. Both land on the budget. Only one of them buys well.


def test_throttle_is_reproducible_under_a_fixed_seed():
    a = day("throttle", budget=BUDGET, seed=3)
    b = day("throttle", budget=BUDGET, seed=3)
    c = day("throttle", budget=BUDGET, seed=4)
    assert abstentions(a) == abstentions(b)
    assert abstentions(a) != abstentions(c), "a different seed has to deal a different hand"


def test_throttle_abstains_roughly_one_minus_p_of_the_time():
    """Held at a fixed p — step 0 never steers — and with no budget to bar it, so the only
    thing deciding whether it enters is the coin. Pooled over twenty seeds, because one
    30-round day is far too few flips to tell 0.6 from 0.5."""
    out = [
        entry
        for seed in range(1, 21)
        for entry in abstentions(day("throttle", {"p_start": 0.4, "step": 0}, seed=seed))
    ]
    assert len(out) == 600
    assert 0.55 < sum(out) / len(out) < 0.65


def test_throttle_draws_from_the_context_stream_and_never_the_global_module():
    """`agt.world` already asserts this for a whole series; this pins it on the one
    strategy in this module that actually needs a coin."""
    import random

    random.seed(99)
    before = random.getstate()
    day("throttle", budget=BUDGET)
    assert random.getstate() == before


def test_throttle_publishes_its_entry_probability_where_a_chart_can_read_it():
    knob = day("throttle", budget=BUDGET).rounds[-1].decisions["A"].control
    assert knob["name"] == "p"
    assert 0 <= knob["value"] <= 1
    assert knob["direction"] in {"up", "down", "steady"}


def test_throttle_lands_on_its_budget_like_pacing_does():
    for seed in range(1, 6):
        s = day("throttle", budget=BUDGET, seed=seed)
        assert 0.6 < spend(s)[-1] / BUDGET <= 1.0


# ------------------------------------------------- the comparison the phase exists for


@pytest.mark.parametrize("seed", [1, 2, 3, 4, 5])
def test_throttle_buys_fewer_and_dearer_impressions_than_pacing(seed):
    """The whole reason `throttle` is in this phase. Both bidders get the same budget, the
    same market and — because value draws are keyed by bidder id and seed — the *same*
    sequence of values. Only the rule differs.

    Throttling pays full price for a randomly chosen subset. Pacing stays in every auction
    with a shaded bid and so wins exactly the impressions its shading can still afford,
    which are the cheap ones. Same budget, same spend, worse basket.
    """
    paced = day("pace_multiplicative", budget=BUDGET, seed=seed)
    throttled = day("throttle", budget=BUDGET, seed=seed)
    assert spend(throttled)[-1] == pytest.approx(spend(paced)[-1], rel=0.35)

    dear, cheap = prices_paid(throttled), prices_paid(paced)
    assert len(dear) < len(cheap), "throttling buys fewer impressions"
    assert mean(dear) > mean(cheap), "and pays more for each of them"


# --------------------------------------------------------------------- pid_winrate
#
# A control loop that rings is the lesson, not a defect to be smoothed away.


def test_pid_winrate_holds_a_rate_a_truthful_bidder_blows_straight_past():
    target = 0.3
    held = mean(win_flags(day("pid_winrate", {"target_rate": target})))
    greedy = mean(win_flags(day("truthful")))
    assert greedy > 0.9, "the market is cheap enough that honesty wins nearly everything"
    assert abs(held - target) < abs(greedy - target)
    assert abs(held - target) < 0.15


def test_pid_winrate_chases_a_higher_target_harder():
    low = mean(win_flags(day("pid_winrate", {"target_rate": 0.2})))
    high = mean(win_flags(day("pid_winrate", {"target_rate": 0.7})))
    assert low < high


def test_pid_winrate_publishes_the_multiplier_it_is_steering():
    knob = day("pid_winrate").rounds[-1].decisions["A"].control
    assert knob["name"] == "multiplier"
    assert knob["direction"] in {"up", "down", "steady"}


def test_a_high_proportional_gain_makes_the_multiplier_ring():
    calm = [c["value"] for c in control(day("pid_winrate", {"kp": 1}))]
    hot = [c["value"] for c in control(day("pid_winrate", {"kp": 8}))]
    assert swing(hot) > swing(calm)
    assert reversals(hot) > reversals(calm)


@pytest.mark.parametrize("seed", [1, 2, 3, 4, 5])
def test_a_high_proportional_gain_slams_the_multiplier_into_both_stops(seed):
    """The overshoot, asserted rather than hidden — and asserted where it actually lives.

    At kp = 8 the multiplier does not approach a level, it bangs between its two limits:
    3.0 one round, 0.0 the next, reversing direction roughly every round of the day. The
    swing is the full range of the variable in 10 seeds out of 10.
    """
    path = [c["value"] for c in control(day("pid_winrate", {"kp": 8}, seed=seed))]
    assert max(path) >= MAX_MULTIPLIER, "it saturates the top stop"
    assert min(path) <= 0, "and the bottom one"
    assert reversals(path) > len(path) / 2, "reversing course on most rounds of the day"


def test_the_swing_grows_with_the_gain_all_the_way_to_saturation():
    """Measured over ten seeds: swing runs 1.03-1.08 at kp 0.5, 1.18-1.24 at 1, 1.60 at 2,
    2.20 at 4, and pins at the full 3.00 from kp 8 up."""
    swings = [
        swing([c["value"] for c in control(day("pid_winrate", {"kp": kp}))])
        for kp in (0.5, 1, 2, 4)
    ]
    assert swings == sorted(swings), f"a harder gain has to swing further, got {swings}"


def test_the_overshoot_is_paid_for_in_the_opening_round():
    """Where a badly tuned loop actually costs money. Having won nothing yet, the bidder
    opens on the full error, so a high kp opens by bidding several times its value and
    buying an impression at a large loss. Measured across ten seeds, the opening round
    loses 7-12 at kp 0.5 and 98-156 at kp 8."""

    def opening_utility(kp):
        return day("pid_winrate", {"kp": kp}).rounds[0].trace.result["utilities"]["A"]

    assert opening_utility(8) < opening_utility(0.5) < 0
    assert opening_utility(8) < -50, "a gain that high buys its first impression at a rout"


def test_a_ringing_loop_still_averages_near_its_target_which_is_the_trap():
    """The honest surprise, and the reason the test above measures the multiplier and not
    the win rate. Asserting "high kp overshoots the *target*" was tried first and is
    false: measured over ten seeds the realised win rate is 0.27-0.33 at kp 0.5 and
    0.33-0.33 at kp 8, and the win-rate overshoot is *larger* at the low gain, not the
    high one — trailing peaks of 0.50-0.83 against 0.33-0.50.

    A bang-bang actuator integrates to a very accurate average. So the output alone says
    the hot loop is the better tuned of the two, while it is in fact alternating between
    bidding triple value and bidding nothing. Judge a controller by its control variable,
    not only by the number it is holding.
    """
    target = 0.3
    calm = mean(win_flags(day("pid_winrate", {"target_rate": target, "kp": 1})))
    hot = mean(win_flags(day("pid_winrate", {"target_rate": target, "kp": 8})))
    assert abs(hot - target) <= abs(calm - target) + 0.05, (
        f"the ringing loop's output is not worse: calm {calm:.2f}, hot {hot:.2f}"
    )
    assert swing([c["value"] for c in control(day("pid_winrate", {"kp": 8}))]) > 2
