"""Deterministic calibration workflow contracts and orchestration."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
import json
import math
from pathlib import Path
from typing import Any, Protocol
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from .analysis import AnalysisSpec
from .calibration import (
    BoundedSearchPolicy,
    CalibrationDecision,
    CalibrationDecisionStatus,
    CalibrationError,
    CalibrationObservation,
    CalibrationSpec,
    Monotonicity,
    ObjectiveSpec,
    ParameterSpec,
)
from .executor import StaticPlanExecutor
from .models import utc_now
from .presets import create_runout_analysis_column_collapse_task
from .store import AgentTaskStore, TaskNotFoundError
from .tools import ToolRegistry, build_builtin_tool_registry
from sphinxsim.services import CLICommandResult, CLIInvocationError


class CalibrationPlanner(Protocol):
    """Boundary implemented by a mock now and a structured-output LLM later."""

    def plan(self, user_prompt: str) -> "CalibrationWorkflowDefinition": ...


class CalibrationCommandAdapter(Protocol):
    """Existing CLI methods used by the deterministic calibration workflow."""

    cwd: Path

    def generate(
        self,
        description: str,
        output_path: str | Path,
        *,
        timeout: float | None = None,
    ) -> CLICommandResult: ...

    def validate(
        self,
        config_path: str | Path,
        *,
        timeout: float | None = None,
    ) -> CLICommandResult: ...

    def run(
        self,
        config_path: str | Path,
        *,
        output_dir: str | Path | None = None,
        timeout: float | None = None,
    ) -> CLICommandResult: ...


class CalibrationWorkflowDefinition(BaseModel):
    """Validated planner output consumed by the deterministic workflow."""

    model_config = ConfigDict(extra="forbid")

    generation_description: str = Field(..., min_length=1)
    case_name: str = Field(
        default="column_collapse",
        min_length=1,
        pattern=r"^[A-Za-z0-9_-]+$",
    )
    body_name: str = Field(..., min_length=1)
    calibration: CalibrationSpec
    analysis: AnalysisSpec

    def validate_tools(self, registry: ToolRegistry) -> None:
        self.calibration.validate_tools(registry)


class MockColumnCollapseCalibrationPlanner:
    """Return fixed structured output without interpreting natural language."""

    def __init__(
        self,
        *,
        target_runout_extension: float = 0.20,
        tolerance: float = 0.005,
        lower_friction_angle: float = 0.0,
        upper_friction_angle: float = 40.0,
        maximum_iterations: int = 8,
    ) -> None:
        self._definition = CalibrationWorkflowDefinition(
            generation_description=(
                "Generate a 2D granular column collapse simulation with a plastic "
                "continuum body named GranularBody."
            ),
            case_name="column_collapse",
            body_name="GranularBody",
            calibration=CalibrationSpec(
                calibration_id="column_collapse_runout_extension",
                parameter=ParameterSpec(
                    tool_name="set_friction_angle",
                    input_field="friction_angle_degrees",
                    lower_bound=lower_friction_angle,
                    upper_bound=upper_friction_angle,
                    unit="degree",
                ),
                objective=ObjectiveSpec(
                    tool_name="analyze_runout",
                    metric_name="runout_extension",
                    target_value=target_runout_extension,
                    tolerance=tolerance,
                    unit="m",
                ),
                expected_monotonicity=Monotonicity.DECREASING,
                maximum_iterations=maximum_iterations,
            ),
            analysis=AnalysisSpec(
                metric="runout",
                body_name="GranularBody",
                time_selection="final",
                direction_vector=(1.0, 0.0),
                initial_front_source="geometry",
                front_method="maximum",
                unit="m",
            ),
        )

    def plan(self, user_prompt: str) -> CalibrationWorkflowDefinition:
        if not isinstance(user_prompt, str) or not user_prompt.strip():
            raise ValueError("user_prompt must not be empty")
        return self._definition.model_copy(deep=True)


class CalibrationWorkflowStatus(str, Enum):
    RUNNING = "running"
    CONVERGED = "converged"
    EXHAUSTED = "exhausted"
    CANCELLED = "cancelled"
    FAILED = "failed"


class CalibrationIteration(BaseModel):
    """Auditable record of one real or test-double SPHinXsim execution."""

    model_config = ConfigDict(extra="forbid")

    iteration: int = Field(..., ge=1)
    parameter_value: float
    task_id: str
    case_path: str
    run_id: str
    analysis_id: str
    metric_name: str
    metric_value: float
    absolute_error: float = Field(..., ge=0.0)


class CalibrationWorkflowResult(BaseModel):
    """Persisted state and outcome of the complete calibration loop."""

    model_config = ConfigDict(extra="forbid")

    workflow_id: str = Field(..., pattern=r"^[A-Za-z0-9_-]+$")
    user_prompt: str
    definition: CalibrationWorkflowDefinition
    status: CalibrationWorkflowStatus
    baseline_case_path: str
    iterations: list[CalibrationIteration] = Field(default_factory=list)
    final_decision: CalibrationDecision | None = None
    error_message: str | None = None
    replan_history: list[dict[str, Any]] = Field(default_factory=list)
    started_at: datetime
    finished_at: datetime | None = None

    @property
    def succeeded(self) -> bool:
        return self.status == CalibrationWorkflowStatus.CONVERGED


class DeterministicCalibrationWorkflow:
    """Run generate/validate/simulate/analyze iterations without LLM reasoning."""

    def __init__(
        self,
        store: AgentTaskStore,
        planner: CalibrationPlanner,
        command_adapter: CalibrationCommandAdapter,
        *,
        search_policy: BoundedSearchPolicy | None = None,
        tool_registry: ToolRegistry | None = None,
        command_timeout: float | None = None,
    ) -> None:
        self.store = store
        self.planner = planner
        self.command_adapter = command_adapter
        self.search_policy = search_policy or BoundedSearchPolicy()
        self.tool_registry = tool_registry or build_builtin_tool_registry()
        self.command_timeout = command_timeout

    def execute(
        self,
        user_prompt: str,
        *,
        workflow_id: str | None = None,
    ) -> CalibrationWorkflowResult:
        result = self.initialize(user_prompt, workflow_id=workflow_id)
        if result.status != CalibrationWorkflowStatus.RUNNING:
            return result
        try:
            while result.status == CalibrationWorkflowStatus.RUNNING:
                decision = self.propose_next(result)
                self.apply_decision(result, decision)
                if decision.status != CalibrationDecisionStatus.NEXT:
                    return result
                if decision.candidate_value is None:
                    return self.fail(result, "Search policy returned no candidate value.")
                self.execute_candidate(result, decision.candidate_value)
            return result
        except (CalibrationError, CLIInvocationError, OSError, ValueError) as exc:
            return self.fail(result, str(exc))

    def initialize(
        self,
        user_prompt: str,
        *,
        workflow_id: str | None = None,
    ) -> CalibrationWorkflowResult:
        """Create, generate, and validate one workflow before any simulation."""
        definition = self.planner.plan(user_prompt)
        definition.validate_tools(self.tool_registry)
        identifier = workflow_id or f"calibration_{uuid4().hex[:12]}"
        if not identifier or not identifier.replace("_", "").replace("-", "").isalnum():
            raise ValueError("workflow_id must contain only letters, digits, '_' or '-'")
        if self._result_path(identifier).exists():
            raise ValueError(
                f"calibration workflow already exists: {identifier}; "
                "resume support is not implemented yet"
            )

        baseline = (
            self.store.root
            / "workflows"
            / identifier
            / "cases"
            / f"{definition.case_name}_generated.json"
        ).resolve()
        result = CalibrationWorkflowResult(
            workflow_id=identifier,
            user_prompt=user_prompt,
            definition=definition,
            status=CalibrationWorkflowStatus.RUNNING,
            baseline_case_path=str(baseline),
            started_at=utc_now(),
        )
        self._save(result)

        try:
            generated = self.command_adapter.generate(
                definition.generation_description,
                baseline,
                timeout=self.command_timeout,
            )
            self._save_command_logs(identifier, "generate", generated)
            if not generated.succeeded:
                return self.fail(
                    result,
                    generated.stderr.strip() or "SPHinXsim generate command failed.",
                )
            if not baseline.is_file():
                return self.fail(
                    result,
                    "SPHinXsim generate command did not create the baseline JSON.",
                )

            validated = self.command_adapter.validate(
                baseline,
                timeout=self.command_timeout,
            )
            self._save_command_logs(identifier, "validate_generated", validated)
            if not validated.succeeded:
                return self.fail(
                    result,
                    validated.stderr.strip() or "Generated configuration validation failed.",
                )
            return result
        except (CalibrationError, CLIInvocationError, OSError, ValueError) as exc:
            return self.fail(result, str(exc))

    def propose_next(self, result: CalibrationWorkflowResult) -> CalibrationDecision:
        """Derive the next deterministic decision from persisted observations."""
        if result.status != CalibrationWorkflowStatus.RUNNING:
            raise ValueError("next decision requires a running calibration workflow")
        observations = [
            CalibrationObservation(
                parameter_value=item.parameter_value,
                metric_value=item.metric_value,
                run_id=item.run_id,
                analysis_id=item.analysis_id,
            )
            for item in result.iterations
        ]
        return self.search_policy.propose_next(
            result.definition.calibration,
            observations,
        )

    def apply_decision(
        self,
        result: CalibrationWorkflowResult,
        decision: CalibrationDecision,
    ) -> CalibrationWorkflowResult:
        """Persist one policy decision and apply terminal state transitions."""
        result.final_decision = decision
        if decision.status == CalibrationDecisionStatus.CONVERGED:
            result.status = CalibrationWorkflowStatus.CONVERGED
            result.finished_at = utc_now()
        elif decision.status == CalibrationDecisionStatus.EXHAUSTED:
            result.status = CalibrationWorkflowStatus.EXHAUSTED
            result.finished_at = utc_now()
        self._save(result)
        return result

    def execute_candidate(
        self,
        result: CalibrationWorkflowResult,
        parameter_value: float,
    ) -> CalibrationIteration:
        """Execute and persist one candidate, recovering a completed child task."""
        if result.status != CalibrationWorkflowStatus.RUNNING:
            raise ValueError("candidate execution requires a running workflow")
        iteration = len(result.iterations) + 1
        record = self._execute_iteration(result, parameter_value, iteration)
        result.iterations.append(record)
        self._save(result)
        return record

    def _execute_iteration(
        self,
        workflow: CalibrationWorkflowResult,
        parameter_value: float,
        iteration: int,
    ) -> CalibrationIteration:
        suffix = f"{iteration:03d}"
        task_id = f"{workflow.workflow_id}_iteration_{suffix}"
        try:
            task = self.store.load_task(task_id)
            plan = self.store.load_active_plan(task_id)
            planned_value = plan.steps[1].parameters.get("friction_angle_degrees")
            if planned_value != parameter_value:
                raise ValueError(
                    f"existing calibration iteration {iteration} uses a different "
                    "parameter value"
                )
        except TaskNotFoundError:
            task, plan = create_runout_analysis_column_collapse_task(
                self.store,
                friction_angle_degrees=parameter_value,
                config_path=workflow.baseline_case_path,
                task_id=task_id,
            )
        execution = StaticPlanExecutor(
            self.store,
            self.command_adapter,
            command_timeout=self.command_timeout,
        ).execute(task.task_id)
        if not execution.succeeded:
            raise ValueError(
                f"calibration iteration {iteration} failed at "
                f"{execution.failed_step_id}: {execution.message}"
            )

        run_id = "run_001"
        analysis_id = "analysis_001"
        analysis = self.store.load_analysis(task_id, run_id, analysis_id)
        metric_name = workflow.definition.calibration.objective.metric_name
        metric_value = analysis.metrics.get(metric_name)
        if isinstance(metric_value, bool) or not isinstance(metric_value, (int, float)):
            raise ValueError(
                f"analysis did not produce numeric metric {metric_name!r}"
            )
        numeric_metric = float(metric_value)
        if not math.isfinite(numeric_metric):
            raise ValueError(f"analysis metric {metric_name!r} must be finite")
        case_path = plan.steps[1].parameters["config_path"]
        return CalibrationIteration(
            iteration=iteration,
            parameter_value=parameter_value,
            task_id=task_id,
            case_path=case_path,
            run_id=run_id,
            analysis_id=analysis_id,
            metric_name=metric_name,
            metric_value=numeric_metric,
            absolute_error=abs(
                numeric_metric
                - workflow.definition.calibration.objective.target_value
            ),
        )

    def fail(
        self,
        result: CalibrationWorkflowResult,
        message: str,
    ) -> CalibrationWorkflowResult:
        result.status = CalibrationWorkflowStatus.FAILED
        result.error_message = message
        result.finished_at = utc_now()
        self._save(result)
        return result

    def cancel(
        self,
        result: CalibrationWorkflowResult,
        message: str,
    ) -> CalibrationWorkflowResult:
        """Persist a user cancellation without classifying it as a failure."""
        result.status = CalibrationWorkflowStatus.CANCELLED
        result.error_message = message
        result.finished_at = utc_now()
        self._save(result)
        return result

    def resume_after_failure(
        self,
        result: CalibrationWorkflowResult,
    ) -> CalibrationWorkflowResult:
        """Return a failed workflow to running after a validated replan."""
        if result.status != CalibrationWorkflowStatus.FAILED:
            raise ValueError("only a failed workflow can be resumed")
        result.status = CalibrationWorkflowStatus.RUNNING
        result.error_message = None
        result.finished_at = None
        self._save(result)
        return result

    def revise_parameter_bounds(
        self,
        result: CalibrationWorkflowResult,
        *,
        lower_bound: float,
        upper_bound: float,
    ) -> CalibrationWorkflowResult:
        """Apply the only scientific-contract change allowed during replanning."""
        parameter = result.definition.calibration.parameter.model_copy(
            update={"lower_bound": lower_bound, "upper_bound": upper_bound}
        )
        calibration = result.definition.calibration.model_copy(
            update={"parameter": parameter}
        )
        result.definition = result.definition.model_copy(
            update={"calibration": calibration}
        )
        return self.resume_after_failure(result)

    def record_replan(
        self,
        result: CalibrationWorkflowResult,
        record: dict[str, Any],
    ) -> CalibrationWorkflowResult:
        result.replan_history.append(record)
        self._save(result)
        return result

    def _save(self, result: CalibrationWorkflowResult) -> Path:
        path = self._result_path(result.workflow_id)
        self.store._atomic_write(path, result.model_dump_json(indent=2))
        return path

    def _save_command_logs(
        self,
        workflow_id: str,
        name: str,
        command: CLICommandResult,
    ) -> None:
        directory = self.store.root / "workflows" / workflow_id / "logs"
        directory.mkdir(parents=True, exist_ok=True)
        (directory / f"{name}.stdout.log").write_text(
            command.stdout,
            encoding="utf-8",
        )
        (directory / f"{name}.stderr.log").write_text(
            command.stderr,
            encoding="utf-8",
        )

    def _result_path(self, workflow_id: str) -> Path:
        return self.store.root / "workflows" / workflow_id / "workflow.json"

    def load(self, workflow_id: str) -> CalibrationWorkflowResult:
        return CalibrationWorkflowResult.model_validate_json(
            self._result_path(workflow_id).read_text(encoding="utf-8")
        )
