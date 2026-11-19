"""Tests for the provider-neutral structured planning boundary."""

from __future__ import annotations

from pathlib import Path

import pytest

from sphinxsim.agent import (
    MockColumnCollapseCalibrationPlanner,
    MockStructuredPlannerClient,
    PlannerContextBuilder,
    StructuredCalibrationPlanner,
    StructuredPlanningError,
    build_builtin_tool_registry,
    build_capability_registry,
    build_default_knowledge_base,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
PROMPT = (
    "Calibrate a 2D column-collapse runout extension to 0.2 m by adjusting "
    "friction angle."
)


def make_planner(response: dict) -> tuple[StructuredCalibrationPlanner, MockStructuredPlannerClient]:
    tools = build_builtin_tool_registry()
    capabilities = build_capability_registry(tools)
    context = PlannerContextBuilder(
        tools,
        capabilities,
        build_default_knowledge_base(PROJECT_ROOT, tools, capabilities),
    )
    client = MockStructuredPlannerClient(response)
    return StructuredCalibrationPlanner(context, client, tools), client


def valid_response() -> dict:
    return MockColumnCollapseCalibrationPlanner().plan(PROMPT).model_dump(mode="json")


def test_mock_client_uses_production_request_and_schema() -> None:
    planner, client = make_planner(valid_response())

    definition = planner.plan(PROMPT)

    assert definition.calibration.objective.metric_name == "runout_extension"
    assert definition.calibration.objective.target_value == pytest.approx(0.2)
    assert len(client.requests) == 1
    request = client.requests[0]
    assert request.user_prompt == PROMPT
    assert request.output_schema["title"] == "CalibrationWorkflowDefinition"
    assert "domain.runout" in {
        evidence.document_id for evidence in request.context.evidence
    }


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (lambda value: value.update({"python_code": "print('unsafe')"}), "extra"),
        (
            lambda value: value["calibration"]["parameter"].update(
                {"tool_name": "execute_python"}
            ),
            "unknown Agent tool",
        ),
        (
            lambda value: value["calibration"]["objective"].update(
                {"metric_name": "maximum_velocity"}
            ),
            "metric is not supported",
        ),
    ],
)
def test_invalid_structured_output_is_rejected_before_execution(
    mutation,
    message: str,
) -> None:
    response = valid_response()
    mutation(response)
    planner, _ = make_planner(response)

    with pytest.raises(StructuredPlanningError, match=message):
        planner.plan(PROMPT)
