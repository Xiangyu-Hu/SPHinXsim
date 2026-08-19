"""Durable LangGraph orchestration for deterministic calibration."""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
import sqlite3
from typing import Any, TypedDict

from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from .calibration import CalibrationDecisionStatus
from .calibration_workflow import (
    CalibrationWorkflowResult,
    CalibrationWorkflowStatus,
    DeterministicCalibrationWorkflow,
)


class CalibrationGraphState(TypedDict, total=False):
    """JSON-serializable state persisted between expensive workflow nodes."""

    user_prompt: str
    workflow_id: str
    workflow_result: dict[str, Any]
    decision: dict[str, Any]
    approval: dict[str, Any]


class CalibrationGraph:
    """Build explicit initialization, decision, and candidate execution nodes."""

    def __init__(
        self,
        workflow: DeterministicCalibrationWorkflow,
        *,
        require_approval: bool = False,
    ) -> None:
        self.workflow = workflow
        self.require_approval = require_approval

    def initialize(self, state: CalibrationGraphState) -> CalibrationGraphState:
        if "workflow_result" in state:
            return {}
        result = self.workflow.initialize(
            state["user_prompt"],
            workflow_id=state["workflow_id"],
        )
        return {"workflow_result": result.model_dump(mode="json")}

    def decide(self, state: CalibrationGraphState) -> CalibrationGraphState:
        result = self._result(state)
        if result.status != CalibrationWorkflowStatus.RUNNING:
            return {"workflow_result": result.model_dump(mode="json")}
        try:
            decision = self.workflow.propose_next(result)
            self.workflow.apply_decision(result, decision)
        except (OSError, ValueError) as exc:
            result = self.workflow.fail(result, str(exc))
            return {"workflow_result": result.model_dump(mode="json")}
        return {
            "workflow_result": result.model_dump(mode="json"),
            "decision": decision.model_dump(mode="json"),
        }

    def execute_candidate(self, state: CalibrationGraphState) -> CalibrationGraphState:
        result = self._result(state)
        decision = state.get("decision")
        candidate = None if decision is None else decision.get("candidate_value")
        if isinstance(candidate, bool) or not isinstance(candidate, (int, float)):
            result = self.workflow.fail(
                result,
                "LangGraph candidate execution requires a numeric candidate value.",
            )
            return {"workflow_result": result.model_dump(mode="json")}
        try:
            self.workflow.execute_candidate(result, float(candidate))
        except (OSError, ValueError) as exc:
            result = self.workflow.fail(result, str(exc))
        return {"workflow_result": result.model_dump(mode="json")}

    def request_approval(self, state: CalibrationGraphState) -> CalibrationGraphState:
        """Pause before an expensive simulation and apply the user's decision."""
        result = self._result(state)
        decision = state.get("decision") or {}
        response = interrupt(
            {
                "action": "run_calibration_candidate",
                "workflow_id": result.workflow_id,
                "iteration": len(result.iterations) + 1,
                "candidate_value": decision.get("candidate_value"),
                "parameter_unit": result.definition.calibration.parameter.unit,
                "target_metric": result.definition.calibration.objective.metric_name,
                "target_value": result.definition.calibration.objective.target_value,
                "message": "Approve the next real SPHinXsim simulation.",
            }
        )
        approved = response is True or (
            isinstance(response, dict)
            and (
                response.get("approved") is True
                or response.get("action") == "approve"
            )
        )
        approval = {
            "approved": approved,
            "response": response if isinstance(response, dict) else {"value": response},
        }
        if not approved:
            result = self.workflow.cancel(
                result,
                "The user rejected the next SPHinXsim simulation.",
            )
        return {
            "workflow_result": result.model_dump(mode="json"),
            "approval": approval,
        }

    def route_after_decision(self, state: CalibrationGraphState) -> str:
        result = self._result(state)
        if result.status != CalibrationWorkflowStatus.RUNNING:
            return END
        decision = state.get("decision")
        if decision and decision.get("status") == CalibrationDecisionStatus.NEXT.value:
            return "request_approval" if self.require_approval else "execute_candidate"
        return END

    def route_after_approval(self, state: CalibrationGraphState) -> str:
        result = self._result(state)
        if result.status == CalibrationWorkflowStatus.RUNNING:
            return "execute_candidate"
        return END

    @staticmethod
    def _result(state: CalibrationGraphState) -> CalibrationWorkflowResult:
        raw_result = state.get("workflow_result")
        if raw_result is None:
            raise ValueError("LangGraph state does not contain workflow_result")
        return CalibrationWorkflowResult.model_validate(raw_result)

    def build(
        self,
        *,
        checkpointer: Any = None,
        interrupt_after: list[str] | None = None,
    ) -> Any:
        builder = StateGraph(CalibrationGraphState)
        builder.add_node("initialize", self.initialize)
        builder.add_node("decide", self.decide)
        builder.add_node("request_approval", self.request_approval)
        builder.add_node("execute_candidate", self.execute_candidate)
        builder.add_edge(START, "initialize")
        builder.add_edge("initialize", "decide")
        builder.add_conditional_edges("decide", self.route_after_decision)
        builder.add_conditional_edges("request_approval", self.route_after_approval)
        builder.add_edge("execute_candidate", "decide")
        return builder.compile(
            checkpointer=checkpointer,
            interrupt_after=interrupt_after,
        )


def build_calibration_graph(
    workflow: DeterministicCalibrationWorkflow,
    *,
    checkpointer: Any = None,
    interrupt_after: list[str] | None = None,
    require_approval: bool = False,
) -> Any:
    return CalibrationGraph(workflow, require_approval=require_approval).build(
        checkpointer=checkpointer,
        interrupt_after=interrupt_after,
    )


@contextmanager
def open_sqlite_calibration_graph(
    workflow: DeterministicCalibrationWorkflow,
    database_path: str | Path,
    *,
    interrupt_after: list[str] | None = None,
    require_approval: bool = False,
):
    """Open a process-restart-safe local graph backed by SQLite checkpoints."""
    path = Path(database_path).resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path, check_same_thread=False)
    serializer = JsonPlusSerializer(allowed_msgpack_modules=[])
    checkpointer = SqliteSaver(connection, serde=serializer)
    try:
        yield build_calibration_graph(
            workflow,
            checkpointer=checkpointer,
            interrupt_after=interrupt_after,
            require_approval=require_approval,
        )
    finally:
        connection.close()
