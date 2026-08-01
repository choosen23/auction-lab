# Phase 3: Multi-Item Mechanisms — Implementation Plan

**Goal:** Allocate several things at once — ad slots, then arbitrary bundles of items — and see what changes when one winner becomes many.

**Architecture:** Two sub-phases. **3a (position auctions)** keeps today's scalar `Bidder` untouched and only generalizes scoring from one winner to an allocation, which buys GSP and VCG. **3b (combinatorial)** introduces XOR package bids as a second *input kind*, declared per mechanism so the form switches itself.

**Tech Stack:** Python 3.11+ stdlib only, pytest, vanilla JS + SVG. No new dependencies.

**Spec:** [2026-07-30-auction-visualizer-design.md](../specs/2026-07-30-auction-visualizer-design.md) (phase 3)
**Builds on:** phases 1 and 2, complete, 256 tests green.

---

## The teaching payoff

Phase 3 exists for one comparison: **GSP is not truthful, VCG is** — on the same slots, the same
bidders, the same click-through rates, differing only in the payment rule. Sponsored search runs
GSP anyway. Making that trade-off concrete is the point of the sub-phase.

Then 3b adds the second lesson: **finding the best allocation is NP-hard**, so practical
combinatorial auctions run greedy and leave welfare on the table. Showing greedy and optimal side
by side puts a number on what the approximation costs.

A bonus falls out for free: `best_response` from phase 2 evaluates candidates by *running the
mechanism*, and GSP takes scalar bids, so GSP best-response dynamics work with no new strategy
code. GSP is known to cycle rather than settle. Check it and report what you see.

## Two seams that will break if you are careless

1. **`outcome()` in `agt/trace.py` is single-winner.** Do not change its signature — five
   mechanisms and 256 tests depend on it. Add a sibling for allocations instead.
2. **The cross-mechanism invariant tests are parametrized over the whole registry.** Every
   mechanism you register inherits them, and they currently assume one winner and read
   `state["winner"]`. Generalize the invariants to hold for both shapes rather than excluding
   multi-item mechanisms from them — an excluded mechanism is an untested mechanism. If some
   invariant genuinely cannot generalize, say so explicitly rather than quietly narrowing the
   parametrization.

---

# Phase 3a — Position auctions

### Task 0 (prerequisite): mechanisms declare whether truth is dominant

**Files:** `agt/registry.py`, `agt/mechanisms.py`, `agt/strategies.py`, tests

`agt/strategies.py` hardcodes `FIRST_PRICE_LIKE` / `SECOND_PRICE_LIKE` tuples to choose which
caveat sentence a `why` string shows. They are correct for the five current mechanisms and wrong
the moment VCG exists: VCG would fall into the `else` branch and tell the learner truthful bidding
is not dominant, which is false.

Move the fact to where it belongs. Add `truthful_dominant: bool` to the `@mechanism` decorator and
the `Mechanism` record; have `truthful` and `shade_bne` read
`REGISTRY[context.mechanism].truthful_dominant`; delete both tuples. Include it in
`registry_schema()` so the UI could surface it later.

- [ ] Test first: every registered mechanism declares the flag; `truthful`'s `why` says "dominant"
      for second-price/English and "not dominant" for first-price/Dutch/all-pay, driven by the flag
      rather than by a name list.
- [ ] Implement, full suite green, commit — `refactor: mechanisms declare whether truth is dominant`

### Task 1: allocation scoring

**Files:** `agt/trace.py`, `tests/test_trace.py`

Add alongside `outcome()`, leaving it untouched:

```python
outcome_allocation(bidders, allocation, payments, gains, best_possible_welfare) -> dict
```

- `allocation`: bidder id → what they got (a slot index, a tuple of items), absent when unallocated.
- `gains`: bidder id → gross value received. For positions that is `value_per_click × ctr`, so it
  is *not* simply the bidder's `value` — which is exactly why this cannot reuse `outcome()`.
- `best_possible_welfare` is passed in, because only the mechanism knows what was achievable.

Returns the phase 1 keys plus `allocation` and `best_possible_welfare`. Keep `winner` and `price`
populated with the top allocated bidder and their payment, so the **existing UI result panel keeps
working unchanged**. `efficient` is `welfare == best_possible_welfare`.

