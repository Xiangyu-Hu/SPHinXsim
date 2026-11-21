from __future__ import annotations

import json
from pathlib import Path

import pytest

from sphinxsim.config.schemas import SimulationConfig
from sphinxsim.json_editor.model import (
    edit_list,
    parse_field_value,
    pop_undo_snapshot,
    set_value_at_path,
    snapshot_payload,
    validate_payload,
    value_at_path,
)


def test_shared_field_operations_parse_and_update_nested_values():
    payload = {"solver": {"end_time": 1.0, "enabled": True}}

    set_value_at_path(payload, ("solver", "end_time"), parse_field_value(1.0, "0.5"))
    set_value_at_path(payload, ("solver", "enabled"), parse_field_value(True, False))

    assert value_at_path(payload, ("solver", "end_time")) == 0.5
    assert value_at_path(payload, ("solver", "enabled")) is False
    with pytest.raises(ValueError):
        parse_field_value(1, "not an integer")


def test_shared_list_operations_preserve_independent_duplicate_values():
    payload = {"items": [{"value": 1}, {"value": 2}]}

    assert edit_list(payload, ("items", 0), "duplicate")
    value_at_path(payload, ("items", 1))["value"] = 9
    assert payload["items"] == [
        {"value": 1},
        {"value": 9},
        {"value": 2},
    ]

    assert edit_list(payload, ("items",), "add", new_item={"value": 3})
    assert edit_list(payload, ("items", 2), "up")
    assert edit_list(payload, ("items", 0), "remove")
    assert payload["items"] == [{"value": 2}, {"value": 9}, {"value": 3}]


def test_shared_snapshot_undo_and_config_validation():
    source = (
        Path(__file__).parent
        / "test_simulation"
        / "test_2d_simulation"
        / "data"
        / "column_collapse.json"
    )
    payload = json.loads(source.read_text(encoding="utf-8"))
    snapshot = snapshot_payload(payload)
    history = [snapshot]

    payload["solver_parameters"]["end_time"] = 0.123
    restored = json.loads(pop_undo_snapshot(history, "{}"))

    assert restored["solver_parameters"]["end_time"] != 0.123
    assert isinstance(validate_payload(restored, SimulationConfig), SimulationConfig)
    assert pop_undo_snapshot(history, snapshot) == snapshot
