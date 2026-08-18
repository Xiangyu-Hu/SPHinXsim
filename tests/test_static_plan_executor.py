"""Tests for the minimal preset Plan Executor."""

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
    StaticPlanExecutor,
    StepStatus,
    TaskStatus,
    create_column_collapse_task,
    create_task,
)
from sphinxsim.services import CLICommandResult


def _command_result(*, returncode: int = 0, stderr: str = "", timed_out: bool = False):
    return CLICommandResult(
        command=("sphinxsim",),
        returncode=None if timed_out else returncode,
        stdout="ok" if returncode == 0 else "",
        stderr=stderr,
        duration_seconds=0.01,
        timed_out=timed_out,
    )


class FakeCommandAdapter:
    def __init__(self, *, validate_result=None, run_result=None) -> None:
        self.validate_result = validate_result or _command_result()
        self.run_result = run_result or _command_result()
        self.calls: list[tuple[str, str, float | None]] = []

    def validate(self, config_path: str, *, timeout=None):
        self.calls.append(("validate", config_path, timeout))
        return self.validate_result

    def run(self, config_path: str, *, timeout=None):
        self.calls.append(("run", config_path, timeout))
        return self.run_result


@pytest.fixture
def store(build_temp_path: Path) -> AgentTaskStore:
    return AgentTaskStore(build_temp_path / f"static_executor_{uuid4().hex}")


def test_preset_plan_executes_validate_then_run(store: AgentTaskStore) -> None:
    task, plan = create_column_collapse_task(store, task_id="task_column_collapse")
    adapter = FakeCommandAdapter()

    result = StaticPlanExecutor(store, adapter, command_timeout=30).execute(task.task_id)

    assert result.succeeded is True
    assert result.completed_step_ids == ("validate_case", "run_case")
    assert adapter.calls == [
        ("validate", plan.steps[0].parameters["config_path"], 30),
        ("run", plan.steps[1].parameters["config_path"], 30),
    ]
    assert store.load_task(task.task_id).status == TaskStatus.COMPLETED
    persisted_plan = store.load_active_plan(task.task_id)
    assert persisted_plan.status == PlanStatus.COMPLETED
    assert all(step.status == StepStatus.COMPLETED for step in persisted_plan.steps)


def test_validation_failure_prevents_run(store: AgentTaskStore) -> None:
    task, _ = create_column_collapse_task(store, task_id="task_validation_failure")
    adapter = FakeCommandAdapter(
        validate_result=_command_result(returncode=1, stderr="invalid configuration")
    )

    result = StaticPlanExecutor(store, adapter).execute(task.task_id)

    assert result.succeeded is False
    assert result.failed_step_id == "validate_case"
    assert adapter.calls[0][0] == "validate"
    assert len(adapter.calls) == 1
    assert store.load_task(task.task_id).status == TaskStatus.FAILED
    plan = store.load_active_plan(task.task_id)
    assert plan.steps[0].status == StepStatus.FAILED
    assert plan.steps[1].status == StepStatus.PENDING


def test_run_timeout_is_persisted_as_failure(store: AgentTaskStore) -> None:
    task, _ = create_column_collapse_task(store, task_id="task_run_timeout")
    adapter = FakeCommandAdapter(run_result=_command_result(timed_out=True))

    result = StaticPlanExecutor(store, adapter).execute(task.task_id)

    assert result.succeeded is False
    assert result.completed_step_ids == ("validate_case",)
    assert result.failed_step_id == "run_case"
    assert "timed out" in result.message
    assert store.load_active_plan(task.task_id).steps[1].status == StepStatus.FAILED


def test_unsupported_action_stops_without_running_commands(store: AgentTaskStore) -> None:
    task = create_task("Generate a case.", task_id="task_unsupported")
    store.create_task(task)
    plan = AgentPlan(
        plan_id="task_unsupported_plan_v1",
        version=1,
        objective=task.objective,
        steps=[
            PlanStep(
                step_id="generate_case",
                action=PlanAction.GENERATE_CASE,
                description="Generate a case.",
                parameters={"description": "Generate a case."},
            )
        ],
    )
    store.add_plan(task.task_id, plan)
    adapter = FakeCommandAdapter()

    result = StaticPlanExecutor(store, adapter).execute(task.task_id)

    assert result.succeeded is False
    assert result.failed_step_id == "generate_case"
    assert adapter.calls == []


def test_completed_plan_is_idempotent(store: AgentTaskStore) -> None:
    task, _ = create_column_collapse_task(store, task_id="task_idempotent")
    first_adapter = FakeCommandAdapter()
    executor = StaticPlanExecutor(store, first_adapter)
    first = executor.execute(task.task_id)
    second = executor.execute(task.task_id)

    assert first.succeeded is True
    assert second.succeeded is True
    assert len(first_adapter.calls) == 2
    assert second.message == "Plan was already completed."


def test_executor_does_not_print_internal_plan(store: AgentTaskStore, capsys) -> None:
    task, _ = create_column_collapse_task(store, task_id="task_hidden_execution")
    result = StaticPlanExecutor(store, FakeCommandAdapter()).execute(task.task_id)
    captured = capsys.readouterr()
    assert result.succeeded is True
    assert captured.out == ""
    assert captured.err == ""
