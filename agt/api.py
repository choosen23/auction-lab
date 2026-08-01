"""The trust boundary: JSON in, ``(status, JSON)`` out, with no socket anywhere.

Everything the two POST endpoints do apart from HTTP itself lives here — validating an
untrusted body and turning an engine call into a status code. :mod:`agt.serve` is then
only routing, static files and headers, the same way :mod:`agt.mechanisms` is only
auctions once :mod:`agt.registry` holds the plumbing.

Three rules hold this module together:

* **One copy of the bidder rules.** :func:`validate_series` calls :func:`validate` and
  adds two checks; package bids reuse :func:`_number` rather than restating what finite
  and non-negative mean. A second, drifting copy of "what is a legal offer" is how one
  endpoint quietly becomes the weaker door.
* **The mechanism says what it takes.** Which of ``bidders`` and ``packages`` a body must
  carry is read off ``input_kind`` in the registry, so adding a mechanism never means
  editing a list of names here. The other kind is refused by name, in both directions.
* **Shape here, ranges in the engine.** These functions make sure a body cannot reach
  the engine as a ``KeyError`` or a ``TypeError``; whether a reserve is negative is the
  engine's business, and its ``ValueError`` becomes the same 400 as one raised here.

Every message is written for a learner staring at the setup form, because that is
exactly where it is displayed.
"""

import math
import traceback
from typing import Any

from agt.equilibrium import (
    DEFAULT_STEPS,
    MAX_STEPS,
    MIN_STEPS,
    analyse,
    refuse_grid_search,
)
from agt.mechanisms import REGISTRY, Mechanism, run
from agt.packages import MAX_BIDS, MAX_ITEMS, PackageBid
from agt.revenue import DEFAULT_DRAWS, MAX_DRAWS, MIN_DRAWS
from agt.series import MAX_ROUNDS, refuse_repeated_rounds, run_series
from agt.strategies import STRATEGIES
from agt.trace import Bidder
from agt.world import World

# 12 is where the visualization stops being readable; it also bounds compute.
MAX_BIDDERS = 12
# What a series runs when the form does not say. Mirrors ``run_series``' own default.
DEFAULT_ROUNDS = 8

# What one record of each input kind has to carry, in the words the error message uses.
# The keys are the ``input_kind`` values the registry declares.
SHAPES = {
    "single": "an id, a value and a bid",
    "package": "a bidder, a list of items, a value and a bid",
}


# ------------------------------------------------------------------- validation


def validate(payload: Any) -> dict[str, Any]:
    """Check an untrusted ``/run`` body and return ``{mechanism, bidders, params}``.

    ``bidders`` carries whichever input kind the mechanism declares — scalar
    :class:`~agt.trace.Bidder` records for ``"single"``, :class:`PackageBid` records for
    ``"package"``. It keeps the one name because that is the slot they both go into:
    :attr:`agt.trace.Trace.bidders`, and from there the ``bidders`` key on the wire.
    """
    spec = _mechanism(payload)
    entrants = _entrants(spec, payload)
    params = _params(repr(spec.name), payload.get("params"), spec.params)
    return {"mechanism": spec.name, "bidders": entrants, "params": params}


def validate_series(payload: Any) -> dict[str, Any]:
    """Check an untrusted ``/run_series`` body: :func:`validate`, plus rounds and strategies.

    The input-kind check comes first so a package mechanism is told the real reason it
    cannot be run in rounds, rather than being told its ``bidders`` list is the wrong key.
    """
    refuse_repeated_rounds(_mechanism(payload).name)
    valid = validate(payload)
    valid["rounds"] = _rounds(payload.get("rounds"))
    valid["strategies"] = _strategies(payload.get("strategies"), valid["bidders"])
    valid["world"] = _world(payload.get("world"), valid["rounds"], valid["bidders"])
    return valid


