"""Tests for JSON persistence of Agent tasks and plan revisions."""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest
import sphinxsim.agent.store as store_module

from sphinxsim.agent import (
    AgentAnalysis,
    AgentPlan,
    AgentRun,
    AgentTaskStore,
    AnalysisStatus,
    PlanAction,
    PlanStatus,
    PlanStep,
    StoreError,
    TaskAlreadyExistsError,
    TaskNotFoundError,
    TaskStatus,
    RunStatus,
    create_task,
)


def _plan(version: int, *, reason: str | None = None) -> AgentPlan:
    return AgentPlan(
        plan_id=f"plan_store_v{version}",
        version=version,
        objective="Run and analyze a landslide",
        revision_reason=reason,
        steps=[
            PlanStep(
                step_id=f"generate_v{version}",
                action=PlanAction.GENERATE_CASE,
                description="Generate a validated case",
            )
        ],
    )


@pytest.fixture
def store(build_temp_path: Path) -> AgentTaskStore:
    # The repository test fixture intentionally retains artifacts.  Give each
    # test invocation a fresh store so repeated local test runs stay isolated.
    return AgentTaskStore(build_temp_path / f"agent_tasks_{uuid4().hex}")


def test_create_and_load_task(store: AgentTaskStore) -> None:
    task = create_task("simulate a landslide", task_id="task_store")
    store.create_task(task)
    assert store.load_task(task.task_id) == task


def test_atomic_write_retries_transient_windows_permission_error(
    store: AgentTaskStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    real_replace = store_module.os.replace
    attempts = 0

    def transient_replace(source: Path, destination: Path) -> None:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise PermissionError("transient file lock")
        real_replace(source, destination)

    monkeypatch.setattr(store_module.os, "replace", transient_replace)
    task = create_task("simulate a landslide", task_id="task_retry_write")

    store.create_task(task)

    assert attempts == 2
    assert store.load_task(task.task_id) == task


def test_create_task_does_not_overwrite(store: AgentTaskStore) -> None:
    task = create_task("simulate a landslide", task_id="task_duplicate")
    store.create_task(task)
    with pytest.raises(TaskAlreadyExistsError):
        store.create_task(task)


def test_add_plan_activates_plan_and_updates_task(store: AgentTaskStore) -> None:
    task = create_task("simulate a landslide", task_id="task_plan")
    store.create_task(task)

    updated = store.add_plan(task.task_id, _plan(1))

    assert updated.status == TaskStatus.PLANNED
    assert updated.active_plan_version == 1
    assert updated.plan_versions == [1]
    assert store.load_active_plan(task.task_id).version == 1


def test_new_plan_revision_preserves_and_supersedes_previous(store: AgentTaskStore) -> None:
    task = create_task("simulate a landslide", task_id="task_revision")
    store.create_task(task)
    store.add_plan(task.task_id, _plan(1))

    updated = store.add_plan(task.task_id, _plan(2, reason="simulation was not stable"))

    assert updated.plan_versions == [1, 2]
    assert updated.active_plan_version == 2
    assert store.load_plan(task.task_id, 1).status == PlanStatus.SUPERSEDED
    assert store.load_plan(task.task_id, 2).revision_reason == "simulation was not stable"


def test_plan_versions_must_be_contiguous(store: AgentTaskStore) -> None:
    task = create_task("simulate a landslide", task_id="task_gap")
    store.create_task(task)
    with pytest.raises(StoreError, match="must be 1"):
        store.add_plan(task.task_id, _plan(2))


def test_missing_task_and_active_plan_are_reported(store: AgentTaskStore) -> None:
    with pytest.raises(TaskNotFoundError):
        store.load_task("missing")

    task = create_task("simulate a landslide", task_id="task_no_plan")
    store.create_task(task)
    with pytest.raises(TaskNotFoundError, match="no active plan"):
        store.load_active_plan(task.task_id)


def test_store_rejects_path_traversal_task_id(store: AgentTaskStore) -> None:
    with pytest.raises(StoreError, match="task_id"):
        store.load_task("../outside")


def test_corrupt_task_json_is_reported(store: AgentTaskStore) -> None:
    task = create_task("simulate a landslide", task_id="task_corrupt")
    store.create_task(task)
    task_file = store.root / task.task_id / "task.json"
    task_file.write_text("not valid json", encoding="utf-8")
    with pytest.raises(StoreError, match="unable to read valid Agent state"):
        store.load_task(task.task_id)


def test_create_finish_and_load_run_record(store: AgentTaskStore) -> None:
    task = create_task("run a case", task_id="task_run_record")
    store.create_task(task)
    output_dir = store.run_directory(task.task_id, "run_001")
    run = AgentRun(
        run_id="run_001",
        task_id=task.task_id,
        case_path="case.json",
        output_dir=str(output_dir),
    )

    store.create_run(run)
    stdout_path, stderr_path = store.save_run_logs(
        task.task_id,
        run.run_id,
        stdout="completed",
        stderr="",
    )
    run.stdout_log = str(stdout_path)
    run.stderr_log = str(stderr_path)
    run.finish(RunStatus.COMPLETED, returncode=0)
    store.save_run(run)

    persisted = store.load_run(task.task_id, run.run_id)
    assert persisted.status == RunStatus.COMPLETED
    assert persisted.output_dir == str(output_dir)
    assert Path(persisted.stdout_log).read_text(encoding="utf-8") == "completed"


def test_run_record_does_not_overwrite_existing_run(store: AgentTaskStore) -> None:
    task = create_task("run a case", task_id="task_duplicate_run")
    store.create_task(task)
    run = AgentRun(
        run_id="run_001",
        task_id=task.task_id,
        case_path="case.json",
        output_dir=str(store.run_directory(task.task_id, "run_001")),
    )
    store.create_run(run)

    with pytest.raises(TaskAlreadyExistsError, match="run already exists"):
        store.create_run(run)


def test_create_finish_and_load_analysis_record(store: AgentTaskStore) -> None:
    task = create_task("analyze a run", task_id="task_analysis_record")
    store.create_task(task)
    run = AgentRun(
        run_id="run_001",
        task_id=task.task_id,
        case_path="case.json",
        output_dir=str(store.run_directory(task.task_id, "run_001")),
    )
    store.create_run(run)
    analysis = AgentAnalysis(
        analysis_id="analysis_001",
        task_id=task.task_id,
        run_id=run.run_id,
        config_path="case.json",
        spec={
            "metric": "runout",
            "body_name": "GranularBody",
            "direction_vector": [1.0, 0.0],
        },
    )

    store.create_analysis(analysis)
    analysis.finish(
        AnalysisStatus.COMPLETED,
        metrics={"final_front": 0.4, "unit": "m"},
    )
    store.save_analysis(analysis)

    persisted = store.load_analysis(task.task_id, run.run_id, analysis.analysis_id)
    assert persisted.status == AnalysisStatus.COMPLETED
    assert persisted.metrics["final_front"] == pytest.approx(0.4)
    assert store.analysis_path(
        task.task_id,
        run.run_id,
        analysis.analysis_id,
    ).is_file()
