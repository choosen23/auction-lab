# Phase 2: Bidder Strategies and Repeated Rounds — Implementation Plan

**Goal:** Watch *how bidders choose their bids*, and watch those choices move across repeated rounds until they settle.

**Architecture:** A strategy registry mirrors the existing mechanism registry, so the UI builds strategy dropdowns exactly as it already builds mechanism forms. A series runner computes each round's bids from strategies plus history, then calls the **unchanged** phase 1 `run()`. A round is just a `Trace`, so the existing step renderer keeps working untouched.

**Tech Stack:** Python 3.11+ stdlib only, pytest, vanilla JS + SVG. No new dependencies.

**Spec:** [2026-07-30-auction-visualizer-design.md](../specs/2026-07-30-auction-visualizer-design.md) (phase 2)
**Builds on:** [2026-07-30-phase1-single-item-auctions.md](./2026-07-30-phase1-single-item-auctions.md) (complete, 152 tests green)

---

## The teaching payoff

`best_response` is deliberately mechanism-agnostic: it evaluates candidate bids by *running the
mechanism*, rather than by closed-form math per mechanism. One consequence is the whole point of
the phase — the same strategy code produces opposite dynamics:

- Under **second-price**, best response lands on truthful in round 1 and never moves again.
- Under **first-price**, it walks downward into shading and keeps chasing rivals.

Seeing one algorithm behave in two ways because the *mechanism* changed is the lesson. It also
means every mechanism added later — including phase 3's VCG and GSP — gets a working
best-response bidder for free.

## What must not change

`agt/trace.py`, `agt/mechanisms.py`, `agt/stages.py`, `POST /run`, and the step renderer. Phase 2
is additive. If a task appears to require editing a mechanism, stop and escalate — it means the
seam is wrong.

## File Structure

| File | Responsibility |
|---|---|
| `agt/strategies.py` | Strategy registry, `StrategyContext`, `BidDecision`, and the four strategies. |
| `agt/series.py` | `run_series()`: drive rounds, thread history, compute the summary. |
| `agt/serve.py` (modify) | Add `GET /strategies` and `POST /run_series` plus their validation. |
| `web/app.js` (modify) | Round timeline, bid-path chart, strategy column in the bidder table. |
| `tests/test_strategies.py` | Per-strategy bid rules and the privacy guarantee. |
| `tests/test_series.py` | Round threading, convergence, summary math, the two dynamics above. |

---

### Task 1: Strategy registry and the three simple strategies

**Files:**
- Create: `agt/strategies.py`, `tests/test_strategies.py`

**Design — copy the shape of `agt/registry.py`, do not invent a second convention.**

```python
@dataclass(frozen=True)
class StrategyContext:
    bidder: Bidder            # id, private value, and the manually typed bid
    rival_ids: list[str]      # ids only — see the privacy rule below
    n: int                    # total bidders
    round: int                # 0-based
    history: list[RoundRecord]
    mechanism: str
    params: dict              # the mechanism's resolved params (reserve, etc.)

@dataclass(frozen=True)
class BidDecision:
    bid: Number
    why: str                          # one plain sentence, shown in the UI
    considered: list[dict] | None = None   # candidates weighed, for best_response
```

**Privacy rule (load-bearing):** a strategy may see its own bidder's private value, rivals' *ids*,
and rivals' *past bids* via `history`. It must **never** see a rival's current private value.
`StrategyContext` therefore carries `rival_ids`, not rival `Bidder` objects. A strategy that could
read rival values would silently teach that auctions are a full-information game, which is the
opposite of the point. Write a test that asserts the context exposes no rival values.

Strategies to implement:

- `manual` — returns the typed bid verbatim. The default, so phase 1 behavior is preserved.
- `truthful` — `bid = value`.
- `shade_bne` — `bid = value * (n - 1) / n`. This is the symmetric BNE for a first-price auction
  when values are i.i.d. uniform. **State that assumption in the `why` string**, including that it
  is not the equilibrium for other mechanisms or other value distributions. A learner applying it
  to second-price should be told, in the UI, that it is the wrong tool there.

- [ ] **Step 1: Write the failing tests** — each strategy's bid on a known context; `manual`
      round-trips the typed bid; `shade_bne` with n=3 and value 90 gives 60; the privacy test
      asserts `StrategyContext` has no attribute exposing rival values, and that no strategy's
      output changes when a rival's value (but not bid) changes.
