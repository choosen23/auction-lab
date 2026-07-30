"""The mechanism registry: registration, parameter validation, and the generator driver.

This is plumbing — it knows that a mechanism is a generator of steps with a declared
parameter schema, and nothing about auctions. The mechanisms themselves live in
:mod:`agt.mechanisms`, which is also where the public entry points are re-exported from.
"""

import copy
import math
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from typing import Any

from agt.trace import Step, Trace

MechanismFn = Callable[..., Iterator[Step]]


# What each input kind is called on the wire, and the attribute that identifies one of
# its records. The engine needs the identity and nothing else about the shape; checking
# the rest of it is the trust boundary's job in :mod:`agt.api`.
IDENTITY = {"single": ("bidders", "id"), "package": ("packages", "bidder")}


@dataclass(frozen=True)
class Mechanism:
    """A registered mechanism: how to run it and how to build a form for it.

    ``truthful_dominant`` says whether bidding your private value is a dominant strategy
    under these rules. It lives here, next to the payment rule that decides it, because
    anything else keeping the list — a strategy module, the UI — has to remember to
    update itself every time a mechanism is added, and silence there reads as "no".

    ``input_kind`` says what the mechanism is *handed*: ``"single"`` for a list of
    :class:`~agt.trace.Bidder` records carrying one scalar bid each, ``"package"`` for a
    list of all-or-nothing package bids. It defaults to ``"single"`` because that is what
    every mechanism took before combinatorial auctions existed, and the UI switches its
    setup form on it rather than on a list of mechanism names kept in JavaScript.
    """

    name: str
    label: str
    description: str
    params: dict[str, dict[str, Any]]
    fn: MechanismFn
    truthful_dominant: bool = False
    input_kind: str = "single"


REGISTRY: dict[str, Mechanism] = {}


def mechanism(
    name: str,
    *,
    label: str,
    description: str,
    truthful_dominant: bool,
    params: dict[str, dict[str, Any]] | None = None,
    input_kind: str = "single",
) -> Callable[[MechanismFn], MechanismFn]:
    """Register a mechanism generator under ``name`` with a form-ready params schema.

    ``truthful_dominant`` has no default on purpose: a new mechanism must answer the
    question rather than inherit an answer by omission. ``input_kind`` does have one,
    because the answer for a mechanism that says nothing is the same one seven existing
    mechanisms give, and a wrong kind is caught immediately rather than silently.
    """
    if input_kind not in IDENTITY:
        raise ValueError(
            f"unknown input_kind {input_kind!r} for {name!r}; "
            f"expected one of {sorted(IDENTITY)}"
        )

    def decorate(fn: MechanismFn) -> MechanismFn:
        REGISTRY[name] = Mechanism(
            name, label, description, params or {}, fn, truthful_dominant, input_kind
        )
        return fn

    return decorate


def registry_schema() -> dict[str, dict[str, Any]]:
    """Serialize REGISTRY to a JSON-safe dict. The UI generates its form from this."""
    return {
        name: {
            "name": m.name,
            "label": m.label,
            "description": m.description,
            "truthful_dominant": m.truthful_dominant,
            "input_kind": m.input_kind,
            "params": copy.deepcopy(m.params),
        }
        for name, m in REGISTRY.items()
    }


def run(
    name: str,
    participants: list[Any],
    params: dict[str, Any] | None = None,
) -> Trace:
    """Run mechanism ``name`` to completion and return its :class:`Trace`.

    ``participants`` is whatever the mechanism's declared ``input_kind`` takes: scalar
    :class:`~agt.trace.Bidder` records for ``"single"``, package bids for ``"package"``.
    One driver rather than one per kind, because everything after the input is identical
    — resolve the params, pump the generator, wrap the steps in a trace.
    """
    if name not in REGISTRY:
        raise ValueError(
            f"unknown mechanism {name!r}; expected one of {sorted(REGISTRY)}"
        )
    spec = REGISTRY[name]
    _check_participants(spec, participants)
    resolved = _resolve_params(spec, params or {})

    generator = spec.fn(list(participants), **resolved)
    steps: list[Step] = []
    while True:
        try:
            steps.append(next(generator))
        except StopIteration as stop:  # the generator's ``return`` lands here
            result = stop.value
            break
    if result is None:
        raise ValueError(f"mechanism {name!r} finished without returning a result")
    return Trace(
        mechanism=name,
        params=resolved,
        bidders=list(participants),
        steps=steps,
        result=result,
    )


def _check_participants(spec: Mechanism, participants: list[Any]) -> None:
    """Guard the caller mistakes the engine cannot survive: an empty room, input of the
    wrong kind, and — for single-item mechanisms only — colliding ids.

    ``payments`` and ``utilities`` are keyed by bidder id, so two scalar bidders sharing
    one id silently collapse into a single entry and the trace stops adding up. Package
    bids are the opposite case: XOR means one bidder may legitimately submit several
    bundles, so repeats there are the point rather than a mistake.

    This lives here rather than only in :mod:`agt.serve` because the engine is meant to
    run under Pyodide with no server in front of it. It is a guard, not a validation
    layer — payload shape is the server's job.
    """
    kind, attribute = IDENTITY[spec.input_kind]
    if not participants:
        raise ValueError(
            "at least one package bid is required"
            if spec.input_kind == "package"
            else "at least one bidder is required"
        )
    try:
        seen = [getattr(p, attribute) for p in participants]
    except AttributeError:
        raise ValueError(
            f"{spec.name!r} reads {kind}, and was handed something else: its input kind "
            f"is {spec.input_kind!r}, so every entry must carry a {attribute!r}"
        ) from None
    if spec.input_kind == "single" and len(set(seen)) != len(seen):
        raise ValueError(f"bidder ids must be unique, got {seen}")


def _resolve_params(spec: Mechanism, given: dict[str, Any]) -> dict[str, Any]:
    """Fill in schema defaults and reject anything the schema does not declare."""
    for key in given:
        if key not in spec.params:
            raise ValueError(
                f"unknown parameter {key!r} for {spec.name!r}; "
                f"expected one of {sorted(spec.params)}"
            )
    resolved = {}
    for key, schema in spec.params.items():
        value = given.get(key, schema["default"])
        resolved[key] = _validate_param(key, value, schema)
    return resolved


def _validate_param(key: str, value: Any, schema: dict[str, Any]) -> Any:
    """Validate one param against its schema entry. ``None`` means 'use the default'."""
    if value is None and schema["default"] is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"parameter {key!r} must be a number, got {value!r}")
    if not math.isfinite(value):
        raise ValueError(f"parameter {key!r} must be finite, got {value!r}")
    low, high = schema.get("min"), schema.get("max")
    if low is not None and value < low:
        raise ValueError(f"parameter {key!r} must be >= {low}, got {value!r}")
    if high is not None and value > high:
        raise ValueError(f"parameter {key!r} must be <= {high}, got {value!r}")
    return value