- [ ] Test first: utilities are `gains - payments`; unallocated bidders get 0 gain; `welfare` sums
      gains; `efficient` true only at the optimum; `winner`/`price` still populated for a
      single-slot allocation and match what `outcome()` would have produced for the same case.
- [ ] Implement, full suite green, commit — `feat: allocation scoring for multi-item mechanisms`

### Task 2: GSP and VCG position auctions

**Files:** `agt/mechanisms.py` (or a new `agt/positions.py` if it would pass ~400 lines), tests

Both take scalar per-click bids — today's `Bidder` unchanged — plus params:
`slots` (int, default 3) and `ctr_decay` (number, default 0.5), giving slot *i* a click-through
rate of `ctr_decay ** i`.

> `ponytail:` geometric CTRs from two number params, because the params schema validates numbers
> only. Ceiling: real CTR curves are not geometric. Upgrade: widen `_validate_param` to accept a
> list of numbers and take explicit CTRs.

- **`gsp`** — bidders ranked by bid; slot *i* goes to the *i*th ranked bidder, who pays the **next
  bidder's bid** per click. Not truthful.
- **`vcg_positions`** — same assortative allocation, but each winner pays the externality they
  impose: the welfare others would have gained had this bidder not participated, minus what they
  actually gained. Truthful.

Steps must make the payment rules legible: rank, assign slots, then a per-winner payment step
showing the arithmetic. Reuse `agt/stages.py` helpers where they fit rather than duplicating them.

**The tests that carry the lesson:**

- [ ] With identical bidders and slots, `gsp` and `vcg_positions` produce the **same allocation**
      but **different payments** — assert both halves; that pairing is the whole point.
- [ ] `vcg_positions` passes the existing truthfulness property test — add it to that
      parametrized list.
- [ ] **`gsp` fails truthfulness, and a test asserts a profitable deviation exists.** Construct one
      explicitly and pin it. A test that merely omits GSP from the truthful list would let a
      silently-truthful (i.e. wrong) GSP pass.
- [ ] Efficiency: both allocate assortatively, so both are efficient; `best_possible_welfare` is
      the assortative welfare.
- [ ] Edge cases: fewer bidders than slots, more bidders than slots, `slots=1` (GSP collapses to
      second-price and `vcg_positions` to second-price — assert both, they are free equivalence
      checks), ties, and a reserve if you support one.
- [ ] Implement, full suite green, commit — `feat: GSP and VCG position auctions`

### Task 3: GSP best-response dynamics (investigation, not new code)

- [ ] Run `run_series` with `gsp` + `best_response` and record what happens. GSP is known to cycle
      rather than converge. Report the actual bid paths and whether `converged` came back false.
      If `best_response` cannot drive GSP at all, that is a seam defect — report it, do not paper
      over it.
- [ ] Add a test pinning whatever the real behaviour is, by direction and not by exact sequence.
- [ ] Commit — `test: GSP best-response dynamics`

### Task 4: allocation in the UI

**Files:** `web/app.js`, `web/index.html`, `web/style.css`

The step renderer stays generic. `result.allocation` and the per-slot view are additive.

- [ ] Show the slot ladder: which bidder holds which slot, its CTR, the per-click price, and the
      resulting payment and utility. Follow the `dataviz` skill.
- [ ] Single-winner mechanisms must render exactly as they do today — `allocation` is simply
      absent, and nothing on screen may change for them.
- [ ] Verify by hand that `gsp` and `vcg_positions` on the same input show the same ladder with
      different prices. That contrast is the acceptance test for 3a.
- [ ] Commit — `feat: slot ladder for position auctions`

---

# Phase 3b — Combinatorial auctions

### Task 5: XOR package bids and winner determination

**Files:** `agt/packages.py`, `tests/test_packages.py`

```python
@dataclass(frozen=True)
class PackageBid:
    bidder: str
    items: tuple[str, ...]
    value: Number
    bid: Number
```

The item universe is the union of items mentioned across all bids — no separate items parameter.
**XOR semantics:** a bidder may submit several package bids but can win at most one.

Two solvers:

- `greedy_allocate(bids)` — take bids in descending bid order, skipping any that conflict with an
  already-accepted bid (shares an item, or is from a bidder who already won). The practical
  algorithm.
- `optimal_allocate(bids)` — exhaustive search for maximum total bid subject to the same
  constraints. Winner determination is NP-hard, so bound the input: refuse above roughly 12 items
  or 20 bids with a clear `ValueError` naming the limit. Verify the bound empirically and state
  your measured worst case.

