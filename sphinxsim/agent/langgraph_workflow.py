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
from .replanning import (
    BoundedReplanningPolicy,
    ReplanAction,
    ReplanningContext,
    ReplanningDecision,
    ReplanningError,
    StructuredReplanner,
)


class CalibrationGraphState(TypedDict, total=False):
    """JSON-serializable state persisted between expensive workflow nodes."""

    user_prompt: str
    workflow_id: str
    workflow_result: dict[str, Any]
    decision: dict[str, Any]
    approval: dict[str, Any]
    failed_node: str
    failed_candidate: float
    replan_count: int
    replan_decision: dict[str, Any]
    replan_route: str


class CalibrationGraph:
    """Build explicit initialization, decision, and candidate execution nodes."""

    def __init__(
        self,
        workflow: DeterministicCalibrationWorkflow,
        *,
        require_approval: bool = False,
        replanner: StructuredReplanner | None = None,
        replanning_policy: BoundedReplanningPolicy | None = None,
    ) -> None:
        self.workflow = workflow
        self.require_approval = require_approval
        self.replanner = replanner
        self.replanning_policy = replanning_policy or BoundedReplanningPolicy()

    def draft_plan(self, state: CalibrationGraphState) -> CalibrationGraphState:
        if "workflow_result" in state:
            return {}
        result = self.workflow.draft_plan(
            state["user_prompt"],
            workflow_id=state["workflow_id"],
        )
        return {"workflow_result": result.model_dump(mode="json")}

    def prepare_case(self, state: CalibrationGraphState) -> CalibrationGraphState:
        result = self._result(state)
        try:
            result = self.workflow.prepare_case(result)
        except (OSError, ValueError) as exc:
            result = self.workflow.fail(result, str(exc))
        update: CalibrationGraphState = {
            "workflow_result": result.model_dump(mode="json")
        }
        if result.status == CalibrationWorkflowStatus.FAILED:
            update["failed_node"] = "prepare_case"
        return update

    def decide(self, state: CalibrationGraphState) -> CalibrationGraphState:
        result = self._result(state)
        if result.status != CalibrationWorkflowStatus.RUNNING:
            return {"workflow_result": result.model_dump(mode="json")}
        try:
            decision = self.workflow.propose_next(result)
            self.workflow.apply_decision(result, decision)
        except (OSError, ValueError) as exc:
            result = self.workflow.fail(result, str(exc))
            return {
                "workflow_result": result.model_dump(mode="json"),
                "failed_node": "decide",
            }
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
            return {
                "workflow_result": result.model_dump(mode="json"),
                "failed_node": "execute_candidate",
                "failed_candidate": float(candidate),
            }
        return {"workflow_result": result.model_dump(mode="json")}

    def replan(self, state: CalibrationGraphState) -> CalibrationGraphState:
        """Request and apply one bounded structured recovery decision."""
        result = self._result(state)
        if self.replanner is None or result.status != CalibrationWorkflowStatus.FAILED:
            return {"replan_route": ReplanAction.STOP.value}
        count = int(state.get("replan_count", 0))
        context = self._replanning_context(state, result, count + 1)
        if count >= self.replanning_policy.maximum_replans:
            decision = ReplanningDecision(
                action=ReplanAction.STOP,
                reason="The maximum number of replanning attempts was reached.",
            )
        else:
            try:
                decision = self.replanner.replan(context)
                decision = self.replanning_policy.validate(context, decision)
            except (ReplanningError, ValueError) as exc:
                decision = ReplanningDecision(
                    action=ReplanAction.STOP,
                    reason=f"Replanning was rejected: {exc}",
                )

        record = {
            "attempt": context.replan_attempt,
            "failed_node": context.failed_node,
            "error_message": context.error_message,
            "failed_candidate": context.failed_candidate,
            "decision": decision.model_dump(mode="json"),
        }
        self.workflow.record_replan(result, record)
        applied_count = count
        if decision.action != ReplanAction.STOP:
            applied_count += 1
        if decision.action == ReplanAction.RETRY_CANDIDATE:
            self.workflow.resume_after_failure(result)
        elif decision.action == ReplanAction.REVISE_PARAMETER_BOUNDS:
            assert decision.lower_bound is not None
            assert decision.upper_bound is not None
            self.workflow.revise_parameter_bounds(
                result,
                lower_bound=decision.lower_bound,
                upper_bound=decision.upper_bound,
            )
        return {
            "workflow_result": result.model_dump(mode="json"),
            "replan_count": applied_count,
            "replan_decision": decision.model_dump(mode="json"),
            "replan_route": decision.action.value,
        }

    def _replanning_context(
        self,
        state: CalibrationGraphState,
        result: CalibrationWorkflowResult,
        attempt: int,
    ) -> ReplanningContext:
        return ReplanningContext(
            workflow_id=result.workflow_id,
            failed_node=state.get("failed_node", "unknown"),
            error_message=result.error_message or "Unknown workflow failure.",
            failed_candidate=state.get("failed_candidate"),
            replan_attempt=attempt,
            maximum_replans=self.replanning_policy.maximum_replans,
            definition=result.definition,
            observations=[
                {
                    "parameter_value": item.parameter_value,
                    "metric_value": item.metric_value,
                }
                for item in result.iterations
            ],
        )

    def request_plan_approval(
        self,
        state: CalibrationGraphState,
    ) -> CalibrationGraphState:
        """Pause once before any case generation or simulation side effects."""
        result = self._result(state)
        calibration = result.definition.calibration
        parameter = calibration.parameter
        objective = calibration.objective
        response = interrupt(
            {
                "action": "approve_calibration_plan",
                "workflow_id": result.workflow_id,
                "case_name": result.definition.case_name,
                "parameter_name": parameter.input_field,
                "parameter_bounds": [parameter.lower_bound, parameter.upper_bound],
                "parameter_unit": parameter.unit,
                "target_metric": objective.metric_name,
                "target_value": objective.target_value,
                "target_tolerance": objective.tolerance,
                "maximum_simulations": calibration.maximum_iterations,
                "message": "Approve the complete calibration Plan.",
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
        if approved:
            result = self.workflow.approve_plan(result)
        else:
            result = self.workflow.cancel(
                result,
                "The user rejected the calibration Plan.",
            )
        return {
            "workflow_result": result.model_dump(mode="json"),
            "approval": approval,
        }

    def route_after_draft(self, state: CalibrationGraphState) -> str:
        result = self._result(state)
        if result.status != CalibrationWorkflowStatus.RUNNING:
            return END
        return "request_plan_approval" if self.require_approval else "prepare_case"

    def route_after_preparation(self, state: CalibrationGraphState) -> str:
        result = self._result(state)
        if result.status == CalibrationWorkflowStatus.FAILED and self.replanner:
            return "replan"
        if result.status != CalibrationWorkflowStatus.RUNNING:
            return END
        return "decide"

    def route_after_decision(self, state: CalibrationGraphState) -> str:
        result = self._result(state)
        if result.status == CalibrationWorkflowStatus.FAILED and self.replanner:
            return "replan"
        if result.status != CalibrationWorkflowStatus.RUNNING:
            return END
        decision = state.get("decision")
        if decision and decision.get("status") == CalibrationDecisionStatus.NEXT.value:
            return "execute_candidate"
        return END

    def route_after_candidate(self, state: CalibrationGraphState) -> str:
        result = self._result(state)
        if result.status == CalibrationWorkflowStatus.FAILED and self.replanner:
            return "replan"
        return "decide"

    def route_after_replan(self, state: CalibrationGraphState) -> str:
        route = state.get("replan_route")
        if route == ReplanAction.RETRY_CANDIDATE.value:
            return "execute_candidate"
        if route == ReplanAction.REVISE_PARAMETER_BOUNDS.value:
            return "decide"
        return END

    def route_after_plan_approval(self, state: CalibrationGraphState) -> str:
        result = self._result(state)
        if result.status == CalibrationWorkflowStatus.RUNNING:
            return "prepare_case"
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
        builder.add_node("draft_plan", self.draft_plan)
        builder.add_node("request_plan_approval", self.request_plan_approval)
        builder.add_node("prepare_case", self.prepare_case)
        builder.add_node("decide", self.decide)
        builder.add_node("execute_candidate", self.execute_candidate)
        builder.add_node("replan", self.replan)
        builder.add_edge(START, "draft_plan")
        builder.add_conditional_edges("draft_plan", self.route_after_draft)
        builder.add_conditional_edges(
            "request_plan_approval",
            self.route_after_plan_approval,
        )
        builder.add_conditional_edges("prepare_case", self.route_after_preparation)
        builder.add_conditional_edges("decide", self.route_after_decision)
        builder.add_conditional_edges("execute_candidate", self.route_after_candidate)
        builder.add_conditional_edges("replan", self.route_after_replan)
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
    replanner: StructuredReplanner | None = None,
    replanning_policy: BoundedReplanningPolicy | None = None,
) -> Any:
    return CalibrationGraph(
        workflow,
        require_approval=require_approval,
        replanner=replanner,
        replanning_policy=replanning_policy,
    ).build(
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
    replanner: StructuredReplanner | None = None,
    replanning_policy: BoundedReplanningPolicy | None = None,
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
            replanner=replanner,
            replanning_policy=replanning_policy,
        )
    finally:
        connection.close()
