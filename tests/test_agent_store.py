"""Tests for JSON persistence of Agent tasks and plan revisions."""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest

from sphinxsim.agent import (
    AgentPlan,
    AgentTaskStore,
    PlanAction,
    PlanStatus,
    PlanStep,
    StoreError,
    TaskAlreadyExistsError,
    TaskNotFoundError,
    TaskStatus,
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
