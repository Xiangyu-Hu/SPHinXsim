"""Minimal synchronous executor for preset SPHinXsim Agent plans."""

from __future__ import annotations

from dataclasses import dataclass
import threading
from typing import Protocol

from sphinxsim.services import CLICommandResult, CLIInvocationError

from .models import (
    AgentPlan,
    AgentTask,
    PlanAction,
    PlanStatus,
    PlanStep,
    StepStatus,
    TaskStatus,
)
from .store import AgentTaskStore


class CommandAdapter(Protocol):
    """Command subset required by the initial static executor."""

    def validate(self, config_path: str, *, timeout: float | None = None) -> CLICommandResult:
        ...

    def run(self, config_path: str, *, timeout: float | None = None) -> CLICommandResult:
        ...


@dataclass(frozen=True)
class PlanExecutionResult:
    """Structured result of one complete static-plan execution attempt."""

    task_id: str
    plan_version: int
    succeeded: bool
    completed_step_ids: tuple[str, ...]
    failed_step_id: str | None = None
    message: str = ""


class PlanExecutionError(RuntimeError):
    """The persisted Plan cannot be safely executed."""


class StaticPlanExecutor:
    """Execute validate/run steps sequentially through the existing CLI adapter.

    This executor intentionally supports only the first fixed vertical slice.
    A process-local lock prevents multiple executor instances from invoking
    ``run_case`` concurrently.  Cross-process queueing and isolated output
    directories remain the responsibility of the later RunManager stage.
    """

    _simulation_lock = threading.Lock()
    SUPPORTED_ACTIONS = frozenset({PlanAction.VALIDATE_CASE, PlanAction.RUN_CASE})

    def __init__(
        self,
        store: AgentTaskStore,
        command_adapter: CommandAdapter,
        *,
        command_timeout: float | None = None,
    ) -> None:
        self.store = store
        self.command_adapter = command_adapter
        self.command_timeout = command_timeout

    def execute(self, task_id: str) -> PlanExecutionResult:
        """Execute every pending supported step in the active Plan."""
        task = self.store.load_task(task_id)
        plan = self.store.load_active_plan(task_id)

        if task.status == TaskStatus.COMPLETED and plan.status == PlanStatus.COMPLETED:
            return self._result(task, plan, True, "Plan was already completed.")
        if plan.status == PlanStatus.SUPERSEDED:
            raise PlanExecutionError("A superseded Plan cannot be executed.")

        self._start_task(task)
        plan.status = PlanStatus.ACTIVE
        self.store.save_plan(task_id, plan)
        completed = {
            step.step_id for step in plan.steps if step.status == StepStatus.COMPLETED
        }

        for step in plan.steps:
            if step.status in {StepStatus.COMPLETED, StepStatus.SKIPPED}:
                continue
            if step.action not in self.SUPPORTED_ACTIONS:
                return self._fail_step(
                    task,
                    plan,
                    step,
                    f"Unsupported action for StaticPlanExecutor: {step.action.value}",
                )
            missing_dependencies = set(step.depends_on) - completed
            if missing_dependencies:
                return self._fail_step(
                    task,
                    plan,
                    step,
                    f"Uncompleted dependencies: {sorted(missing_dependencies)}",
                )

            if step.status == StepStatus.FAILED:
                step.transition_to(StepStatus.IN_PROGRESS)
            elif step.status == StepStatus.PENDING:
                step.transition_to(StepStatus.IN_PROGRESS)
            else:
                return self._fail_step(
                    task,
                    plan,
                    step,
                    f"Step cannot be executed from status: {step.status.value}",
                )
            self.store.save_plan(task_id, plan)

            try:
                command_result = self._execute_step(step)
            except (CLIInvocationError, KeyError, TypeError, ValueError) as exc:
                return self._fail_step(task, plan, step, str(exc))

            if not command_result.succeeded:
                if command_result.timed_out:
                    detail = "SPHinXsim command timed out."
                else:
                    detail = command_result.stderr.strip() or (
                        f"SPHinXsim command failed with exit code {command_result.returncode}."
                    )
                return self._fail_step(task, plan, step, detail)

            step.transition_to(
                StepStatus.COMPLETED,
                summary=f"{step.action.value} completed successfully.",
            )
            completed.add(step.step_id)
            self.store.save_plan(task_id, plan)

        plan.status = PlanStatus.COMPLETED
        task.transition_to(TaskStatus.COMPLETED)
        self.store.save_plan(task_id, plan)
        self.store.save_task(task)
        return self._result(task, plan, True, "Static Plan completed successfully.")

    def _execute_step(self, step: PlanStep) -> CLICommandResult:
        config_path = step.parameters["config_path"]
        if not isinstance(config_path, str) or not config_path.strip():
            raise ValueError(f"{step.action.value} requires a non-empty config_path")
        if step.action == PlanAction.VALIDATE_CASE:
            return self.command_adapter.validate(
                config_path,
                timeout=self.command_timeout,
            )
        if step.action == PlanAction.RUN_CASE:
            with self._simulation_lock:
                return self.command_adapter.run(
                    config_path,
                    timeout=self.command_timeout,
                )
        raise PlanExecutionError(f"Unsupported action: {step.action.value}")

    @staticmethod
    def _start_task(task: AgentTask) -> None:
        if task.status == TaskStatus.FAILED:
            task.transition_to(TaskStatus.PLANNED)
        if task.status == TaskStatus.PLANNED:
            task.transition_to(TaskStatus.APPROVED)
        if task.status in {TaskStatus.APPROVED, TaskStatus.PAUSED}:
            task.transition_to(TaskStatus.RUNNING)
        if task.status != TaskStatus.RUNNING:
            raise PlanExecutionError(
                f"Task cannot be executed from status: {task.status.value}"
            )

    def _fail_step(
        self,
        task: AgentTask,
        plan: AgentPlan,
        step: PlanStep,
        message: str,
    ) -> PlanExecutionResult:
        if step.status == StepStatus.PENDING:
            step.transition_to(StepStatus.IN_PROGRESS)
        if step.status == StepStatus.IN_PROGRESS:
            step.transition_to(StepStatus.FAILED, summary=message)
        if task.status == TaskStatus.RUNNING:
            task.transition_to(TaskStatus.FAILED)
        self.store.save_plan(task.task_id, plan)
        self.store.save_task(task)
        return self._result(task, plan, False, message, failed_step_id=step.step_id)

    @staticmethod
    def _result(
        task: AgentTask,
        plan: AgentPlan,
        succeeded: bool,
        message: str,
        *,
        failed_step_id: str | None = None,
    ) -> PlanExecutionResult:
        completed = tuple(
            step.step_id for step in plan.steps if step.status == StepStatus.COMPLETED
        )
        return PlanExecutionResult(
            task_id=task.task_id,
            plan_version=plan.version,
            succeeded=succeeded,
            completed_step_ids=completed,
            failed_step_id=failed_step_id,
            message=message,
        )
