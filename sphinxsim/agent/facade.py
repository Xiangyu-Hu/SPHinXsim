"""Product-facing facade that composes the durable AgentCAE components."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field
from sphinxsim.llm import normalize_llm_provider

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
from .analysis_plan import AnalysisPlan
from .runtime import AgentRuntime, AgentRuntimeSettings
from .service import CalibrationPlanSummary, AgentServiceError, AgentServiceStatus
from .store import AgentTaskStore


class AgentCAEIterationReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    iteration: int = Field(..., ge=1)
    parameter_value: float
    task_id: str
    metric_name: str
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
    plan_summary: CalibrationPlanSummary
    plan_approved: bool
    approved_at: datetime | None = None
    target_metric: str
    target_value: float
    target_tolerance: float = Field(..., ge=0.0)
    parameter_name: str
    parameter_unit: str
    best_result: AgentCAEBestResult | None = None
    iterations: list[AgentCAEIterationReport] = Field(default_factory=list)
    replan_history: list[dict[str, Any]] = Field(default_factory=list)
    analysis_plan: AnalysisPlan | None = None
    execution_attempts: list[dict[str, Any]] = Field(default_factory=list)
    failure_kind: str | None = None
    preview_status: str
    preview_fingerprint: str | None = None
    preview_attempts: list[dict[str, Any]] = Field(default_factory=list)
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
            plan_summary=CalibrationPlanSummary.from_result(result),
            plan_approved=result.plan_approved,
            approved_at=result.approved_at,
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
                    task_id=item.task_id,
                    metric_name=item.metric_name,
                    metric_value=item.metric_value,
                    absolute_error=item.absolute_error,
                    case_path=item.case_path,
                    run_id=item.run_id,
                    analysis_id=item.analysis_id,
                )
                for item in result.iterations
            ],
            replan_history=result.replan_history,
            analysis_plan=result.definition.analysis_plan,
            failure_kind=result.failure_kind,
            preview_status=result.preview_status,
            preview_fingerprint=result.preview_fingerprint,
            preview_attempts=result.preview_attempts,
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
        planner_provider: str | None = None,
        replanner_provider: str | None = None,
        generation_provider: str | None = None,
        preview_provider: str | None = None,
        require_approval: bool = True,
        command_timeout: float = 300.0,
        analysis_plan: AnalysisPlan | None = None,
    ) -> AgentServiceStatus:
        identifier = workflow_id or f"agent_{uuid4().hex[:12]}"
        configured_provider = normalize_llm_provider()
        settings = AgentRuntimeSettings(
            workflow_id=identifier,
            planner_provider=planner_provider or configured_provider,
            replanner_provider=replanner_provider or configured_provider,
            generation_provider=generation_provider or configured_provider,
            preview_provider=preview_provider or configured_provider,
            require_approval=require_approval,
            command_timeout=command_timeout,
            analysis_plan=analysis_plan,
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
        record = self.run_manager.cancel(workflow_id)
        if record.get("state") == "cancelling":
            status = self.status(workflow_id)
            return status.model_copy(update={"phase": "cancelling"})
        return self._service(workflow_id, execution_enabled=False).cancel(workflow_id)

    @property
    def run_manager(self):
        from .run_manager import RunManager
        return RunManager(self.runtime)

    def submit(self, workflow_id: str, *, action: str = "approve") -> dict:
        return self.run_manager.submit(workflow_id, action=action)

    def execution_status(self, workflow_id: str) -> dict:
        return self.run_manager.status(workflow_id)

    def events(self, workflow_id: str, *, after: int = 0) -> list[dict]:
        return self.run_manager.events(workflow_id, after)

    def live_results(self, workflow_id: str) -> list[dict]:
        from sphinxsim.results import ResultCatalog
        result = self.report(workflow_id, save=False)
        catalog = getattr(self, "_result_catalog", None)
        if catalog is None:
            self._result_catalog = catalog = ResultCatalog()
        runs = [r for r in self.run_manager.attempts(workflow_id) if r.get("operation") == "run" and r.get("output_dir")]
        if not runs:
            return []
        frames = catalog.latest(runs[-1]["output_dir"], [result.plan_summary.body_name])
        return [dict(body_name=f.body_name, path=str(f.path), time=f.time, version=f.version)
                for f in frames.values()]

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
        from sphinxsim.runtime.client import MCPClient
        from .analysis_plan import AnalysisPlan
        definitions = self.load_extractors().extractors
        selected = next((item for item in definitions if item.extractor_id == extractor_id), None)
        if selected is None:
            raise ValueError(f"unknown analysis extractor: {extractor_id}")
        plan = AnalysisPlan(kind="declarative", metric_name=selected.outputs[0].output_name, declarative=selected)
        client = MCPClient(self.runtime.project_root)
        response = client.call("analyze_result", {"config_file": client.relative(config_path),
            "output_dir": client.relative(output_dir), "analysis_plan": plan.model_dump(mode="json")})
        return DeclarativeExtractorResult.model_validate(response["metrics"])

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
        report.execution_attempts = self.run_manager.attempts(workflow_id)
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
