# agt-training

Watch auction mechanisms run, one algorithmic step at a time.

Set up bidders and their private values, pick a mechanism, and step through it: bids arrive, get
sorted, a winner is chosen, a pricing rule fires, payments settle. Every step shows the rule that
fired and the numbers it produced.

```
python3 -m agt.serve
```

Then open <http://127.0.0.1:8000>. No dependencies, no build step — Python 3.11+ stdlib and vanilla
JS. `--port` changes the port.

## Mechanisms (phase 1)

| Name | Rule | Lesson |
|---|---|---|
| `first_price` | Highest bid wins, pays own bid | Rewards shading; bidding your value earns nothing |
| `second_price` | Highest bid wins, pays highest losing bid | Truthful bidding is dominant |
| `all_pay` | Highest bid wins, **everyone** pays their bid | Losers go negative |
| `english` | Ascending clock, last bidder standing | Equivalent to second-price |
| `dutch` | Descending clock, first to accept | Equivalent to first-price |

All support a `reserve` price. A reserve that blocks the sale is flagged inefficient — that cost is
the point.

## Selling more than one thing (phase 3)

**Position auctions** sell several ad slots to the same scalar per-click bids. Slot *i* gets
`ctr_decay ** i` of the clicks slot 0 gets.

| Name | Rule |
|---|---|
| `gsp` | Slots go to the highest bids; each winner pays the **next bidder's** bid per click |
| `vcg_positions` | Same allocation, but each winner pays the externality they impose |

They exist to be compared. On identical input they allocate *identically* and charge differently —
and only one of them is truthful:

| A values clicks at 10, B bids 8, C bids 2, two slots | honest (bid 10) | lying (bid 3) |
|---|---|---|
| `gsp` | slot 0, pays 8, utility **2** | slot 1, pays 1, utility **4** ← lying wins |
| `vcg_positions` | slot 0, pays 5, utility **5** ← honesty wins | slot 1, pays 1, utility 4 |

Sponsored search runs GSP anyway. Set `slots=1` and both collapse to second-price.

**Combinatorial auctions** sell bundles. Bidders submit XOR package bids — several bundles each,
winning at most one — and the item universe is whatever items get named.

| Name | Rule |
|---|---|
| `greedy_package` | Take bids highest-first, skip conflicts, winners pay their own bid |
| `vcg_package` | Solve for the *optimal* allocation, charge VCG prices |

Finding the best allocation is NP-hard, so the practical algorithm is greedy — and greedy leaves
money on the table. Both mechanisms report the gap:

| A wants `{north, south}` for 10, B wants `{north}` for 6, C wants `{south}` for 6 | |
|---|---|
| greedy | takes A's single big bid, then is blocked — welfare **10**, inefficient |
| optimal | splits the pair between B and C — welfare **12**, efficient |

VCG's truthfulness depends on the allocation being optimal, which is why running VCG payments on
top of a greedy allocation quietly breaks it. That is a real trap, not a footnote.

Inputs are bounded at 20 package bids and 12 distinct items — the exhaustive search is exponential,
and the auction says out loud how big a problem it will solve exactly rather than hanging inside a
request.

## Strategies (phase 2)

Each bidder can pick how it chooses its bid, and you can run repeated rounds to watch those choices
move.

| Name | Bid rule |
|---|---|
| `manual` | Whatever you typed. The default, so single auctions behave exactly as before |
| `truthful` | `bid = value` |
| `shade_bne` | `bid = value·(n−1)/n` — the first-price equilibrium for i.i.d. uniform values, and *only* for those |
| `best_response` | Best reply to rivals' previous-round bids, assuming they repeat them |

`best_response` never does closed-form math. It evaluates candidate bids by **running the mechanism**
and reading its own utility, so it works on any mechanism — including ones not written yet. That is
what makes the same strategy code produce opposite dynamics:

| Mechanism | Bidder A (value 100, opening bid 95) | |
|---|---|---|
| `second_price` | `95 → 100 100 100 100 100` | Truthful by round 1, never moves again |
| `first_price` | `95 → 72 72 72 72 72` | Shades below value and settles |
| `all_pay` | `95 → 72 0 0 1 2 …` | Never settles |

The `all_pay` row is not a bug. All-pay auctions have no pure-strategy equilibrium, so
best-response cycles forever, and the summary reports `converged: false`. So does first-price when
bidders start far apart — they leapfrog each other in an Edgeworth cycle rather than settling. The
tool says "still moving" in both cases instead of implying an equilibrium that was never reached.

Strategies see their own private value, rivals' ids, and rivals' **past bids** — never a rival's
current value. Auctions are not a full-information game, and the code enforces that rather than
relying on good manners.

