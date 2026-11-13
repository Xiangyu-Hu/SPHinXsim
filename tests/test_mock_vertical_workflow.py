"""Fast vertical acceptance test for the Mock AgentCAE control path."""

from __future__ import annotations

import json
import math
from pathlib import Path
from uuid import uuid4

import pytest

from sphinxsim.agent import (
    AgentCAE,
    AgentCAEAgentService,
    AgentRuntimeSettings,
    AgentServiceError,
    CalibrationWorkflowStatus,
    MockColumnCollapseCalibrationPlanner,
    MockStructuredPlannerClient,
    PlannerContextBuilder,
    StructuredCalibrationPlanner,
    build_builtin_tool_registry,
    build_capability_registry,
    build_default_knowledge_base,
)
from tests.test_deterministic_calibration_workflow import (
    DeterministicArtifactAdapter,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
PROMPT = "Calibrate a 2D column-collapse runout extension to 0.2 m."


def test_mock_vertical_plan_approve_execute_analyze_and_report(
    build_temp_path: Path,
) -> None:
    workspace = build_temp_path / f"mock_vertical_acceptance_{uuid4().hex}"
    workflow_id = "mock_vertical_acceptance"
    tools = build_builtin_tool_registry()
    capabilities = build_capability_registry(tools)
    context_builder = PlannerContextBuilder(
        tools,
        capabilities,
        build_default_knowledge_base(PROJECT_ROOT, tools, capabilities),
    )
    definition = MockColumnCollapseCalibrationPlanner().plan(PROMPT)
    planner_client = MockStructuredPlannerClient(
        definition.model_dump(mode="json")
    )
    planner = StructuredCalibrationPlanner(context_builder, planner_client, tools)
    adapter = DeterministicArtifactAdapter()
    facade = AgentCAE(PROJECT_ROOT, workspace)
    facade.runtime.save_settings(
        AgentRuntimeSettings(workflow_id=workflow_id, require_approval=True)
    )
    service = AgentCAEAgentService(
        facade.runtime.store,
        facade.runtime.database_path,
        planner,
        adapter,
        require_approval=True,
    )

    drafted = service.start(PROMPT, workflow_id=workflow_id)

    assert drafted.phase == "awaiting_plan_approval"
    assert drafted.awaiting_approval is True
    assert drafted.plan_approved is False
    assert drafted.case_prepared is False
    assert drafted.plan_summary.target_value == pytest.approx(0.2)
    assert drafted.plan_summary.target_tolerance == pytest.approx(0.005)
    assert drafted.plan_summary.maximum_simulations == 8
    assert drafted.approval_request["action"] == "approve_calibration_plan"
    assert adapter.generate_calls == 0
    assert adapter.validate_calls == 0
    assert adapter.run_calls == 0
    assert not Path(
        facade.runtime.store.root,
        "workflows",
        workflow_id,
        "cases",
        "column_collapse_generated.json",
    ).exists()

    assert len(planner_client.requests) == 1
    planning_request = planner_client.requests[0]
    assert planning_request.user_prompt == PROMPT
    assert planning_request.context.evidence
    assert "set_friction_angle" in {
        item["name"] for item in planning_request.context.tool_capabilities
    }
    assert "runout" in planning_request.context.metrics
    assert planning_request.output_schema["title"] == (
        "CalibrationWorkflowDefinition"
    )

    completed = service.approve(workflow_id)

    assert completed.status == CalibrationWorkflowStatus.CONVERGED
    assert completed.phase == "converged"
    assert completed.awaiting_approval is False
    assert completed.plan_approved is True
    assert completed.approved_at is not None
    assert completed.case_prepared is True
    assert completed.iteration_count == 4
    assert completed.best_parameter_value == pytest.approx(10.0)
    assert completed.best_metric_value == pytest.approx(0.2)
    assert completed.best_absolute_error == pytest.approx(0.0)
    assert adapter.generate_calls == 1
    assert adapter.validate_calls == 5
    assert adapter.run_calls == 4

    with pytest.raises(AgentServiceError, match="not awaiting approval"):
        service.approve(workflow_id)

    persisted = service.workflow.load(workflow_id)
    assert persisted.plan_approved is True
    assert persisted.case_prepared is True
    assert [item.parameter_value for item in persisted.iterations] == [
        0.0,
        40.0,
        20.0,
        10.0,
    ]
    assert [item.metric_value for item in persisted.iterations] == pytest.approx(
        [0.25, 0.08, 0.15, 0.20]
    )

    for iteration in persisted.iterations:
        case_payload = json.loads(
            Path(iteration.case_path).read_text(encoding="utf-8")
        )
        stored_angle = case_payload["continuum_bodies"][0]["material"][
            "friction_angle"
        ]
        assert math.degrees(stored_angle) == pytest.approx(
            iteration.parameter_value
        )
        run = facade.runtime.store.load_run(
            iteration.task_id,
            iteration.run_id,
        )
        analysis = facade.runtime.store.load_analysis(
            iteration.task_id,
            iteration.run_id,
            iteration.analysis_id,
        )
        assert Path(run.output_dir, "output").is_dir()
        assert Path(analysis.metrics["state_file"]).is_file()
        assert analysis.metrics["runout_extension"] == pytest.approx(
            iteration.metric_value
        )

    reopened = AgentCAE(PROJECT_ROOT, workspace)
    status_from_new_process_boundary = reopened.status(workflow_id)
    report = reopened.report(workflow_id)

    assert status_from_new_process_boundary == completed
    assert report.succeeded is True
    assert report.plan_approved is True
    assert report.plan_summary.target_value == pytest.approx(0.2)
    assert report.best_result is not None
    assert report.best_result.parameter_value == pytest.approx(10.0)
    assert report.best_result.metric_value == pytest.approx(0.2)
    assert report.best_result.absolute_error == pytest.approx(0.0)
    assert len(report.iterations) == 4
    assert (workspace / "checkpoints.sqlite").is_file()
    assert (workspace / "reports" / f"{workflow_id}.json").is_file()