def validate_equilibrium(payload: Any) -> dict[str, Any]:
    """Check an untrusted ``/equilibrium`` body: :func:`validate`, plus the search knobs.

    The input-kind check comes first for the same reason it does in
    :func:`validate_series`: a package mechanism should be told that its bidders have no
    single number to search over, not that its ``bidders`` list is the wrong key.

    There are no strategies here and no world. Equilibrium analysis is a question about the
    mechanism itself — which profiles are stable, whatever anybody's strategy happens to be
    — so a body carrying either would be a body whose author expected a different endpoint.
    """
    refuse_grid_search(_mechanism(payload).name)
    valid = validate(payload)
    valid["steps"] = _whole("grid steps", payload.get("steps"), MIN_STEPS, MAX_STEPS, DEFAULT_STEPS)
    valid["draws"] = _whole("draws", payload.get("draws"), MIN_DRAWS, MAX_DRAWS, DEFAULT_DRAWS)
    valid["seed"] = _seed(payload.get("seed"))
    return valid


def _mechanism(payload: Any) -> Mechanism:
    """The registered mechanism a body asks for, or a message naming the ones that exist."""
    if not isinstance(payload, dict):
        raise ValueError("request body must be a JSON object")
    name = payload.get("mechanism")
    if not isinstance(name, str) or name not in REGISTRY:
        raise ValueError(
            f"unknown mechanism {name!r}; expected one of {sorted(REGISTRY)}"
        )
    return REGISTRY[name]


def _entrants(spec: Mechanism, payload: dict[str, Any]) -> list[Any]:
    """Read the input kind the mechanism declares, refusing the other one by name.

    Refusing rather than ignoring: a body carrying ``bidders`` for a combinatorial
    auction is a form that has not caught up, and quietly dropping the key would run an
    auction on defaults nobody typed.
    """
    wanted, unwanted = (
        ("packages", "bidders")
        if spec.input_kind == "package"
        else ("bidders", "packages")
    )
    if unwanted in payload:
        raise ValueError(
            f"{spec.name!r} expects {wanted!r}, not {unwanted!r}: its input kind is "
            f"{spec.input_kind!r}, so every entry needs {SHAPES[spec.input_kind]}"
        )
    return _packages(payload) if spec.input_kind == "package" else _bidders(payload)


def _bidders(payload: dict[str, Any]) -> list[Bidder]:
    """One scalar bid each, with ids that do not collide."""
    entries = payload.get("bidders")
    if not isinstance(entries, list) or not 1 <= len(entries) <= MAX_BIDDERS:
        raise ValueError(
            f"bidders must be a list of between 1 and {MAX_BIDDERS} entries"
        )
    bidders = [_bidder(i, entry) for i, entry in enumerate(entries)]
    seen = [b.id for b in bidders]
    if len(set(seen)) != len(seen):
        raise ValueError(f"bidder ids must be unique, got {seen}")
    return bidders


def _packages(payload: dict[str, Any]) -> list[PackageBid]:
    """All-or-nothing bundles, bounded by what the exhaustive search can finish.

    The caps are the solver's own, not a second opinion: ``greedy_package`` reports the
    greedy-vs-optimal gap and so runs the exhaustive search too, which means both package
    mechanisms refuse an oversized input. Meeting that refusal here makes it a 400 the
    setup form can show, instead of a ``ValueError`` surfacing from inside a generator.

    Repeated bidder ids are legal and are not checked: XOR means one bidder may submit
    several bundles as alternatives, and at most one of them can win.
    """
    entries = payload.get("packages")
    if not isinstance(entries, list) or not 1 <= len(entries) <= MAX_BIDS:
        raise ValueError(
            f"packages must be a list of between 1 and {MAX_BIDS} package bids: "
            "choosing which bids to accept is NP-hard, so the exhaustive search that "
            "shows what the greedy shortcut costs is capped"
        )
    bids = [_package(i, entry) for i, entry in enumerate(entries)]
    universe = {item for b in bids for item in b.items}
    if len(universe) > MAX_ITEMS:
        raise ValueError(
            f"the bids name {len(universe)} different items and the exhaustive search "
            f"is capped at {MAX_ITEMS}: choosing which bids to accept is NP-hard, so "
            "fewer items or fewer bundles"
        )
    return bids


def _package(index: int, entry: Any) -> PackageBid:
    where = f"package bid {index}"
    if not isinstance(entry, dict):
        raise ValueError(
            f"{where} must be a JSON object with bidder, items, value and bid"
        )
    bidder = entry.get("bidder")
    if not isinstance(bidder, str) or not bidder.strip():
        raise ValueError(f"{where} needs a non-empty string bidder, got {bidder!r}")
    return PackageBid(
        bidder=bidder,
        items=_items(f"{where} ({bidder})", entry.get("items")),
        value=_number(f"{where} ({bidder}) value", entry.get("value")),
        bid=_number(f"{where} ({bidder}) bid", entry.get("bid")),
    )


