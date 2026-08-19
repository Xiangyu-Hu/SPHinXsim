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
    build_calibration_graph,
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
        fronts = {20.0: 0.35, 30.0: 0.30, 40.0: 0.28}
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
    assert adapter.validate_calls == 4
    assert adapter.run_calls == 3
    assert [item.parameter_value for item in result.iterations] == [20.0, 40.0, 30.0]
    assert [item.metric_value for item in result.iterations] == pytest.approx(
        [0.35, 0.28, 0.30]
    )
    assert result.final_decision is not None
    assert result.final_decision.best_parameter_value == pytest.approx(30.0)
    assert workflow.load(result.workflow_id) == result
    assert all(Path(item.case_path).is_file() for item in result.iterations)


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
        MockColumnCollapseCalibrationPlanner(target_final_front=0.40),
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
    assert first_iteration.parameter_value == pytest.approx(20.0)
    assert first_iteration.metric_value == pytest.approx(0.35)
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

    workflow.execute_candidate(result, 20.0)
    recovered = workflow.execute_candidate(stale_checkpoint, 20.0)

    assert recovered.metric_value == pytest.approx(0.35)
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
    assert len(result.iterations) == 3
    assert adapter.run_calls == 3
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


def test_langgraph_requires_approval_before_solver_and_can_cancel(
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

    assert adapter.run_calls == 0
    assert paused["__interrupt__"][0].value["candidate_value"] == pytest.approx(20.0)

    graph.invoke(Command(resume={"action": "approve"}), config)
    assert adapter.run_calls == 1

    final = graph.invoke(Command(resume={"action": "reject"}), config)
    result = workflow.load("langgraph_approval_workflow")
    assert adapter.run_calls == 1
    assert result.status == CalibrationWorkflowStatus.CANCELLED
    assert final["workflow_result"]["status"] == "cancelled"
