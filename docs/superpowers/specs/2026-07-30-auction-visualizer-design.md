# Auction & Mechanism Visualizer — Design

**Date:** 2026-07-30
**Status:** Approved (design), not yet implemented

## Goal

A tool for building intuition about auction mechanisms by watching them run, one algorithmic
step at a time. Set up bidders and their private values, pick a mechanism, then step through:
bids arrive, get sorted, a winner is chosen, a pricing rule fires, payments settle. Every step
shows the rule that fired and the numbers it produced.

Covers classic algorithmic game theory first, then the ad-tech variants that build on it.

## Non-goals

- Not a real auction platform. No money, no accounts, no persistence, no multiplayer.
- Not a research benchmark. `amazon-science/auction-gym` already does RL bidder evaluation;
  this is for understanding, not for publishing numbers.
- Not a general game-theory solver. `open_spiel` and Gambit exist.

## Prior art considered

| Project | Verdict |
|---|---|
| `amazon-science/auction-gym` (196★) | Reference for realistic ad-auction parameters (value distributions, CTR model, reserve prices). Not a dependency — it is Jupyter research code with RL deps, and produces aggregate plots, not step-through traces. |
| `google-deepmind/open_spiel` (5.4k★) | Has first-price and all-pay auction games, but as RL environments in C++. Too heavy, no visual layer. |
| `phelps-sg/jasa`, `donghun2018/adclick-simulator`, `fbanelli/ad-auction-sim` | Small or abandoned; no step-through UI. |

No existing project renders an auction mechanism as an inspectable sequence of algorithm steps.
The mechanism math is not the hard part (second-price is three lines) — the trace format and the
renderer are. Those get built here; the parameter realism gets borrowed from auction-gym.

## Architecture

**Trace-based.** The Python engine runs an auction to completion and emits a JSON *trace*: an
ordered list of steps. The web UI is a dumb renderer — it displays step *N* of a trace and knows
nothing about auctions. Adding a mechanism means writing one Python generator; the UI needs no
changes.

```
browser  --POST /run {mechanism, bidders, params}-->  Python engine
         <--------------- trace JSON ---------------
```

Two consequences that motivate the choice:

1. **The trace is the test fixture.** Assertions run against trace steps, so mechanism
   correctness is tested without touching the UI.
2. **Transport is swappable.** The same engine can later run in-browser under Pyodide for a
   static, serverless deploy. Nothing in the engine knows about HTTP.

Rejected alternatives:

- *Live-stepping over websockets* — lets you change a bid mid-auction, but costs websockets,
  state sync, and reconnect handling. "Tweak and re-run" delivers nearly the same insight.
- *Pyodide from day one* — good end state, bad while building. ~10MB wasm download and awkward
  debugging. Reachable later without an engine rewrite.

## Trace format

```json
{
  "mechanism": "second_price",
  "params": { "reserve": 0 },
  "bidders": [ { "id": "A", "value": 100, "bid": 95 } ],
  "steps": [
    {
      "label": "collect bids",
      "detail": "Each bidder submits one sealed bid; values stay private.",
      "formula": null,
      "highlight": { "stage": "collect", "bidders": ["A", "B", "C"] },
      "state": { "bids": { "A": 95, "B": 72, "C": 41 } }
    },
    {
      "label": "price rule",
      "detail": "Winner pays the highest losing bid, not their own.",
      "formula": "p = max(b_-i) = 72",
      "highlight": { "stage": "price", "bidders": ["B"] },
      "state": { "bids": { "A": 95, "B": 72, "C": 41 }, "winner": "A", "price": 72 }
    }
  ],
  "result": {
    "winner": "A", "price": 72,
    "payments": { "A": 72 },
    "utilities": { "A": 28, "B": 0, "C": 0 },
    "revenue": 72, "welfare": 100, "efficient": true
  }
}
```

Each step carries a **full state snapshot**, not a diff. The renderer draws `state` directly with
no replay logic, and traces stay small at realistic bidder counts.
`ponytail: full snapshots; switch to diffs only if a trace exceeds ~1MB.`