def _items(where: str, raw: Any) -> tuple[str, ...]:
    """The bundle a package bid is for: at least one item, each one named once.

    A bundle of nothing conflicts with nothing and would win for free, and an item listed
    twice is a form slip rather than an offer — nobody can be sold the same licence twice.
    """
    if not isinstance(raw, list) or not 1 <= len(raw) <= MAX_ITEMS:
        raise ValueError(
            f"{where} items must be a list of between 1 and {MAX_ITEMS} item names"
        )
    for item in raw:
        if not isinstance(item, str) or not item.strip():
            raise ValueError(f"{where} needs non-empty string item names, got {item!r}")
    if len(set(raw)) != len(raw):
        raise ValueError(f"{where} names the same item twice: {raw}")
    return tuple(raw)


def _params(where: str, given: Any, declared: dict[str, Any]) -> dict[str, Any]:
    """Check param *keys* against a schema. Shared by mechanisms and strategies.

    The mechanism or strategy owns its parameter list, so ask the registry rather than
    restating it here. Ranges and types are the engine's, on the way in.
    """
    if given is None:
        given = {}
    if not isinstance(given, dict):
        raise ValueError(f"params for {where} must be a JSON object")
    for key in given:
        if key not in declared:
            raise ValueError(
                f"unknown parameter {key!r} for {where}; expected one of {sorted(declared)}"
            )
    return dict(given)


def _whole(where: str, value: Any, low: int, high: int, default: int) -> int:
    """A whole number in ``[low, high]``. ``None`` means 'use the default'.

    One copy, because rounds, grid steps and Monte-Carlo draws are the same rule with
    different numbers, and three drifting copies of "what counts as a whole number" is how
    one of them ends up accepting ``True``.
    """
    if value is None:
        value = default
    # bool is an int subclass, and ``True`` as a round count would silently become 1.
    # An integral float is fine: a JSON round-trip through a form turns 3 into 3.0.
    whole = isinstance(value, int) and not isinstance(value, bool)
    whole = whole or (isinstance(value, float) and value.is_integer())
    if not whole:
        raise ValueError(f"{where} must be a whole number, got {value!r:.60}")
    if not low <= value <= high:
        raise ValueError(f"{where} must be between {low} and {high}, got {value!r:.60}")
    return int(value)


def _rounds(value: Any) -> int:
    """A whole number of rounds within the engine's cap. ``None`` means 'use the default'."""
    return _whole("rounds", value, 1, MAX_ROUNDS, DEFAULT_ROUNDS)


def _strategies(entries: Any, bidders: list[Bidder]) -> dict[str, dict[str, Any]]:
    """One strategy per bidder, keyed by id, covering exactly the bidders submitted.

    Silence is not consent here: an unmatched id means the strategy form and the bidder
    table disagree about who is in the auction, and guessing which one is right would
    run an auction nobody asked for.
    """
    if not isinstance(entries, dict):
        raise ValueError(
            "strategies must be a JSON object mapping every bidder id to "
            '{"name": ..., "params": {...}}'
        )
    playing = [b.id for b in bidders]
    missing = sorted(set(playing) - set(entries))
    strangers = sorted(set(entries) - set(playing), key=str)
    if missing or strangers:
        problems = [
            *([f"no strategy for {missing}"] if missing else []),
            *(
                [f"strategies for bidders not in the auction: {strangers}"]
                if strangers
                else []
            ),
        ]
        raise ValueError(
            f"strategies must name exactly the bidders in the auction {playing} — "
            + "; ".join(problems)
        )
    return {bidder_id: _strategy(bidder_id, entries[bidder_id]) for bidder_id in playing}


def _strategy(bidder_id: str, entry: Any) -> dict[str, Any]:
    where = f"strategy for bidder {bidder_id!r}"
    if not isinstance(entry, dict):
        raise ValueError(f"{where} must be a JSON object with a name and params")
    name = entry.get("name")
    if not isinstance(name, str) or name not in STRATEGIES:
        raise ValueError(
            f"unknown strategy {name!r} for bidder {bidder_id!r}; "
            f"expected one of {sorted(STRATEGIES)}"
        )
    params = _params(f"strategy {name!r}", entry.get("params"), STRATEGIES[name].params)
    return {"name": name, "params": params}


