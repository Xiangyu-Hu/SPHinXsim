"""Persistent application service for the AgentCAE workflow lifecycle."""

from __future__ import annotations

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


class AgentServiceStatus(BaseModel):
    """Stable JSON-friendly status returned by the service and CLI."""

    model_config = ConfigDict(extra="forbid")

    workflow_id: str
    status: CalibrationWorkflowStatus
    awaiting_approval: bool
    approval_request: dict[str, Any] | None = None
    next_nodes: list[str] = Field(default_factory=list)
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
    ) -> None:
        self.store = store
        self.database_path = Path(database_path).resolve()
        self.workflow = DeterministicCalibrationWorkflow(
            store,
            planner,
            command_adapter,
            command_timeout=command_timeout,
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
        return AgentServiceStatus(
            workflow_id=result.workflow_id,
            status=result.status,
            awaiting_approval=approval_request is not None,
            approval_request=approval_request,
            next_nodes=list(snapshot.next),
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