`formula` is a plain string with numbers already substituted — the point is seeing the rule fire
on *these* numbers, not rendering LaTeX.

## Mechanism registry

Mechanisms register themselves with a name, a generator function, and a parameter schema.
`GET /mechanisms` returns the registry, and the UI builds its setup form from it. Adding a
mechanism therefore touches exactly one Python file.

```python
@mechanism("second_price", params={"reserve": {"type": "number", "default": 0}})
def second_price(bidders, reserve=0):
    yield step("collect bids", detail=..., state={"bids": ...})
    ...
```

## Server

Python stdlib `http.server`, roughly 40 lines: static files from `web/`, plus `GET /mechanisms`
and `POST /run`.
`ponytail: no FastAPI — one endpoint does not earn a dependency. Add it at ~5 endpoints or when
async is needed.`

## File layout (phase 1)

```
agt/
  trace.py         # Step, Trace, step() helper, JSON encoding
  mechanisms.py    # registry + single-item mechanisms
  serve.py         # stdlib HTTP server
web/
  index.html       # setup form + stage list + bidder bars (SVG)
  app.js           # fetch trace, render step N, prev/next/auto/reset
tests/
  test_mechanisms.py
```

Later phases add `agt/bidders.py` (strategies), `agt/pacing.py`, `agt/equilibrium.py`. Files get
split when one exceeds ~400 lines, not before.

## Validation and error handling

`POST /run` is the trust boundary. Validate before running anything:

- `mechanism` must exist in the registry → else 400 with the list of valid names.
- 1–12 bidders. Above 12 the visualization stops being readable, and it bounds compute.
- Each `value` and `bid` must be a finite, non-negative number.
- Mechanism params validated against the registry's declared schema; unknown keys rejected.

Errors return `{"error": "<message>"}` with a 4xx status; the UI shows the message inline next to
the setup form rather than silently failing. Engine bugs (an exception mid-trace) return 500 with
the partial trace attached, so a broken mechanism is still debuggable in the UI.

## Testing

pytest, asserting against traces:

- **Per mechanism:** a known input produces the expected winner, price, payments, and step labels.
- **Invariants across all registered mechanisms:** payment never exceeds the winner's bid;
  utilities equal `value - payment` for the winner and 0 for losers; revenue equals the sum of
  payments; the last step's state matches `result`.
- **Truthfulness property test:** for second-price and VCG, over random value profiles, no bidder
  improves utility by bidding anything other than their value. This is the property the whole tool
  exists to make visible, so it is checked mechanically too.
- **Registry contract:** every registered mechanism produces a schema-valid trace on random input.

No UI test framework. The renderer is thin and the trace is what carries meaning.

## Phases

Each phase is independently useful; phase 1 alone is a working tool.

| Phase | Scope |
|---|---|
| 1 | Trace engine, registry, server, renderer. Single-item mechanisms: first-price, second-price, all-pay, English (ascending clock), Dutch (descending clock), reserve prices. |
| 2 | Bidder strategies: truthful, equilibrium shading, best-response dynamics over repeated rounds. Adds a round timeline to the UI. |
| 3 | Multi-item: VCG, GSP, greedy combinatorial allocation. Trace carries allocation matrices; same UI primitives. |
| 4 | Ad-tech: budget pacing, win-rate control loops, bandit bidders over a simulated day. Long-horizon chart view. |
| 5 | Equilibrium analysis: best-response plots, Nash computation for small games, revenue-equivalence check. |

## Phase 1 acceptance criteria

- `python -m agt.serve` starts a server; the browser page loads with a default 3-bidder setup.
- All six phase-1 mechanisms run and produce schema-valid traces.
- The UI steps forward and backward through every step, with the current stage highlighted, the
  active bidders emphasized, and the formula shown with real numbers substituted.
- Editing a bidder's value or bid and re-running updates the trace without a page reload.
- Invalid input shows an inline error instead of a blank screen or a stack trace.
- `pytest` passes, including the truthfulness property test for second-price.
