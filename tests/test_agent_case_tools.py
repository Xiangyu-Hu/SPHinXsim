"""Tests for deterministic Agent case copying and parameter patching."""

from __future__ import annotations

import json
import math
from pathlib import Path

import pytest

from sphinxsim.agent import CaseToolError, CaseWorkspace


def _case_payload() -> dict:
    return {
        "simulation_type": "continuum_dynamics",
        "continuum_bodies": [
            {
                "name": "GranularBody",
                "material": {
                    "type": "plastic_continuum",
                    "density": 1600.0,
                    "friction_angle": 0.3,
                    "cohesion": 50.0,
                },
            }
        ],
        "solver_parameters": {"end_time": 0.8},
    }


def test_prepare_and_update_case_preserve_source(build_temp_path: Path) -> None:
    source = build_temp_path / "source.json"
    source.write_text(json.dumps(_case_payload(), indent=2), encoding="utf-8")
    original_text = source.read_text(encoding="utf-8")
    workspace_root = build_temp_path / "agent_store"
    workspace = CaseWorkspace(workspace_root)
    output = workspace_root / "task_case" / "cases" / "case_v1.json"

    prepared = workspace.prepare_case(source, output)
    updated, angle_radians = workspace.update_friction_angle(
        prepared,
        30.0,
        body_name="GranularBody",
    )

    assert updated == output.resolve()
    assert source.read_text(encoding="utf-8") == original_text
    payload = json.loads(updated.read_text(encoding="utf-8"))
    material = payload["continuum_bodies"][0]["material"]
    assert material["friction_angle"] == pytest.approx(math.pi / 6)
    assert material["density"] == 1600.0
    assert payload["solver_parameters"] == {"end_time": 0.8}
    assert angle_radians == pytest.approx(math.pi / 6)


@pytest.mark.parametrize("angle", [-1.0, 90.0, float("inf"), True, "30"])
def test_update_rejects_invalid_friction_angle(
    build_temp_path: Path,
    angle: object,
) -> None:
    workspace = CaseWorkspace(build_temp_path / "agent_store")
    output = workspace.root / "task_case" / "case.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(_case_payload()), encoding="utf-8")

    with pytest.raises(CaseToolError, match="friction_angle_degrees"):
        workspace.update_friction_angle(output, angle, body_name="GranularBody")


def test_case_output_cannot_escape_workspace(build_temp_path: Path) -> None:
    source = build_temp_path / "source.json"
    source.write_text(json.dumps(_case_payload()), encoding="utf-8")
    workspace = CaseWorkspace(build_temp_path / "agent_store")

    with pytest.raises(CaseToolError, match="inside Agent workspace"):
        workspace.prepare_case(source, build_temp_path / "outside.json")


def test_update_requires_one_named_plastic_body(build_temp_path: Path) -> None:
    workspace = CaseWorkspace(build_temp_path / "agent_store")
    output = workspace.root / "task_case" / "case.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(_case_payload()), encoding="utf-8")

    with pytest.raises(CaseToolError, match="found 0"):
        workspace.update_friction_angle(output, 30.0, body_name="MissingBody")
