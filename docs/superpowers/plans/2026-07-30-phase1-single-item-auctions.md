# Phase 1: Single-Item Auction Visualizer — Implementation Plan

**Goal:** Step through single-item auction mechanisms in a browser, watching each algorithmic stage fire on real numbers.

**Architecture:** A Python engine runs an auction and emits a JSON *trace* — an ordered list of steps, each carrying a full state snapshot. A stdlib HTTP server exposes the mechanism registry and a run endpoint. The web UI is a dumb renderer that displays step *N* and knows nothing about auctions.

**Tech Stack:** Python 3.11+ stdlib only (dataclasses, `http.server`, `json`), pytest for tests, vanilla JS + SVG for the UI. No runtime dependencies.

**Spec:** [2026-07-30-auction-visualizer-design.md](../specs/2026-07-30-auction-visualizer-design.md)

---

## File Structure

| File | Responsibility |
|---|---|
| `agt/trace.py` | Data model: `Bidder`, `Step`, `Trace`, the `step()` helper, `outcome()` computation, JSON encoding. Knows nothing about specific mechanisms. |
| `agt/mechanisms.py` | The registry plus every single-item mechanism. Each is a generator yielding `Step`s and returning a result dict. |
| `agt/serve.py` | stdlib HTTP server: static files, `GET /mechanisms`, `POST /run`, request validation. |
| `web/index.html` | Page skeleton and styles: setup form, stage list, bidder chart, formula panel, result panel. |
| `web/app.js` | Fetch registry, build form, POST runs, render step *N*, wire prev/next/auto/reset. |
| `tests/test_trace.py` | `outcome()` math and trace serialization. |
| `tests/test_mechanisms.py` | Per-mechanism expected results, cross-mechanism invariants, truthfulness property test. |
| `tests/test_serve.py` | Validation rejects bad input; happy path returns a schema-valid trace. |

Split only when a file exceeds ~400 lines.

---

### Task 1: Trace data model

**Files:**
- Create: `agt/__init__.py`, `agt/trace.py`
- Test: `tests/test_trace.py`

- [ ] **Step 1: Write the failing tests**

```python
from agt.trace import Bidder, outcome, step

def test_outcome_second_price_style():
    bidders = [Bidder("A", 100, 95), Bidder("B", 72, 72), Bidder("C", 41, 41)]
    r = outcome(bidders, winner="A", payments={"A": 72, "B": 0, "C": 0})
    assert r["winner"] == "A"
    assert r["price"] == 72
    assert r["utilities"] == {"A": 28, "B": 0, "C": 0}
    assert r["revenue"] == 72
    assert r["welfare"] == 100
    assert r["efficient"] is True

def test_outcome_all_pay_losers_pay_too():
    bidders = [Bidder("A", 100, 60), Bidder("B", 72, 50)]
    r = outcome(bidders, winner="A", payments={"A": 60, "B": 50})
    assert r["utilities"] == {"A": 40, "B": -50}
    assert r["revenue"] == 110

def test_outcome_no_winner_is_inefficient_when_value_exists():
    bidders = [Bidder("A", 100, 10)]
    r = outcome(bidders, winner=None, payments={"A": 0})
    assert r["welfare"] == 0
    assert r["efficient"] is False

def test_step_carries_full_state_snapshot():
    s = step("sort", "Rank bids high to low.", {"bids": {"A": 95}}, formula="b_(1) = 95", stage="sort")
    assert s.state == {"bids": {"A": 95}}
    assert s.highlight == {"stage": "sort"}
```

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest tests/test_trace.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'agt'`

- [ ] **Step 3: Implement `agt/trace.py`**

Frozen dataclasses (immutability is a project rule — never mutate a `Step` after creation).
`outcome()` computes payments, utilities, revenue, welfare, and efficiency. Efficiency means
the item reached the highest-value bidder: `welfare == max(values)`. A reserve price that blocks
all sales is therefore *inefficient* whenever some bidder valued the item — which is the lesson.

- [ ] **Step 4: Run to verify pass**

Run: `python -m pytest tests/test_trace.py -v`
Expected: 4 passed

- [ ] **Step 5: Commit**

```bash
git add agt/ tests/test_trace.py
git commit -m "feat: trace data model with outcome computation"
```

---

### Task 2: Registry and sealed-bid mechanisms

**Files:**
- Create: `agt/mechanisms.py`
- Test: `tests/test_mechanisms.py`

Mechanisms are generators: they `yield` each `Step` and `return` the result dict. `run()` drives
the generator and captures the return value from `StopIteration`. This keeps mechanism bodies
readable top-to-bottom, which matters because those bodies *are* the teaching material.

- [ ] **Step 1: Write the failing tests**

```python
import pytest
from agt.trace import Bidder
from agt.mechanisms import REGISTRY, run

BIDDERS = [Bidder("A", 100, 95), Bidder("B", 72, 72), Bidder("C", 41, 41)]

