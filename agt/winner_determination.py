"""Winner determination: choosing which package bids to accept.

This module is the combinatorial-optimisation half of a package auction and knows nothing
about money. A :class:`PackageBid` is an all-or-nothing offer — "I will pay 10 for the
northern and southern licences together, and nothing for either alone" — which is exactly
what running several single-item auctions side by side cannot express: a bidder who needs
both halves of a radio band is worth more with the pair than the halves are worth apart.

**XOR semantics.** A bidder may submit several package bids, and they are alternatives:
at most one of them can win. The item universe is simply the union of every item anybody
mentioned; there is no separate list of what is for sale.

There are two solvers here because the honest answer is expensive. Choosing the best set
of bids to accept is NP-hard — it is weighted set packing — so real combinatorial
auctions run something greedy and accept that they left welfare on the table.
:func:`greedy_allocate` is that practical algorithm and :func:`optimal_allocate` is the
exhaustive one, kept side by side so the cost of the approximation is a number on the
screen rather than a claim in a footnote.

The auctions that put a price on these allocations live in :mod:`agt.packages`.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from agt.trace import Number

# ponytail: hard caps on the exhaustive search rather than a time budget or a real
# branch-and-bound solver, because a cap is one comparison and a time budget makes the
# answer depend on how busy the machine is — and a teaching example that silently
# returned a different allocation on a slow day would be worse than one that refuses.
# Measured, not assumed: the slowest of 24,000 random instances plus hand-built
# adversaries at exactly this bound takes 3.8ms for one search. The worst shape is 20
# near-equal singleton bids from 20 bidders over 12 items, which maximises the number of
# feasible allocations the search has to walk. End to end that is 9.5ms for a whole
# `greedy_package` run and 33.5ms for `vcg_package`, which runs one further search per
# winner — comfortably inside one HTTP request.
# Ceiling: a real spectrum auction has hundreds of items and this would refuse it.
# Upgrade: swap the body of `optimal_allocate` for an LP relaxation with branch-and-bound.
MAX_ITEMS = 12
MAX_BIDS = 20


@dataclass(frozen=True)
class PackageBid:
    """One all-or-nothing offer: ``bid`` for exactly ``items``, worth ``value`` to win.

    ``value`` is private and only the engine sees it, exactly as in :class:`~agt.trace.Bidder`;
    ``bid`` is what the mechanism is allowed to look at.
    """

    bidder: str
    items: tuple[str, ...]
    value: Number
    bid: Number

    def to_dict(self) -> dict[str, Any]:
        return {
            "bidder": self.bidder,
            "items": list(self.items),
            "value": self.value,
            "bid": self.bid,
        }


Bids = Sequence[PackageBid]
Allocation = tuple[PackageBid, ...]


def item_universe(bids: Bids) -> tuple[str, ...]:
    """Everything anybody bid on, sorted, each item once."""
    return tuple(sorted({item for b in bids for item in b.items}))


def total_bid(accepted: Bids) -> Number:
    """What an allocation is worth to the auctioneer, in submitted bids."""
    return sum(b.bid for b in accepted)


def ranked(bids: Bids) -> list[PackageBid]:
    """Highest bid first. ``sorted`` is stable, so ties keep the listed order — the same
    tie rule the single-item mechanisms use, and the reason both solvers are reproducible."""
    for b in bids:
        if not b.items:
            raise ValueError(
                f"{b.bidder!r} submitted a package of no items; a package bid must name "
                "at least one item, or it would conflict with nothing and win for free"
            )
        # The exhaustive search bounds a branch by the plain sum of the bids still ahead
        # of it. That is only an upper bound while every bid is non-negative: one negative
        # bid makes the sum understate what the branch could reach, and the search prunes
        # away the true optimum. Refusing the bid is both truer to the auction — offering
        # to be paid for taking a bundle is not a bid — and smaller than making the bound
        # sound over a case that should not exist.
        if b.bid < 0:
            raise ValueError(
                f"{b.bidder!r} bid {b.bid} for {list(b.items)}; a package bid must be "
                "non-negative"
            )
    return sorted(bids, key=lambda b: -b.bid)


def _masks(order: list[PackageBid]) -> list[tuple[int, int]]:
    """One (items, bidder) bit pair per bid, so a conflict is a single ``&``."""
    item_bit = {name: 1 << i for i, name in enumerate(item_universe(order))}
    owner_bit = {
        name: 1 << i for i, name in enumerate(dict.fromkeys(b.bidder for b in order))
    }
    return [
        (sum(item_bit[i] for i in set(b.items)), owner_bit[b.bidder]) for b in order
    ]


def _pack(masks: list[tuple[int, int]], order: range) -> tuple[int, ...]:
    """The greedy rule itself: accept in ``order``, skip anything that conflicts.

    Two things count as a conflict — the bundle shares an item with something already
    accepted, or its bidder has already won a different bundle (XOR).
    """
    items = owners = 0
    chosen: list[int] = []
    for j in order:
        bundle, owner = masks[j]
        if items & bundle or owners & owner:
            continue
        chosen.append(j)
        items |= bundle
        owners |= owner
    return tuple(chosen)


def greedy_allocate(bids: Bids) -> Allocation:
    """Accept bids in descending order, skipping every one that conflicts. Linearithmic.

    This is what a practical auction runs, and it is not optimal — a single large bid
    taken early can block a set of smaller ones worth more together.
    """
    order = ranked(bids)
    return tuple(order[j] for j in _pack(_masks(order), range(len(order))))


def optimal_allocate(bids: Bids) -> Allocation:
    """The allocation with the highest total bid. Exponential, hence the size guard.

    Depth-first over the same descending order greedy walks, carrying the used items and
    the bidders who have already won as bitmasks, and cutting any branch whose remaining
    bids could not add up past the best allocation found so far.

    The search *starts* from greedy's answer and only ever replaces it on a strict
    improvement. That is worth two things: greedy is a real lower bound, so seeding with
    it prunes far more of the tree than starting from nothing, and it settles the
    tie-break — among allocations worth exactly the same, the one greedy would have
    picked is the one returned, so the answer is reproducible.
    """
    order = ranked(bids)
    _check_size(order)
    masks = _masks(order)

    n = len(order)
    remaining = [0] * (n + 1)  # the most the bids from index i onwards could ever add
    for i in range(n - 1, -1, -1):
        remaining[i] = remaining[i + 1] + order[i].bid

    seed = _pack(masks, range(n))
    best: tuple[Number, tuple[int, ...]] = (sum(order[j].bid for j in seed), seed)

    def search(
        start: int, items: int, owners: int, total: Number, chosen: tuple[int, ...]
    ) -> None:
        nonlocal best
        if total > best[0]:
            best = (total, chosen)
        for j in range(start, n):
            if total + remaining[j] <= best[0]:
                return  # nothing left in this branch can beat what we already have
            bundle, owner = masks[j]
            if items & bundle or owners & owner:
                continue
            search(j + 1, items | bundle, owners | owner, total + order[j].bid, chosen + (j,))

    search(0, 0, 0, 0, ())
    return tuple(order[j] for j in best[1])


def _check_size(bids: Bids) -> None:
    """Refuse inputs the exhaustive search cannot be trusted to finish quickly.

    This guard is the honest face of NP-hardness: rather than hang inside a web request,
    the auction says out loud how big a problem it is willing to solve exactly.
    """
    items = item_universe(bids)
    if len(items) > MAX_ITEMS or len(bids) > MAX_BIDS:
        raise ValueError(
            "winner determination is NP-hard, so the exhaustive search is capped at "
            f"{MAX_ITEMS} items and {MAX_BIDS} bids; this input has items="
            f"{len(items)}, bids={len(bids)}. Greedy allocation has no such limit."
        )
