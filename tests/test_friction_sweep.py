"""Tests for deterministic comparison of friction-angle samples."""

from __future__ import annotations

import json
import math
from pathlib import Path
from uuid import uuid4

import pytest

from sphinxsim.agent import (
    AgentTaskStore,
    AnalysisError,
    AnalysisStatus,
    FrictionAngleSweepAnalyzer,
    PlanAction,
    StaticPlanExecutor,
    TaskStatus,
    create_friction_angle_sweep_task,
)
from sphinxsim.services import CLICommandResult


def _metrics(final_front: float) -> dict:
    return {
        "final_front": final_front,
        "runout_extension": final_front - 0.2,
        "unit": "m",
    }


def test_sweep_summary_selects_sample_closest_to_target() -> None:
    result = FrictionAngleSweepAnalyzer().summarize(
        sweep_id="sweep_001",
        target_value=0.4,
        samples=[
            (20.0, "run_001", "analysis_001", _metrics(0.45)),
            (25.0, "run_002", "analysis_002", _metrics(0.425)),
            (30.0, "run_003", "analysis_003", _metrics(0.4)),
            (35.0, "run_004", "analysis_004", _metrics(0.375)),
            (40.0, "run_005", "analysis_005", _metrics(0.35)),
        ],
    )

    assert len(result.samples) == 5
    assert result.best_friction_angle_degrees == pytest.approx(30.0)
    assert result.best_final_front == pytest.approx(0.4)
    assert result.best_absolute_error == pytest.approx(0.0)
    assert result.samples[0].absolute_error == pytest.approx(0.05)


def test_sweep_summary_rejects_missing_metrics() -> None:
    with pytest.raises(AnalysisError, match="requires numeric"):
        FrictionAngleSweepAnalyzer().summarize(
            sweep_id="sweep_001",
            target_value=0.4,
            samples=[(30.0, "run_001", "analysis_001", {"unit": "m"})],
        )


def test_sweep_summary_rejects_inconsistent_units() -> None:
    metrics = _metrics(0.4)
    metrics["unit"] = "mm"

    with pytest.raises(AnalysisError, match="metres"):
        FrictionAngleSweepAnalyzer().summarize(
            sweep_id="sweep_001",
            target_value=0.4,
            samples=[(30.0, "run_001", "analysis_001", metrics)],
        )


def _success() -> CLICommandResult:
    return CLICommandResult(
        command=("sphinxsim",),
        returncode=0,
        stdout="ok",
        stderr="",
        duration_seconds=0.01,
    )


class SweepArtifactAdapter:
    def __init__(self) -> None:
        self.validated: list[str] = []
        self.runs: list[tuple[str, str]] = []

    def validate(self, config_path: str, *, timeout=None) -> CLICommandResult:
        self.validated.append(config_path)
        return _success()

    def run(
        self,
        config_path: str,
        *,
        output_dir=None,
        timeout=None,
    ) -> CLICommandResult:
        self.runs.append((config_path, output_dir))
        payload = json.loads(Path(config_path).read_text(encoding="utf-8"))
        angle_radians = payload["continuum_bodies"][0]["material"]["friction_angle"]
        angle_degrees = math.degrees(angle_radians)
        final_front = 0.55 - 0.005 * angle_degrees
        data_dir = Path(output_dir) / "output"
        data_dir.mkdir(parents=True, exist_ok=True)
        (data_dir / "GranularBody_0001.vtp").write_text(
            f'''<?xml version="1.0"?>
<VTKFile type="PolyData" version="1.0" byte_order="LittleEndian">
  <PolyData>
    <FieldData><DataArray Name="TimeValue" format="ascii">0.8</DataArray></FieldData>
    <Piece Name="GranularBody" NumberOfPoints="2">
      <Points><DataArray Name="Position" NumberOfComponents="3" format="ascii">
        0.10 0.00 0.00  {final_front} 0.02 0.00
      </DataArray></Points>
    </Piece>
  </PolyData>
</VTKFile>
''',
            encoding="utf-8",
        )
        return _success()


def test_fixed_sweep_plan_runs_analyzes_and_compares_all_samples(
    build_temp_path: Path,
) -> None:
    store = AgentTaskStore(build_temp_path / f"sweep_executor_{uuid4().hex}")
    task, plan = create_friction_angle_sweep_task(
        store,
        friction_angles_degrees=(20.0, 25.0, 30.0, 35.0, 40.0),
        target_final_front=0.4,
        task_id="task_fixed_sweep",
    )
    adapter = SweepArtifactAdapter()

    result = StaticPlanExecutor(store, adapter).execute(task.task_id)

    assert result.succeeded is True
    assert len(result.completed_step_ids) == 26
    assert plan.steps[-1].action == PlanAction.COMPARE_RESULTS
    saved_task = store.load_task(task.task_id)
    assert saved_task.status == TaskStatus.COMPLETED
    assert len(saved_task.case_versions) == 5
    assert saved_task.run_ids == [f"run_{index:03d}" for index in range(1, 6)]
    assert saved_task.sweep_ids == ["sweep_001"]
    assert len(adapter.validated) == 5
    assert len(adapter.runs) == 5

    for index in range(1, 6):
        run_id = f"run_{index:03d}"
        analysis_id = f"analysis_{index:03d}"
        run = store.load_run(task.task_id, run_id)
        analysis = store.load_analysis(task.task_id, run_id, analysis_id)
        assert run.analysis_ids == [analysis_id]
        assert analysis.status == AnalysisStatus.COMPLETED

    sweep = store.load_sweep_result(task.task_id, "sweep_001")
    assert len(sweep.samples) == 5
    assert sweep.best_friction_angle_degrees == pytest.approx(30.0)
    assert sweep.best_final_front == pytest.approx(0.4)
    assert sweep.best_absolute_error == pytest.approx(0.0)
    saved_plan = store.load_active_plan(task.task_id)
    assert saved_plan.steps[-1].artifacts == [
        str(store.sweep_path(task.task_id, "sweep_001"))
    ]


def test_sweep_preset_rejects_duplicate_angles(build_temp_path: Path) -> None:
    store = AgentTaskStore(build_temp_path / f"invalid_sweep_{uuid4().hex}")

    with pytest.raises(ValueError, match="duplicates"):
        create_friction_angle_sweep_task(
            store,
            friction_angles_degrees=(30.0, 30.0),
        )
