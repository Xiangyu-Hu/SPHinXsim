"""Fast tests for the complete deterministic calibration control loop."""

from __future__ import annotations

import json
import math
from pathlib import Path
from uuid import uuid4

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from sphinxsim.agent import (
    AgentTaskStore,
    CalibrationWorkflowStatus,
    DeterministicCalibrationWorkflow,
    MockColumnCollapseCalibrationPlanner,
    MockReplanningClient,
    StructuredReplanner,
    build_calibration_graph,
    open_sqlite_calibration_graph,
)
from sphinxsim.services import CLICommandResult


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SOURCE_CASE = (
    PROJECT_ROOT
    / "tests"
    / "test_simulation"
    / "test_2d_simulation"
    / "data"
    / "column_collapse.json"
)


def make_store(build_temp_path: Path, name: str) -> AgentTaskStore:
    return AgentTaskStore(build_temp_path / f"{name}_{uuid4().hex}")


def success(command: str) -> CLICommandResult:
    return CLICommandResult(
        command=(command,),
        returncode=0,
        stdout="ok",
        stderr="",
        duration_seconds=0.01,
    )


class DeterministicArtifactAdapter:
    cwd = PROJECT_ROOT

    def __init__(
        self,
        *,
        fail_run_number: int | None = None,
        omit_vtp_run_number: int | None = None,
    ) -> None:
        self.fail_run_number = fail_run_number
        self.omit_vtp_run_number = omit_vtp_run_number
        self.generate_calls = 0
        self.validate_calls = 0
        self.run_calls = 0

    def generate(self, description, output_path, *, timeout=None) -> CLICommandResult:
        self.generate_calls += 1
        destination = Path(output_path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(SOURCE_CASE.read_text(encoding="utf-8"), encoding="utf-8")
        return success("generate")

    def validate(self, config_path, *, timeout=None) -> CLICommandResult:
        self.validate_calls += 1
        json.loads(Path(config_path).read_text(encoding="utf-8"))
        return success("validate")

    def run(self, config_path, *, output_dir=None, timeout=None) -> CLICommandResult:
        self.run_calls += 1
        if self.run_calls == self.fail_run_number:
            return CLICommandResult(
                command=("run",),
                returncode=1,
                stdout="",
                stderr="forced solver failure",
                duration_seconds=0.01,
            )
        if self.run_calls == self.omit_vtp_run_number:
            return success("run")
        payload = json.loads(Path(config_path).read_text(encoding="utf-8"))
        angle_radians = payload["continuum_bodies"][0]["material"]["friction_angle"]
        angle_degrees = math.degrees(angle_radians)
        fronts = {0.0: 0.45, 10.0: 0.40, 20.0: 0.35, 40.0: 0.28}
        final_front = fronts[round(angle_degrees, 6)]
        output_path = Path(output_dir) / "output"
        output_path.mkdir(parents=True, exist_ok=True)
        (output_path / "GranularBody_0000001000.vtp").write_text(
            f'''<?xml version="1.0"?>
<VTKFile type="PolyData" version="1.0" byte_order="LittleEndian">
  <PolyData>
    <FieldData><DataArray Name="TimeValue" format="ascii">0.8</DataArray></FieldData>
    <Piece Name="GranularBody" NumberOfPoints="3">
      <Points><DataArray Name="Position" NumberOfComponents="3" format="ascii">
        0.10 0.00 0.00  {final_front} 0.02 0.00  0.20 0.05 0.00
      </DataArray></Points>
    </Piece>
  </PolyData>
</VTKFile>
''',
            encoding="utf-8",
        )
        return success("run")


class AlwaysFailingArtifactAdapter(DeterministicArtifactAdapter):
    def run(self, config_path, *, output_dir=None, timeout=None) -> CLICommandResult:
        self.run_calls += 1
        return CLICommandResult(
            command=("run",),
            returncode=1,
            stdout="",
            stderr="persistent solver failure",
            duration_seconds=0.01,
        )


class FormulaArtifactAdapter(DeterministicArtifactAdapter):
    def run(self, config_path, *, output_dir=None, timeout=None) -> CLICommandResult:
        self.run_calls += 1
        payload = json.loads(Path(config_path).read_text(encoding="utf-8"))
        angle = math.degrees(
            payload["continuum_bodies"][0]["material"]["friction_angle"]
        )
        extension = 0.30 - 0.005 * angle
        final_front = 0.20 + extension
        output_path = Path(output_dir) / "output"
        output_path.mkdir(parents=True, exist_ok=True)
        (output_path / "GranularBody_0000001000.vtp").write_text(
            f'''<?xml version="1.0"?>
<VTKFile type="PolyData" version="1.0" byte_order="LittleEndian">
  <PolyData><FieldData><DataArray Name="TimeValue" format="ascii">0.8</DataArray></FieldData>
    <Piece Name="GranularBody" NumberOfPoints="3"><Points>
      <DataArray Name="Position" NumberOfComponents="3" format="ascii">
        0.10 0.00 0.00 {final_front} 0.02 0.00 0.20 0.05 0.00
      </DataArray>
    </Points></Piece>
  </PolyData>
</VTKFile>
''',
            encoding="utf-8",
        )
        return success("run")


def test_complete_workflow_generates_runs_analyzes_and_converges(
    build_temp_path: Path,
) -> None:
    store = make_store(build_temp_path, "deterministic_calibration")
    adapter = DeterministicArtifactAdapter()
    workflow = DeterministicCalibrationWorkflow(
        store,
        MockColumnCollapseCalibrationPlanner(),
        adapter,
    )

    result = workflow.execute(
        "Calibrate a column collapse final front.",
        workflow_id="workflow_complete",
    )

    assert result.status == CalibrationWorkflowStatus.CONVERGED
    assert result.succeeded is True
    assert adapter.generate_calls == 1
    assert adapter.validate_calls == 5
    assert adapter.run_calls == 4
    assert [item.parameter_value for item in result.iterations] == [0.0, 40.0, 20.0, 10.0]
    assert [item.metric_value for item in result.iterations] == pytest.approx(
        [0.25, 0.08, 0.15, 0.20]
    )
    assert result.final_decision is not None
    assert result.final_decision.best_parameter_value == pytest.approx(10.0)
    assert workflow.load(result.workflow_id) == result
    assert all(Path(item.case_path).is_file() for item in result.iterations)


def test_plan_drafting_has_no_case_side_effects_and_preparation_is_explicit(
    build_temp_path: Path,
) -> None:
    store = make_store(build_temp_path, "draft_then_prepare")
    adapter = DeterministicArtifactAdapter()
    workflow = DeterministicCalibrationWorkflow(
        store,
        MockColumnCollapseCalibrationPlanner(),
        adapter,
    )

    result = workflow.draft_plan(
        "Calibrate a 2D column-collapse runout extension to 0.2 m.",
        workflow_id="draft_then_prepare_workflow",
    )

    assert result.case_prepared is False
    assert result.definition.calibration.objective.target_value == pytest.approx(0.2)
    assert adapter.generate_calls == 0
    assert adapter.validate_calls == 0
    assert not Path(result.baseline_case_path).exists()

    prepared = workflow.prepare_case(result)

    assert prepared.case_prepared is True
    assert adapter.generate_calls == 1
    assert adapter.validate_calls == 1
    assert Path(prepared.baseline_case_path).is_file()
    assert workflow.load(prepared.workflow_id).case_prepared is True


def test_case_preparation_is_idempotent(build_temp_path: Path) -> None:
    store = make_store(build_temp_path, "idempotent_prepare")
    adapter = DeterministicArtifactAdapter()
    workflow = DeterministicCalibrationWorkflow(
        store,
        MockColumnCollapseCalibrationPlanner(),
        adapter,
    )
    result = workflow.draft_plan(
        "Reach a runout extension of 0.2 m.",
        workflow_id="idempotent_prepare_workflow",
    )

    workflow.prepare_case(result)
    workflow.prepare_case(result)

    assert adapter.generate_calls == 1
    assert adapter.validate_calls == 1


def test_workflow_persists_real_step_failure_context(build_temp_path: Path) -> None:
    store = make_store(build_temp_path, "failed_calibration")
    adapter = DeterministicArtifactAdapter(fail_run_number=2)

    result = DeterministicCalibrationWorkflow(
        store,
        MockColumnCollapseCalibrationPlanner(),
        adapter,
    ).execute("Calibrate a column collapse.", workflow_id="workflow_failure")

    assert result.status == CalibrationWorkflowStatus.FAILED
    assert result.succeeded is False
    assert len(result.iterations) == 1
    assert "iteration 2 failed at run_case" in result.error_message
    assert "forced solver failure" in result.error_message


def test_workflow_fails_when_solver_produces_no_vtp(build_temp_path: Path) -> None:
    store = make_store(build_temp_path, "missing_vtp_calibration")

    result = DeterministicCalibrationWorkflow(
        store,
        MockColumnCollapseCalibrationPlanner(),
        DeterministicArtifactAdapter(omit_vtp_run_number=1),
    ).execute("Calibrate a column collapse.", workflow_id="workflow_missing_vtp")

    assert result.status == CalibrationWorkflowStatus.FAILED
    assert result.iterations == []
    assert "iteration 1 failed at analyze_result" in result.error_message
    assert "no state VTP" in result.error_message


def test_workflow_fails_when_target_is_not_bracketed(build_temp_path: Path) -> None:
    store = make_store(build_temp_path, "unbracketed_calibration")

    result = DeterministicCalibrationWorkflow(
        store,
        MockColumnCollapseCalibrationPlanner(target_runout_extension=0.40),
        DeterministicArtifactAdapter(),
    ).execute("Calibrate a column collapse.", workflow_id="workflow_unbracketed")

    assert result.status == CalibrationWorkflowStatus.FAILED
    assert len(result.iterations) == 2
    assert "not bracketed" in result.error_message


def test_workflow_stops_at_iteration_limit(build_temp_path: Path) -> None:
    store = make_store(build_temp_path, "exhausted_calibration")

    result = DeterministicCalibrationWorkflow(
        store,
        MockColumnCollapseCalibrationPlanner(maximum_iterations=2),
        DeterministicArtifactAdapter(),
    ).execute("Calibrate a column collapse.", workflow_id="workflow_exhausted")

    assert result.status == CalibrationWorkflowStatus.EXHAUSTED
    assert len(result.iterations) == 2
    assert result.final_decision is not None
    assert result.final_decision.reason == (
        "The maximum number of calibration iterations was reached."
    )


def test_workflow_rejects_duplicate_identifier_until_resume_exists(
    build_temp_path: Path,
) -> None:
    store = make_store(build_temp_path, "duplicate_calibration")
    workflow = DeterministicCalibrationWorkflow(
        store,
        MockColumnCollapseCalibrationPlanner(maximum_iterations=2),
        DeterministicArtifactAdapter(),
    )
    workflow.execute("Calibrate a column collapse.", workflow_id="duplicate_workflow")

    with pytest.raises(ValueError, match="resume support is not implemented"):
        workflow.execute(
            "Calibrate a column collapse.",
            workflow_id="duplicate_workflow",
        )


def test_incremental_operations_advance_one_candidate_at_a_time(
    build_temp_path: Path,
) -> None:
    store = make_store(build_temp_path, "incremental_calibration")
    adapter = DeterministicArtifactAdapter()
    workflow = DeterministicCalibrationWorkflow(
        store,
        MockColumnCollapseCalibrationPlanner(),
        adapter,
    )

    result = workflow.initialize(
        "Calibrate a column collapse.",
        workflow_id="incremental_workflow",
    )
    first_decision = workflow.propose_next(result)
    workflow.apply_decision(result, first_decision)
    first_iteration = workflow.execute_candidate(
        result,
        first_decision.candidate_value,
    )
    second_decision = workflow.propose_next(result)

    assert result.status == CalibrationWorkflowStatus.RUNNING
    assert first_iteration.parameter_value == pytest.approx(0.0)
    assert first_iteration.metric_value == pytest.approx(0.25)
    assert second_decision.candidate_value == pytest.approx(40.0)
    assert adapter.generate_calls == 1
    assert adapter.run_calls == 1


def test_candidate_execution_recovers_completed_child_task_without_rerun(
    build_temp_path: Path,
) -> None:
    store = make_store(build_temp_path, "recover_calibration")
    adapter = DeterministicArtifactAdapter()
    workflow = DeterministicCalibrationWorkflow(
        store,
        MockColumnCollapseCalibrationPlanner(),
        adapter,
    )
    result = workflow.initialize(
        "Calibrate a column collapse.",
        workflow_id="recover_workflow",
    )
    stale_checkpoint = result.model_copy(deep=True)

    workflow.execute_candidate(result, 0.0)
    recovered = workflow.execute_candidate(stale_checkpoint, 0.0)

    assert recovered.metric_value == pytest.approx(0.25)
    assert len(stale_checkpoint.iterations) == 1
    assert adapter.run_calls == 1


def test_langgraph_runs_complete_deterministic_loop(build_temp_path: Path) -> None:
    store = make_store(build_temp_path, "langgraph_complete")
    adapter = DeterministicArtifactAdapter()
    workflow = DeterministicCalibrationWorkflow(
        store,
        MockColumnCollapseCalibrationPlanner(),
        adapter,
    )
    graph = build_calibration_graph(workflow, checkpointer=InMemorySaver())
    config = {"configurable": {"thread_id": "thread_complete"}}

    state = graph.invoke(
        {
            "user_prompt": "Calibrate a column collapse.",
            "workflow_id": "langgraph_complete_workflow",
        },
        config,
    )
    result = workflow.load("langgraph_complete_workflow")

    assert result.status == CalibrationWorkflowStatus.CONVERGED
    assert len(result.iterations) == 4
    assert adapter.run_calls == 4
    assert state["workflow_result"]["status"] == "converged"
    assert len(list(graph.get_state_history(config))) >= 8


def test_langgraph_resumes_after_candidate_checkpoint_without_rerun(
    build_temp_path: Path,
) -> None:
    store = make_store(build_temp_path, "langgraph_resume")
    adapter = DeterministicArtifactAdapter()
    workflow = DeterministicCalibrationWorkflow(
        store,
        MockColumnCollapseCalibrationPlanner(),
        adapter,
    )
    graph = build_calibration_graph(
        workflow,
        checkpointer=InMemorySaver(),
        interrupt_after=["execute_candidate"],
    )
    config = {"configurable": {"thread_id": "thread_resume"}}

    paused = graph.invoke(
        {
            "user_prompt": "Calibrate a column collapse.",
            "workflow_id": "langgraph_resume_workflow",
        },
        config,
    )

    assert len(paused["workflow_result"]["iterations"]) == 1
    assert adapter.run_calls == 1
    assert graph.get_state(config).next == ("decide",)

    graph.invoke(None, config)
    assert adapter.run_calls == 2
    assert len(workflow.load("langgraph_resume_workflow").iterations) == 2


def test_langgraph_approves_plan_once_before_any_side_effects(
    build_temp_path: Path,
) -> None:
    store = make_store(build_temp_path, "langgraph_approval")
    adapter = DeterministicArtifactAdapter()
    workflow = DeterministicCalibrationWorkflow(
        store,
        MockColumnCollapseCalibrationPlanner(),
        adapter,
    )
    graph = build_calibration_graph(
        workflow,
        checkpointer=InMemorySaver(),
        require_approval=True,
    )
    config = {"configurable": {"thread_id": "thread_approval"}}

    paused = graph.invoke(
        {
            "user_prompt": "Calibrate a column collapse.",
            "workflow_id": "langgraph_approval_workflow",
        },
        config,
    )

    assert adapter.generate_calls == 0
    assert adapter.validate_calls == 0
    assert adapter.run_calls == 0
    request = paused["__interrupt__"][0].value
    assert request["action"] == "approve_calibration_plan"
    assert request["target_value"] == pytest.approx(0.2)
    assert request["parameter_bounds"] == [0.0, 40.0]

    final = graph.invoke(Command(resume={"action": "approve"}), config)
    result = workflow.load("langgraph_approval_workflow")
    assert adapter.generate_calls == 1
    assert adapter.run_calls == 4
    assert result.plan_approved is True
    assert result.approved_at is not None
    assert result.status == CalibrationWorkflowStatus.CONVERGED
    assert not final.get("__interrupt__")
    assert final["workflow_result"]["status"] == "converged"


def test_langgraph_can_reject_plan_without_case_side_effects(
    build_temp_path: Path,
) -> None:
    store = make_store(build_temp_path, "langgraph_plan_rejection")
    adapter = DeterministicArtifactAdapter()
    workflow = DeterministicCalibrationWorkflow(
        store,
        MockColumnCollapseCalibrationPlanner(),
        adapter,
    )
    graph = build_calibration_graph(
        workflow,
        checkpointer=InMemorySaver(),
        require_approval=True,
    )
    config = {"configurable": {"thread_id": "thread_plan_rejection"}}
    graph.invoke(
        {
            "user_prompt": "Calibrate a column collapse.",
            "workflow_id": "langgraph_plan_rejection_workflow",
        },
        config,
    )

    final = graph.invoke(Command(resume={"action": "reject"}), config)
    result = workflow.load("langgraph_plan_rejection_workflow")

    assert result.status == CalibrationWorkflowStatus.CANCELLED
    assert result.plan_approved is False
    assert result.case_prepared is False
    assert adapter.generate_calls == 0
    assert adapter.validate_calls == 0
    assert adapter.run_calls == 0
    assert final["workflow_result"]["status"] == "cancelled"


def test_sqlite_graph_reopens_and_resumes_same_thread(build_temp_path: Path) -> None:
    store = make_store(build_temp_path, "sqlite_resume")
    database = store.root / "checkpoints.sqlite"
    adapter = DeterministicArtifactAdapter()
    workflow = DeterministicCalibrationWorkflow(
        store,
        MockColumnCollapseCalibrationPlanner(),
        adapter,
    )
    config = {"configurable": {"thread_id": "sqlite_resume_thread"}}

    with open_sqlite_calibration_graph(
        workflow,
        database,
        interrupt_after=["execute_candidate"],
    ) as first_graph:
        first_graph.invoke(
            {
                "user_prompt": "Reach a runout extension of 0.2 m.",
                "workflow_id": "sqlite_resume_workflow",
            },
            config,
        )
        assert adapter.run_calls == 1

    with open_sqlite_calibration_graph(
        workflow,
        database,
        interrupt_after=["execute_candidate"],
    ) as reopened_graph:
        reopened_graph.invoke(None, config)
        assert reopened_graph.get_state(config).next == ("decide",)

    assert database.is_file()
    assert adapter.run_calls == 2
    assert len(workflow.load("sqlite_resume_workflow").iterations) == 2


def test_langgraph_replanner_retries_transient_solver_failure(
    build_temp_path: Path,
) -> None:
    store = make_store(build_temp_path, "replan_retry")
    adapter = DeterministicArtifactAdapter(fail_run_number=1)
    workflow = DeterministicCalibrationWorkflow(
        store,
        MockColumnCollapseCalibrationPlanner(),
        adapter,
    )
    client = MockReplanningClient(
        [{"action": "retry_candidate", "reason": "Retry one transient failure."}]
    )
    graph = build_calibration_graph(
        workflow,
        checkpointer=InMemorySaver(),
        replanner=StructuredReplanner(client),
    )

    state = graph.invoke(
        {
            "user_prompt": "Reach a runout extension of 0.2 m.",
            "workflow_id": "replan_retry_workflow",
        },
        {"configurable": {"thread_id": "replan_retry_thread"}},
    )
    result = workflow.load("replan_retry_workflow")
    first_run = store.load_run("replan_retry_workflow_iteration_001", "run_001")

    assert state["workflow_result"]["status"] == "converged"
    assert result.status == CalibrationWorkflowStatus.CONVERGED
    assert len(client.requests) == 1
    assert first_run.attempt_count == 2
    assert first_run.attempt_history[0]["status"] == "failed"


def test_langgraph_replanner_stops_after_retry_limit(build_temp_path: Path) -> None:
    store = make_store(build_temp_path, "replan_limit")
    adapter = AlwaysFailingArtifactAdapter()
    workflow = DeterministicCalibrationWorkflow(
        store,
        MockColumnCollapseCalibrationPlanner(),
        adapter,
    )
    client = MockReplanningClient(
        [{"action": "retry_candidate", "reason": "Retry the solver."}]
    )
    graph = build_calibration_graph(
        workflow,
        checkpointer=InMemorySaver(),
        replanner=StructuredReplanner(client),
    )

    graph.invoke(
        {
            "user_prompt": "Reach a runout extension of 0.2 m.",
            "workflow_id": "replan_limit_workflow",
        },
        {"configurable": {"thread_id": "replan_limit_thread"}},
    )
    result = workflow.load("replan_limit_workflow")

    assert result.status == CalibrationWorkflowStatus.FAILED
    assert adapter.run_calls == 3
    assert len(client.requests) == 2
    assert len(result.replan_history) == 3
    assert result.replan_history[-1]["decision"]["action"] == "stop"


def test_langgraph_replanner_can_extend_bounds_without_changing_objective(
    build_temp_path: Path,
) -> None:
    store = make_store(build_temp_path, "replan_bounds")
    adapter = FormulaArtifactAdapter()
    planner = MockColumnCollapseCalibrationPlanner(target_runout_extension=0.05)
    workflow = DeterministicCalibrationWorkflow(store, planner, adapter)
    client = MockReplanningClient(
        [
            {
                "action": "revise_parameter_bounds",
                "reason": "Extend the upper friction-angle bracket.",
                "lower_bound": 0.0,
                "upper_bound": 60.0,
            }
        ]
    )
    graph = build_calibration_graph(
        workflow,
        checkpointer=InMemorySaver(),
        replanner=StructuredReplanner(client),
    )

    graph.invoke(
        {
            "user_prompt": "Reach a runout extension of 0.05 m.",
            "workflow_id": "replan_bounds_workflow",
        },
        {"configurable": {"thread_id": "replan_bounds_thread"}},
    )
    result = workflow.load("replan_bounds_workflow")

    assert result.status == CalibrationWorkflowStatus.CONVERGED
    assert [item.parameter_value for item in result.iterations] == [
        0.0,
        40.0,
        60.0,
        50.0,
    ]
    assert result.definition.calibration.parameter.upper_bound == pytest.approx(60.0)
    assert result.definition.calibration.objective.target_value == pytest.approx(0.05)
    assert result.definition.calibration.objective.metric_name == "runout_extension"
    assert result.replan_history[0]["decision"]["action"] == (
        "revise_parameter_bounds"
    )


def test_replanned_solver_retry_reuses_plan_approval(build_temp_path: Path) -> None:
    store = make_store(build_temp_path, "replan_approval")
    adapter = DeterministicArtifactAdapter(fail_run_number=1)
    workflow = DeterministicCalibrationWorkflow(
        store,
        MockColumnCollapseCalibrationPlanner(),
        adapter,
    )
    client = MockReplanningClient(
        [{"action": "retry_candidate", "reason": "Retry one timeout."}]
    )
    graph = build_calibration_graph(
        workflow,
        checkpointer=InMemorySaver(),
        require_approval=True,
        replanner=StructuredReplanner(client),
    )
    config = {"configurable": {"thread_id": "replan_approval_thread"}}

    first_approval = graph.invoke(
        {
            "user_prompt": "Reach a runout extension of 0.2 m.",
            "workflow_id": "replan_approval_workflow",
        },
        config,
    )
    final = graph.invoke(Command(resume={"action": "approve"}), config)

    assert first_approval["__interrupt__"]
    assert not final.get("__interrupt__")
    assert adapter.run_calls == 5
    assert len(client.requests) == 1
    assert workflow.load("replan_approval_workflow").status == (
        CalibrationWorkflowStatus.CONVERGED
    )
