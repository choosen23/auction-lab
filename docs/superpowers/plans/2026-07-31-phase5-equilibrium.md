# Phase 5: Equilibrium Analysis — Implementation Plan

Phases 1–4 all answer the same shape of question: *given these bids, what happens?* A trace
runs one profile. A series runs a sequence of profiles that strategies happened to choose.
Neither ever asks the question underneath, which is the one game theory is actually about:

> Of all the bid profiles that could have been played, which ones are **stable** — nobody
> wishing they had done something else — and what does a bidder's payoff look like across
> its whole range rather than at the single point it bid?

Phase 5 answers that, and it answers it the same way phase 2's `best_response` does: by
**running the mechanism**, never by deriving a formula. That is the constraint the whole
phase hangs off, and it is why a mechanism written next year gets equilibrium analysis for
free the same way it got a best-response bidder for free.

## The teaching payoff

Three things a learner cannot get from any earlier phase.

**A best-response curve shows the shape of the incentive, not one point on it.** Phase 2's
`best_response` reports the argmax and nothing else, so a learner sees *what* to bid and
never *why*. The curve shows the plateau under second-price rules — every bid from the
rival's bid up to your value pays exactly the same, which is the visual form of "your bid
does not set your price" — against the single sharp peak under first-price rules, where
one bid is right and both directions off it cost money.

**Nash is weaker than dominance, and second-price proves it on its own grid.** Truthful
bidding is a Nash equilibrium of a second-price auction. So is "the bidder who values the
item at 100 bids nothing, and the bidder who values it at 80 bids 100 and takes it" —
neither can improve unilaterally, so it is genuinely an equilibrium, and it is obvious
nonsense. Search the grid and both fall out, side by side. A learner who has only ever
heard "second-price is truthful" gets shown the exact sense in which that sentence is
about *dominance* and the exact sense in which Nash does not deliver it.

**Revenue equivalence is a theorem about equilibrium, not about mechanisms.** Run
first-price with its shading rule, second-price with truth, all-pay with its own, over the
same value draws: three completely different payment rules, one expected revenue, matching
the closed form `H(n-1)/(n+1)`. Then break one assumption — let bidders draw from
*different* ranges while still playing the symmetric rule — and first-price revenue departs
from second-price by six to ten currency units on a hundred-unit item. The theorem did not
fail; its hypothesis did, and the check says which one.

## The seam that decides this phase

Everything here is a **read-only analysis of a mechanism the engine already has**. It adds
no mechanism, no strategy, no round, and no state. Concretely:

* it calls `run()` and reads `result["utilities"]`, which is the same door `best_response`
  and `regret.py` already use;
* it therefore works on every mechanism declaring `input_kind="single"`, including the
  multi-slot ones, with no per-mechanism code;
* nothing in phases 1–4 changes. The endpoint is additive, the UI panel is additive, and
  a failure in any of it cannot reach `/run` or `/run_series`.

The one thing it may **not** do is derive a payoff. The moment a payment rule is restated
outside the mechanism that owns it, the analysis is of a model of the auction rather than
of the auction, and every lesson it teaches is one refactor from becoming a lie.

## The two model choices that are easy to get wrong

**A grid is a different game from the continuum, and the report must say so.** Pure Nash
equilibria are found by exhaustive search over a finite grid of bids. That is a Nash
equilibrium *of the discretized game*. Coarsen the grid and equilibria appear that the
continuous game does not have; refine it and some vanish. The grid is therefore reported
alongside the equilibria, always, and every bidder's own value is forced onto it — without
that, "is truthful an equilibrium?" is a question the search is structurally unable to
answer, and it would answer "no" rather than "I cannot see it".

**Monte-Carlo agreement is a statistical claim and needs a statistical test.** Two revenue
means differing by 0.24 on a sample of 400 draws is not evidence of anything. The check
uses **common random numbers** — every mechanism is scored on the identical value draws —
so the comparison is a paired difference whose standard error is far smaller than either
mean's, and the verdict is `|difference| <= 3 x stderr(difference)`. Reporting two means
and eyeballing them would fail to detect a real gap and would also invent gaps that are not
there.

## Task 1: the grid game — payoff table, best-response curves

`agt/equilibrium.py`. `bid_grid()` builds evenly spaced bids from 0 to the largest value in
the table, unioned with every bidder's own value. `payoff_table()` runs the mechanism at
every profile and stores each bidder's utility, storing `None` for a profile the mechanism
refuses. `best_response_curve()` holds rivals at their submitted bids and sweeps one bidder
across the grid.

- [ ] **Test first:** a spot-checked cell of the payoff table equals a direct `run()`;
      the second-price curve is a plateau over `[rival bid, value]` and the first-price
      curve has a strict single peak below value.
