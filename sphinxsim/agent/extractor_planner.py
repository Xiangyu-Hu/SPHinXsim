"""Structured planning boundary for safe declarative result extractors."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
import json
import os
from pathlib import Path
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from sphinxsim.llm.common import strip_code_fences
from sphinxsim.llm.nvidia_nim_llm import NvidiaNIMLLM
from sphinxsim.config import SimulationConfig

from .declarative_extractors import DeclarativeExtractorSpec


EXTRACTOR_PLANNER_SYSTEM_PROMPT = (
    "You define a data-only SPHinXsim VTP result extractor. Return exactly one "
    "JSON object matching the supplied schema. Use only a listed body and Position "
    "component. Never return Python code, expressions, imports, paths, or shell "
    "commands. Do not claim that analysis or simulation has run."
)


class ExtractorPlanningContext(BaseModel):
    """Allow-listed information that may be used to define one extractor."""

    model_config = ConfigDict(extra="forbid")

    body_names: list[str] = Field(..., min_length=1, max_length=32)
    spatial_dimension: int = Field(..., ge=1, le=3)
    source: str = "ascii_vtp_position"
    available_components: dict[str, int]
    available_reductions: list[str] = Field(
        default_factory=lambda: ["minimum", "maximum"]
    )
    maximum_outputs: int = Field(default=8, ge=1, le=8)

    @model_validator(mode="after")
    def validate_components(self) -> "ExtractorPlanningContext":
        expected = set(range(self.spatial_dimension))
        actual = set(self.available_components.values())
        if actual != expected:
            raise ValueError(
                "available_components must map exactly the spatial dimensions"
            )
        if len(actual) != len(self.available_components):
            raise ValueError("available_components indices must be unique")
        return self

    @classmethod
    def from_case(cls, config_path: str | Path) -> "ExtractorPlanningContext":
        """Validate a case and derive the minimum safe planning allow-list."""
        path = Path(config_path).resolve()
        config = SimulationConfig.model_validate_json(
            path.read_text(encoding="utf-8")
        )
        domain = config.geometries.system_domain
        dimension = (
            len(domain.lower_bound) if domain is not None else config._infer_spatial_dim()
        )
        if dimension is None:
            raise ExtractorPlanningError(
                "case does not provide enough information to infer spatial dimension"
            )
        body_names = sorted(
            {
                body.name
                for collection in (
                    config.fluid_bodies,
                    config.continuum_bodies,
                    config.solid_bodies,
                )
                for body in collection
            }
        )
        if not body_names:
            raise ExtractorPlanningError(
                "case does not define a simulation body for VTP extraction"
            )
        component_names = ("x", "y", "z")
        return cls(
            body_names=body_names,
            spatial_dimension=dimension,
            available_components={
                component_names[index]: index for index in range(dimension)
            },
        )


class ExtractorPlannerRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    system_prompt: str = Field(..., min_length=1)
    user_request: str = Field(..., min_length=1)
    context: ExtractorPlanningContext
    output_schema: dict[str, Any]


class ExtractorPlannerClient(Protocol):
    def complete_json(self, request: ExtractorPlannerRequest) -> dict[str, Any]: ...


class ExtractorPlanningError(ValueError):
    """Raised when a proposed extractor violates the safe contract."""


class MockExtractorPlannerClient:
    """Test double using the exact production request and validation path."""

    def __init__(self, response: dict[str, Any]) -> None:
        self.response = deepcopy(response)
        self.requests: list[ExtractorPlannerRequest] = []

    def complete_json(self, request: ExtractorPlannerRequest) -> dict[str, Any]:
        self.requests.append(request.model_copy(deep=True))
        return deepcopy(self.response)


@dataclass
class NvidiaExtractorPlannerClient:
    """NVIDIA implementation; callers control when bounded context is exported."""

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
    def from_environment(cls) -> "NvidiaExtractorPlannerClient":
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

    def complete_json(self, request: ExtractorPlannerRequest) -> dict[str, Any]:
        payload = {
            "user_request": request.user_request,
            "extractor_context": request.context.model_dump(mode="json"),
            "output_schema": request.output_schema,
        }
        content = self._llm._post_chat(
            messages=[
                {"role": "system", "content": request.system_prompt},
                {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
            ],
            temperature=0.0,
        )
        try:
            value = json.loads(strip_code_fences(content).strip())
        except json.JSONDecodeError as exc:
            raise ExtractorPlanningError(
                "NVIDIA extractor planner returned invalid JSON"
            ) from exc
        if not isinstance(value, dict):
            raise ExtractorPlanningError(
                "NVIDIA extractor planner must return one JSON object"
            )
        return value


class StructuredExtractorPlanner:
    """Validate an LLM proposal against schema and case-specific allow-lists."""

    def __init__(self, client: ExtractorPlannerClient) -> None:
        self.client = client

    def plan(
        self,
        user_request: str,
        context: ExtractorPlanningContext,
    ) -> DeclarativeExtractorSpec:
        if not isinstance(user_request, str) or not user_request.strip():
            raise ExtractorPlanningError("user_request must not be empty")
        request = ExtractorPlannerRequest(
            system_prompt=EXTRACTOR_PLANNER_SYSTEM_PROMPT,
            user_request=user_request.strip(),
            context=context,
            output_schema=DeclarativeExtractorSpec.model_json_schema(),
        )
        raw = self.client.complete_json(request)
        try:
            spec = DeclarativeExtractorSpec.model_validate(raw)
            self._validate_against_context(spec, context)
        except (ValidationError, ValueError) as exc:
            raise ExtractorPlanningError(
                f"extractor planner returned an invalid definition: {exc}"
            ) from exc
        return spec

    @staticmethod
    def _validate_against_context(
        spec: DeclarativeExtractorSpec,
        context: ExtractorPlanningContext,
    ) -> None:
        if spec.body_name not in context.body_names:
            raise ValueError("extractor body_name is not available")
        if spec.source != context.source:
            raise ValueError("extractor source is not available")
        if len(spec.outputs) > context.maximum_outputs:
            raise ValueError("extractor has too many outputs")
        allowed_components = set(context.available_components.values())
        allowed_reductions = set(context.available_reductions)
        for output in spec.outputs:
            if output.component_index not in allowed_components:
                raise ValueError("extractor Position component is not available")
            if output.reduction.value not in allowed_reductions:
                raise ValueError("extractor reduction is not available")
