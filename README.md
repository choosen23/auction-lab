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

| Path | Responsibility |
|---|---|
| `agt/trace.py` | `Bidder`, `Step`, `Trace`, and the `outcome()` math |
| `agt/registry.py` | Mechanism registry, param schemas, `run()` |
| `agt/stages.py` | Step generators shared across mechanisms |
| `agt/mechanisms.py` | The five mechanisms |
| `agt/api.py` | Request validation and the JSON-in/JSON-out endpoint bodies |
| `agt/serve.py` | stdlib HTTP server: routing, static files, byte caps |
| `web/` | Renderer |

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
inherits them automatically.

## Tests

```
python3 -m pytest -q
```

Beyond per-mechanism expected values, the suite checks invariants across every registered
mechanism (revenue equals total payments, utility equals value minus payment) and a property test
asserting that no deviation from truthful bidding beats honesty in second-price and English. If
that one fails, the mechanism is wrong — not the test.

## Roadmap

Phase 1 (this) is single-item mechanisms. Then: bidder strategies and best-response dynamics,
multi-item (VCG, GSP, combinatorial), ad-tech budget pacing, and equilibrium analysis. See
`docs/superpowers/specs/`.

## Prior art

[`amazon-science/auction-gym`](https://github.com/amazon-science/auction-gym) is the reference for
realistic ad-auction parameters, and [`open_spiel`](https://github.com/google-deepmind/open_spiel)
has auction games as RL environments. Neither renders a mechanism as an inspectable sequence of
steps, which is the entire point here.
