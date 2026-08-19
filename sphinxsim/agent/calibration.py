"""Typed calibration objectives and deterministic candidate-selection policies."""

from __future__ import annotations

import math
from enum import Enum
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .tools import ToolRegistry, ToolRegistryError


class CalibrationError(ValueError):
    """Raised when a calibration contract or observation set is invalid."""


class Monotonicity(str, Enum):
    INCREASING = "increasing"
    DECREASING = "decreasing"


class CalibrationDecisionStatus(str, Enum):
    NEXT = "next"
    CONVERGED = "converged"
    EXHAUSTED = "exhausted"


class ParameterSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tool_name: str = Field(..., min_length=1)
    input_field: str = Field(..., min_length=1)
    lower_bound: float
    upper_bound: float
    unit: str = Field(..., min_length=1)

    @model_validator(mode="after")
    def validate_bounds(self) -> "ParameterSpec":
        if not math.isfinite(self.lower_bound) or not math.isfinite(self.upper_bound):
            raise ValueError("parameter bounds must be finite")
        if self.lower_bound >= self.upper_bound:
            raise ValueError("lower_bound must be smaller than upper_bound")
        return self


class ObjectiveSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tool_name: str = Field(..., min_length=1)
    metric_name: str = Field(..., min_length=1)
    target_value: float
    tolerance: float = Field(..., gt=0.0)
    unit: str = Field(..., min_length=1)

    @model_validator(mode="after")
    def validate_numbers(self) -> "ObjectiveSpec":
        if not math.isfinite(self.target_value) or not math.isfinite(self.tolerance):
            raise ValueError("target_value and tolerance must be finite")
        return self


class CalibrationSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    calibration_id: str = Field(..., min_length=1, pattern=r"^[A-Za-z0-9_-]+$")
    parameter: ParameterSpec
    objective: ObjectiveSpec
    expected_monotonicity: Monotonicity
    maximum_iterations: int = Field(default=8, ge=2, le=100)

    def validate_tools(self, registry: ToolRegistry) -> None:
        """Check that referenced tools and fields exist in the Tool Registry."""
        try:
            parameter_tool = registry.get(self.parameter.tool_name)
            registry.get(self.objective.tool_name)
        except ToolRegistryError as exc:
            raise CalibrationError(str(exc)) from exc

        fields = parameter_tool.input_model.model_fields
        if self.parameter.input_field not in fields:
            raise CalibrationError(
                f"tool {self.parameter.tool_name!r} has no input field "
                f"{self.parameter.input_field!r}"
            )


class CalibrationObservation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    parameter_value: float
    metric_value: float
    run_id: str | None = None
    analysis_id: str | None = None

    @model_validator(mode="after")
    def validate_numbers(self) -> "CalibrationObservation":
        if not math.isfinite(self.parameter_value) or not math.isfinite(
            self.metric_value
        ):
            raise ValueError("observation values must be finite")
        return self


class CalibrationDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: CalibrationDecisionStatus
    reason: str
    candidate_value: float | None = None
    best_parameter_value: float | None = None
    best_metric_value: float | None = None
    best_absolute_error: float | None = Field(default=None, ge=0.0)


class SearchPolicy(Protocol):
    def propose_next(
        self,
        spec: CalibrationSpec,
        observations: list[CalibrationObservation],
    ) -> CalibrationDecision: ...


