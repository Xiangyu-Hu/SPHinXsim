"""Structured-output calibration planner shared by mock and remote LLM clients."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
import json
import os
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .calibration import CalibrationError
from .calibration_workflow import CalibrationWorkflowDefinition
from .planner_context import PlannerContext, PlannerContextBuilder
from .tools import ToolRegistry
from sphinxsim.llm.common import strip_code_fences
from sphinxsim.llm.nvidia_nim_llm import NvidiaNIMLLM


PLANNER_SYSTEM_PROMPT = (
    "You are the planning component of SPHinXsim AgentCAE. Return exactly one "
    "JSON object matching the supplied output schema. Use only listed tools and "
    "capabilities. Do not invent files, tools, metrics, units, or solver results. "
    "Planning is non-executing: never claim that a simulation has run. Interpret "
    "runout_extension as final_front minus initial_front."
)


class StructuredPlannerRequest(BaseModel):
    """Provider-neutral request contract for one planning call."""

    model_config = ConfigDict(extra="forbid")

    system_prompt: str = Field(..., min_length=1)
    user_prompt: str = Field(..., min_length=1)
    context: PlannerContext
    output_schema: dict[str, Any]


class StructuredPlannerClient(Protocol):
    """Small boundary implemented by mock and NVIDIA clients."""

    def complete_json(self, request: StructuredPlannerRequest) -> dict[str, Any]: ...


class StructuredPlanningError(ValueError):
    """Raised when provider output violates the planner contract."""


class MockStructuredPlannerClient:
    """Return a predefined payload through the production planner boundary."""

    def __init__(self, response: dict[str, Any]) -> None:
        self.response = deepcopy(response)
        self.requests: list[StructuredPlannerRequest] = []

    def complete_json(self, request: StructuredPlannerRequest) -> dict[str, Any]:
        self.requests.append(request.model_copy(deep=True))
        return deepcopy(self.response)


@dataclass
class NvidiaStructuredPlannerClient:
    """NVIDIA NIM implementation of the provider-neutral planner client."""

    api_key: str | None
    base_url: str = "https://integrate.api.nvidia.com/v1"
    model: str = "z-ai/glm-5.2"
    fallback_models: tuple[str, ...] = ()
    timeout: float = 120.0
    _llm: NvidiaNIMLLM = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self._llm = NvidiaNIMLLM(
            api_key=self.api_key,
            base_url=self.base_url,
            model=self.model,
            fallback_models=self.fallback_models,
            timeout=self.timeout,
        )

    @classmethod
    def from_environment(cls) -> "NvidiaStructuredPlannerClient":
        fallback = tuple(
            item.strip()
            for item in os.getenv("NVIDIA_NIM_FALLBACK_MODELS", "").split(",")
            if item.strip()
        )
        return cls(
            api_key=os.getenv("NVIDIA_NIM_API_KEY", os.getenv("NVIDIA_API_KEY")),
            base_url=os.getenv(
                "NVIDIA_NIM_BASE_URL",
                "https://integrate.api.nvidia.com/v1",
            ),
            model=os.getenv("NVIDIA_NIM_MODEL", os.getenv("NVIDIA_NIM", "z-ai/glm-5.2")),
            fallback_models=fallback,
            timeout=float(os.getenv("NVIDIA_NIM_TIMEOUT", "120")),
        )

    def complete_json(self, request: StructuredPlannerRequest) -> dict[str, Any]:
        user_payload = {
            "user_prompt": request.user_prompt,
            "planner_context": request.context.model_dump(mode="json"),
            "output_schema": request.output_schema,
        }
        content = self._llm._post_chat(
            messages=[
                {"role": "system", "content": request.system_prompt},
                {
                    "role": "user",
                    "content": json.dumps(user_payload, ensure_ascii=False),
                },
            ],
            temperature=0.0,
        )
        try:
            value = json.loads(strip_code_fences(content).strip())
        except json.JSONDecodeError as exc:
            raise StructuredPlanningError(
                "NVIDIA planner returned invalid JSON"
            ) from exc
        if not isinstance(value, dict):
            raise StructuredPlanningError(
                "NVIDIA planner must return exactly one JSON object"
            )
        return value


class StructuredCalibrationPlanner:
    """Ground a request, call one client, and validate its complete definition."""

    def __init__(
        self,
        context_builder: PlannerContextBuilder,
        client: StructuredPlannerClient,
        tool_registry: ToolRegistry,
    ) -> None:
        self.context_builder = context_builder
        self.client = client
        self.tool_registry = tool_registry

    def plan(self, user_prompt: str) -> CalibrationWorkflowDefinition:
        context = self.context_builder.build(user_prompt)
        request = StructuredPlannerRequest(
            system_prompt=PLANNER_SYSTEM_PROMPT,
            user_prompt=context.user_prompt,
            context=context,
            output_schema=CalibrationWorkflowDefinition.model_json_schema(),
        )
        raw = self.client.complete_json(request)
        if not isinstance(raw, dict):
            raise StructuredPlanningError("structured planner must return a JSON object")
        try:
            definition = CalibrationWorkflowDefinition.model_validate(raw)
            definition.validate_tools(self.tool_registry)
            self._validate_capabilities(definition, context)
        except (ValidationError, CalibrationError, ValueError) as exc:
            raise StructuredPlanningError(
                f"structured planner returned an invalid workflow definition: {exc}"
            ) from exc
        return definition

    @staticmethod
    def _validate_capabilities(
        definition: CalibrationWorkflowDefinition,
        context: PlannerContext,
    ) -> None:
        parameter = definition.calibration.parameter
        friction = context.parameters.get("friction_angle")
        if parameter.tool_name == "set_friction_angle":
            if friction is None or parameter.unit not in friction["input_units"]:
                raise ValueError("friction-angle unit is not supported")
            allowed = friction["input_range"]
            if parameter.lower_bound < allowed["minimum"]:
                raise ValueError("friction-angle lower bound is not supported")
            if parameter.upper_bound >= allowed["maximum_exclusive"]:
                raise ValueError("friction-angle upper bound is not supported")

        objective = definition.calibration.objective
        runout = context.metrics.get("runout")
        if objective.tool_name == "analyze_runout":
            if runout is None or objective.metric_name not in runout["outputs"]:
                raise ValueError("runout objective metric is not supported")
            if objective.unit != runout["unit"]:
                raise ValueError("runout objective unit is not supported")
