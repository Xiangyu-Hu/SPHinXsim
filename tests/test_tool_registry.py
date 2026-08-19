"""Tests for typed tool and capability registries."""

from __future__ import annotations

import pytest

from sphinxsim.agent.capabilities import build_capability_registry
from sphinxsim.agent.tools import (
    ToolRegistryError,
    build_builtin_tool_registry,
)


EXPECTED_TOOLS = {
    "prepare_case",
    "set_friction_angle",
    "validate_case",
    "run_case",
    "analyze_runout",
    "compare_sweep_results",
}


def test_builtin_registry_exposes_current_deterministic_tools() -> None:
    registry = build_builtin_tool_registry()

    assert set(registry.names()) == EXPECTED_TOOLS
    assert registry.get("run_case").approval_required_by_default is True
    assert registry.get("run_case").cost.value == "high"
    assert registry.get("analyze_runout").action.value == "analyze_result"


def test_registry_validates_tool_input_and_rejects_extra_fields() -> None:
    registry = build_builtin_tool_registry()
    validated = registry.validate_input(
        "set_friction_angle",
        {
            "config_path": "case.json",
            "body_name": "GranularBody",
            "friction_angle_degrees": 30.0,
        },
    )

    assert validated.friction_angle_degrees == pytest.approx(30.0)
    with pytest.raises(ToolRegistryError, match="invalid input"):
        registry.validate_input(
            "set_friction_angle",
            {
                "config_path": "case.json",
                "body_name": "GranularBody",
                "friction_angle_degrees": 30.0,
                "python_code": "print('unsafe')",
            },
        )


def test_registry_rejects_unknown_tool() -> None:
    with pytest.raises(ToolRegistryError, match="unknown Agent tool"):
        build_builtin_tool_registry().get("execute_python")


def test_capability_export_contains_json_schemas_and_domain_limits() -> None:
    registry = build_builtin_tool_registry()
    capabilities = build_capability_registry(registry)

    assert capabilities.version == "1.0"
    assert capabilities.simulation_dimensions == [2, 3]
    assert capabilities.parameters["friction_angle"]["storage_unit"] == "radian"
    assert capabilities.metrics["runout"]["front_methods"] == ["maximum"]
    assert capabilities.artifact_formats["particle_state"]["encoding"] == ["ascii"]
    tool_capabilities = {tool["name"]: tool for tool in capabilities.tools}
    assert set(tool_capabilities) == EXPECTED_TOOLS
    assert tool_capabilities["run_case"]["input_schema"]["additionalProperties"] is False
