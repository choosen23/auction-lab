"""The world a series runs in: how long the day is, what values it draws, who has money.

Phase 2's ``run_series`` held every value fixed and moved only bids, which is the right
model for watching a bid path settle and the wrong one for watching a budget get spent.
A budget is a *sequencing* problem — spend now or spend later — and it only exists if
each round is a fresh opportunity worth a freshly drawn amount.

Rather than fork a second runner, this module supplies the one optional argument
``run_series`` takes, and every default in it reproduces phase 2:

* no value range, so :meth:`World.value_for` hands back the value already on the bidder;
* no budgets, so :meth:`World.budget_for` returns ``None`` and nobody is ever barred;
* a seed that is only consulted by code that asks for a random stream.

That is what lets the runner read the world unconditionally. There is exactly one place
in ``run_series`` that knows a world might not have been supplied, and it is the line
that builds this default.
"""

import random
from dataclasses import dataclass, field
from typing import Any

from agt.trace import Bidder, Number


@dataclass(frozen=True)
class World:
    """The environment a series runs in, and the source of all its randomness.

    ``rounds``     how many rounds the series plays. The world owns this rather than
                   ``run_series``' own ``rounds`` argument, because a bidder pacing a
                   budget steers on how much of the day is left, and a horizon that
                   disagreed with the loop would make every pacing decision wrong.
    ``seed``       every random stream in the series is derived from this, so a series
                   is exactly reproducible. See :meth:`rng_for`.
    ``value_low``  bottom of the per-round value draw, or ``None`` for fixed values.
    ``value_high`` top of it. Both or neither — half a range is a typo, not a meaning.
    ``budgets``    bidder id -> total money for the whole series. A bidder with no entry
                   here has no budget and can never be barred, which is phase 2.
    """

    rounds: int
    seed: int = 0
    value_low: Number | None = None
    value_high: Number | None = None
    budgets: dict[str, Number] = field(default_factory=dict)

    def __post_init__(self) -> None:
        given = [self.value_low, self.value_high]
        if (self.value_low is None) != (self.value_high is None):
            raise ValueError(
                "value_low and value_high must be given together or not at all, got "
                f"value_low={self.value_low!r} and value_high={self.value_high!r}; "
                "half a range says nothing about what to draw"
            )
        if any(v is not None and v < 0 for v in given):
            raise ValueError(f"value_low and value_high must not be negative, got {given}")
        if self.value_low is not None and self.value_low > self.value_high:
            raise ValueError(
                f"value_low must not exceed value_high, got {self.value_low} > {self.value_high}"
            )
        for who, budget in self.budgets.items():
            if budget < 0:
                raise ValueError(f"budget for {who!r} must not be negative, got {budget}")

    @property
    def draws_values(self) -> bool:
        """Whether each round redraws values, or values stay fixed as they did in phase 2."""
        return self.value_low is not None

    def rng_for(self, *parts: Any) -> random.Random:
        """A stream determined entirely by the seed and ``parts`` — never global state.

        Callers name a stream by what it is for ("value", a bidder id, a round number),
        so streams stay independent: adding a value range does not shift the draws a
        throttling strategy makes, and one bidder's draws never depend on another's.

        ponytail: string-seeded ``random.Random``, one instance per use. Ceiling — it
        hashes the label on every call, so this is not the thing to do a million times
        per round; 50 rounds x 12 bidders is nothing. Upgrade: cache per bidder if a
        strategy ever needs a stream inside an inner loop.
        """
        return random.Random("|".join(str(part) for part in (self.seed, *parts)))

    def value_for(self, bidder: Bidder, round_index: int) -> Number:
        """This round's value for ``bidder`` — drawn if there is a range, else unchanged."""
        if not self.draws_values:
            return bidder.value
        return self.rng_for("value", bidder.id, round_index).uniform(
            self.value_low, self.value_high
        )

    def budget_for(self, bidder_id: str) -> Number | None:
        """``bidder_id``'s budget for the whole series, or ``None`` for no limit."""
        return self.budgets.get(bidder_id)
