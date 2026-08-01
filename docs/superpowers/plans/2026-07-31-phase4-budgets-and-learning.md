# Phase 4: Budget Pacing and Learning Bidders — Implementation Plan

**Goal:** Run a day of impressions instead of a single auction, and watch a budget get spent — well or badly.

**Architecture:** One additive `World` parameter on the existing `run_series`, carrying per-round value draws, per-bidder budgets, and a seeded RNG. Its default reproduces phase 2 exactly. Six new strategies read budget state from their context; the UI adds spend and win-rate charts beside the existing round timeline.

**Tech Stack:** Python 3.11+ stdlib only (`random` for seeded draws), pytest, vanilla JS + SVG.

**Spec:** [2026-07-30-auction-visualizer-design.md](../specs/2026-07-30-auction-visualizer-design.md) (phase 4)
**Builds on:** phases 1–3, complete, 430 tests green.

---

## The teaching payoff

A budget turns bidding into a *sequencing* problem. Four bidders with the same budget and the same
values differ only in how they spread it, and the spend curve makes the difference obvious:

- **`budget_blind`** bids its value until the money is gone, then goes silent. It spends out before
  the day is half over and misses everything after, including impressions it valued more than the
  ones it bought. This is the control case, and it is why pacing exists.
- **`pace_multiplicative`** scales every bid by μ and steers μ toward finishing the day on budget.
  Watching μ converge is watching a shadow price on the budget appear.
- **`throttle`** bids its full value but enters only a fraction of auctions. It can hit the same
  spend as pacing while buying a *different set of impressions* — full price, chosen at random,
  instead of cheap ones preferentially. Same budget, same spend, worse basket.
- **`pid_winrate`** steers a win rate instead of a spend rate, and will overshoot and oscillate when
  its gains are wrong. That is not a bug to hide; a control loop that rings is the lesson.

Then two learners over the same bid grid, judged by regret: **ε-greedy** explores by flipping a
coin forever, **UCB** explores by preferring what it knows least about. The regret curves separate.

## The seam that decides this phase

`run_series` currently holds values fixed and moves only bids. Pacing is meaningless under fixed
values, so rounds must redraw them.

**Extend `run_series` with one optional `world` argument whose default reproduces today's behaviour
exactly.** Do not fork a second runner — one round timeline, one `RoundRecord`, one UI path. Phase
2's tests are the proof: they must pass untouched, with no test-only branch keeping them alive.

## Two model additions that are easy to get wrong

**Abstention is not a bid of 0.** A bidder that is out of budget, or that throttling told to sit
out, did not enter the auction. Bidding 0 is different — with a reserve of 0 it is eligible and can
*win at a price of 0*, which would be a fabricated impression. Add `abstain: bool` to `BidDecision`
and have the runner drop abstainers from that round's bidder list.

When *everyone* abstains there is no auction. Record the round with `trace: None` rather than
inventing one — "no fill" is a real outcome and the UI should say so.

**Randomness must be seeded and must live in the context.** `throttle` and ε-greedy need an RNG.
Put a `random.Random` on `StrategyContext`, derived deterministically from the series seed, the
bidder id and the round, so a series is reproducible and every test is stable. A strategy must
never call the global `random` module.

---

### Task 1: the world — value draws, budgets, spend, seeded RNG

**Files:** `agt/world.py` (new), `agt/series.py`, `agt/strategies.py`, `tests/test_world.py`, `tests/test_series.py`

```python
@dataclass(frozen=True)
class World:
    rounds: int
    seed: int = 0
    value_low: Number | None = None    # None => values stay fixed, i.e. phase 2
    value_high: Number | None = None
    budgets: dict[str, Number] = field(default_factory=dict)   # bidder id -> budget
```

Each round, every bidder draws a value in `[value_low, value_high]` when a range is given.
`StrategyContext` gains `budget`, `spent`, `rounds_left`, and `rng`. The runner tracks spend from
each round's payments and refuses to let a bidder spend past its budget — a bidder whose remaining
budget is below what it would owe cannot enter. State that rule in the trace text; it is a
simplification worth naming, because real exchanges enforce it differently.

- [ ] **Test first:** the default `World` reproduces a phase 2 series bid-for-bid (assert against a
      run with no world at all); the same seed reproduces the same series exactly and a different
      seed does not; values land inside the range; spend accumulates from payments; a bidder is
      barred once its budget cannot cover a win; `abstain=True` removes a bidder from that round;
      an all-abstain round records `trace: None` without crashing the summary.
- [ ] Implement, full suite green, commit — `feat: seeded world with value draws and budgets`

### Task 2: `budget_blind` and `pace_multiplicative`

**Files:** `agt/pacing.py` (new), tests

- **`budget_blind`** — bid your value; abstain once the budget cannot cover a win. The control.
- **`pace_multiplicative`** — bid `value × μ`. After each round, steer μ by the ratio of target
  spend to actual spend so far, clamped to `[0, 1]`. Params: starting μ and a step size.

The `why` string must say what μ currently is and which way it is moving, since μ *is* the lesson.

