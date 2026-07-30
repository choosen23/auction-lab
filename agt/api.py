"""The trust boundary: JSON in, ``(status, JSON)`` out, with no socket anywhere.

Everything the two POST endpoints do apart from HTTP itself lives here — validating an
untrusted body and turning an engine call into a status code. :mod:`agt.serve` is then
only routing, static files and headers, the same way :mod:`agt.mechanisms` is only
auctions once :mod:`agt.registry` holds the plumbing.

Two rules hold this module together:

* **One copy of the bidder rules.** :func:`validate_series` calls :func:`validate` and
  adds two checks. A second, drifting copy of "what is a legal bidder" is how one
  endpoint quietly becomes the weaker door.
* **Shape here, ranges in the engine.** These functions make sure a body cannot reach
  the engine as a ``KeyError`` or a ``TypeError``; whether a reserve is negative is the
  engine's business, and its ``ValueError`` becomes the same 400 as one raised here.

Every message is written for a learner staring at the setup form, because that is
exactly where it is displayed.
"""

import math
import traceback
from typing import Any

from agt.mechanisms import REGISTRY, run
from agt.series import MAX_ROUNDS, run_series
from agt.strategies import STRATEGIES
from agt.trace import Bidder

# 12 is where the visualization stops being readable; it also bounds compute.
MAX_BIDDERS = 12
# What a series runs when the form does not say. Mirrors ``run_series``' own default.
DEFAULT_ROUNDS = 8


# ------------------------------------------------------------------- validation


def validate(payload: Any) -> dict[str, Any]:
    """Check an untrusted ``/run`` body and return ``{mechanism, bidders, params}``."""
    if not isinstance(payload, dict):
        raise ValueError("request body must be a JSON object")

    name = payload.get("mechanism")
    if not isinstance(name, str) or name not in REGISTRY:
        raise ValueError(
            f"unknown mechanism {name!r}; expected one of {sorted(REGISTRY)}"
        )

    entries = payload.get("bidders")
    if not isinstance(entries, list) or not 1 <= len(entries) <= MAX_BIDDERS:
        raise ValueError(
            f"bidders must be a list of between 1 and {MAX_BIDDERS} entries"
        )
    bidders = [_bidder(i, entry) for i, entry in enumerate(entries)]
    seen = [b.id for b in bidders]
    if len(set(seen)) != len(seen):
        raise ValueError(f"bidder ids must be unique, got {seen}")

    params = _params(repr(name), payload.get("params"), REGISTRY[name].params)
    return {"mechanism": name, "bidders": bidders, "params": params}


def validate_series(payload: Any) -> dict[str, Any]:
    """Check an untrusted ``/run_series`` body: :func:`validate`, plus rounds and strategies."""
    valid = validate(payload)
    valid["rounds"] = _rounds(payload.get("rounds"))
    valid["strategies"] = _strategies(payload.get("strategies"), valid["bidders"])
    return valid


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


def _rounds(value: Any) -> int:
    """A whole number of rounds within the engine's cap. ``None`` means 'use the default'."""
    if value is None:
        value = DEFAULT_ROUNDS
    # bool is an int subclass, and ``True`` as a round count would silently become 1.
    # An integral float is fine: a JSON round-trip through a form turns 3 into 3.0.
    whole = isinstance(value, int) and not isinstance(value, bool)
    whole = whole or (isinstance(value, float) and value.is_integer())
    if not whole:
        raise ValueError(f"rounds must be a whole number, got {value!r:.60}")
    if not 1 <= value <= MAX_ROUNDS:
        raise ValueError(f"rounds must be between 1 and {MAX_ROUNDS}, got {value!r:.60}")
    return int(value)


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
