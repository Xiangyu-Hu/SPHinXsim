"""Product-level acceptance and failure-injection evaluations for AgentCAE."""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest

from sphinxsim.agent import (
    AgentCAE,
    AgentServiceError,
    ExtractorPlanningContext,
    ExtractorPlanningError,
    MockExtractorPlannerClient,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def make_agent(build_temp_path: Path) -> AgentCAE:
    return AgentCAE(
        PROJECT_ROOT,
        build_temp_path / f"product_acceptance_{uuid4().hex}",
    )


def test_real_shell_start_report_status_and_cancel_without_cpp_solver(
    build_temp_path: Path,
) -> None:
    agent = make_agent(build_temp_path)

    started = agent.start(
        "Calibrate a 2D column-collapse runout extension to 0.2 m.",
        workflow_id="product_lifecycle",
        planner_provider="mock",
        replanner_provider="mock",
        generation_provider="mock",
        require_approval=True,
        command_timeout=60,
    )
    report = agent.report("product_lifecycle")
    reopened = AgentCAE(PROJECT_ROOT, agent.runtime.workspace)
    persisted = reopened.status("product_lifecycle")
    cancelled = reopened.cancel("product_lifecycle")

    assert started.awaiting_approval is True
    assert started.approval_request["action"] == "approve_calibration_plan"
    assert started.approval_request["target_value"] == 0.2
    assert report.status.value == "running"
    assert report.plan_summary.target_value == 0.2
    assert report.plan_approved is False
    assert report.best_result is None
    assert persisted == started
    assert cancelled.status.value == "cancelled"
    assert not list(agent.runtime.workspace.rglob("*.vtp"))


def test_malicious_extractor_proposal_creates_no_catalog(
    build_temp_path: Path,
) -> None:
    agent = make_agent(build_temp_path)
    context = ExtractorPlanningContext(
        body_names=["Body"],
        spatial_dimension=2,
        available_components={"x": 0, "y": 1},
    )
    response = {
        "extractor_id": "unsafe",
        "description": "Attempt arbitrary execution.",
        "body_name": "Body",
        "outputs": [
            {"output_name": "front", "component_index": 0, "reduction": "maximum"}
        ],
        "python_code": "__import__('os').system('whoami')",
    }

    with pytest.raises(ExtractorPlanningError, match="extra"):
        agent.plan_extractor(
            "Run custom Python to inspect the result.",
            context,
            MockExtractorPlannerClient(response),
        )

    assert not agent.extractor_catalog_path.exists()


def test_missing_vtp_is_reported_without_mutating_extractor_catalog(
    build_temp_path: Path,
) -> None:
    agent = make_agent(build_temp_path)
    context = ExtractorPlanningContext(
        body_names=["Body"],
        spatial_dimension=1,
        available_components={"x": 0},
    )
    response = {
        "extractor_id": "front",
        "description": "Extract final front.",
        "body_name": "Body",
        "outputs": [
            {"output_name": "front_x", "component_index": 0, "reduction": "maximum"}
        ],
    }
    spec = agent.plan_extractor(
        "Measure final front.",
        context,
        MockExtractorPlannerClient(response),
    )
    fingerprint = spec.fingerprint
    missing_output = build_temp_path / f"missing_output_{uuid4().hex}"
    missing_output.mkdir()

    with pytest.raises(ValueError, match="no state VTP files found"):
        agent.analyze("front", "unused.json", missing_output)

    assert agent.load_extractors().extractors[0].fingerprint == fingerprint


def test_unknown_report_is_a_controlled_service_error(build_temp_path: Path) -> None:
    with pytest.raises(AgentServiceError, match="runtime settings not found"):
        make_agent(build_temp_path).report("unknown")