- [ ] Implement, full suite green, commit — `feat: bid grid, payoff table and best-response curves`

## Task 2: pure Nash and dominance

Exhaustive search over the profile space, bounded by `MAX_PROFILES` before it starts.
Dominance asks the sharper question the same table already answers: is bidding your value a
best reply to *every* rival profile, not merely to one? When it is not, report the
profitable deviation explicitly.

- [ ] **Test first:** truthful is a pure Nash of `second_price` **and** it is not the only
      one; `first_price` truthfulness is refuted by an explicitly asserted deviation with a
      strictly positive gain; `all_pay` has no pure Nash on the grid.
- [ ] **Test first (cross-registry):** for every mechanism reading scalar bids, the grid's
      dominance verdict equals the `truthful_dominant` flag it declares. A mechanism whose
      declaration is wrong fails here rather than quietly teaching the wrong lesson.
- [ ] Implement, full suite green, commit — `feat: pure Nash search and grid dominance`

## Task 3: the revenue-equivalence check

`agt/revenue.py`. Symmetric BNE bid functions for the five single-item mechanisms, Monte
Carlo over seeded i.i.d. uniform draws with common random numbers, paired-difference
verdicts against second-price, and the closed-form benchmark.

- [ ] **Test first:** symmetric draws — all five mechanisms agree within the paired error
      and land on `H(n-1)/(n+1)`; `english` matches `second_price` and `dutch` matches
      `first_price` *exactly*, draw for draw, because they are the same auction.
- [ ] **Test first:** asymmetric ranges — `first_price` revenue diverges from `second_price`
      by many standard errors, and the report says the hypothesis that failed.
- [ ] **Test first:** the whole check draws from a seeded stream and leaves the global
      `random` module untouched.
- [ ] Implement, full suite green, commit — `feat: revenue equivalence check with paired verdicts`

## Task 4: API and validation

One endpoint, `POST /equilibrium`, reusing `validate()` for the bidder rules and adding
`steps`, `draws` and `seed`. Package mechanisms are refused by name and reason, exactly as
repeated rounds refuses them.

- [ ] **Test first:** a package mechanism is refused with its reason; out-of-range `steps`
      and `draws` are 400s; a valid body is a 200 carrying all three analyses.
- [ ] Implement, full suite green, commit — `feat: equilibrium analysis through the API`

## Task 5: the equilibrium panel

`web/equilibrium.js`, booted from the existing `window.<name>Ext` seam in `app.js` and
adding nothing to the step renderer. Best-response curve as an SVG line chart with the
value and the argmax marked, an equilibrium table, and the revenue comparison.

- [ ] No JavaScript branches on a mechanism name. Every element is drawn because the
      response carries the series it needs.
- [ ] Verify by hand: the second-price plateau, the first-price peak, second-price showing
      several equilibria of which one is truthful, and the asymmetric revenue gap.
- [ ] Commit — `feat: equilibrium panel with best-response and revenue charts`

## Task 6: README and acceptance

- [ ] README: the grid caveat, what Nash does and does not promise, and the two revenue
      tables.
- [ ] Full suite green; walk the acceptance criteria.
- [ ] Commit — `docs: phase 5 README and acceptance pass`

## Phase 5 acceptance criteria

1. Every payoff comes from a real `run()` of a real mechanism. No payment rule is restated.
2. The reported grid is always shown with the equilibria found on it, and every bidder's
   value is on that grid.
3. `second_price` reports truthful bidding as dominant, as a Nash equilibrium, and as **not
   the only** Nash equilibrium.
4. `first_price`, `dutch`, `all_pay` and `gsp` each report truthfulness refuted by a
   deviation with a strictly positive gain.
5. The grid dominance verdict agrees with every mechanism's declared `truthful_dominant`.
6. Symmetric revenue equivalence holds within the paired Monte-Carlo error; the asymmetric
   run breaks it and names the assumption.
7. Work is bounded before it starts. A request too large to search says so and still
   returns the analyses that do fit.
8. Phases 1–4 are untouched and their tests still pass.

## Out of scope

* **Mixed-strategy equilibria.** `all_pay` has no pure equilibrium and its mixed one is the
  interesting object, but support enumeration is a solver, not a seam, and it would be the
  first thing here that is genuinely a different program. The report says "none on this
  grid" and says why, which is honest; claiming there is no equilibrium would not be.
* **Bayes-Nash computation.** The revenue check *uses* known BNE bid functions; it does not
  compute them. Solving for one numerically is a fixed-point problem over function space.
* **Equilibria of the package mechanisms.** A bidder submitting XOR bundles has no scalar
  strategy, so it has no grid — the same reason repeated rounds refuses them.