def _bidder(index: int, entry: Any) -> Bidder:
    where = f"bidder {index}"
    if not isinstance(entry, dict):
        raise ValueError(f"{where} must be a JSON object with id, value and bid")
    bidder_id = entry.get("id")
    if not isinstance(bidder_id, str) or not bidder_id.strip():
        raise ValueError(f"{where} needs a non-empty string id, got {bidder_id!r}")
    return Bidder(
        id=bidder_id,
        value=_number(f"{where} ({bidder_id}) value", entry.get("value")),
        bid=_number(f"{where} ({bidder_id}) bid", entry.get("bid")),
    )


def _world(given: Any, rounds: int, bidders: list[Any]) -> World | None:
    """The optional day a series runs inside: value draws, budgets, and a seed.

    ``None`` means the phase 2 series — values fixed, no budgets — which is the whole
    point of the world being optional, so an absent key is not an error and not a
    silently-substituted default either.

    ``rounds`` comes from the request, never from the world body: pacing steers on how
    many rounds are left, so a horizon disagreeing with the loop would make every pacer
    quietly wrong.
    """
    if given is None:
        return None
    if not isinstance(given, dict):
        raise ValueError(f"world must be a JSON object, got {given!r:.60}")

    unknown = set(given) - {"seed", "value_low", "value_high", "budgets"}
    if unknown:
        raise ValueError(
            f"world has unknown parameter(s) {sorted(unknown)}; expected any of "
            "['budgets', 'seed', 'value_high', 'value_low']"
        )

    low, high = given.get("value_low"), given.get("value_high")
    if (low is None) != (high is None):
        raise ValueError(
            "value_low and value_high must both be given or both left out — half a "
            "range says nothing about where values come from"
        )
    if low is not None:
        low = _number("world value_low", low)
        high = _number("world value_high", high)
        if low > high:
            raise ValueError(f"world value_low ({low}) must not exceed value_high ({high})")

    # `or {}` would be wrong here: an empty list is falsy, so a caller sending
    # "budgets": [] would have it silently become a valid empty mapping.
    budgets = given.get("budgets")
    budgets = {} if budgets is None else budgets
    if not isinstance(budgets, dict):
        raise ValueError(f"world budgets must be a JSON object, got {budgets!r:.60}")
    known = {b.id for b in bidders}
    for who, amount in budgets.items():
        if who not in known:
            raise ValueError(
                f"world budget names {who!r}, which is not a bidder in this auction "
                f"({sorted(known)})"
            )
        _number(f"world budget for {who!r}", amount)

    return World(
        rounds=rounds,
        seed=_seed(given.get("seed")),
        value_low=low,
        value_high=high,
        budgets=dict(budgets),
    )


def _seed(value: Any) -> int:
    """A whole number, so the same request replays the same day."""
    if value is None:
        return 0
    whole = isinstance(value, int) and not isinstance(value, bool)
    whole = whole or (isinstance(value, float) and value.is_integer())
    if not whole:
        raise ValueError(f"world seed must be a whole number, got {value!r:.60}")
    if not 0 <= value < 2**32:
        raise ValueError(f"world seed must be between 0 and 2^32, got {value!r:.60}")
    return int(value)


def _number(where: str, x: Any) -> float:
    # bool is an int subclass, and ``True`` as a bid would silently become 1.
    if isinstance(x, bool) or not isinstance(x, (int, float)):
        raise ValueError(f"{where} must be a number, got {x!r}")
    # json.loads accepts the NaN and Infinity literals, so these do arrive over the
    # wire — and json.dumps would then emit a trace no browser can parse.
    try:
        finite = math.isfinite(x)
    except OverflowError:  # JSON ints are unbounded; 10**400 has no float to test
        finite = False
    if not finite:
        raise ValueError(f"{where} must be finite, got {x!r:.60}")
    if x < 0:
        raise ValueError(f"{where} must be non-negative, got {x!r}")
    return x


# --------------------------------------------------------------------- running


