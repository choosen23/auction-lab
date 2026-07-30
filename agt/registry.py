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

from agt.trace import Bidder, Step, Trace

MechanismFn = Callable[..., Iterator[Step]]


@dataclass(frozen=True)
class Mechanism:
    """A registered mechanism: how to run it and how to build a form for it."""

    name: str
    label: str
    description: str
    params: dict[str, dict[str, Any]]
    fn: MechanismFn


REGISTRY: dict[str, Mechanism] = {}


def mechanism(
    name: str,
    *,
    label: str,
    description: str,
    params: dict[str, dict[str, Any]] | None = None,
) -> Callable[[MechanismFn], MechanismFn]:
    """Register a mechanism generator under ``name`` with a form-ready params schema."""

    def decorate(fn: MechanismFn) -> MechanismFn:
        REGISTRY[name] = Mechanism(name, label, description, params or {}, fn)
        return fn

    return decorate


def registry_schema() -> dict[str, dict[str, Any]]:
    """Serialize REGISTRY to a JSON-safe dict. The UI generates its form from this."""
    return {
        name: {
            "name": m.name,
            "label": m.label,
            "description": m.description,
            "params": copy.deepcopy(m.params),
        }
        for name, m in REGISTRY.items()
    }


def run(
    name: str,
    bidders: list[Bidder],
    params: dict[str, Any] | None = None,
) -> Trace:
    """Run mechanism ``name`` to completion and return its :class:`Trace`."""
    if name not in REGISTRY:
        raise ValueError(
            f"unknown mechanism {name!r}; expected one of {sorted(REGISTRY)}"
        )
    _check_bidders(bidders)
    spec = REGISTRY[name]
    resolved = _resolve_params(spec, params or {})

    generator = spec.fn(list(bidders), **resolved)
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
        bidders=list(bidders),
        steps=steps,
        result=result,
    )


def _check_bidders(bidders: list[Bidder]) -> None:
    """Guard the one caller mistake the engine cannot survive: colliding or absent ids.

    ``payments`` and ``utilities`` are keyed by id, so duplicates silently collapse into
    one entry and the trace stops adding up. This lives here rather than only in
    ``agt.serve`` because the engine is meant to run under Pyodide with no server in
    front of it. It is a guard, not a validation layer — payload shape is the server's job.
    """
    if not bidders:
        raise ValueError("at least one bidder is required")
    seen = [b.id for b in bidders]
    if len(set(seen)) != len(seen):
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
