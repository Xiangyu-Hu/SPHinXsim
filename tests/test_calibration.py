"""Tests for calibration contracts and deterministic search policy."""

from __future__ import annotations

import pytest

from sphinxsim.agent.calibration import (
    BoundedSearchPolicy,
    CalibrationDecisionStatus,
    CalibrationError,
    CalibrationObservation,
    CalibrationSpec,
    Monotonicity,
    ObjectiveSpec,
    ParameterSpec,
)
from sphinxsim.agent.tools import build_builtin_tool_registry


def make_spec(**overrides: object) -> CalibrationSpec:
    values: dict[str, object] = {
        "calibration_id": "column_collapse_runout",
        "parameter": ParameterSpec(
            tool_name="set_friction_angle",
            input_field="friction_angle_degrees",
            lower_bound=20.0,
            upper_bound=40.0,
            unit="degree",
        ),
        "objective": ObjectiveSpec(
            tool_name="analyze_runout",
            metric_name="final_front",
            target_value=0.4,
            tolerance=0.01,
            unit="m",
        ),
        "expected_monotonicity": Monotonicity.DECREASING,
        "maximum_iterations": 6,
    }
    values.update(overrides)
    return CalibrationSpec.model_validate(values)


def observation(parameter: float, metric: float) -> CalibrationObservation:
    return CalibrationObservation(parameter_value=parameter, metric_value=metric)


def test_spec_references_registered_tools_and_parameter_field() -> None:
    spec = make_spec()
    spec.validate_tools(build_builtin_tool_registry())

    invalid = make_spec(
        parameter=ParameterSpec(
            tool_name="set_friction_angle",
            input_field="arbitrary_python",
            lower_bound=20.0,
            upper_bound=40.0,
            unit="degree",
        )
    )
    with pytest.raises(CalibrationError, match="has no input field"):
        invalid.validate_tools(build_builtin_tool_registry())


def test_decreasing_policy_samples_bounds_then_midpoint() -> None:
    policy = BoundedSearchPolicy()
    spec = make_spec()

    first = policy.propose_next(spec, [])
    second = policy.propose_next(spec, [observation(20.0, 0.5)])
    third = policy.propose_next(
        spec,
        [observation(20.0, 0.5), observation(40.0, 0.3)],
    )

    assert first.candidate_value == pytest.approx(20.0)
    assert second.candidate_value == pytest.approx(40.0)
    assert third.candidate_value == pytest.approx(30.0)
    assert third.status == CalibrationDecisionStatus.NEXT


def test_policy_reports_convergence_and_best_observation() -> None:
    decision = BoundedSearchPolicy().propose_next(
        make_spec(),
        [observation(20.0, 0.5), observation(40.0, 0.3), observation(30.0, 0.405)],
    )

    assert decision.status == CalibrationDecisionStatus.CONVERGED
    assert decision.candidate_value is None
    assert decision.best_parameter_value == pytest.approx(30.0)
    assert decision.best_absolute_error == pytest.approx(0.005)


def test_policy_rejects_unbracketed_and_non_monotonic_results() -> None:
    policy = BoundedSearchPolicy()
    spec = make_spec()

    with pytest.raises(CalibrationError, match="not bracketed"):
        policy.propose_next(
            spec,
            [observation(20.0, 0.6), observation(40.0, 0.5)],
        )
    with pytest.raises(CalibrationError, match="violate"):
        policy.propose_next(
            spec,
            [observation(20.0, 0.5), observation(30.0, 0.55)],
        )


def test_increasing_policy_and_iteration_limit() -> None:
    spec = make_spec(
        expected_monotonicity=Monotonicity.INCREASING,
        maximum_iterations=2,
    )
    decision = BoundedSearchPolicy().propose_next(
        spec,
        [observation(20.0, 0.3), observation(40.0, 0.5)],
    )

    assert decision.status == CalibrationDecisionStatus.EXHAUSTED
    assert decision.best_absolute_error == pytest.approx(0.1)
