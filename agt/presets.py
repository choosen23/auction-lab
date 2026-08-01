"""Worked examples: a setup plus the question it answers, ready to run in one click.

A preset is *only* a filled-in form. It carries no results and no explanation of what
happened — the trace already explains itself, and a preset that also described the
outcome would be a second source of truth to keep in sync.

They live here rather than in ``web/`` for the same reason the mechanism list does:
THE ONE RULE is that no mechanism or strategy name appears in the JavaScript. The UI
renders whatever ``GET /presets`` hands it and clicks the button the preset names.

Every preset is checked against the live registries at import time by :func:`_audit`,
so a renamed mechanism or strategy breaks the test suite rather than shipping a chip
that fails when someone clicks it.
"""

import copy
from dataclasses import dataclass, field
from typing import Any

from agt.mechanisms import REGISTRY
from agt.registry import IDENTITY
from agt.strategies import STRATEGIES

__all__ = ["PRESETS", "Preset", "preset_schema"]

# Which run button a preset wants. The UI maps these to its three actions; none of
# them is a mechanism name, so the rule holds.
MODES = ("single", "series", "equilibrium")


@dataclass(frozen=True)
class Preset:
    """One worked example.

    ``teaches`` is the hook — what you are about to watch, phrased as the thing that
    is surprising about it. It is the chip's tooltip and the line under the heading,
    so it has to stand alone without the mechanism's own description next to it.

    ``entrants`` is whatever the mechanism's ``input_kind`` takes: ``bidders`` rows
    for ``"single"``, ``packages`` rows for ``"package"``. One field rather than two
    because the mechanism already declares which kind it reads, and two fields would
    let a preset carry the wrong one without saying so.
    """

    name: str
    label: str
    teaches: str
    mechanism: str
    mode: str = "single"
    entrants: list[dict[str, Any]] = field(default_factory=list)
    params: dict[str, Any] = field(default_factory=dict)
    rounds: int | None = None
    world: dict[str, Any] | None = None


PRESETS: tuple[Preset, ...] = (
    Preset(
        name="truthful_pays_less",
        label="Honesty is safe",
        teaches="Three bidders all name their true value. The winner pays the "
        "runner-up's bid, not their own — which is why nobody gains by lying.",
        mechanism="second_price",
        entrants=[
            {"id": "A", "value": 90, "bid": 90},
            {"id": "B", "value": 70, "bid": 70},
            {"id": "C", "value": 45, "bid": 45},
        ],
    ),
    Preset(
        name="shading_settles",
        label="Why everyone shades",
        teaches="Pay your own bid and honesty earns you nothing. Watch four bidders "
        "best-respond round after round, and see where the bids stop falling.",
        mechanism="first_price",
        mode="series",
        rounds=14,
        entrants=[
            {"id": "A", "value": 95, "bid": 95, "strategy": "best_response"},
            {"id": "B", "value": 80, "bid": 80, "strategy": "best_response"},
            {"id": "C", "value": 65, "bid": 65, "strategy": "best_response"},
            {"id": "D", "value": 50, "bid": 50, "strategy": "best_response"},
        ],
    ),
    Preset(
        name="losers_pay",
        label="Losers pay too",
        teaches="Everyone pays what they bid and only one of them wins anything. "
        "Three of these four bidders end the auction underwater.",
        mechanism="all_pay",
        entrants=[
            {"id": "A", "value": 100, "bid": 62},
            {"id": "B", "value": 88, "bid": 58},
            {"id": "C", "value": 74, "bid": 44},
            {"id": "D", "value": 60, "bid": 31},
        ],
    ),
    Preset(
        name="reserve_kills_the_sale",
        label="A reserve too high",
        teaches="The seller refuses anything under 80 and the room tops out at 72. "
        "Nobody wins, nobody pays, and the item everyone wanted goes nowhere.",
        mechanism="second_price",
        params={"reserve": 80},
        entrants=[
            {"id": "A", "value": 72, "bid": 72},
            {"id": "B", "value": 61, "bid": 61},
            {"id": "C", "value": 40, "bid": 40},
        ],
    ),
    Preset(
        name="slots_not_one_prize",
        label="Ads: four slots, one ladder",
        teaches="Search ads sell position, not the page. Higher slots get more "
        "clicks, so what each bidder pays depends on who is standing below them.",
        mechanism="gsp",
        params={"slots": 3},
        entrants=[
            {"id": "A", "value": 100, "bid": 92},
            {"id": "B", "value": 85, "bid": 78},
            {"id": "C", "value": 70, "bid": 66},
            {"id": "D", "value": 55, "bid": 51},
        ],
    ),
    Preset(
        name="bundles_dont_add_up",
        label="Bundles don't add up",
        teaches="These bidders want combinations, not items — a pair is worth more "
        "than its halves. Greedy grabs the best-looking bid first and leaves money behind.",
        mechanism="greedy_package",
        entrants=[
            {"bidder": "A", "items": "north, south", "value": 100, "bid": 100},
            {"bidder": "B", "items": "north", "value": 60, "bid": 60},
            {"bidder": "C", "items": "south", "value": 55, "bid": 55},
            {"bidder": "D", "items": "north, south", "value": 90, "bid": 90},
        ],
    ),
    Preset(
        name="budget_runs_out",
        label="The budget runs out",
        teaches="Twenty impressions, real budgets, fresh values each round. One "
        "bidder spends until they are broke; the other paces itself and is still bidding at the end.",
        mechanism="first_price",
        mode="series",
        rounds=20,
        entrants=[
            {"id": "A", "value": 70, "bid": 70, "strategy": "budget_blind"},
            {"id": "B", "value": 70, "bid": 70, "strategy": "pace_multiplicative"},
            {"id": "C", "value": 70, "bid": 70, "strategy": "truthful"},
        ],
        world={
            "seed": 7,
            "value_low": 40,
            "value_high": 100,
            "budgets": {"A": 300, "B": 300},
        },
    ),
    Preset(
        name="where_it_settles",
        label="Where does it settle?",
        teaches="Not what happens at these bids — what happens at every other bid "
        "too. The reply curves say whether anyone still has a reason to move.",
        mechanism="first_price",
        mode="equilibrium",
        entrants=[
            {"id": "A", "value": 90, "bid": 60},
            {"id": "B", "value": 70, "bid": 50},
        ],
    ),
)


