"""Minimal synchronous executor for preset SPHinXsim Agent plans."""

from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path
import threading
from typing import Protocol

from sphinxsim.services import CLICommandResult, CLIInvocationError

from .analysis import (
    AnalysisError,
    AnalysisSpec,
    FrictionAngleSweepAnalyzer,
    RunoutAnalyzer,
)
from .case_tools import CaseWorkspace
from .models import (
    AgentAnalysis,
    AgentPlan,
    AgentRun,
    AgentTask,
    AnalysisStatus,
    PlanAction,
    PlanStatus,
    PlanStep,
    RunStatus,
    StepStatus,
    TaskStatus,
)
from .store import AgentTaskStore, TaskNotFoundError


class CommandAdapter(Protocol):
    """Command subset required by the initial static executor."""

    def validate(self, config_path: str, *, timeout: float | None = None) -> CLICommandResult:
        ...

    def run(
        self,
        config_path: str,
        *,
        output_dir: str | None = None,
        timeout: float | None = None,
    ) -> CLICommandResult:
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


@dataclass(frozen=True)
class _StepExecutionResult:
    succeeded: bool
    message: str
    artifacts: tuple[str, ...] = ()
    returncode: int | None = 0
    stderr: str = ""
    timed_out: bool = False
    run_id: str | None = None