- [ ] **Test first:** with a budget that binds, `budget_blind` spends out strictly before the last
      round and abstains after — assert the silent tail exists; `pace_multiplicative` finishes the
      day having spent a larger share of its budget than `budget_blind` does *usefully*, and its μ
      is lower at the end than at the start when the budget binds; with a budget so large it never
      binds, μ climbs to its ceiling and pacing bids essentially truthfully.
- [ ] Implement, full suite green, commit — `feat: budget-blind and multiplicative pacing`

### Task 3: `throttle` and `pid_winrate`

**Files:** `agt/pacing.py`, tests

- **`throttle`** — bid the full value with probability *p*, abstain otherwise; steer *p* toward
  finishing on budget. Uses `context.rng`, never global `random`.
- **`pid_winrate`** — hold a target win rate using proportional and integral terms on the
  win-rate error, applied to a bid multiplier. Params: target rate, `kp`, `ki`.

- [ ] **Test first:** `throttle` is reproducible under a fixed seed and abstains roughly `1-p` of
      rounds; at equal spend it wins *fewer, more expensive* impressions than pacing does — that
      comparison is the whole reason it is here, so assert it directly; `pid_winrate` moves toward
      its target, and a deliberately high `kp` visibly overshoots (assert the overshoot exists
      rather than pretending the loop is smooth).
- [ ] Implement, full suite green, commit — `feat: probabilistic throttling and PID win-rate control`

### Task 4: bandit bidders and regret

**Files:** `agt/bandits.py` (new), tests

Both learners choose from the same discrete grid of bid multipliers and use realised utility as the
reward.

- **`bandit_epsilon`** — explore uniformly with probability ε, else take the best mean so far.
- **`bandit_ucb`** — pick the arm maximising `mean + sqrt(2 ln t / n)`.

Report **regret** in the series summary: per round, the best achievable utility in hindsight minus
what was actually earned, accumulated.

- [ ] **Test first:** both learners pull every arm at least once before settling; both converge on
      the best arm in a stationary environment; **UCB's cumulative regret is lower than ε-greedy's**
      averaged over several seeds — assert it across seeds, not on one lucky run, and if the claim
      does not hold, report what you actually measured rather than tuning the test until it does.
- [ ] Implement, full suite green, commit — `feat: epsilon-greedy and UCB bidders with regret`

### Task 5: API and validation

**Files:** `agt/api.py`, `agt/serve.py`, tests

`POST /run_series` accepts `world`: `{seed, value_low, value_high, budgets}`. Validate seed is an
int in range, `value_low <= value_high`, both finite and non-negative, budgets keyed by submitted
bidder ids only and non-negative. Reuse the existing number helpers. Package mechanisms stay
refused, as in phase 3.

Bound the compute: 50 rounds × 12 bidders × learners is fine, but confirm with a timing test rather
than assuming.

- [ ] Test first, implement, full suite green, commit — `feat: world parameters through the API`

### Task 6: campaign charts

**Files:** `web/campaign.js` (new), `web/index.html`, `web/style.css`

Reuse the phase 2 round timeline; add beside it:

- **Spend against budget** — cumulative spend per bidder over rounds, with each budget as a
  reference line. The `budget_blind` cliff and the paced ramp are the picture this phase exists for.
- **Win rate** per bidder over rounds.
- **The control variable** — μ, *p*, or the PID multiplier, whichever the strategy publishes, on its
  own chart. One axis per chart; never two scales on one plot.
- **Regret** when a bandit is in play.
- A round with `trace: null` must read as **"no auction — nobody entered"**, not as an error or a
  blank.

Follow the `dataviz` skill. Charts appear because the summary carries the series they need, never
because of a strategy name.

- [ ] Verify by hand: `budget_blind` visibly spends out early and flatlines; `pace_multiplicative`
      spreads across the day. That contrast is the acceptance test for the phase.
- [ ] Commit — `feat: spend, win-rate and regret charts`

### Task 7: README and acceptance

- [ ] README: the world model, the four pacers, the two learners, and how to add a strategy that
      needs budget state.
- [ ] Full suite green; walk the acceptance criteria.
- [ ] Commit — `docs: phase 4 README and acceptance pass`

## Phase 4 acceptance criteria

- A series with no `world` behaves **exactly** as it did in phase 2 — same bids, same summary.
- The same seed reproduces a series exactly; a different seed changes it.
- `budget_blind` spends out early and has a silent tail; `pace_multiplicative` does not.
- `throttle` at comparable spend wins fewer and more expensive impressions than pacing.
- `pid_winrate` overshoots at high `kp`, and a test asserts the overshoot rather than hiding it.
- Both bandits pull every arm, both converge, and measured regret is reported honestly across seeds.
- An all-abstain round records no auction and the UI says so.
- No strategy calls global `random`; all randomness comes from `context.rng`.
- Package mechanisms remain refused by `/run_series`.

## Out of scope

Equilibrium computation (phase 5), non-stationary environments, cross-campaign competition for the
same budget, second-price-specific pacing theory, and any bid language beyond scalar bids.
