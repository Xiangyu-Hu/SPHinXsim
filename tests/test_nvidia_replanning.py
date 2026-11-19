"""Tests for NVIDIA-backed bounded workflow replanning."""

from __future__ import annotations

import json
import os

import pytest

from sphinxsim.agent import MockColumnCollapseCalibrationPlanner
from sphinxsim.agent.replanning import (
    NvidiaReplanningClient,
    ReplanAction,
    ReplanningContext,
    ReplanningRequest,
    StructuredReplanner,
)


def make_context() -> ReplanningContext:
    return ReplanningContext(
        workflow_id="nvidia_replan_workflow",
        failed_node="execute_candidate",
        error_message="SPHinXsim command timed out.",
        failed_candidate=0.0,
        replan_attempt=1,
        maximum_replans=2,
        definition=MockColumnCollapseCalibrationPlanner().plan(
            "Reach a runout extension of 0.2 m."
        ),
        observations=[],
    )


def test_nvidia_replanner_serializes_failure_and_parses_decision(monkeypatch) -> None:
    client = NvidiaReplanningClient(api_key="test-key")
    captured = {}

    def fake_post_chat(*, messages, temperature):
        captured["messages"] = messages
        captured["temperature"] = temperature
        return "```json\n{\"action\":\"retry_candidate\",\"reason\":\"Retry timeout.\"}\n```"

    monkeypatch.setattr(client._llm, "_post_chat", fake_post_chat)
    context = make_context()
    request = ReplanningRequest(
        system_prompt="Return one JSON object.",
        context=context,
        output_schema={},
    )

    response = client.complete_json(request)

    assert response["action"] == "retry_candidate"
    payload = json.loads(captured["messages"][1]["content"])
    assert payload["failure_context"]["error_message"].endswith("timed out.")
    assert captured["temperature"] == 0.0


@pytest.mark.skipif(
    os.getenv("SPHINXSIM_RUN_NVIDIA_REPLANNER") != "1",
    reason="set SPHINXSIM_RUN_NVIDIA_REPLANNER=1 to call NVIDIA NIM",
)
def test_live_nvidia_replanner_returns_allowlisted_decision() -> None:
    decision = StructuredReplanner(
        NvidiaReplanningClient.from_environment()
    ).replan(make_context())

    assert decision.action in {
        ReplanAction.RETRY_CANDIDATE,
        ReplanAction.REVISE_PARAMETER_BOUNDS,
        ReplanAction.STOP,
    }
