"""Every preset is a promise that one click works. These tests make it run the payload.

``agt.presets._audit`` already checks the names at import. What it cannot check is
whether the *numbers* still produce a run — a tightened bidder cap, a reserve rule that
learned to reject, a strategy that now needs a param. So each preset is pushed through
the same three entry points the browser posts to, and has to come back 200.
"""

import pytest

from agt.presets import PRESETS, Preset, _audit, preset_schema
from agt.serve import equilibrium_payload, run_payload, run_series_payload

# The UI's mode -> endpoint mapping, restated here so a preset that changes mode is
# still exercised by the endpoint it will actually reach.
RUNNERS = {
    "single": run_payload,
    "series": run_series_payload,
    "equilibrium": equilibrium_payload,
}


def payload_for(preset: dict) -> dict:
    """Build the request body the browser would post for this preset."""
    key = "packages" if preset["kind"] == "package" else "bidders"
    entrants = [
        {k: v for k, v in entry.items() if k != "strategy"} for entry in preset["entrants"]
    ]
    if key == "packages":
        entrants = [
            {**e, "items": [s.strip() for s in e["items"].split(",")]} for e in entrants
        ]

    body = {"mechanism": preset["mechanism"], key: entrants, "params": preset["params"]}
    if preset["mode"] != "series":
        return body

    body["rounds"] = preset["rounds"]
    body["strategies"] = {
        e["id"]: {"name": e.get("strategy", "manual")} for e in preset["entrants"]
    }
    if preset["world"] is not None:
        body["world"] = preset["world"]
    return body


@pytest.mark.parametrize("preset", preset_schema(), ids=lambda p: p["name"])
def test_preset_runs(preset):
    """The click works: the payload validates and the engine produces a trace."""
    status, body = RUNNERS[preset["mode"]](payload_for(preset))
    assert status == 200, body


@pytest.mark.parametrize("preset", preset_schema(), ids=lambda p: p["name"])
def test_preset_is_self_describing(preset):
    """A chip with no hook is a chip nobody clicks."""
    assert preset["label"].strip()
    assert len(preset["teaches"].strip()) > 40


def test_schema_is_a_copy():
    """The UI must not be able to mutate the presets by editing what it was handed."""
    preset_schema()[0]["entrants"].clear()
    assert preset_schema()[0]["entrants"]


def test_audit_rejects_unknown_mechanism():
    with pytest.raises(ValueError, match="unknown mechanism"):
        _audit((Preset("x", "X", "t", "no_such_mechanism", entrants=[{"id": "A"}]),))


def test_audit_rejects_unknown_strategy():
    bad = Preset(
        "x", "X", "t", "first_price", mode="series",
        entrants=[{"id": "A", "value": 1, "bid": 1, "strategy": "no_such_strategy"}],
    )
    with pytest.raises(ValueError, match="unknown strategy"):
        _audit((bad,))


def test_audit_rejects_undeclared_param():
    bad = Preset("x", "X", "t", "first_price", params={"slots": 2}, entrants=[{"id": "A"}])
    with pytest.raises(ValueError, match="does not declare"):
        _audit((bad,))


def test_audit_rejects_wrong_entrant_kind():
    """A single-item mechanism handed package rows must not ship as a working chip."""
    bad = Preset("x", "X", "t", "first_price", entrants=[{"bidder": "A"}])
    with pytest.raises(ValueError, match="needs a 'id'"):
        _audit((bad,))


def test_audit_rejects_budget_for_a_stranger():
    bad = Preset(
        "x", "X", "t", "first_price", mode="series", rounds=3,
        entrants=[{"id": "A", "value": 1, "bid": 1}],
        world={"budgets": {"Z": 10}},
    )
    with pytest.raises(ValueError, match="not in the auction"):
        _audit((bad,))


def test_audit_rejects_world_without_a_series():
    bad = Preset("x", "X", "t", "first_price", entrants=[{"id": "A"}], world={"seed": 1})
    with pytest.raises(ValueError, match="does not run a series"):
        _audit((bad,))


def test_audit_rejects_duplicate_names():
    one = Preset("dup", "X", "t", "first_price", entrants=[{"id": "A"}])
    with pytest.raises(ValueError, match="duplicate preset name"):
        _audit((one, one))


def test_shipped_presets_pass_their_own_audit():
    _audit(PRESETS)
