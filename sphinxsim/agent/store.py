"""Filesystem persistence for AgentCAE tasks and versioned plans."""

from __future__ import annotations

import os
from pathlib import Path
import tempfile
import time

from pydantic import ValidationError

from .analysis import FrictionAngleSweepResult
from .models import AgentAnalysis, AgentPlan, AgentRun, AgentTask, PlanStatus, TaskStatus


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

    def _run_dir(self, task_id: str, run_id: str) -> Path:
        if not run_id or any(
            ch not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-"
            for ch in run_id
        ):
            raise StoreError("run_id may contain only letters, digits, '_' and '-'")
        return self._task_dir(task_id) / "runs" / run_id

    def _run_path(self, task_id: str, run_id: str) -> Path:
        return self._run_dir(task_id, run_id) / "run.json"

    def _analysis_path(self, task_id: str, run_id: str, analysis_id: str) -> Path:
        if not analysis_id or any(
            ch not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-"
            for ch in analysis_id
        ):
            raise StoreError("analysis_id may contain only letters, digits, '_' and '-'")
        return self._run_dir(task_id, run_id) / "analyses" / f"{analysis_id}.json"

    def _sweep_path(self, task_id: str, sweep_id: str) -> Path:
        if not sweep_id or any(
            ch not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-"
            for ch in sweep_id
        ):
            raise StoreError("sweep_id may contain only letters, digits, '_' and '-'")
        return self._task_dir(task_id) / "sweeps" / f"{sweep_id}.json"

    def run_directory(self, task_id: str, run_id: str) -> Path:
        """Return the only allowed isolated output root for one Agent run."""
        return self._run_dir(task_id, run_id).resolve()

    def analysis_path(self, task_id: str, run_id: str, analysis_id: str) -> Path:
        """Return the persisted record path for one run analysis."""
        return self._analysis_path(task_id, run_id, analysis_id).resolve()

    def sweep_path(self, task_id: str, sweep_id: str) -> Path:
        """Return the persisted path for one deterministic sweep summary."""
        return self._sweep_path(task_id, sweep_id).resolve()

    @staticmethod
    def _atomic_write(path: Path, content: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode="wb", dir=path.parent, delete=False) as handle:
                temporary = Path(handle.name)
                handle.write(content.encode("utf-8"))
            for attempt in range(5):
                try:
                    os.replace(temporary, path)
                    break
                except PermissionError:
                    if attempt == 4:
                        raise
                    time.sleep(0.01 * (2**attempt))
        except OSError as exc:
            try:
                if temporary is not None:
                    temporary.unlink(missing_ok=True)
            except OSError:
                pass
            raise StoreError(f"unable to write Agent state file {path}: {exc}") from exc

    @staticmethod
    def _read_model(
        path: Path,
        model_type: (
            type[AgentTask]
            | type[AgentPlan]
            | type[AgentRun]
            | type[AgentAnalysis]
        ),
    ):
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

    def create_run(self, run: AgentRun) -> None:
        """Create a run record without overwriting an earlier attempt."""
        self.load_task(run.task_id)
        run_path = self._run_path(run.task_id, run.run_id)
        if run_path.exists():
            raise TaskAlreadyExistsError(
                f"Agent run already exists: task={run.task_id}, run={run.run_id}"
            )
        self._atomic_write(run_path, run.model_dump_json(indent=2))

    def save_run(self, run: AgentRun) -> None:
        """Persist an existing run record."""
        run_path = self._run_path(run.task_id, run.run_id)
        if not run_path.exists():
            raise TaskNotFoundError(
                f"Agent run not found: task={run.task_id}, run={run.run_id}"
            )
        self._atomic_write(run_path, run.model_dump_json(indent=2))

    def load_run(self, task_id: str, run_id: str) -> AgentRun:
        run_path = self._run_path(task_id, run_id)
        if not run_path.exists():
            raise TaskNotFoundError(
                f"Agent run not found: task={task_id}, run={run_id}"
            )
        return self._read_model(run_path, AgentRun)

    def save_run_logs(
        self,
        task_id: str,
        run_id: str,
        *,
        stdout: str,
        stderr: str,
    ) -> tuple[Path, Path]:
        """Persist captured CLI streams beside the run record."""
        run_dir = self._run_dir(task_id, run_id)
        if not self._run_path(task_id, run_id).exists():
            raise TaskNotFoundError(
                f"Agent run not found: task={task_id}, run={run_id}"
            )
        stdout_path = run_dir / "stdout.log"
        stderr_path = run_dir / "stderr.log"
        self._atomic_write(stdout_path, stdout)
        self._atomic_write(stderr_path, stderr)
        return stdout_path, stderr_path

    def create_analysis(self, analysis: AgentAnalysis) -> None:
        """Create an analysis record under an existing run."""
        self.load_run(analysis.task_id, analysis.run_id)
        path = self._analysis_path(
            analysis.task_id,
            analysis.run_id,
            analysis.analysis_id,
        )
        if path.exists():
            raise TaskAlreadyExistsError(
                "Agent analysis already exists: "
                f"task={analysis.task_id}, run={analysis.run_id}, "
                f"analysis={analysis.analysis_id}"
            )
        self._atomic_write(path, analysis.model_dump_json(indent=2))

    def save_analysis(self, analysis: AgentAnalysis) -> None:
        """Persist an existing analysis record."""
        path = self._analysis_path(
            analysis.task_id,
            analysis.run_id,
            analysis.analysis_id,
        )
        if not path.exists():
            raise TaskNotFoundError(
                "Agent analysis not found: "
                f"task={analysis.task_id}, run={analysis.run_id}, "
                f"analysis={analysis.analysis_id}"
            )
        self._atomic_write(path, analysis.model_dump_json(indent=2))

    def load_analysis(
        self,
        task_id: str,
        run_id: str,
        analysis_id: str,
    ) -> AgentAnalysis:
        path = self._analysis_path(task_id, run_id, analysis_id)
        if not path.exists():
            raise TaskNotFoundError(
                "Agent analysis not found: "
                f"task={task_id}, run={run_id}, analysis={analysis_id}"
            )
        return self._read_model(path, AgentAnalysis)

    def create_sweep_result(
        self,
        task_id: str,
        result: FrictionAngleSweepResult,
    ) -> None:
        """Persist a completed sweep comparison without overwriting it."""
        self.load_task(task_id)
        path = self._sweep_path(task_id, result.sweep_id)
        if path.exists():
            raise TaskAlreadyExistsError(
                f"Agent sweep already exists: task={task_id}, sweep={result.sweep_id}"
            )
        self._atomic_write(path, result.model_dump_json(indent=2))

    def load_sweep_result(
        self,
        task_id: str,
        sweep_id: str,
    ) -> FrictionAngleSweepResult:
        path = self._sweep_path(task_id, sweep_id)
        if not path.exists():
            raise TaskNotFoundError(
                f"Agent sweep not found: task={task_id}, sweep={sweep_id}"
            )
        try:
            return FrictionAngleSweepResult.model_validate_json(
                path.read_text(encoding="utf-8")
            )
        except (OSError, ValidationError, ValueError) as exc:
            raise StoreError(f"unable to read valid Agent state from {path}: {exc}") from exc

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