def test_second_price_winner_pays_second_bid():
    t = run("second_price", BIDDERS)
    assert t.result["winner"] == "A"
    assert t.result["price"] == 72
    assert t.result["utilities"]["A"] == 28

def test_first_price_winner_pays_own_bid():
    t = run("first_price", BIDDERS)
    assert t.result["price"] == 95
    assert t.result["utilities"]["A"] == 5

def test_all_pay_everyone_pays():
    t = run("all_pay", BIDDERS)
    assert t.result["revenue"] == 95 + 72 + 41
    assert t.result["utilities"]["C"] == -41

def test_reserve_above_all_bids_blocks_sale():
    t = run("second_price", BIDDERS, {"reserve": 200})
    assert t.result["winner"] is None
    assert t.result["revenue"] == 0

def test_reserve_between_bids_raises_price():
    t = run("second_price", BIDDERS, {"reserve": 80})
    assert t.result["price"] == 80

def test_steps_are_ordered_and_labelled():
    t = run("second_price", BIDDERS)
    labels = [s.label for s in t.steps]
    assert labels[0] == "collect bids"
    assert "price rule" in labels
    assert t.steps[-1].state["winner"] == "A"
```

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest tests/test_mechanisms.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'agt.mechanisms'`

- [ ] **Step 3: Implement registry + `first_price`, `second_price`, `all_pay`**

The `@mechanism` decorator records name, human label, description, and a params schema. `run(name,
bidders, params)` validates params against that schema, drives the generator, and wraps everything
in a `Trace`. Ties break by bidder order, stated explicitly in the step detail so it is visible
rather than surprising.

- [ ] **Step 4: Run to verify pass**

Run: `python -m pytest tests/test_mechanisms.py -v`
Expected: 6 passed

- [ ] **Step 5: Commit**

```bash
git add agt/mechanisms.py tests/test_mechanisms.py
git commit -m "feat: mechanism registry with sealed-bid auctions"
```

---

### Task 3: Clock auctions (English, Dutch)

**Files:**
- Modify: `agt/mechanisms.py`
- Modify: `tests/test_mechanisms.py`

The clock jumps to each next dropout price rather than ticking by a fixed increment. This keeps
traces short and makes the English ≡ second-price and Dutch ≡ first-price equivalences visible in
a handful of steps — which is the whole point of showing them.

- [ ] **Step 1: Write the failing tests**

```python
def test_english_equals_second_price():
    t = run("english", BIDDERS)
    assert t.result["winner"] == "A"
    assert t.result["price"] == 72

def test_english_steps_show_each_dropout():
    t = run("english", BIDDERS)
    dropouts = [s for s in t.steps if s.highlight.get("stage") == "dropout"]
    assert len(dropouts) == 2

def test_dutch_equals_first_price():
    t = run("dutch", BIDDERS)
    assert t.result["winner"] == "A"
    assert t.result["price"] == 95
```

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest tests/test_mechanisms.py -k "english or dutch" -v`
Expected: FAIL — `KeyError: 'english'`

- [ ] **Step 3: Implement `english` and `dutch`**

- [ ] **Step 4: Run to verify pass**

Run: `python -m pytest tests/test_mechanisms.py -v`
Expected: 9 passed

- [ ] **Step 5: Commit**

```bash
git add agt/mechanisms.py tests/test_mechanisms.py
git commit -m "feat: English and Dutch clock auctions"
```

---

### Task 4: Cross-mechanism invariants and truthfulness

**Files:**
- Modify: `tests/test_mechanisms.py`

These run against *every* registered mechanism, so a mechanism added in a later phase inherits the
checks for free.

- [ ] **Step 1: Write the failing tests**

```python
import random

@pytest.mark.parametrize("name", sorted(REGISTRY))
def test_invariants_hold(name):
    t = run(name, BIDDERS)
    r = t.result
    assert r["revenue"] == pytest.approx(sum(r["payments"].values()))
    for b in BIDDERS:
        gain = b.value if b.id == r["winner"] else 0
        assert r["utilities"][b.id] == pytest.approx(gain - r["payments"][b.id])
    assert t.steps, "a mechanism must emit at least one step"
    assert t.steps[-1].state.get("winner", None) == r["winner"]

