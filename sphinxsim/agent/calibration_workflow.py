"""Deterministic calibration workflow contracts and orchestration."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
import hashlib
import json
import math
from pathlib import Path
from typing import Any, Protocol
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .analysis import AnalysisSpec
from .analysis_plan import AnalysisPlan
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
from .preview_review import PreviewDecision, PreviewReviewer
from .store import AgentTaskStore, TaskNotFoundError
from .tools import ToolRegistry, build_builtin_tool_registry
from sphinxsim.services import CLICommandResult, CLIInvocationError


class CalibrationPlanner(Protocol):
    """Boundary implemented by a mock now and a structured-output LLM later."""

    def plan(self, user_prompt: str) -> "CalibrationWorkflowDefinition": ...


class CalibrationCommandAdapter(Protocol):
    """Workflow boundary implemented by the native MCP execution client."""

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

    def update(
        self,
        config_path: str | Path,
        instruction: str,
        *,
        output_path: str | Path | None = None,
        timeout: float | None = None,
        **kwargs: Any,
    ) -> CLICommandResult: ...

    def preview(
        self,
        config_path: str | Path,
        *,
        with_particles: bool = False,
        off_screen: bool = False,
        screenshot_path: str | Path | None = None,
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
    analysis: AnalysisSpec | None = None
    analysis_plan: AnalysisPlan | None = None

    @model_validator(mode="after")
    def validate_analysis_plan(self):
        if self.analysis_plan is None:
            if self.analysis is None:
                raise ValueError("an analysis contract is required")
            self.analysis_plan = AnalysisPlan(builtin=self.analysis,
                metric_name=self.calibration.objective.metric_name)
        plan = self.analysis_plan
        if self.analysis is not None and (plan.kind != "builtin" or plan.builtin != self.analysis):
            raise ValueError("legacy analysis and analysis_plan disagree")
        if plan.body_name != self.body_name:
            raise ValueError("analysis body must match workflow body")
        if plan.metric_name != self.calibration.objective.metric_name or plan.unit != self.calibration.objective.unit:
            raise ValueError("analysis metric is not supported or does not match the objective and unit")
        return self

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
    case_prepared: bool = False
    preview_status: str = "pending"
    preview_fingerprint: str | None = None
    preview_attempts: list[dict[str, Any]] = Field(default_factory=list)
    plan_approved: bool = False
    approved_at: datetime | None = None
    iterations: list[CalibrationIteration] = Field(default_factory=list)
    final_decision: CalibrationDecision | None = None
    error_message: str | None = None
    replan_history: list[dict[str, Any]] = Field(default_factory=list)
    failure_kind: str | None = None
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
        preview_reviewer: PreviewReviewer | None = None,
        maximum_preview_attempts: int = 2,
    ) -> None:
        self.store = store
        self.planner = planner
        self.command_adapter = command_adapter
        self.search_policy = search_policy or BoundedSearchPolicy()
        self.tool_registry = tool_registry or build_builtin_tool_registry()
        self.command_timeout = command_timeout
        self.preview_reviewer = preview_reviewer
        self.maximum_preview_attempts = maximum_preview_attempts

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
        result = self.draft_plan(user_prompt, workflow_id=workflow_id)
        result = self.prepare_case(result)
        if result.status == CalibrationWorkflowStatus.RUNNING:
            result = self.preview_case(result)
        return result

    def draft_plan(
        self,
        user_prompt: str,
        *,
        workflow_id: str | None = None,
    ) -> CalibrationWorkflowResult:
        """Create and persist a validated Plan without producing case artifacts."""
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
        return result

    def prepare_case(
        self,
        result: CalibrationWorkflowResult,
    ) -> CalibrationWorkflowResult:
        """Generate and validate the baseline case for an approved Plan."""
        if result.status != CalibrationWorkflowStatus.RUNNING:
            raise ValueError("case preparation requires a running workflow")
        if result.case_prepared:
            return result

        try:
            generated = self.command_adapter.generate(
                result.definition.generation_description,
                result.baseline_case_path,
                timeout=self.command_timeout,
            )
            self._save_command_logs(result.workflow_id, "generate", generated)
            if not generated.succeeded:
                return self.fail(
                    result,
                    generated.stderr.strip() or "SPHinXsim generate command failed.",
                )
            if not Path(result.baseline_case_path).is_file():
                return self.fail(
                    result,
                    "SPHinXsim generate command did not create the baseline JSON.",
                )

            validated = self.command_adapter.validate(
                result.baseline_case_path,
                timeout=self.command_timeout,
            )
            self._save_command_logs(
                result.workflow_id,
                "validate_generated",
                validated,
            )
            if not validated.succeeded:
                return self.fail(
                    result,
                    validated.stderr.strip() or "Generated configuration validation failed.",
                )
            result.case_prepared = True
            self._save(result)
            return result
        except (CalibrationError, CLIInvocationError, OSError, ValueError) as exc:
            return self.fail(result, str(exc))

    def approve_plan(
        self,
        result: CalibrationWorkflowResult,
    ) -> CalibrationWorkflowResult:
        """Persist one user approval for the complete execution Plan."""
        if result.status != CalibrationWorkflowStatus.RUNNING:
            raise ValueError("Plan approval requires a running workflow")
        if result.case_prepared:
            raise ValueError("Plan approval must happen before case preparation")
        result.plan_approved = True
        result.approved_at = utc_now()
        self._save(result)
        return result

    def preview_case(
        self,
        result: CalibrationWorkflowResult,
    ) -> CalibrationWorkflowResult:
        """Render and visually review a baseline without involving the user."""
        if result.status != CalibrationWorkflowStatus.RUNNING:
            raise ValueError("visual preflight requires a running workflow")
        if not result.case_prepared:
            raise ValueError("visual preflight requires a prepared case")
        if result.preview_status in {"accepted", "skipped"}:
            return result
        if self.preview_reviewer is None:
            result.preview_status = "skipped"
            result.preview_fingerprint = self._config_fingerprint(
                result.baseline_case_path
            )
            self._save(result)
            return result

        preview_dir = self._result_path(result.workflow_id).parent / "previews"
        preview_dir.mkdir(parents=True, exist_ok=True)
        for attempt in range(len(result.preview_attempts) + 1, self.maximum_preview_attempts + 1):
            screenshot = preview_dir / f"attempt_{attempt:03d}.png"
            rendered = self.command_adapter.preview(
                result.baseline_case_path,
                with_particles=True,
                off_screen=True,
                screenshot_path=screenshot,
                timeout=self.command_timeout,
            )
            self._save_command_logs(result.workflow_id, f"preview_{attempt:03d}", rendered)
            if not rendered.succeeded or not screenshot.is_file():
                result.failure_kind = "preview_render_error"
                return self.fail(
                    result,
                    rendered.stderr.strip() or "Preview did not create a screenshot.",
                )
            try:
                review = self.preview_reviewer.review(
                    screenshot,
                    result.baseline_case_path,
                    attempt=attempt,
                    request=(
                        result.user_prompt
                        + "\nGenerated case intent: "
                        + result.definition.generation_description
                    ),
                )
            except (OSError, ValueError) as exc:
                result.failure_kind = "preview_model_error"
                return self.fail(result, f"Visual preflight failed: {exc}")
            result.preview_attempts.append(
                {
                    "attempt": attempt,
                    "screenshot_path": str(screenshot),
                    "review": review.model_dump(mode="json"),
                }
            )
            self._save(result)
            if review.decision == PreviewDecision.ACCEPT:
                result.preview_status = "accepted"
                result.preview_fingerprint = self._config_fingerprint(
                    result.baseline_case_path
                )
                self._save(result)
                return result
            if review.decision == PreviewDecision.REJECT:
                result.preview_status = "rejected"
                result.failure_kind = "preview_rejected"
                return self.fail(result, review.summary)
            if attempt >= self.maximum_preview_attempts:
                break
            updated = self.command_adapter.update(
                result.baseline_case_path,
                review.revision_instruction or "",
                output_path=result.baseline_case_path,
                timeout=self.command_timeout,
            )
            self._save_command_logs(result.workflow_id, f"preview_update_{attempt:03d}", updated)
            if not updated.succeeded:
                result.failure_kind = "preview_revision_error"
                return self.fail(
                    result,
                    updated.stderr.strip() or "Preview revision failed.",
                )
            validated = self.command_adapter.validate(
                result.baseline_case_path,
                timeout=self.command_timeout,
            )
            if not validated.succeeded:
                result.failure_kind = "preview_revision_error"
                return self.fail(
                    result,
                    validated.stderr.strip() or "Revised configuration is invalid.",
                )
        result.preview_status = "rejected"
        result.failure_kind = "preview_attempts_exhausted"
        return self.fail(result, "Visual preflight exhausted its revision attempts.")

    @staticmethod
    def _config_fingerprint(config_path: str | Path) -> str:
        payload = json.loads(Path(config_path).read_text(encoding="utf-8"))
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

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
            plan.steps[1].parameters["body_name"] = workflow.definition.body_name
            registry, spec = workflow.definition.analysis_plan.registry_and_spec()
            plan.steps[-1].parameters["analysis_spec"] = spec
            plan.steps[-1].parameters["analysis_plan"] = workflow.definition.analysis_plan.model_dump(mode="json")
            self.store.save_plan(task_id, plan)
        registry, _ = workflow.definition.analysis_plan.registry_and_spec()
        execution = StaticPlanExecutor(
            self.store,
            self.command_adapter,
            command_timeout=self.command_timeout,
            extractor_registry=registry,
        ).execute(task.task_id)
        if not execution.succeeded:
            try:
                run = self.store.load_run(task_id, "run_001")
                workflow.failure_kind = run.failure_kind
            except TaskNotFoundError:
                workflow.failure_kind = "validation_error" if execution.failed_step_id == "validate_case" else "case_error"
            if execution.failed_step_id == "analyze_result":
                workflow.failure_kind = "missing_vtp" if "no state VTP" in execution.message else "analysis_error"
            raise ValueError(
                f"calibration iteration {iteration} failed at "
                f"{execution.failed_step_id}: {execution.message}"
            )

        run_id = "run_001"
        analysis_id = "analysis_001"
        analysis = self.store.load_analysis(task_id, run_id, analysis_id)
        metric_name = workflow.definition.calibration.objective.metric_name
        metric_value = analysis.metrics.get(metric_name, analysis.metrics.get("values", {}).get(metric_name))
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
        control = getattr(self.command_adapter, "control_dir", None)
        if control and (control / "cancel.request").exists():
            result.status = CalibrationWorkflowStatus.CANCELLED
            result.error_message = "The user cancelled the workflow."
            result.failure_kind = "cancelled"
            result.finished_at = utc_now()
        path = self._result_path(result.workflow_id)
        self.store._atomic_write(path, result.model_dump_json(indent=2))
        if control:
            from types import SimpleNamespace
            from .run_manager import RunManager
            RunManager(SimpleNamespace(workspace=control.parent.parent)).event(
                result.workflow_id, "workflow_state", status=result.status.value,
                iterations=len(result.iterations), case_prepared=result.case_prepared,
                plan_approved=result.plan_approved, failure_kind=result.failure_kind)
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
