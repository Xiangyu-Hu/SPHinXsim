"""Tests for the five-step preset Plan including persisted runout analysis."""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest

from sphinxsim.agent import (
    AgentTaskStore,
    AnalysisStatus,
    PlanStatus,
    RunStatus,
    StaticPlanExecutor,
    StepStatus,
    TaskStatus,
    create_runout_analysis_column_collapse_task,
    build_builtin_extractor_registry,
    DeclarativeExtractorCatalog,
    DeclarativeExtractorSpec,
)
from sphinxsim.services import CLICommandResult


def _success(stdout: str = "ok") -> CLICommandResult:
    return CLICommandResult(
        command=("sphinxsim",),
        returncode=0,
        stdout=stdout,
        stderr="",
        duration_seconds=0.01,
    )


def _write_final_vtp(output_dir: Path) -> None:
    data_dir = output_dir / "output"
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / "GranularBody_0000001000.vtp").write_text(
        '''<?xml version="1.0"?>
<VTKFile type="PolyData" version="1.0" byte_order="LittleEndian">
  <PolyData>
    <FieldData><DataArray Name="TimeValue" format="ascii">0.8</DataArray></FieldData>
    <Piece Name="GranularBody" NumberOfPoints="3">
      <Points><DataArray Name="Position" NumberOfComponents="3" format="ascii">
        0.10 0.00 0.00  0.40 0.02 0.00  0.30 0.05 0.00
      </DataArray></Points>
    </Piece>
  </PolyData>
</VTKFile>
''',
        encoding="utf-8",
    )


class ArtifactWritingAdapter:
    def __init__(self, *, write_vtp: bool = True) -> None:
        self.write_vtp = write_vtp
        self.calls: list[str] = []

    def validate(self, config_path: str, *, timeout=None) -> CLICommandResult:
        self.calls.append("validate")
        return _success()

    def run(
        self,
        config_path: str,
        *,
        output_dir=None,
        timeout=None,
    ) -> CLICommandResult:
        self.calls.append("run")
        if self.write_vtp:
            _write_final_vtp(Path(output_dir))
        return _success("simulation completed")


@pytest.fixture
def store(build_temp_path: Path) -> AgentTaskStore:
    return AgentTaskStore(build_temp_path / f"analysis_executor_{uuid4().hex}")


def test_five_step_plan_persists_runout_analysis(store: AgentTaskStore) -> None:
    task, _plan = create_runout_analysis_column_collapse_task(
        store,
        friction_angle_degrees=30.0,
        task_id="task_analyzed_column",
    )
    adapter = ArtifactWritingAdapter()

    result = StaticPlanExecutor(store, adapter).execute(task.task_id)

    assert result.succeeded is True
    assert result.completed_step_ids == (
        "prepare_case",
        "update_friction_angle",
        "validate_case",
        "run_case",
        "analyze_result",
    )
    saved_task = store.load_task(task.task_id)
    saved_plan = store.load_active_plan(task.task_id)
    saved_run = store.load_run(task.task_id, "run_001")
    analysis = store.load_analysis(task.task_id, "run_001", "analysis_001")

    assert saved_task.status == TaskStatus.COMPLETED
    assert saved_plan.status == PlanStatus.COMPLETED
    assert all(step.status == StepStatus.COMPLETED for step in saved_plan.steps)
    assert saved_run.status == RunStatus.COMPLETED
    assert saved_run.analysis_ids == ["analysis_001"]
    assert analysis.status == AnalysisStatus.COMPLETED
    assert analysis.metrics["initial_front"] == pytest.approx(0.2)
    assert analysis.metrics["final_front"] == pytest.approx(0.4)
    assert analysis.metrics["runout_extension"] == pytest.approx(0.2)
    assert Path(analysis.metrics["state_file"]).is_file()
    assert saved_plan.steps[-1].artifacts == [
        str(store.analysis_path(task.task_id, "run_001", "analysis_001"))
    ]
    assert adapter.calls == ["validate", "run"]


def test_missing_vtp_persists_failed_analysis_and_fails_plan(
    store: AgentTaskStore,
) -> None:
    task, _plan = create_runout_analysis_column_collapse_task(
        store,
        task_id="task_missing_vtp",
    )

    result = StaticPlanExecutor(
        store,
        ArtifactWritingAdapter(write_vtp=False),
    ).execute(task.task_id)

    assert result.succeeded is False
    assert result.failed_step_id == "analyze_result"
    assert store.load_task(task.task_id).status == TaskStatus.FAILED
    assert store.load_run(task.task_id, "run_001").status == RunStatus.COMPLETED
    analysis = store.load_analysis(task.task_id, "run_001", "analysis_001")
    assert analysis.status == AnalysisStatus.FAILED
    assert "no state VTP" in analysis.error_message


def test_missing_vtp_analysis_can_be_retried_without_rerunning_solver(
    store: AgentTaskStore,
) -> None:
    task, _ = create_runout_analysis_column_collapse_task(
        store,
        task_id="task_retry_missing_vtp",
    )
    adapter = ArtifactWritingAdapter(write_vtp=False)
    executor = StaticPlanExecutor(store, adapter)
    first = executor.execute(task.task_id)
    run = store.load_run(task.task_id, "run_001")
    _write_final_vtp(Path(run.output_dir))
    second = executor.execute(task.task_id)

    assert first.succeeded is False
    assert second.succeeded is True
    assert adapter.calls == ["validate", "run"]
    analysis = store.load_analysis(task.task_id, "run_001", "analysis_001")
    assert analysis.status == AnalysisStatus.COMPLETED
    assert analysis.attempt_count == 2
    assert analysis.attempt_history[0]["status"] == "failed"


def test_executor_dispatches_compiled_declarative_extractor(
    store: AgentTaskStore,
) -> None:
    task, plan = create_runout_analysis_column_collapse_task(
        store,
        task_id="task_declarative_extractor",
    )
    plan.steps[-1].parameters["analysis_spec"] = {"metric": "granular_extent"}
    store.save_plan(task.task_id, plan)
    spec = DeclarativeExtractorSpec(
        extractor_id="granular_extent",
        description="Extract final granular coordinate bounds.",
        body_name="GranularBody",
        outputs=[
            {"output_name": "minimum_x", "component_index": 0, "reduction": "minimum"},
            {"output_name": "maximum_x", "component_index": 0, "reduction": "maximum"},
            {"output_name": "maximum_y", "component_index": 1, "reduction": "maximum"},
        ],
    )
    registry = DeclarativeExtractorCatalog(extractors=[spec]).build_registry(
        build_builtin_extractor_registry()
    )

    result = StaticPlanExecutor(
        store,
        ArtifactWritingAdapter(),
        extractor_registry=registry,
    ).execute(task.task_id)

    assert result.succeeded is True
    analysis = store.load_analysis(task.task_id, "run_001", "analysis_001")
    assert analysis.metrics["metric"] == "granular_extent"
    assert analysis.metrics["values"] == {
        "minimum_x": pytest.approx(0.1),
        "maximum_x": pytest.approx(0.4),
        "maximum_y": pytest.approx(0.05),
    }
    assert analysis.metrics["extractor_fingerprint"] == spec.fingerprint