- [ ] **Step 2: Run to verify failure** — `python3 -m pytest tests/test_strategies.py -v`
- [ ] **Step 3: Implement** the registry, `@strategy` decorator with a form-ready params schema, a
      `strategy_schema()` serializer, and the three strategies.
- [ ] **Step 4: Run to verify pass** — plus the full suite, which must stay green.
- [ ] **Step 5: Commit** — `feat: strategy registry with manual, truthful and BNE shading`

---

### Task 2: `best_response`

**Files:**
- Modify: `agt/strategies.py`, `tests/test_strategies.py`

Best response to rivals' **previous-round** bids, assuming they repeat them (the standard Cournot /
fictitious-play assumption — say so in the `why` string, since it is exactly the assumption that
makes the dynamics interesting and is also the assumption that fails in reality).

- Round 0 has no history, so start from the bidder's typed bid.
- Candidate bids: `0`, the bidder's own `value`, and each rival's last bid both as-is and plus one
  `tick`, all clipped to `[0, value]` and de-duplicated. `tick` is a strategy param, default 1.
- Evaluate each candidate by **calling `run()`** with that candidate and the rivals' last bids,
  reading the resulting utility for this bidder. No per-mechanism math.
- Pick the highest utility. Break ties toward the **lower** bid, then toward the truthful bid — a
  bidder indifferent between bids should not be shown bidding aggressively for no reason.
- Record every candidate and its utility in `BidDecision.considered` so the UI can show the
  deliberation, not just the conclusion.

Never bid above `value` (clipping guarantees it). Assert this in a test — it is the invariant that
keeps the demonstration honest.

- [ ] **Step 1: Write the failing tests**
  - Under `second_price`, best response equals the bidder's value (truthfulness is the best reply
    to anything) — test across several rival configurations.
  - Under `first_price` against a known rival bid, the best response is just above that rival bid,
    not the bidder's value, and strictly below `value` when there is room.
  - Candidates never exceed `value`.
  - `considered` is populated and each entry carries a candidate bid and its utility.
- [ ] **Step 2: Run to verify failure**
- [ ] **Step 3: Implement**
- [ ] **Step 4: Run to verify pass** — full suite green.
- [ ] **Step 5: Commit** — `feat: mechanism-agnostic best-response bidder`

---

### Task 3: `run_series` and the summary

**Files:**
- Create: `agt/series.py`, `tests/test_series.py`

```python
run_series(mechanism, bidders, strategies, rounds=8, params=None) -> Series
```

`strategies` maps bidder id → `{"name": str, "params": dict}`. Each round: build a
`StrategyContext` per bidder, collect `BidDecision`s, construct the round's `Bidder` list with
those bids, call the unchanged `run()`, and append a `RoundRecord`.

```python
@dataclass(frozen=True)
class RoundRecord:
    round: int
    decisions: dict[str, BidDecision]   # why each bidder bid what they bid
    trace: Trace

@dataclass(frozen=True)
class Series:
    mechanism: str
    params: dict
    strategies: dict
    rounds: list[RoundRecord]
    summary: dict
```

`summary` carries: `bid_paths` (bidder id → bid per round, what the timeline chart draws),
`utilities` (per round) and `cumulative_utilities`, `revenue` per round, `efficiency_rate` (share
of rounds allocated to the highest-value bidder), plus `converged` and `converged_round`.

**Convergence:** every bidder's bid changes by less than a tolerance between consecutive rounds.
Report the first round at which this holds and continues to hold. `converged` is `False` when the
series ends still moving — say so rather than implying a settled equilibrium.

Validate `rounds` in 1–50, and every strategy name against the registry. Bidder values stay fixed
across rounds in this phase; only bids move.

- [ ] **Step 1: Write the failing tests**
  - A one-round series with `manual` reproduces exactly what `run()` alone produces — the seam
    holds, phase 1 is genuinely untouched.
  - **`second_price` + `best_response` converges by round 1 and every bidder's settled bid equals
    their value.**
  - **`first_price` + `best_response` produces settled bids strictly below value for the winner.**
    (Assert the direction and the bound, not an exact sequence — the exact path is an
    implementation detail and pinning it would make the test brittle.)
  - `cumulative_utilities` equals the per-round sum; `efficiency_rate` is right on a hand-checked
    series; a non-converging series reports `converged is False`.
  - Unknown strategy name and out-of-range `rounds` raise `ValueError`.