def preset_schema() -> list[dict[str, Any]]:
    """Serialize PRESETS to a JSON-safe list. The UI renders its chips from this.

    ``kind`` is the mechanism's own ``input_kind``, copied in so the UI knows which
    setup table to fill without looking the mechanism up a second time — and without
    ever branching on the mechanism's name.
    """
    return [
        {
            "name": p.name,
            "label": p.label,
            "teaches": p.teaches,
            "mechanism": p.mechanism,
            "mode": p.mode,
            "kind": REGISTRY[p.mechanism].input_kind,
            "entrants": copy.deepcopy(p.entrants),
            "params": copy.deepcopy(p.params),
            "rounds": p.rounds,
            "world": copy.deepcopy(p.world),
        }
        for p in PRESETS
    ]


def _audit(presets: tuple[Preset, ...] = PRESETS) -> None:
    """Fail at import if a preset names something that no longer exists.

    A preset is a promise that a click works. Checking it here means a renamed
    mechanism, a dropped strategy or a param that lost its schema entry surfaces on
    the next test run instead of on a visitor's first click.
    """
    seen: set[str] = set()
    for p in presets:
        if p.name in seen:
            raise ValueError(f"duplicate preset name {p.name!r}")
        seen.add(p.name)

        if p.mode not in MODES:
            raise ValueError(f"preset {p.name!r} has unknown mode {p.mode!r}")
        spec = REGISTRY.get(p.mechanism)
        if spec is None:
            raise ValueError(f"preset {p.name!r} names unknown mechanism {p.mechanism!r}")

        for key in p.params:
            if key not in spec.params:
                raise ValueError(
                    f"preset {p.name!r} sets {key!r}, which {p.mechanism!r} does not declare"
                )

        if not p.entrants:
            raise ValueError(f"preset {p.name!r} has no entrants")
        _, identifier = IDENTITY[spec.input_kind]
        for entry in p.entrants:
            if identifier not in entry:
                raise ValueError(
                    f"preset {p.name!r} feeds {p.mechanism!r} (input_kind "
                    f"{spec.input_kind!r}), so every entrant needs a {identifier!r}"
                )
            name = entry.get("strategy")
            if name is not None and name not in STRATEGIES:
                raise ValueError(f"preset {p.name!r} names unknown strategy {name!r}")

        # A world only means anything inside a series: it is what supplies each round's
        # fresh value draw, and a single run has exactly one round to draw for.
        if p.world is not None and p.mode != "series":
            raise ValueError(f"preset {p.name!r} carries a world but does not run a series")
        known = {e[identifier] for e in p.entrants}
        for who in (p.world or {}).get("budgets", {}):
            if who not in known:
                raise ValueError(
                    f"preset {p.name!r} budgets {who!r}, who is not in the auction"
                )


_audit()