class BoundedSearchPolicy:
    """Choose candidates by deterministic bracketing and midpoint refinement."""

    def propose_next(
        self,
        spec: CalibrationSpec,
        observations: list[CalibrationObservation],
    ) -> CalibrationDecision:
        self._validate_observations(spec, observations)
        best = self._best(spec, observations)

        if best is not None and best[2] <= spec.objective.tolerance:
            return self._decision(
                CalibrationDecisionStatus.CONVERGED,
                "An observation satisfies the objective tolerance.",
                best,
            )

        if len(observations) >= spec.maximum_iterations:
            return self._decision(
                CalibrationDecisionStatus.EXHAUSTED,
                "The maximum number of calibration iterations was reached.",
                best,
            )

        lower = spec.parameter.lower_bound
        upper = spec.parameter.upper_bound
        sampled = {observation.parameter_value for observation in observations}
        if lower not in sampled:
            return self._decision(
                CalibrationDecisionStatus.NEXT,
                "Sample the lower parameter bound to establish the response bracket.",
                best,
                lower,
            )
        if upper not in sampled:
            return self._decision(
                CalibrationDecisionStatus.NEXT,
                "Sample the upper parameter bound to establish the response bracket.",
                best,
                upper,
            )

        lower_bracket, upper_bracket = self._metric_bracket(spec, observations)
        candidate = (lower_bracket.parameter_value + upper_bracket.parameter_value) / 2.0
        if candidate in sampled:
            return self._decision(
                CalibrationDecisionStatus.EXHAUSTED,
                "The bounded search cannot produce a new floating-point candidate.",
                best,
            )
        return self._decision(
            CalibrationDecisionStatus.NEXT,
            "Refine the parameter interval with its midpoint.",
            best,
            candidate,
        )

    @staticmethod
    def _best(
        spec: CalibrationSpec,
        observations: list[CalibrationObservation],
    ) -> tuple[float, float, float] | None:
        if not observations:
            return None
        observation = min(
            observations,
            key=lambda item: abs(item.metric_value - spec.objective.target_value),
        )
        return (
            observation.parameter_value,
            observation.metric_value,
            abs(observation.metric_value - spec.objective.target_value),
        )

    @staticmethod
    def _decision(
        status: CalibrationDecisionStatus,
        reason: str,
        best: tuple[float, float, float] | None,
        candidate: float | None = None,
    ) -> CalibrationDecision:
        return CalibrationDecision(
            status=status,
            reason=reason,
            candidate_value=candidate,
            best_parameter_value=None if best is None else best[0],
            best_metric_value=None if best is None else best[1],
            best_absolute_error=None if best is None else best[2],
        )

    @staticmethod
    def _validate_observations(
        spec: CalibrationSpec,
        observations: list[CalibrationObservation],
    ) -> None:
        values = [observation.parameter_value for observation in observations]
        if len(values) != len(set(values)):
            raise CalibrationError("parameter observations must be unique")
        if any(
            value < spec.parameter.lower_bound
            or value > spec.parameter.upper_bound
            for value in values
        ):
            raise CalibrationError("parameter observation is outside the configured bounds")

        ordered = sorted(observations, key=lambda item: item.parameter_value)
        metrics = [observation.metric_value for observation in ordered]
        pairs = zip(metrics, metrics[1:])
        if spec.expected_monotonicity == Monotonicity.INCREASING:
            monotonic = all(left <= right for left, right in pairs)
        else:
            monotonic = all(left >= right for left, right in pairs)
        if not monotonic:
            raise CalibrationError(
                "observations violate the expected monotonic response"
            )

    @staticmethod
    def _metric_bracket(
        spec: CalibrationSpec,
        observations: list[CalibrationObservation],
    ) -> tuple[CalibrationObservation, CalibrationObservation]:
        target = spec.objective.target_value
        if spec.expected_monotonicity == Monotonicity.INCREASING:
            lower_side = [item for item in observations if item.metric_value < target]
            upper_side = [item for item in observations if item.metric_value > target]
        else:
            lower_side = [item for item in observations if item.metric_value > target]
            upper_side = [item for item in observations if item.metric_value < target]

        if not lower_side or not upper_side:
            raise CalibrationError(
                "the target metric is not bracketed by the sampled parameter bounds"
            )
        return (
            max(lower_side, key=lambda item: item.parameter_value),
            min(upper_side, key=lambda item: item.parameter_value),
        )