class StaticPlanExecutor:
    """Execute validate/run steps sequentially through the existing CLI adapter.

    A process-local lock prevents multiple executor instances from invoking
    ``run_case`` concurrently. Parameterized Agent runs use isolated output
    directories; cross-process queueing remains a later RunManager concern.
    """

    _simulation_lock = threading.Lock()
    SUPPORTED_ACTIONS = frozenset(
        {
            PlanAction.PREPARE_CASE,
            PlanAction.UPDATE_CASE,
            PlanAction.VALIDATE_CASE,
            PlanAction.RUN_CASE,
            PlanAction.ANALYZE_RESULT,
            PlanAction.COMPARE_RESULTS,
        }
    )

    def __init__(
        self,
        store: AgentTaskStore,
        command_adapter: CommandAdapter,
        *,
        command_timeout: float | None = None,
        case_workspace: CaseWorkspace | None = None,
        runout_analyzer: RunoutAnalyzer | None = None,
        sweep_analyzer: FrictionAngleSweepAnalyzer | None = None,
    ) -> None:
        self.store = store
        self.command_adapter = command_adapter
        self.command_timeout = command_timeout
        source_root = getattr(command_adapter, "cwd", Path.cwd())
        self.case_workspace = case_workspace or CaseWorkspace(
            store.root,
            source_root=source_root,
        )
        self.runout_analyzer = runout_analyzer or RunoutAnalyzer()
        self.sweep_analyzer = sweep_analyzer or FrictionAngleSweepAnalyzer()

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
                step_result = self._execute_step(task, step)
            except (CLIInvocationError, KeyError, TypeError, ValueError) as exc:
                return self._fail_step(task, plan, step, str(exc))

            if not step_result.succeeded:
                if step_result.timed_out:
                    detail = "SPHinXsim command timed out."
                else:
                    detail = step_result.stderr.strip() or step_result.message or (
                        f"SPHinXsim command failed with exit code {step_result.returncode}."
                    )
                return self._fail_step(task, plan, step, detail)

            for artifact in step_result.artifacts:
                if artifact not in step.artifacts:
                    step.artifacts.append(artifact)
                if step.action == PlanAction.UPDATE_CASE and artifact not in task.case_versions:
                    task.case_versions.append(artifact)
            if step_result.run_id and step_result.run_id not in task.run_ids:
                task.run_ids.append(step_result.run_id)
            step.transition_to(
                StepStatus.COMPLETED,
                summary=step_result.message,
            )
            completed.add(step.step_id)
            self.store.save_plan(task_id, plan)
            self.store.save_task(task)

        plan.status = PlanStatus.COMPLETED
        task.transition_to(TaskStatus.COMPLETED)
        self.store.save_plan(task_id, plan)
        self.store.save_task(task)
        return self._result(task, plan, True, "Static Plan completed successfully.")

    def recover_interrupted(self, task_id: str) -> PlanExecutionResult:
        """Mark one orphaned in-progress step failed so it can be retried.

        The caller must first ensure that the earlier worker and solver process
        are no longer running. This method deliberately does not guess whether
        a live external process is safe to replace.
        """
        task = self.store.load_task(task_id)
        plan = self.store.load_active_plan(task_id)
        running_steps = [
            step for step in plan.steps if step.status == StepStatus.IN_PROGRESS
        ]
        if task.status != TaskStatus.RUNNING or len(running_steps) != 1:
            raise PlanExecutionError(
                "interrupted recovery requires one in-progress step on a running task"
            )
        step = running_steps[0]
        message = "Interrupted worker state was recovered for an explicit retry."

        if step.action == PlanAction.RUN_CASE:
            run_id = step.parameters.get("run_id")
            if isinstance(run_id, str):
                try:
                    run = self.store.load_run(task_id, run_id)
                except TaskNotFoundError:
                    run = None
                if run is not None and run.status == RunStatus.RUNNING:
                    run.finish(
                        RunStatus.FAILED,
                        returncode=None,
                        error_message=message,
                    )
                    self.store.save_run(run)
        elif step.action == PlanAction.ANALYZE_RESULT:
            run_id = step.parameters.get("run_id")
            analysis_id = step.parameters.get("analysis_id")
            if isinstance(run_id, str) and isinstance(analysis_id, str):
                try:
                    analysis = self.store.load_analysis(
                        task_id,
                        run_id,
                        analysis_id,
                    )
                except TaskNotFoundError:
                    analysis = None
                if analysis is not None and analysis.status == AnalysisStatus.RUNNING:
                    analysis.finish(AnalysisStatus.FAILED, error_message=message)
                    self.store.save_analysis(analysis)

        return self._fail_step(task, plan, step, message)

    def _execute_step(self, task: AgentTask, step: PlanStep) -> _StepExecutionResult:
        if step.action == PlanAction.PREPARE_CASE:
            prepared_path = self.case_workspace.prepare_case(
                step.parameters["source_config_path"],
                step.parameters["output_config_path"],
            )
            return _StepExecutionResult(
                succeeded=True,
                message="Case copied into the isolated task workspace.",
                artifacts=(str(prepared_path),),
            )
        if step.action == PlanAction.UPDATE_CASE:
            updated_path, angle_radians = self.case_workspace.update_friction_angle(
                step.parameters["config_path"],
                step.parameters["friction_angle_degrees"],
                body_name=step.parameters["body_name"],
            )
            return _StepExecutionResult(
                succeeded=True,
                message=f"Friction angle updated to {angle_radians:.12g} radians.",
                artifacts=(str(updated_path),),
            )
        if step.action == PlanAction.COMPARE_RESULTS:
            return self._execute_comparison(task, step)

        config_path = step.parameters["config_path"]
        if not isinstance(config_path, str) or not config_path.strip():
            raise ValueError(f"{step.action.value} requires a non-empty config_path")
        if step.action == PlanAction.VALIDATE_CASE:
            return self._from_cli_result(
                self.command_adapter.validate(
                    config_path,
                    timeout=self.command_timeout,
                ),
                success_message="Configuration validation completed successfully.",
            )
        if step.action == PlanAction.RUN_CASE:
            return self._execute_run(task, step, config_path)
        if step.action == PlanAction.ANALYZE_RESULT:
            return self._execute_analysis(task, step, config_path)
        raise PlanExecutionError(f"Unsupported action: {step.action.value}")

    def _execute_run(
        self,
        task: AgentTask,
        step: PlanStep,
        config_path: str,
    ) -> _StepExecutionResult:
        run_id = step.parameters.get("run_id")
        output_dir = step.parameters.get("output_dir")
        if run_id is None and output_dir is None:
            with self._simulation_lock:
                result = self.command_adapter.run(
                    config_path,
                    timeout=self.command_timeout,
                )
            return self._from_cli_result(
                result,
                success_message="SPHinXsim simulation completed successfully.",
            )
        if not isinstance(run_id, str) or not run_id.strip():
            raise ValueError("run_case requires a non-empty run_id with output_dir")
        if not isinstance(output_dir, str) or not output_dir.strip():
            raise ValueError("run_case requires a non-empty output_dir with run_id")

        expected_output_dir = self.store.run_directory(task.task_id, run_id)
        if Path(output_dir).resolve() != expected_output_dir:
            raise ValueError(
                "run_case output_dir must match the task's isolated run directory"
            )
        try:
            run = self.store.load_run(task.task_id, run_id)
        except TaskNotFoundError:
            run = AgentRun(
                run_id=run_id,
                task_id=task.task_id,
                case_path=str(Path(config_path).resolve()),
                output_dir=str(expected_output_dir),
            )
            self.store.create_run(run)
        else:
            if Path(run.case_path).resolve() != Path(config_path).resolve():
                raise ValueError("existing run uses a different case_path")
            if Path(run.output_dir).resolve() != expected_output_dir:
                raise ValueError("existing run uses a different output_dir")
            if run.status == RunStatus.COMPLETED:
                return _StepExecutionResult(
                    succeeded=True,
                    message="Recovered an already completed SPHinXsim run.",
                    artifacts=(str(expected_output_dir),),
                    run_id=run_id,
                )
            if run.status == RunStatus.RUNNING:
                raise ValueError(
                    "run is still marked running; call recover_interrupted only "
                    "after confirming the previous solver process has stopped"
                )
            run.restart()
            self.store.save_run(run)
        if run_id not in task.run_ids:
            task.run_ids.append(run_id)
            self.store.save_task(task)

        try:
            with self._simulation_lock:
                result = self.command_adapter.run(
                    config_path,
                    output_dir=str(expected_output_dir),
                    timeout=self.command_timeout,
                )
        except CLIInvocationError as exc:
            run.finish(
                RunStatus.FAILED,
                returncode=None,
                error_message=str(exc),
            )
            self.store.save_run(run)
            raise

        stdout_path, stderr_path = self.store.save_run_logs(
            task.task_id,
            run_id,
            stdout=result.stdout,
            stderr=result.stderr,
        )
        run.stdout_log = str(stdout_path)
        run.stderr_log = str(stderr_path)
        if result.succeeded:
            status = RunStatus.COMPLETED
            error_message = None
        elif result.timed_out:
            status = RunStatus.TIMED_OUT
            error_message = "SPHinXsim command timed out."
        else:
            status = RunStatus.FAILED
            error_message = result.stderr.strip() or (
                f"SPHinXsim command failed with exit code {result.returncode}."
            )
        run.finish(
            status,
            returncode=result.returncode,
            timed_out=result.timed_out,
            error_message=error_message,
        )
        self.store.save_run(run)
        converted = self._from_cli_result(
            result,
            success_message="SPHinXsim simulation completed successfully.",
        )
        return replace(
            converted,
            artifacts=(str(expected_output_dir),),
            run_id=run_id,
        )

    def _execute_analysis(
        self,
        task: AgentTask,
        step: PlanStep,
        config_path: str,
    ) -> _StepExecutionResult:
        analysis_id = step.parameters.get("analysis_id")
        run_id = step.parameters.get("run_id")
        raw_spec = step.parameters.get("analysis_spec")
        if not isinstance(analysis_id, str) or not analysis_id.strip():
            raise ValueError("analyze_result requires a non-empty analysis_id")
        if not isinstance(run_id, str) or not run_id.strip():
            raise ValueError("analyze_result requires a non-empty run_id")
        if not isinstance(raw_spec, dict):
            raise ValueError("analyze_result requires an analysis_spec object")

        run = self.store.load_run(task.task_id, run_id)
        if run.status != RunStatus.COMPLETED:
            raise ValueError("analyze_result requires a completed run")
        try:
            analysis = self.store.load_analysis(task.task_id, run_id, analysis_id)
        except TaskNotFoundError:
            analysis = AgentAnalysis(
                analysis_id=analysis_id,
                task_id=task.task_id,
                run_id=run_id,
                config_path=str(Path(config_path).resolve()),
                spec=raw_spec,
            )
            self.store.create_analysis(analysis)
        else:
            if analysis.status == AnalysisStatus.COMPLETED:
                artifact = self.store.analysis_path(task.task_id, run_id, analysis_id)
                return _StepExecutionResult(
                    succeeded=True,
                    message="Recovered an already completed runout analysis.",
                    artifacts=(str(artifact),),
                )
            if analysis.status == AnalysisStatus.RUNNING:
                raise ValueError(
                    "analysis is still marked running; call recover_interrupted "
                    "before retrying"
                )
            analysis.restart()
            self.store.save_analysis(analysis)
        if analysis_id not in run.analysis_ids:
            run.analysis_ids.append(analysis_id)
            self.store.save_run(run)

        try:
            spec = AnalysisSpec.model_validate(raw_spec)
            result = self.runout_analyzer.analyze(
                config_path,
                run.output_dir,
                spec,
            )
        except (AnalysisError, ValueError) as exc:
            analysis.finish(
                AnalysisStatus.FAILED,
                error_message=str(exc),
            )
            self.store.save_analysis(analysis)
            raise AnalysisError(str(exc)) from exc

        metrics = result.model_dump(mode="json")
        analysis.finish(AnalysisStatus.COMPLETED, metrics=metrics)
        self.store.save_analysis(analysis)
        artifact = self.store.analysis_path(task.task_id, run_id, analysis_id)
        return _StepExecutionResult(
            succeeded=True,
            message=(
                "Runout analysis completed: "
                f"final_front={result.final_front:.12g} {result.unit}, "
                f"runout_extension={result.runout_extension:.12g} {result.unit}."
            ),
            artifacts=(str(artifact),),
        )

    def _execute_comparison(
        self,
        task: AgentTask,
        step: PlanStep,
    ) -> _StepExecutionResult:
        sweep_id = step.parameters.get("sweep_id")
        target_value = step.parameters.get("target_value")
        sample_specs = step.parameters.get("samples")
        if not isinstance(sweep_id, str) or not sweep_id.strip():
            raise ValueError("compare_results requires a non-empty sweep_id")
        if isinstance(target_value, bool) or not isinstance(target_value, (int, float)):
            raise ValueError("compare_results requires a numeric target_value")
        if not isinstance(sample_specs, list) or not sample_specs:
            raise ValueError("compare_results requires a non-empty samples list")

        samples: list[tuple[float, str, str, dict]] = []
        for sample in sample_specs:
            if not isinstance(sample, dict):
                raise ValueError("compare_results sample entries must be objects")
            angle = sample.get("friction_angle_degrees")
            run_id = sample.get("run_id")
            analysis_id = sample.get("analysis_id")
            if isinstance(angle, bool) or not isinstance(angle, (int, float)):
                raise ValueError("compare_results sample angle must be numeric")
            if not isinstance(run_id, str) or not isinstance(analysis_id, str):
                raise ValueError("compare_results sample IDs must be strings")
            analysis = self.store.load_analysis(task.task_id, run_id, analysis_id)
            if analysis.status != AnalysisStatus.COMPLETED:
                raise ValueError("compare_results requires completed analyses")
            samples.append((float(angle), run_id, analysis_id, analysis.metrics))

        result = self.sweep_analyzer.summarize(
            sweep_id=sweep_id,
            target_value=float(target_value),
            samples=samples,
        )
        self.store.create_sweep_result(task.task_id, result)
        if sweep_id not in task.sweep_ids:
            task.sweep_ids.append(sweep_id)
            self.store.save_task(task)
        artifact = self.store.sweep_path(task.task_id, sweep_id)
        return _StepExecutionResult(
            succeeded=True,
            message=(
                "Friction-angle sweep comparison completed: "
                f"best_angle={result.best_friction_angle_degrees:.12g} degrees, "
                "best_runout_extension="
                f"{result.best_runout_extension:.12g} {result.unit}, "
                f"absolute_error={result.best_absolute_error:.12g} {result.unit}."
            ),
            artifacts=(str(artifact),),
        )

    @staticmethod
    def _from_cli_result(
        result: CLICommandResult,
        *,
        success_message: str,
    ) -> _StepExecutionResult:
        return _StepExecutionResult(
            succeeded=result.succeeded,
            message=success_message if result.succeeded else "SPHinXsim command failed.",
            returncode=result.returncode,
            stderr=result.stderr,
            timed_out=result.timed_out,
        )

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
