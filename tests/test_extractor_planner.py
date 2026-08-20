"""Tests for the structured, non-executing extractor planner boundary."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from sphinxsim.agent.extractor_planner import (
    EXTRACTOR_PLANNER_SYSTEM_PROMPT,
    ExtractorPlannerRequest,
    ExtractorPlanningContext,
    ExtractorPlanningError,
    MockExtractorPlannerClient,
    NvidiaExtractorPlannerClient,
    StructuredExtractorPlanner,
)


def context_2d() -> ExtractorPlanningContext:
    return ExtractorPlanningContext(
        body_names=["GranularBody"],
        spatial_dimension=2,
        available_components={"x": 0, "y": 1},
    )


def valid_response() -> dict:
    return {
        "extractor_id": "deposit_extent",
        "description": "Extract final deposit length and height.",
        "body_name": "GranularBody",
        "outputs": [
            {"output_name": "front_x", "component_index": 0, "reduction": "maximum"},
            {"output_name": "height_y", "component_index": 1, "reduction": "maximum"},
        ],
    }


def test_mock_uses_production_request_and_returns_validated_spec() -> None:
    client = MockExtractorPlannerClient(valid_response())
    result = StructuredExtractorPlanner(client).plan(
        "Measure the final deposit length and height.",
        context_2d(),
    )

    assert result.extractor_id == "deposit_extent"
    assert len(client.requests) == 1
    request = client.requests[0]
    assert request.system_prompt == EXTRACTOR_PLANNER_SYSTEM_PROMPT
    assert request.output_schema["additionalProperties"] is False
    assert request.context.available_components == {"x": 0, "y": 1}


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (lambda value: value.update(body_name="UnknownBody"), "body_name"),
        (
            lambda value: value["outputs"][0].update(component_index=2),
            "component",
        ),
        (lambda value: value.update(python_code="open('secret')"), "extra"),
    ],
)
def test_rejects_proposals_outside_case_allow_list(mutation, message: str) -> None:
    response = valid_response()
    mutation(response)

    with pytest.raises(ExtractorPlanningError, match=message):
        StructuredExtractorPlanner(MockExtractorPlannerClient(response)).plan(
            "Measure the result.",
            context_2d(),
        )


def test_rejects_empty_request_before_provider_call() -> None:
    client = MockExtractorPlannerClient(valid_response())

    with pytest.raises(ExtractorPlanningError, match="must not be empty"):
        StructuredExtractorPlanner(client).plan("  ", context_2d())

    assert client.requests == []


def test_context_is_derived_from_validated_case() -> None:
    case_path = (
        Path(__file__).resolve().parent
        / "test_simulation"
        / "test_2d_simulation"
        / "data"
        / "column_collapse.json"
    )

    context = ExtractorPlanningContext.from_case(case_path)

    assert context.spatial_dimension == 2
    assert context.available_components == {"x": 0, "y": 1}
    assert context.body_names == ["GranularBody", "WallBoundary"]


def test_nvidia_client_parses_structured_json_without_live_network(monkeypatch) -> None:
    client = NvidiaExtractorPlannerClient(api_key="test-key")
    monkeypatch.setattr(
        client._llm,
        "_post_chat",
        lambda **kwargs: f"```json\n{valid_json()}\n```",
    )
    request = ExtractorPlannerRequest(
        system_prompt=EXTRACTOR_PLANNER_SYSTEM_PROMPT,
        user_request="Measure the result.",
        context=context_2d(),
        output_schema={},
    )

    result = client.complete_json(request)

    assert result["extractor_id"] == "deposit_extent"


def valid_json() -> str:
    return json.dumps(valid_response())


def test_nvidia_client_rejects_invalid_json_without_traceback(monkeypatch) -> None:
    client = NvidiaExtractorPlannerClient(api_key="test-key")
    monkeypatch.setattr(client._llm, "_post_chat", lambda **kwargs: "not json")
    request = ExtractorPlannerRequest(
        system_prompt=EXTRACTOR_PLANNER_SYSTEM_PROMPT,
        user_request="Measure the result.",
        context=context_2d(),
        output_schema={},
    )

    with pytest.raises(ExtractorPlanningError, match="invalid JSON"):
        client.complete_json(request)
