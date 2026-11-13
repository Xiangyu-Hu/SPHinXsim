"""Persistent application service for the AgentCAE workflow lifecycle."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from langgraph.types import Command
from pydantic import BaseModel, ConfigDict, Field

from .calibration_workflow import (
    CalibrationPlanner,
    CalibrationWorkflowResult,
    CalibrationWorkflowStatus,
    DeterministicCalibrationWorkflow,
)
from .langgraph_workflow import open_sqlite_calibration_graph
from .replanning import BoundedReplanningPolicy, StructuredReplanner
from .store import AgentTaskStore


class AgentServiceError(RuntimeError):
    """Raised when a lifecycle command is invalid for persisted Agent state."""


class CalibrationPlanSummary(BaseModel):
    """Stable user-facing summary of the approved scientific contract."""

    model_config = ConfigDict(extra="forbid")

    case_name: str
    generation_description: str
    body_name: str
    parameter_name: str
    parameter_bounds: tuple[float, float]
    parameter_unit: str
    target_metric: str
    target_value: float
    target_tolerance: float = Field(..., ge=0.0)
    target_unit: str
    maximum_simulations: int = Field(..., ge=1)

    @classmethod
    def from_result(
        cls,
        result: CalibrationWorkflowResult,
    ) -> "CalibrationPlanSummary":
        definition = result.definition
        calibration = definition.calibration
        parameter = calibration.parameter
        objective = calibration.objective
        return cls(
            case_name=definition.case_name,
            generation_description=definition.generation_description,
            body_name=definition.body_name,
            parameter_name=parameter.input_field,
            parameter_bounds=(parameter.lower_bound, parameter.upper_bound),
            parameter_unit=parameter.unit,
            target_metric=objective.metric_name,
            target_value=objective.target_value,
            target_tolerance=objective.tolerance,
            target_unit=objective.unit,
            maximum_simulations=calibration.maximum_iterations,
        )


class AgentServiceStatus(BaseModel):
    """Stable JSON-friendly status returned by the service and CLI."""

    model_config = ConfigDict(extra="forbid")

    workflow_id: str
    status: CalibrationWorkflowStatus
    phase: str
    plan_summary: CalibrationPlanSummary
    plan_approved: bool
    approved_at: datetime | None = None
    case_prepared: bool
    preview_status: str
    awaiting_approval: bool
    approval_request: dict[str, Any] | None = None
    next_nodes: list[str] = Field(default_factory=list)
    current_node: str | None = None
    current_candidate_value: float | None = None
    iteration_count: int = Field(..., ge=0)
    replan_count: int = Field(..., ge=0)
    target_metric: str
    target_value: float
    best_parameter_value: float | None = None
    best_metric_value: float | None = None
    best_absolute_error: float | None = None
    error_message: str | None = None


class AgentCAEAgentService:
    """Start and resume one durable Agent workflow through explicit commands."""

    def __init__(
        self,
        store: AgentTaskStore,
        database_path: str | Path,
        planner: CalibrationPlanner,
        command_adapter,
        *,
        command_timeout: float | None = None,
        require_approval: bool = True,
        replanner: StructuredReplanner | None = None,
        replanning_policy: BoundedReplanningPolicy | None = None,
        interrupt_after: list[str] | None = None,
        preview_reviewer=None,
    ) -> None:
        self.store = store
        self.database_path = Path(database_path).resolve()
        self.workflow = DeterministicCalibrationWorkflow(
            store,
            planner,
            command_adapter,
            command_timeout=command_timeout,
            preview_reviewer=preview_reviewer,
        )
        self.require_approval = require_approval
        self.replanner = replanner
        self.replanning_policy = replanning_policy
        self.interrupt_after = interrupt_after

    def start(
        self,
        user_prompt: str,
        *,
        workflow_id: str | None = None,
    ) -> AgentServiceStatus:
        identifier = workflow_id or f"calibration_{uuid4().hex[:12]}"
        with self._open_graph() as graph:
            graph.invoke(
                {
                    "user_prompt": user_prompt,
                    "workflow_id": identifier,
                },
                self._config(identifier),
            )
            return self._status_from_graph(identifier, graph)

    def approve(self, workflow_id: str) -> AgentServiceStatus:
        with self._open_graph() as graph:
            config = self._config(workflow_id)
            snapshot = graph.get_state(config)
            if not snapshot.interrupts:
                raise AgentServiceError("workflow is not awaiting approval")
            graph.invoke(Command(resume={"action": "approve"}), config)
            return self._status_from_graph(workflow_id, graph)

    def resume(self, workflow_id: str) -> AgentServiceStatus:
        with self._open_graph() as graph:
            config = self._config(workflow_id)
            result = self._load(workflow_id)
            snapshot = graph.get_state(config)
            if snapshot.interrupts:
                raise AgentServiceError(
                    "workflow is awaiting approval; use approve or cancel"
                )
            if result.status != CalibrationWorkflowStatus.RUNNING or not snapshot.next:
                return self._status(result, snapshot)
            graph.invoke(None, config)
            return self._status_from_graph(workflow_id, graph)

    def cancel(self, workflow_id: str) -> AgentServiceStatus:
        with self._open_graph() as graph:
            config = self._config(workflow_id)
            snapshot = graph.get_state(config)
            result = self._load(workflow_id)
            if snapshot.interrupts:
                graph.invoke(Command(resume={"action": "reject"}), config)
            elif result.status == CalibrationWorkflowStatus.RUNNING:
                result = self.workflow.cancel(result, "The user cancelled the workflow.")
                graph.update_state(
                    config,
                    {"workflow_result": result.model_dump(mode="json")},
                )
            return self._status_from_graph(workflow_id, graph)

    def status(self, workflow_id: str) -> AgentServiceStatus:
        with self._open_graph() as graph:
            return self._status_from_graph(workflow_id, graph)

    def _open_graph(self):
        return open_sqlite_calibration_graph(
            self.workflow,
            self.database_path,
            interrupt_after=self.interrupt_after,
            require_approval=self.require_approval,
            replanner=self.replanner,
            replanning_policy=self.replanning_policy,
        )

    @staticmethod
    def _config(workflow_id: str) -> dict[str, dict[str, str]]:
        if not workflow_id:
            raise AgentServiceError("workflow_id is required")
        return {"configurable": {"thread_id": workflow_id}}

    def _status_from_graph(self, workflow_id: str, graph) -> AgentServiceStatus:
        result = self._load(workflow_id)
        return self._status(result, graph.get_state(self._config(workflow_id)))

    @staticmethod
    def _status(result: CalibrationWorkflowResult, snapshot) -> AgentServiceStatus:
        approval_request = None
        if snapshot.interrupts:
            value = snapshot.interrupts[0].value
            if isinstance(value, dict):
                approval_request = value
        final = result.final_decision
        next_nodes = list(snapshot.next)
        graph_values = getattr(snapshot, "values", {}) or {}
        decision = graph_values.get("decision")
        current_candidate = None
        if isinstance(decision, dict):
            value = decision.get("candidate_value")
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                current_candidate = float(value)
        if approval_request is not None:
            phase = "awaiting_plan_approval"
        elif result.status != CalibrationWorkflowStatus.RUNNING:
            phase = result.status.value
        elif not result.case_prepared:
            phase = "plan_drafted"
        else:
            phase = "calibrating"
        return AgentServiceStatus(
            workflow_id=result.workflow_id,
            status=result.status,
            phase=phase,
            plan_summary=CalibrationPlanSummary.from_result(result),
            plan_approved=result.plan_approved,
            approved_at=result.approved_at,
            case_prepared=result.case_prepared,
            preview_status=result.preview_status,
            awaiting_approval=approval_request is not None,
            approval_request=approval_request,
            next_nodes=next_nodes,
            current_node=next_nodes[0] if next_nodes else None,
            current_candidate_value=current_candidate,
            iteration_count=len(result.iterations),
            replan_count=len(result.replan_history),
            target_metric=result.definition.calibration.objective.metric_name,
            target_value=result.definition.calibration.objective.target_value,
            best_parameter_value=(
                None if final is None else final.best_parameter_value
            ),
            best_metric_value=None if final is None else final.best_metric_value,
            best_absolute_error=None if final is None else final.best_absolute_error,
            error_message=result.error_message,
        )

    def _load(self, workflow_id: str) -> CalibrationWorkflowResult:
        try:
            return self.workflow.load(workflow_id)
        except (OSError, ValueError) as exc:
            raise AgentServiceError(f"workflow not found: {workflow_id}") from exc