## Running a day, not an auction (phase 4)

Open **"Run it as a day with budgets"** and each round becomes one impression with a freshly drawn
value, bid for out of a budget that runs out. That turns bidding into a *sequencing* problem, and
these four bidders differ only in how they spread the same money:

| Name | How it spends |
|---|---|
| `budget_blind` | Bids its value until the money is gone, then goes silent |
| `pace_multiplicative` | Bids `value × μ`, steering μ to finish the day on budget |
| `throttle` | Bids its full value, but only enters a fraction *p* of auctions |
| `pid_winrate` | Steers a target *win rate* with proportional + integral terms |

Same budget of 400 over a 30-round day, same market, same draws:

| | Final spend | Last round it spent in | Rounds silent |
|---|---|---|---|
| `budget_blind` | 378 | **12** of 30 | 20 |
| `pace_multiplicative` | 384 | **29** of 30 | 0 |
| `throttle` | 367 | 24 | 18 |

`budget_blind` is done by lunchtime and misses everything after, including impressions it valued
more than the ones it bought. That is why pacing exists. `throttle` reaches a similar spend but
buys a *different basket* — full price, chosen at random, rather than cheap ones preferentially:
across five seeds it won ~5 impressions at ~70 each where pacing won ~13 at ~24.

A round where every bidder is out of budget has **no auction at all**, and the page says so rather
than showing a stale one. That is a real outcome, not an error.

### Learning bidders

`bandit_epsilon` and `bandit_ucb` choose from the same grid of bid multipliers, rewarded by
realised utility, and are scored by **regret against the best fixed arm in hindsight**.

Over ten seeds, mean final regret:

| | |
|---|---|
| UCB | **127.9** |
| ε-greedy | 209.7 |
| ε-greedy with ε = 0 (no exploration at all) | **129.0** |

The headline is real but the advertised reason is not. On this market UCB's confidence bonus is
worth about 1 where the arms are 13 apart, so it changes the chosen arm on 0.1 rounds in 45 — UCB
wins because a fixed exploration rate never stops charging rent, **not** because optimism finds it
anything. Both facts are asserted as tests.

And `sqrt(2 ln t / n)` is an absolute number: UCB1's constant assumes rewards on `[0, 1]`. Run the
identical market with every number divided by 100 and UCB's regret goes to **762.7** — it
round-robins all day and loses to the coin it just beat. Worth knowing before trusting a UCB on a
reward you have not normalized.

Everything is seeded: the same seed replays the same day exactly, and no strategy may touch the
global `random` module — a test asserts `random.getstate()` is untouched across a whole series.

## How it works

The Python engine runs an auction and emits a **trace**: an ordered list of steps, each carrying a
full state snapshot plus the rule that fired, in plain English, with the real numbers substituted.

The web UI is a dumb renderer. It reads `step.label`, `step.detail`, `step.formula`,
`step.highlight`, and `step.state` and draws them — it has no idea what an auction is. If the
JavaScript ever branches on a mechanism name, the architecture has been broken.

```
browser  --POST /run {mechanism, bidders, params}-->  engine
         <--------------- trace JSON ---------------
```

A repeated-round series is the same thing in a loop. `POST /run_series` computes each round's bids
from the strategies plus history, then calls that same unchanged `run()`. **A round is just a
trace**, so selecting a round on the timeline feeds the ordinary step view — phase 2 added no new
rendering path.

| Path | Responsibility |
|---|---|
| `agt/trace.py` | `Bidder`, `Step`, `Trace`, and the `outcome()` math |
| `agt/registry.py` | Mechanism registry, param schemas, `run()` |
| `agt/stages.py` | Step generators shared across mechanisms |
| `agt/mechanisms.py` | The five mechanisms |
| `agt/positions.py` | GSP and VCG position auctions |
| `agt/winner_determination.py` | `PackageBid` and the greedy / optimal set-packing solvers |
| `agt/packages.py` | The two combinatorial mechanisms |
| `agt/strategies.py` | Strategy registry and the four phase 2 bidders |
| `agt/series.py` | `run_series()`: rounds, history, convergence, budgets |
| `agt/world.py` | The day: value draws, budgets, seeded RNG |
| `agt/pacing.py`, `agt/steering.py` | The four budget pacers and their shared feedback law |
| `agt/bandits.py`, `agt/regret.py` | The two learners and regret against the hindsight arm |
| `agt/api.py` | Request validation and the JSON-in/JSON-out endpoint bodies |
| `agt/serve.py` | stdlib HTTP server: routing, static files, byte caps |
| `web/app.js` | Step renderer (phase 1) |
| `web/series.js` | Round timeline, bid-path chart, decision panel (phase 2) |
| `web/positions.js` | Slot ladder (phase 3a) |
| `web/packages.js` | Package bid table, bundle allocation, welfare gap (phase 3b) |
| `web/campaign.js` | Spend, win-rate, steering and regret charts (phase 4) |