@pytest.mark.parametrize("name", ["second_price", "english"])
def test_truthful_bidding_is_dominant(name):
    """No deviation from truthful bidding beats it, over random value profiles."""
    rng = random.Random(0)
    for _ in range(200):
        values = [rng.randint(1, 100) for _ in range(3)]
        others = [Bidder(i, v, v) for i, v in zip("BC", values[1:])]
        truthful = run(name, [Bidder("A", values[0], values[0])] + others)
        honest_utility = truthful.result["utilities"]["A"]
        for lie in (values[0] // 2, values[0] * 2):
            deviant = run(name, [Bidder("A", values[0], lie)] + others)
            assert deviant.result["utilities"]["A"] <= honest_utility + 1e-9
```

- [ ] **Step 2: Run to verify they exercise real behaviour**

Run: `python -m pytest tests/test_mechanisms.py -v`
Expected: all pass. If truthfulness fails, the *mechanism* is wrong, not the test.

- [ ] **Step 3: Commit**

```bash
git add tests/test_mechanisms.py
git commit -m "test: cross-mechanism invariants and truthfulness property"
```

---

### Task 5: HTTP server with validation

**Files:**
- Create: `agt/serve.py`
- Test: `tests/test_serve.py`

`POST /run` is the trust boundary. Validate before running anything. The server binds `127.0.0.1`
only and resolves static paths against `web/` to block traversal — it is a local tool, but a local
tool that serves arbitrary files is still a bug.

- [ ] **Step 1: Write the failing tests**

```python
import pytest
from agt.serve import validate

VALID = {"mechanism": "second_price", "bidders": [{"id": "A", "value": 100, "bid": 95}], "params": {}}

def test_validate_accepts_good_payload():
    assert validate(VALID)["mechanism"] == "second_price"

@pytest.mark.parametrize("payload,message", [
    ({**VALID, "mechanism": "nope"}, "unknown mechanism"),
    ({**VALID, "bidders": []}, "between 1 and 12"),
    ({**VALID, "bidders": [{"id": "A", "value": -5, "bid": 1}]}, "non-negative"),
    ({**VALID, "bidders": [{"id": "A", "value": float("inf"), "bid": 1}]}, "finite"),
    ({**VALID, "params": {"bogus": 1}}, "unknown parameter"),
])
def test_validate_rejects_bad_payload(payload, message):
    with pytest.raises(ValueError, match=message):
        validate(payload)

def test_duplicate_bidder_ids_rejected():
    payload = {**VALID, "bidders": [{"id": "A", "value": 1, "bid": 1}, {"id": "A", "value": 2, "bid": 2}]}
    with pytest.raises(ValueError, match="unique"):
        validate(payload)
```

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest tests/test_serve.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'agt.serve'`

- [ ] **Step 3: Implement `agt/serve.py`**

Handler routes: `GET /` → `web/index.html`, `GET /<static>` → `web/` files, `GET /mechanisms` →
registry JSON, `POST /run` → validate then run. `ValueError` becomes 400 with its message; an
unexpected exception becomes 500 with the partial trace attached so a broken mechanism is still
debuggable in the UI. Body size capped so a huge POST cannot exhaust memory.

- [ ] **Step 4: Run to verify pass**

Run: `python -m pytest tests/test_serve.py -v`
Expected: 8 passed

- [ ] **Step 5: Commit**

```bash
git add agt/serve.py tests/test_serve.py
git commit -m "feat: stdlib HTTP server with request validation"
```

---

### Task 6: Web renderer

**Files:**
- Create: `web/index.html`, `web/app.js`

The renderer must stay mechanism-agnostic: it reads `step.label`, `step.detail`, `step.formula`,
`step.highlight`, and `step.state`, and draws them. If it ever branches on mechanism name, the
architecture has been violated.

Layout: setup panel on the left (mechanism select, bidder rows, params, Run). Main area shows the
stage list with the current step marked, a horizontal bar per bidder (value bar and bid bar,
highlighted bidders emphasized), the formula with real numbers, and transport controls. Result
panel below shows winner, price, revenue, welfare, and the efficiency flag.

- [ ] **Step 1: Build the form from `GET /mechanisms`**

The mechanism select and its parameter inputs are generated from the registry response — no
mechanism names hardcoded in JS.

- [ ] **Step 2: Render a trace**

`render(stepIndex)` draws the stage list, bars, formula, and result. Prev/next/auto/reset only
change `stepIndex` and re-render — no incremental mutation.

- [ ] **Step 3: Verify by hand**

Run: `python -m agt.serve` then open `http://127.0.0.1:8000`
Expected: default 3-bidder setup loads; stepping through second-price shows the price rule
selecting bidder B's bid; switching to English shows two dropout steps.

- [ ] **Step 4: Commit**

```bash
git add web/
git commit -m "feat: mechanism-agnostic step renderer"
```

---

### Task 7: Entry point, README, acceptance pass

**Files:**
- Create: `README.md`
- Modify: `agt/serve.py` (add `__main__` guard if not already present)

- [ ] **Step 1: Write the README** — what it is, `python -m agt.serve`, how to add a mechanism (one
      decorated generator in `agt/mechanisms.py`, zero UI changes), how to run tests.

- [ ] **Step 2: Run the full suite**

Run: `python -m pytest -v`
Expected: all pass

- [ ] **Step 3: Walk the acceptance criteria** from the spec — all six mechanisms run, stepping
      works both directions, editing a value and re-running updates without reload, invalid input
      shows an inline error.

- [ ] **Step 4: Commit**

```bash
git add README.md agt/serve.py
git commit -m "docs: README and phase 1 acceptance pass"
```

---

## Out of scope for phase 1

Bidder strategies, multi-item mechanisms (VCG, GSP), budget pacing, and equilibrium analysis are
phases 2–5 in the spec. Do not build hooks for them now; the registry and trace format are already
the extension points they need.
