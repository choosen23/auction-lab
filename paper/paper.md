---
title: 'auction-lab: stepping through auction mechanisms one rule at a time'
tags:
  - auction theory
  - mechanism design
  - algorithmic game theory
  - economics education
  - interactive visualization
  - Python
authors:
  - name: Michail Filippou
    orcid: 0009-0005-7388-0680
    affiliation: 1
affiliations:
  - name: 'TODO: your university / institute, exactly as you want it printed'
    index: 1
date: 2 September 2026
bibliography: paper.bib
---

# Summary

`auction-lab` is an interactive teaching tool that renders auction mechanisms as
inspectable sequences of algorithmic steps. A learner sets up bidders and their
private values, picks a mechanism, and steps through the run: bids arrive, get
sorted, a winner is chosen, a pricing rule fires, payments settle. Every step
shows the rule that fired, in plain language, with the real numbers substituted
— so "the winner pays the highest losing bid" is not a sentence to memorize but
a state transition to watch.

The tool covers the classical single-item mechanisms (first-price, second-price
[@vickrey1961], all-pay, English and Dutch clocks), position auctions (GSP
[@edelman2007] and VCG), combinatorial auctions with greedy and optimal winner
determination, repeated-round bidder strategies with budget pacing and bandit
learners, and pure-strategy equilibrium analysis including a seeded,
statistically paired test of revenue equivalence [@myerson1981]. It ships as a
Python standard-library server and dependency-free JavaScript: no packages, no
build step. A hosted instance runs at <https://auctionlab.dev>; locally,
`python3 -m agt.serve` is the entire installation.

# Statement of need

Auction theory is taught almost everywhere strategic reasoning is taught —
economics, computer science, operations research [@krishna2009;
@roughgarden2016] — and its central results are famously counterintuitive.
Truthful bidding being *dominant* under second-price rules, lying being
*profitable* under GSP, revenue equivalence holding across payment rules that
look nothing alike: students can verify each claim on paper for one worked
example, but the derivation hides the mechanism's operation behind algebra.

Existing software comes from the other direction. `AuctionGym` [@jeunen2022]
and `OpenSpiel` [@lanctot2019] treat auctions as environments for learning
agents: excellent for research, but the mechanism itself is a black box that
maps bids to outcomes in one step. Classroom experiments (oral double auctions,
web voting tools) exercise intuition but cannot show the algorithm. What was
missing is the middle layer: the mechanism as an *inspectable algorithm*, where
each rule application is a visible, explained state transition.

`auction-lab`'s design commitments follow from that goal:

- **The engine explains itself.** Each mechanism emits a trace — an ordered
  list of steps carrying a state snapshot and the rule that fired, with numbers
  substituted. The web interface is a renderer of traces and knows no mechanism
  by name, so a new mechanism is one decorated Python generator and zero lines
  of JavaScript.
- **Claims are measured, not asserted.** The equilibrium view computes payoffs
  by *running the real mechanism* over a bid grid, so dominance verdicts,
  best-reply curves, and Nash profiles come out per-mechanism without
  per-mechanism analysis code. Untruthfulness is always pinned to an explicit
  profitable deviation, never reported as an absence.
- **Negative results are lessons.** All-pay's missing pure equilibrium,
  best-response cycling, revenue equivalence *breaking* when its i.i.d.
  hypothesis is removed, a reserve price blocking an efficient sale — each is
  reported as the outcome it is, not smoothed over.
- **The test suite enforces the pedagogy.** Cross-mechanism invariants are
  parametrized over the registry; a mechanism's declared `truthful_dominant`
  flag is checked against its own measured payoff table, so a mechanism that
  lies about itself fails CI rather than quietly teaching the wrong lesson.

The tool is aimed at instructors of auction theory and mechanism design
courses, students meeting these results for the first time, and practitioners
(notably in ad tech, where GSP and pacing are daily realities) who want the
textbook results connected to runnable behavior.

# Worked examples as the front door

The page opens on a worked example, with a row of them along the top — each a
question rather than a setting: *Honesty is safe*, *Why everyone shades*,
*Losers pay too*, *The budget runs out*, *Where does it settle?* One click
fills the whole form and runs it. Every example lives on the server and is
tested by posting it to the endpoint its button actually calls, because an
example that fails when clicked is worse than none.

![Stepping through a second-price auction: collect bids, sort, allocate, price
rule, payments.](../docs/img/walkthrough.gif)

# Acknowledgements

The interface's chart conventions follow accessibility-validated palettes, and
the project's phase plans and architecture notes are preserved in the
repository under `docs/`.

# References