def run_payload(payload: Any) -> tuple[int, dict[str, Any]]:
    """Validate and run one request. Returns ``(status, body)`` and never raises."""
    try:
        valid = validate(payload)
    except ValueError as exc:
        return 400, {"error": str(exc)}
    try:
        trace = run(valid["mechanism"], valid["bidders"], valid["params"])
    except ValueError as exc:
        return 400, {"error": str(exc)}
    except Exception as exc:  # a bug in a mechanism, not in the request
        return 500, {
            "error": f"{type(exc).__name__}: {exc}",
            "partial_trace": _partial_trace(valid, exc),
            # ponytail: the traceback goes to the client. Ceiling — it leaks source
            # paths, which is fine for a loopback-only teaching tool and is the point:
            # a broken mechanism stays debuggable in the UI. Upgrade: gate it behind a
            # --debug flag the day this is ever served to anyone but its author.
            "traceback": traceback.format_exc(),
        }
    return 200, trace.to_dict()


def run_series_payload(payload: Any) -> tuple[int, dict[str, Any]]:
    """Validate and run one repeated-round request. Returns ``(status, body)``, never raises.

    This is the expensive endpoint: 50 rounds x 12 ``best_response`` bidders re-runs the
    mechanism a few thousand times. Measured worst case is 0.66s (``english``), which is
    why the caps live in :data:`MAX_BIDDERS` and :data:`agt.series.MAX_ROUNDS` rather
    than in a timeout — the work is bounded before it starts.
    """
    try:
        valid = validate_series(payload)
    except ValueError as exc:
        return 400, {"error": str(exc)}
    try:
        series = run_series(
            valid["mechanism"],
            valid["bidders"],
            valid["strategies"],
            valid["rounds"],
            valid["params"],
            world=valid["world"],
        )
    except ValueError as exc:
        return 400, {"error": str(exc)}
    except Exception as exc:  # a bug in a mechanism or a strategy, not in the request
        # ponytail: no partial series on a crash, unlike ``/run``. Ceiling — a mechanism
        # that explodes in round 7 shows the learner nothing about rounds 1-6. Upgrade:
        # have ``run_series`` take a per-round callback, which is also what
        # ``_partial_trace`` wants; do both at once or not at all.
        return 500, {
            "error": f"{type(exc).__name__}: {exc}",
            "traceback": traceback.format_exc(),
        }
    return 200, series.to_dict()


def equilibrium_payload(payload: Any) -> tuple[int, dict[str, Any]]:
    """Validate and analyse one setup. Returns ``(status, body)`` and never raises.

    Bounded before it starts, like the other two: the profile search is capped in
    :mod:`agt.equilibrium` and the Monte-Carlo draws are capped here, so the most expensive
    body this endpoint accepts is known in advance rather than cut off by a timeout. A
    setup too large to search still gets a 200 — the reply curves and the revenue check do
    not depend on the search, and returning nothing because one third was unaffordable
    would be worse than saying which third.
    """
    try:
        valid = validate_equilibrium(payload)
    except ValueError as exc:
        return 400, {"error": str(exc)}
    try:
        report = analyse(
            valid["mechanism"],
            valid["bidders"],
            valid["params"],
            steps=valid["steps"],
            draws=valid["draws"],
            seed=valid["seed"],
        )
    except ValueError as exc:
        return 400, {"error": str(exc)}
    except Exception as exc:  # a bug in a mechanism, not in the request
        return 500, {
            "error": f"{type(exc).__name__}: {exc}",
            "traceback": traceback.format_exc(),
        }
    return 200, report


def _partial_trace(valid: dict[str, Any], exc: BaseException) -> dict[str, Any]:
    """Recover the steps a mechanism emitted before it blew up.

    ponytail: read them out of ``run``'s still-live frame in the traceback instead of
    giving the engine an out-parameter it has no other use for. Ceiling — it is coupled
    to the name of one local in :func:`agt.registry.run`, and degrades to an empty step
    list if that changes; ``test_run_payload_returns_500_with_the_partial_trace`` is
    what catches the rename. Upgrade: have ``run`` accept a per-step callback.
    """
    steps: list[Any] = []
    tb = exc.__traceback__
    while tb is not None:
        if tb.tb_frame.f_code is run.__code__:
            steps = tb.tb_frame.f_locals.get("steps") or []
            break
        tb = tb.tb_next
    return {
        "mechanism": valid["mechanism"],
        "params": valid["params"],
        "bidders": [b.to_dict() for b in valid["bidders"]],
        "steps": [s.to_dict() for s in steps],
        "result": None,
    }
