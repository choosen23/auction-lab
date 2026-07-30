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
| `agt/strategies.py` | Strategy registry and the four bidders |
| `agt/series.py` | `run_series()`: rounds, history, convergence |
| `agt/api.py` | Request validation and the JSON-in/JSON-out endpoint bodies |
| `agt/serve.py` | stdlib HTTP server: routing, static files, byte caps |
| `web/app.js` | Step renderer (phase 1) |
| `web/series.js` | Round timeline, bid-path chart, decision panel (phase 2) |

## Adding a mechanism

Write one decorated generator in `agt/mechanisms.py`. It yields `Step`s and returns `outcome(...)`.
The UI builds its form from the registry, so **no JavaScript changes are needed** — the new
mechanism and its parameters appear on their own.

```python
@mechanism("third_price", label="Third-price", description="...",
           params={"reserve": {"type": "number", "default": 0, "label": "Reserve price", "min": 0}})
def third_price(bidders, reserve=0):
    yield step("collect bids", "Each bidder submits one sealed bid.",
               {"bids": ...}, stage="collect", bidders=[...])
    ...
    return outcome(bidders, winner=..., payments=...)
```

The cross-mechanism invariant tests are parametrized over the registry, so a new mechanism
inherits them automatically. It also gets a working `best_response` bidder for free, since that
strategy evaluates candidates by running whatever mechanism it was handed.

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

## Roadmap

Phases 1 and 2 are done: single-item mechanisms, and bidder strategies over repeated rounds. Next:
multi-item (VCG, GSP, combinatorial), ad-tech budget pacing, and equilibrium analysis. See
`docs/superpowers/specs/`.

Phase 3a is in: `gsp` and `vcg_positions` sell several ad slots at once to the same scalar
per-click bids, allocating identically and charging differently. `@mechanism` now takes
`truthful_dominant`, so whether honesty is dominant is declared by the mechanism rather than kept
in a list inside `agt/strategies.py` — which is what stopped VCG from being told, falsely, that
bidding your value hands the surplus to the seller.

## Prior art

[`amazon-science/auction-gym`](https://github.com/amazon-science/auction-gym) is the reference for
realistic ad-auction parameters, and [`open_spiel`](https://github.com/google-deepmind/open_spiel)
has auction games as RL environments. Neither renders a mechanism as an inspectable sequence of
steps, which is the entire point here.
