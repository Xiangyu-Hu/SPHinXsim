"""Bounded structured decisions for recovering failed Agent workflows."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field, model_validator

from sphinxsim.llm import get_llm_settings
from .calibration_workflow import CalibrationWorkflowDefinition
from sphinxsim.llm.nvidia_nim_llm import NvidiaNIMLLM
from sphinxsim.llm.structured_provider import (
    ExistingLLMStructuredBackend,
    StructuredJSONBackend,
    StructuredProviderError,
)


REPLANNING_SYSTEM_PROMPT = (
    "You are the failure-replanning component of SPHinXsim AgentCAE. Return "
    "exactly one JSON object matching the supplied schema. You may retry the "
    "same failed candidate, revise only the parameter bounds, or stop. Never "
    "change the objective metric, target, tolerance, units, tool names, case "
    "geometry, or solver output. Never return code or shell commands."
)


class ReplanAction(str, Enum):
    RETRY_CANDIDATE = "retry_candidate"
    REVISE_PARAMETER_BOUNDS = "revise_parameter_bounds"
    STOP = "stop"


class ReplanningContext(BaseModel):
    """Persistable evidence available to one replanning decision."""

    model_config = ConfigDict(extra="forbid")

    workflow_id: str
    failed_node: str
    error_message: str
    failed_candidate: float | None = None
    replan_attempt: int = Field(..., ge=1)
    maximum_replans: int = Field(..., ge=1)
    definition: CalibrationWorkflowDefinition
    observations: list[dict[str, Any]] = Field(default_factory=list)


class ReplanningDecision(BaseModel):
    """Allow-listed change proposed by a mock or LLM replanner."""

    model_config = ConfigDict(extra="forbid")

    action: ReplanAction
    reason: str = Field(..., min_length=1)
    lower_bound: float | None = None
    upper_bound: float | None = None

    @model_validator(mode="after")
    def validate_action_fields(self) -> "ReplanningDecision":
        revises_bounds = self.action == ReplanAction.REVISE_PARAMETER_BOUNDS
        if revises_bounds and (self.lower_bound is None or self.upper_bound is None):
            raise ValueError("bound revision requires lower_bound and upper_bound")
        if not revises_bounds and (
            self.lower_bound is not None or self.upper_bound is not None
        ):
            raise ValueError("only bound revision may provide bounds")
        if revises_bounds and self.lower_bound >= self.upper_bound:
            raise ValueError("lower_bound must be smaller than upper_bound")
        return self


class ReplanningRequest(BaseModel):
    """Provider-neutral request for one structured replanning call."""

    model_config = ConfigDict(extra="forbid")

    system_prompt: str
    context: ReplanningContext
    output_schema: dict[str, Any]


class ReplanningClient(Protocol):
    def complete_json(self, request: ReplanningRequest) -> dict[str, Any]: ...


class ReplanningError(ValueError):
    """Raised when a replanning response is invalid or unsafe."""


class MockReplanningClient:
    """Return queued responses through the same contract used by a remote LLM."""

    def __init__(self, responses: list[dict[str, Any]]) -> None:
        if not responses:
            raise ValueError("mock replanning responses must not be empty")
        self.responses = deepcopy(responses)
        self.requests: list[ReplanningRequest] = []

    def complete_json(self, request: ReplanningRequest) -> dict[str, Any]:
        self.requests.append(request.model_copy(deep=True))
        index = min(len(self.requests) - 1, len(self.responses) - 1)
        return deepcopy(self.responses[index])


class ProviderReplanningClient:
    """Use any configured structured backend through one replanning contract."""

    def __init__(self, backend: StructuredJSONBackend) -> None:
        self.backend = backend

    def complete_json(self, request: ReplanningRequest) -> dict[str, Any]:
        payload = {
            "failure_context": request.context.model_dump(mode="json"),
            "output_schema": request.output_schema,
        }
        try:
            return self.backend.complete_json(
                system_prompt=request.system_prompt,
                payload=payload,
            )
        except StructuredProviderError as exc:
            raise ReplanningError(str(exc)) from exc


@dataclass
class NvidiaReplanningClient:
    """NVIDIA NIM client for bounded structured replanning decisions."""

    api_key: str | None
    base_url: str = "https://integrate.api.nvidia.com/v1"
    model: str = "z-ai/glm-5.2"
    fallback_models: tuple[str, ...] = ()
    timeout: float = 120.0
    _llm: NvidiaNIMLLM = field(init=False, repr=False)
    _client: ProviderReplanningClient = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self._llm = NvidiaNIMLLM(
            api_key=self.api_key,
            base_url=self.base_url,
            model=self.model,
            fallback_models=self.fallback_models,
            timeout=self.timeout,
        )
        self._client = ProviderReplanningClient(
            ExistingLLMStructuredBackend("nvidia_nim", llm=self._llm)
        )

    @classmethod
    def from_environment(cls) -> "NvidiaReplanningClient":
        settings = get_llm_settings("nvidia_nim")
        return cls(
            api_key=settings["api_key"],
            base_url=settings["base_url"],
            model=settings["model"],
            fallback_models=settings["fallback_models"],
            timeout=settings["timeout"],
        )

    def complete_json(self, request: ReplanningRequest) -> dict[str, Any]:
        return self._client.complete_json(request)


class StructuredReplanner:
    """Validate every replanning response before orchestration can apply it."""

    def __init__(self, client: ReplanningClient) -> None:
        self.client = client

    def replan(self, context: ReplanningContext) -> ReplanningDecision:
        request = ReplanningRequest(
            system_prompt=REPLANNING_SYSTEM_PROMPT,
            context=context,
            output_schema=ReplanningDecision.model_json_schema(),
        )
        raw = self.client.complete_json(request)
        if not isinstance(raw, dict):
            raise ReplanningError("structured replanner must return a JSON object")
        try:
            return ReplanningDecision.model_validate(raw)
        except ValueError as exc:
            raise ReplanningError(
                f"structured replanner returned an invalid decision: {exc}"
            ) from exc


class BoundedReplanningPolicy:
    """Enforce retry counts and safe parameter-bound revisions."""

    def __init__(self, *, maximum_replans: int = 2) -> None:
        if maximum_replans < 1:
            raise ValueError("maximum_replans must be at least one")
        self.maximum_replans = maximum_replans

    def validate(
        self,
        context: ReplanningContext,
        decision: ReplanningDecision,
    ) -> ReplanningDecision:
        if context.maximum_replans != self.maximum_replans:
            raise ReplanningError("replanning context uses a different retry limit")
        if context.replan_attempt > self.maximum_replans:
            return ReplanningDecision(
                action=ReplanAction.STOP,
                reason="The maximum number of replanning attempts was reached.",
            )
        if (
            decision.action == ReplanAction.RETRY_CANDIDATE
            and context.failed_candidate is None
        ):
            raise ReplanningError("retry_candidate requires a failed candidate")
        if decision.action == ReplanAction.REVISE_PARAMETER_BOUNDS:
            self._validate_bounds(context, decision)
        return decision

    @staticmethod
    def _validate_bounds(
        context: ReplanningContext,
        decision: ReplanningDecision,
    ) -> None:
        assert decision.lower_bound is not None
        assert decision.upper_bound is not None
        parameter = context.definition.calibration.parameter
        if parameter.tool_name == "set_friction_angle" and not (
            0.0 <= decision.lower_bound < decision.upper_bound < 90.0
        ):
            raise ReplanningError(
                "friction-angle bounds must satisfy 0 <= lower < upper < 90 degrees"
            )
        observed = [float(item["parameter_value"]) for item in context.observations]
        if observed and (
            min(observed) < decision.lower_bound
            or max(observed) > decision.upper_bound
        ):
            raise ReplanningError("revised bounds cannot exclude existing observations")