- [ ] **Step 2: Run to verify failure**
- [ ] **Step 3: Implement**
- [ ] **Step 4: Run to verify pass** — full suite green.
- [ ] **Step 5: Commit** — `feat: repeated-round series runner with convergence summary`

---

### Task 4: Server endpoints

**Files:**
- Modify: `agt/serve.py`, `tests/test_serve.py`

- `GET /strategies` → `strategy_schema()`, same shape contract as `GET /mechanisms`.
- `POST /run_series` → `{mechanism, bidders, strategies, rounds, params}` → `Series.to_dict()`.

Extend `validate()` — or add a sibling that **reuses** its bidder and param checks rather than
duplicating them. Additional rules: `rounds` an int in 1–50; `strategies` a dict keyed by bidder id
covering exactly the submitted bidders; each strategy name in the registry; strategy params checked
against the registry schema. Same error contract as phase 1: `ValueError` → 400 `{"error": ...}`.

Note the compute bound: 50 rounds × 12 bidders × `best_response` evaluating ~2n+2 candidates each
means the existing body-size cap is no longer the only limit that matters. Confirm a worst-case
request completes in reasonable time; if it does not, lower the round cap and say so.

- [ ] **Step 1: Write the failing tests** — happy path, unknown strategy, rounds out of range,
      strategies not matching the bidder ids, and a worst-case timing check.
- [ ] **Step 2: Run to verify failure**
- [ ] **Step 3: Implement**
- [ ] **Step 4: Run to verify pass** — full suite green.
- [ ] **Step 5: Commit** — `feat: strategy and series endpoints`

---

### Task 5: Round timeline UI

**Files:**
- Modify: `web/app.js`, `web/index.html`, `web/style.css`

The step renderer must stay exactly as it is. Everything here wraps *around* it.

- **Strategy column** in the bidder table: a `<select>` per bidder, built from `GET /strategies`,
  plus any params that strategy declares. The typed bid input stays — it is what `manual` uses and
  what `best_response` starts from — but dim it when the selected strategy ignores it.
- **Rounds input** and a Run-series button, alongside the existing single-run button. Running a
  single auction must still work exactly as before.
- **Round timeline** above the stage list: one marker per round, current round marked, click to
  jump. Selecting a round feeds that round's trace to the *existing* `render(stepIndex)`.
- **Bid-path chart**: one line per bidder, bid against round. Mark the converged round when there
  is one. This is where shading and convergence become visible, so follow the `dataviz` skill for
  scale, color, and labeling.
- **Decision panel**: the current round's `why` for each bidder, and for `best_response` the
  `considered` candidates with their utilities.
- Handle `converged: false` honestly — label it "still moving", never imply an equilibrium.

- [ ] **Step 1: Extend the setup form from `GET /strategies`** — no strategy names hardcoded in JS.
- [ ] **Step 2: Round timeline and bid-path chart**
- [ ] **Step 3: Decision panel**
- [ ] **Step 4: Verify by hand** — `python3 -m agt.serve`, then confirm second-price +
      best_response flatlines at truthful by round 1 while first-price + best_response visibly
      shades. That contrast is the acceptance test for the whole phase.
- [ ] **Step 5: Commit** — `feat: round timeline and bid-path chart`

---

### Task 6: README and acceptance

- [ ] Update `README.md`: the strategy table, `run_series`, and how to add a strategy (one
      decorated function, zero JS changes — same promise as mechanisms).
- [ ] Full suite green.
- [ ] Walk the acceptance criteria below.
- [ ] Commit — `docs: phase 2 README and acceptance pass`

## Phase 2 acceptance criteria

- All four strategies selectable per bidder, generated from the registry with no hardcoded names.
- `second_price` + `best_response` converges to truthful bidding by round 1 and holds.
- `first_price` + `best_response` settles strictly below value, visibly shading on the chart.
- Selecting any round drives the unchanged phase 1 step view for that round.
- A single-auction run still behaves exactly as it did in phase 1.
- Each bidder's `why` explains its bid in one plain sentence; `best_response` also shows what it
  weighed.
- Full suite green, including a test that no strategy can read a rival's private value.

## Out of scope

Bandit and RL bidders (phase 4), budget pacing (phase 4), multi-item mechanisms (phase 3),
equilibrium computation (phase 5). Values stay fixed across rounds; redrawn values per round are a
phase 4 concern.