Each phase added its view as an **additive seam** rather than by editing the renderer: `app.js`
gained a handful of `if (window.someExt)` lines and nothing else. Which view appears is decided by
the *shape* of the trace — numeric `allocation` values mean slots, lists of items mean bundles,
neither means a single winner — so the rule that no JavaScript branches on a mechanism name still
holds at three views deep.

## Adding a mechanism

Write one decorated generator in `agt/mechanisms.py`. It yields `Step`s and returns `outcome(...)`.
The UI builds its form from the registry, so **no JavaScript changes are needed** — the new
mechanism and its parameters appear on their own.

```python
@mechanism("third_price", label="Third-price", description="...",
           truthful_dominant=False,
           params={"reserve": {"type": "number", "default": 0, "label": "Reserve price", "min": 0}})
def third_price(bidders, reserve=0):
    yield step("collect bids", "Each bidder submits one sealed bid.",
               {"bids": ...}, stage="collect", bidders=[...])
    ...
    return outcome(bidders, winner=..., payments=...)
```

`truthful_dominant` is required — a mechanism has to answer whether honesty is dominant under it,
because the strategy explanations shown to the reader depend on the answer. It is declared here
rather than kept in a list inside `agt/strategies.py`, which is what stopped VCG from being told,
falsely, that bidding your value hands the surplus to the seller.

Add `input_kind="package"` for a mechanism bid on in bundles; it then receives `PackageBid`s and
the form switches itself. Multi-winner mechanisms return `outcome_allocation(...)` instead, which
takes an allocation and the gross gains rather than one winner.

The cross-mechanism invariant tests are parametrized over the registry, so a new mechanism
inherits them automatically. It also gets a working `best_response` bidder for free, since that
strategy evaluates candidates by running whatever mechanism it was handed — that is how GSP
acquired best-response dynamics without a line of new strategy code.

## Adding a strategy

Same promise: one decorated function in `agt/strategies.py`, **no JavaScript changes**. The bidder
table builds its strategy dropdown and any declared params from the registry.

```python
@strategy("timid", label="Timid", description="...")
def timid(context: StrategyContext) -> BidDecision:
    return BidDecision(bid=context.bidder.value * 0.5,
                       why="Halves its value out of caution, which wins little and saves nothing.")
```

The `why` string is shown to the reader as the explanation for that bid, so it is held to the same
standard as a step's `detail`: it must be true, and it must say what assumption it rests on.
`context` deliberately exposes rivals' ids and past bids but never their current values.

A strategy that spends from a budget reads `context.budget`, `context.spent`, `context.remaining`
and `context.rounds_left`, and takes any randomness from `context.rng` — never the global `random`.
Sitting a round out is `BidDecision(abstain=True)`, **not** a bid of 0: a zero bid is eligible under
a zero reserve and can win at a price of zero, inventing an impression that never happened. Publish
whatever the strategy steers in `BidDecision.control` and it gets a chart for free.

## Tests

```
python3 -m pytest -q
```

Beyond per-mechanism expected values, the suite checks invariants across every registered
mechanism (revenue equals total payments, utility equals value minus payment) and a property test
asserting that no deviation from truthful bidding beats honesty in second-price and English. If
that one fails, the mechanism is wrong — not the test.

Phase 2 adds a test that no strategy can reach a rival's private value, and tests that pin the two
dynamics above by *direction* rather than by exact bid sequence — the path is an implementation
detail, but "second-price settles on truth" and "first-price settles below value" are not.

Phase 3 pins the untruthfulness of `gsp` and `greedy_package` with **explicitly constructed
profitable deviations**, not by leaving them off a list. A mechanism that was silently truthful
would be wrong, and only an asserted deviation catches that. The exhaustive solver is also
cross-checked against naive subset enumeration over hundreds of random instances.

## Roadmap

Phases 1–4 are done: single-item mechanisms, bidder strategies over repeated rounds, position and
combinatorial auctions, and budget pacing with learning bidders. Next: equilibrium analysis — best
response plots, Nash computation for small games, and a revenue-equivalence check. See
`docs/superpowers/specs/`.

## Prior art

[`amazon-science/auction-gym`](https://github.com/amazon-science/auction-gym) is the reference for
realistic ad-auction parameters, and [`open_spiel`](https://github.com/google-deepmind/open_spiel)
has auction games as RL environments. Neither renders a mechanism as an inspectable sequence of
steps, which is the entire point here.
