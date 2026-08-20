"""Product-facing facade that composes the durable AgentCAE components."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from .calibration_workflow import (
    CalibrationWorkflowResult,
    CalibrationWorkflowStatus,
)
from .declarative_extractors import (
    DeclarativeExtractorCatalog,
    DeclarativeExtractorResult,
    DeclarativeExtractorSpec,
)
from .extractor_planner import (
    ExtractorPlannerClient,
    ExtractorPlanningContext,
    StructuredExtractorPlanner,
)
from .models import utc_now
from .runtime import AgentRuntime, AgentRuntimeSettings
from .service import AgentServiceError, AgentServiceStatus
from .store import AgentTaskStore


class AgentCAEIterationReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    iteration: int = Field(..., ge=1)
    parameter_value: float
    metric_value: float
    absolute_error: float = Field(..., ge=0.0)
    case_path: str
    run_id: str
    analysis_id: str


class AgentCAEBestResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    parameter_value: float
    metric_value: float
    absolute_error: float = Field(..., ge=0.0)


class AgentCAEReport(BaseModel):
    """Portable, auditable summary generated from persisted workflow truth."""

    model_config = ConfigDict(extra="forbid")

    report_version: str = "1.0"
    workflow_id: str
    user_prompt: str
    status: CalibrationWorkflowStatus
    succeeded: bool
    target_metric: str
    target_value: float
    target_tolerance: float = Field(..., ge=0.0)
    parameter_name: str
    parameter_unit: str
    best_result: AgentCAEBestResult | None = None
    iterations: list[AgentCAEIterationReport] = Field(default_factory=list)
    replan_history: list[dict[str, Any]] = Field(default_factory=list)
    baseline_case_path: str
    error_message: str | None = None
    started_at: datetime
    finished_at: datetime | None = None
    generated_at: datetime = Field(default_factory=utc_now)

    @classmethod
    def from_workflow(cls, result: CalibrationWorkflowResult) -> "AgentCAEReport":
        objective = result.definition.calibration.objective
        parameter = result.definition.calibration.parameter
        best = result.final_decision
        has_best = best is not None and all(
            value is not None
            for value in (
                best.best_parameter_value,
                best.best_metric_value,
                best.best_absolute_error,
            )
        )
        return cls(
            workflow_id=result.workflow_id,
            user_prompt=result.user_prompt,
            status=result.status,
            succeeded=result.succeeded,
            target_metric=objective.metric_name,
            target_value=objective.target_value,
            target_tolerance=objective.tolerance,
            parameter_name=parameter.input_field,
            parameter_unit=parameter.unit,
            best_result=(
                None
                if not has_best
                else AgentCAEBestResult(
                    parameter_value=best.best_parameter_value,
                    metric_value=best.best_metric_value,
                    absolute_error=best.best_absolute_error,
                )
            ),
            iterations=[
                AgentCAEIterationReport(
                    iteration=item.iteration,
                    parameter_value=item.parameter_value,
                    metric_value=item.metric_value,
                    absolute_error=item.absolute_error,
                    case_path=item.case_path,
                    run_id=item.run_id,
                    analysis_id=item.analysis_id,
                )
                for item in result.iterations
            ],
            replan_history=result.replan_history,
            baseline_case_path=result.baseline_case_path,
            error_message=result.error_message,
            started_at=result.started_at,
            finished_at=result.finished_at,
        )


class AgentCAE:
    """One stable API for lifecycle, safe analysis tools, and final reports."""

    def __init__(self, project_root: str | Path, workspace: str | Path) -> None:
        self.runtime = AgentRuntime(project_root, workspace)
        self.extractor_catalog_path = self.runtime.workspace / "extractors.json"

    def start(
        self,
        prompt: str,
        *,
        workflow_id: str | None = None,
        planner_provider: str = "mock",
        replanner_provider: str = "mock",
        generation_provider: str = "mock",
        require_approval: bool = True,
        command_timeout: float = 300.0,
    ) -> AgentServiceStatus:
        identifier = workflow_id or f"agent_{uuid4().hex[:12]}"
        settings = AgentRuntimeSettings(
            workflow_id=identifier,
            planner_provider=planner_provider,
            replanner_provider=replanner_provider,
            generation_provider=generation_provider,
            require_approval=require_approval,
            command_timeout=command_timeout,
        )
        self.runtime.save_settings(settings)
        return self.runtime.create_service(settings, starting=True).start(
            prompt,
            workflow_id=identifier,
        )

    def status(self, workflow_id: str) -> AgentServiceStatus:
        return self._service(workflow_id, execution_enabled=False).status(workflow_id)

    def approve(self, workflow_id: str) -> AgentServiceStatus:
        return self._service(workflow_id, execution_enabled=True).approve(workflow_id)

    def resume(self, workflow_id: str) -> AgentServiceStatus:
        return self._service(workflow_id, execution_enabled=True).resume(workflow_id)

    def cancel(self, workflow_id: str) -> AgentServiceStatus:
        return self._service(workflow_id, execution_enabled=False).cancel(workflow_id)

    def plan_extractor(
        self,
        request: str,
        context: ExtractorPlanningContext,
        client: ExtractorPlannerClient,
    ) -> DeclarativeExtractorSpec:
        spec = StructuredExtractorPlanner(client).plan(request, context)
        catalog = self.load_extractors()
        if any(item.extractor_id == spec.extractor_id for item in catalog.extractors):
            raise AgentServiceError(
                f"extractor already exists: {spec.extractor_id}"
            )
        updated = catalog.model_copy(
            update={"extractors": [*catalog.extractors, spec]}
        )
        DeclarativeExtractorCatalog.model_validate(updated).save(
            self.extractor_catalog_path
        )
        return spec

    def load_extractors(self) -> DeclarativeExtractorCatalog:
        if not self.extractor_catalog_path.exists():
            return DeclarativeExtractorCatalog()
        return DeclarativeExtractorCatalog.load(self.extractor_catalog_path)

    def analyze(
        self,
        extractor_id: str,
        config_path: str | Path,
        output_dir: str | Path,
    ) -> DeclarativeExtractorResult:
        result = self.load_extractors().build_registry().analyze(
            config_path,
            output_dir,
            {"metric": extractor_id},
        )
        return DeclarativeExtractorResult.model_validate(result)

    def report(self, workflow_id: str, *, save: bool = True) -> AgentCAEReport:
        self.runtime.load_settings(workflow_id)
        result_path = (
            self.runtime.store.root
            / "workflows"
            / workflow_id
            / "workflow.json"
        )
        try:
            result = CalibrationWorkflowResult.model_validate_json(
                result_path.read_text(encoding="utf-8")
            )
        except (OSError, ValueError) as exc:
            raise AgentServiceError(f"workflow not found: {workflow_id}") from exc
        report = AgentCAEReport.from_workflow(result)
        if save:
            destination = self.runtime.workspace / "reports" / f"{workflow_id}.json"
            AgentTaskStore._atomic_write(destination, report.model_dump_json(indent=2))
        return report

    def _service(self, workflow_id: str, *, execution_enabled: bool):
        settings = self.runtime.load_settings(workflow_id)
        return self.runtime.create_service(
            settings,
            starting=False,
            execution_enabled=execution_enabled,
        )
