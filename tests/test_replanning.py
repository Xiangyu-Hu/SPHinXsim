"""Tests for bounded structured workflow replanning."""

from __future__ import annotations

import pytest

from sphinxsim.agent import MockColumnCollapseCalibrationPlanner
from sphinxsim.agent.replanning import (
    BoundedReplanningPolicy,
    MockReplanningClient,
    ReplanAction,
    ReplanningContext,
    ReplanningDecision,
    ReplanningError,
    StructuredReplanner,
)


def make_context(**updates) -> ReplanningContext:
    values = {
        "workflow_id": "replan_workflow",
        "failed_node": "execute_candidate",
        "error_message": "SPHinXsim command timed out.",
        "failed_candidate": 0.0,
        "replan_attempt": 1,
        "maximum_replans": 2,
        "definition": MockColumnCollapseCalibrationPlanner().plan(
            "Reach a runout extension of 0.2 m."
        ),
        "observations": [],
    }
    values.update(updates)
    return ReplanningContext.model_validate(values)


def test_mock_replanner_uses_structured_production_contract() -> None:
    client = MockReplanningClient(
        [{"action": "retry_candidate", "reason": "Retry one timeout."}]
    )
    replanner = StructuredReplanner(client)

    decision = replanner.replan(make_context())

    assert decision.action == ReplanAction.RETRY_CANDIDATE
    assert client.requests[0].output_schema["title"] == "ReplanningDecision"
    assert client.requests[0].context.error_message.endswith("timed out.")


def test_replanner_rejects_code_and_unknown_actions() -> None:
    client = MockReplanningClient(
        [
            {
                "action": "execute_python",
                "reason": "Bypass controls.",
                "python_code": "print('unsafe')",
            }
        ]
    )

    with pytest.raises(ReplanningError, match="invalid decision"):
        StructuredReplanner(client).replan(make_context())


def test_policy_requires_failed_candidate_for_retry() -> None:
    with pytest.raises(ReplanningError, match="failed candidate"):
        BoundedReplanningPolicy().validate(
            make_context(failed_candidate=None),
            ReplanningDecision(
                action=ReplanAction.RETRY_CANDIDATE,
                reason="Retry.",
            ),
        )


def test_policy_accepts_bounds_that_include_observations() -> None:
    decision = ReplanningDecision(
        action=ReplanAction.REVISE_PARAMETER_BOUNDS,
        reason="Extend the upper bracket.",
        lower_bound=0.0,
        upper_bound=60.0,
    )
    context = make_context(
        failed_node="decide",
        failed_candidate=None,
        observations=[
            {"parameter_value": 0.0, "metric_value": 0.25},
            {"parameter_value": 40.0, "metric_value": 0.08},
        ],
    )

    assert BoundedReplanningPolicy().validate(context, decision) == decision


def test_policy_stops_after_bounded_attempt_count() -> None:
    decision = BoundedReplanningPolicy(maximum_replans=2).validate(
        make_context(replan_attempt=3),
        ReplanningDecision(
            action=ReplanAction.RETRY_CANDIDATE,
            reason="Retry again.",
        ),
    )

    assert decision.action == ReplanAction.STOP
