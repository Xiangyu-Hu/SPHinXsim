"""Filesystem persistence for AgentCAE tasks and versioned plans."""

from __future__ import annotations

import os
from pathlib import Path

from pydantic import ValidationError

from .models import AgentPlan, AgentTask, PlanStatus, TaskStatus


class StoreError(RuntimeError):
    """Base error for task-store operations."""


class TaskNotFoundError(StoreError):
    """Raised when a requested task does not exist."""


class TaskAlreadyExistsError(StoreError):
    """Raised when creating a task whose ID already exists."""


class AgentTaskStore:
    """Persist task metadata and versioned plan-state files as JSON."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).resolve()

    def _task_dir(self, task_id: str) -> Path:
        # AgentTask validates this pattern too.  Recheck external lookup input
        # before using it as a path component.
        if not task_id or any(ch not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-" for ch in task_id):
            raise StoreError("task_id may contain only letters, digits, '_' and '-'")
        return self.root / task_id

    def _task_path(self, task_id: str) -> Path:
        return self._task_dir(task_id) / "task.json"

    def _plan_path(self, task_id: str, version: int) -> Path:
        if version < 1:
            raise StoreError("plan version must be at least 1")
        return self._task_dir(task_id) / "plans" / f"plan_v{version}.json"

    @staticmethod
    def _atomic_write(path: Path, content: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + ".tmp")
        try:
            temporary.write_text(content, encoding="utf-8")
            os.replace(temporary, path)
        except OSError as exc:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
            raise StoreError(f"unable to write Agent state file {path}: {exc}") from exc

    @staticmethod
    def _read_model(path: Path, model_type: type[AgentTask] | type[AgentPlan]):
        try:
            content = path.read_text(encoding="utf-8")
            return model_type.model_validate_json(content)
        except (OSError, ValidationError, ValueError) as exc:
            raise StoreError(f"unable to read valid Agent state from {path}: {exc}") from exc

    def create_task(self, task: AgentTask) -> None:
        """Create a new task directory without overwriting existing state."""
        task_path = self._task_path(task.task_id)
        if task_path.exists():
            raise TaskAlreadyExistsError(f"Agent task already exists: {task.task_id}")
        self._atomic_write(task_path, task.model_dump_json(indent=2))

    def save_task(self, task: AgentTask) -> None:
        """Persist an existing task."""
        if not self._task_path(task.task_id).exists():
            raise TaskNotFoundError(f"Agent task not found: {task.task_id}")
        self._atomic_write(self._task_path(task.task_id), task.model_dump_json(indent=2))

    def load_task(self, task_id: str) -> AgentTask:
        task_path = self._task_path(task_id)
        if not task_path.exists():
            raise TaskNotFoundError(f"Agent task not found: {task_id}")
        return self._read_model(task_path, AgentTask)

    def load_plan(self, task_id: str, version: int) -> AgentPlan:
        plan_path = self._plan_path(task_id, version)
        if not plan_path.exists():
            raise TaskNotFoundError(
                f"Agent plan not found: task={task_id}, version={version}"
            )
        return self._read_model(plan_path, AgentPlan)

    def load_active_plan(self, task_id: str) -> AgentPlan:
        task = self.load_task(task_id)
        if task.active_plan_version is None:
            raise TaskNotFoundError(f"Agent task has no active plan: {task_id}")
        return self.load_plan(task_id, task.active_plan_version)

    def save_plan(self, task_id: str, plan: AgentPlan) -> None:
        """Persist execution state for an existing plan version."""
        task = self.load_task(task_id)
        if plan.version not in task.plan_versions:
            raise TaskNotFoundError(
                f"Agent plan not found: task={task_id}, version={plan.version}"
            )
        plan_path = self._plan_path(task_id, plan.version)
        if not plan_path.exists():
            raise TaskNotFoundError(
                f"Agent plan not found: task={task_id}, version={plan.version}"
            )
        self._atomic_write(plan_path, plan.model_dump_json(indent=2))

    def add_plan(self, task_id: str, plan: AgentPlan) -> AgentTask:
        """Add the next plan revision and make it active.

        Existing plan files are retained.  The formerly active plan is marked
        superseded unless it had already reached a terminal completed state.
        """
        task = self.load_task(task_id)
        expected_version = (task.plan_versions[-1] + 1) if task.plan_versions else 1
        if plan.version != expected_version:
            raise StoreError(
                f"next plan version for {task_id} must be {expected_version}, got {plan.version}"
            )
        if self._plan_path(task_id, plan.version).exists():
            raise StoreError(
                f"Agent plan already exists: task={task_id}, version={plan.version}"
            )

        if task.active_plan_version is not None:
            previous = self.load_plan(task_id, task.active_plan_version)
            if previous.status != PlanStatus.COMPLETED:
                previous.status = PlanStatus.SUPERSEDED
                self._atomic_write(
                    self._plan_path(task_id, previous.version),
                    previous.model_dump_json(indent=2),
                )

        self._atomic_write(
            self._plan_path(task_id, plan.version),
            plan.model_dump_json(indent=2),
        )
        task.plan_versions.append(plan.version)
        task.active_plan_version = plan.version
        task.status = TaskStatus.PLANNED
        from .models import utc_now

        task.updated_at = utc_now()
        self._atomic_write(self._task_path(task_id), task.model_dump_json(indent=2))
        return task