- [ ] Test first: greedy and optimal agree on non-conflicting inputs; a hand-built case where
      greedy is **strictly worse** than optimal (the case that justifies showing both); XOR is
      enforced (one bidder never wins two bundles); items are never double-allocated; empty input;
      a single bid; the size guard fires with a readable message.
- [ ] Implement, full suite green, commit — `feat: XOR package bids with greedy and optimal solvers`

### Task 6: combinatorial mechanisms

**Files:** `agt/packages.py`, tests

- **`greedy_package`** — greedy allocation, winners pay their own bid (first-price). Practical, not
  truthful.
- **`vcg_package`** — **optimal** allocation with VCG payments: each winner pays the welfare others
  lose by their presence. Truthful, but only when the allocation is optimal — which is precisely
  why running VCG payments on top of a greedy allocation breaks truthfulness. Say that in the step
  text; it is a real trap in practice, not a footnote.

The result must carry both `welfare` and the **greedy-vs-optimal gap**, so the cost of the
approximation is visible rather than asserted.

- [ ] Test first: `vcg_package` is truthful on small instances (property test over random values);
      `greedy_package` is not, with a pinned profitable deviation; the welfare gap is reported and
      is zero when greedy happens to find the optimum; complementarities work (a bidder wanting
      `{A,B}` together beats two separate bidders when their sum is lower).
- [ ] Implement, full suite green, commit — `feat: greedy and VCG combinatorial auctions`

### Task 7: package input kind through registry, API and validation

**Files:** `agt/registry.py`, `agt/api.py`, `agt/serve.py`, tests

Add `input_kind: "single" | "package"` to `Mechanism`, defaulting to `"single"` so every existing
mechanism is unaffected. Expose it in `registry_schema()` so the UI can switch its form.

`POST /run` accepts `packages` instead of `bidders` when the mechanism declares `"package"`, and
rejects the wrong input kind with a message that names which one was expected. Validate: bidder ids
and item names non-empty strings, values and bids finite and non-negative, bounded counts, and the
solver size guard surfaced as a 400 rather than a 500.

**`POST /run_series` must reject package mechanisms** with a clear message — phase 2 strategies
produce a scalar bid and have no meaning over bundles. Do not fake it.

- [ ] Test first, including both wrong-input-kind directions and the series rejection.
- [ ] Implement, full suite green, commit — `feat: package input kind through the API`

### Task 8: package UI

**Files:** `web/app.js` / a new `web/packages.js`, `web/index.html`, `web/style.css`

- [ ] The bidder table switches to package rows (bidder, items, value, bid) when the selected
      mechanism declares `input_kind: "package"`, driven by the registry — **no mechanism names in
      JS**. Items entered as free text; the universe is whatever was typed.
- [ ] Show the allocation: which bundle each winner took, which items went unsold, payments,
      utilities, and the greedy-vs-optimal welfare gap.
- [ ] Hide or disable the series controls for package mechanisms, matching the API's rejection.
- [ ] Verify by hand end to end.
- [ ] Commit — `feat: package bid table and allocation view`

### Task 9: README and acceptance

- [ ] README: position auctions, the GSP-vs-VCG contrast, package bids, the greedy/optimal gap, and
      how to add a package mechanism. Remove the "known phase 3 prerequisite" note that task 0 fixes.
- [ ] Full suite green; walk the acceptance criteria.
- [ ] Commit — `docs: phase 3 README and acceptance pass`

## Phase 3 acceptance criteria

- `gsp` and `vcg_positions` on identical input allocate identically and charge differently.
- A test pins a **profitable deviation in GSP** and `vcg_positions` passes the truthfulness property.
- `slots=1` collapses both position auctions to second-price.
- Greedy and optimal combinatorial allocations differ on the pinned case, and the gap is shown.
- `vcg_package` is truthful on small instances; `greedy_package` has a pinned profitable deviation.
- Package mechanisms are rejected by `/run_series` with a clear message.
- Single-item mechanisms look and behave exactly as they did in phase 2 — nothing regresses.
- Cross-mechanism invariants still run over *every* registered mechanism, multi-item included.

## Out of scope

Budget pacing and bandit bidders (phase 4), equilibrium computation (phase 5), reserve prices in
combinatorial settings, and any bid language beyond XOR.
