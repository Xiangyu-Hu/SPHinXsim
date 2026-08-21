"""Tests that every Agent LLM role follows the existing provider setting."""

from __future__ import annotations

from pathlib import Path

import pytest

import sphinxsim.agent.runtime as runtime_module
from sphinxsim.agent import (
    AgentRuntime,
    AgentRuntimeSettings,
    MockColumnCollapseCalibrationPlanner,
    MockStructuredPlannerClient,
    ProviderReplanningClient,
    ProviderStructuredPlannerClient,
    StructuredCalibrationPlanner,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class RecordingBackend:
    def __init__(self, provider_name: str, response: dict) -> None:
        self.provider_name = provider_name
        self.response = response
        self.calls: list[dict] = []

    def complete_json(self, *, system_prompt, payload):
        self.calls.append({"system_prompt": system_prompt, "payload": payload})
        return self.response


def test_mock_planner_uses_same_rag_and_structured_validation_path(
    build_temp_path: Path,
) -> None:
    runtime = AgentRuntime(PROJECT_ROOT, build_temp_path / "mock_provider_runtime")
    service = runtime.create_service(
        AgentRuntimeSettings(workflow_id="mock_provider"),
        starting=True,
    )
    planner = service.workflow.planner

    definition = planner.plan("Calibrate runout extension to 0.2 m.")

    assert isinstance(planner, StructuredCalibrationPlanner)
    assert isinstance(planner.client, MockStructuredPlannerClient)
    assert planner.client.requests[0].context.evidence
    assert definition.calibration.objective.target_value == pytest.approx(0.2)


@pytest.mark.parametrize(
    ("configured", "canonical"),
    [
        ("ollama", "ollama"),
        ("openai", "openai"),
        ("nvidia", "nvidia_nim"),
    ],
)
def test_runtime_uses_one_canonical_provider_for_all_llm_roles(
    build_temp_path: Path,
    monkeypatch,
    configured: str,
    canonical: str,
) -> None:
    expected = MockColumnCollapseCalibrationPlanner().plan(
        "Calibrate runout extension to 0.2 m."
    ).model_dump(mode="json")
    created: list[RecordingBackend] = []

    def fake_backend(provider_name):
        backend = RecordingBackend(provider_name, expected)
        created.append(backend)
        return backend

    monkeypatch.setattr(runtime_module, "create_structured_backend", fake_backend)
    monkeypatch.setenv("SPHINXSIM_LLM_API_KEY", "test-key")
    settings = AgentRuntimeSettings(
        workflow_id=f"runtime_{canonical}",
        planner_provider=configured,
        replanner_provider=configured,
        generation_provider=configured,
    )
    runtime = AgentRuntime(
        PROJECT_ROOT,
        build_temp_path / f"provider_runtime_{canonical}",
    )

    service = runtime.create_service(settings, starting=True)
    definition = service.workflow.planner.plan(
        "Calibrate runout extension to 0.2 m."
    )

    assert settings.planner_provider == canonical
    assert settings.replanner_provider == canonical
    assert settings.generation_provider == canonical
    assert [item.provider_name for item in created] == [canonical, canonical]
    assert isinstance(service.workflow.planner.client, ProviderStructuredPlannerClient)
    assert isinstance(service.replanner.client, ProviderReplanningClient)
    assert created[0].calls[0]["payload"]["planner_context"]["evidence"]
    assert definition.calibration.objective.target_value == pytest.approx(0.2)
    assert service.workflow.command_adapter.environment == {
        "SPHINXSIM_LLM_PROVIDER": canonical
    }
