"""Tests for NVIDIA NIM structured calibration planning."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from sphinxsim.agent import (
    MockColumnCollapseCalibrationPlanner,
    NvidiaStructuredPlannerClient,
    PlannerContextBuilder,
    StructuredCalibrationPlanner,
    StructuredPlannerRequest,
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


def make_request() -> StructuredPlannerRequest:
    tools = build_builtin_tool_registry()
    capabilities = build_capability_registry(tools)
    context = PlannerContextBuilder(
        tools,
        capabilities,
        build_default_knowledge_base(PROJECT_ROOT, tools, capabilities),
    ).build(PROMPT)
    return StructuredPlannerRequest(
        system_prompt="Return one valid JSON object.",
        user_prompt=PROMPT,
        context=context,
        output_schema=MockColumnCollapseCalibrationPlanner()
        .plan(PROMPT)
        .model_json_schema(),
    )


def test_nvidia_client_serializes_context_and_parses_json(monkeypatch) -> None:
    expected = MockColumnCollapseCalibrationPlanner().plan(PROMPT).model_dump(mode="json")
    client = NvidiaStructuredPlannerClient(api_key="test-key")
    captured = {}

    def fake_post_chat(*, messages, temperature):
        captured["messages"] = messages
        captured["temperature"] = temperature
        return f"```json\n{json.dumps(expected)}\n```"

    monkeypatch.setattr(client._llm, "_post_chat", fake_post_chat)

    assert client.complete_json(make_request()) == expected
    assert captured["temperature"] == 0.0
    payload = json.loads(captured["messages"][1]["content"])
    assert payload["user_prompt"] == PROMPT
    assert payload["planner_context"]["metrics"]["runout"]["outputs"][-1] == (
        "runout_extension"
    )


def test_nvidia_client_rejects_non_object_json(monkeypatch) -> None:
    client = NvidiaStructuredPlannerClient(api_key="test-key")
    monkeypatch.setattr(client._llm, "_post_chat", lambda **kwargs: "[]")

    with pytest.raises(StructuredPlanningError, match="one JSON object"):
        client.complete_json(make_request())


@pytest.mark.skipif(
    os.getenv("SPHINXSIM_RUN_NVIDIA_PLANNER") != "1",
    reason="set SPHINXSIM_RUN_NVIDIA_PLANNER=1 to call NVIDIA NIM",
)
def test_live_nvidia_planner_returns_validated_definition() -> None:
    tools = build_builtin_tool_registry()
    capabilities = build_capability_registry(tools)
    planner = StructuredCalibrationPlanner(
        PlannerContextBuilder(
            tools,
            capabilities,
            build_default_knowledge_base(PROJECT_ROOT, tools, capabilities),
        ),
        NvidiaStructuredPlannerClient.from_environment(),
        tools,
    )

    definition = planner.plan(PROMPT)

    assert definition.calibration.objective.metric_name == "runout_extension"
    assert definition.calibration.objective.target_value == pytest.approx(0.2)
    assert definition.calibration.parameter.tool_name == "set_friction_angle"
    assert definition.calibration.objective.tool_name == "analyze_runout"
