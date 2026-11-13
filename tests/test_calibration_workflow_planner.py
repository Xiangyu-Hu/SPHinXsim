"""Tests for the deterministic Stage 7 planner boundary."""

from __future__ import annotations

import pytest

from sphinxsim.agent.calibration_workflow import (
    MockColumnCollapseCalibrationPlanner,
)
from sphinxsim.agent.tools import build_builtin_tool_registry


def test_mock_planner_returns_validated_structured_definition() -> None:
    planner = MockColumnCollapseCalibrationPlanner()

    definition = planner.plan(
        "Calibrate a column collapse until its final front reaches the target."
    )
    definition.validate_tools(build_builtin_tool_registry())

    assert definition.body_name == "GranularBody"
    assert definition.calibration.objective.metric_name == "runout_extension"
    assert definition.calibration.objective.target_value == pytest.approx(0.20)
    assert definition.calibration.parameter.lower_bound == pytest.approx(0.0)
    assert definition.calibration.parameter.upper_bound == pytest.approx(40.0)
    assert definition.analysis.direction_vector == pytest.approx((1.0, 0.0))


def test_mock_planner_does_not_parse_or_mutate_user_language() -> None:
    planner = MockColumnCollapseCalibrationPlanner()

    first = planner.plan("First valid English request.")
    second = planner.plan("A completely different valid English request.")

    assert first == second
    assert first is not second


def test_mock_planner_rejects_empty_prompt() -> None:
    with pytest.raises(ValueError, match="user_prompt"):
        MockColumnCollapseCalibrationPlanner().plan("   ")
