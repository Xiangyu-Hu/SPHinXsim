"""Typed input and output contracts for deterministic Agent tools."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from ..analysis import AnalysisSpec


class ToolInput(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ToolOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PrepareCaseInput(ToolInput):
    source_config_path: str = Field(..., min_length=1)
    output_config_path: str = Field(..., min_length=1)


class PrepareCaseOutput(ToolOutput):
    config_path: str


class SetFrictionAngleInput(ToolInput):
    config_path: str = Field(..., min_length=1)
    body_name: str = Field(..., min_length=1)
    friction_angle_degrees: float = Field(..., ge=0.0, lt=90.0)


class SetFrictionAngleOutput(ToolOutput):
    config_path: str
    friction_angle_radians: float = Field(..., ge=0.0)


class ValidateCaseInput(ToolInput):
    config_path: str = Field(..., min_length=1)


class ValidateCaseOutput(ToolOutput):
    succeeded: bool
    returncode: int | None


class RunCaseInput(ToolInput):
    config_path: str = Field(..., min_length=1)
    run_id: str = Field(..., min_length=1, pattern=r"^[A-Za-z0-9_-]+$")
    output_dir: str = Field(..., min_length=1)


class RunCaseOutput(ToolOutput):
    run_id: str
    status: str
    output_dir: str
    returncode: int | None


class AnalyzeRunoutInput(ToolInput):
    analysis_id: str = Field(..., min_length=1, pattern=r"^[A-Za-z0-9_-]+$")
    run_id: str = Field(..., min_length=1, pattern=r"^[A-Za-z0-9_-]+$")
    config_path: str = Field(..., min_length=1)
    analysis_spec: AnalysisSpec


class AnalyzeRunoutOutput(ToolOutput):
    analysis_id: str
    status: str
    metrics: dict[str, Any]


class SweepSampleInput(ToolInput):
    friction_angle_degrees: float = Field(..., ge=0.0, lt=90.0)
    run_id: str = Field(..., min_length=1)
    analysis_id: str = Field(..., min_length=1)


class CompareSweepResultsInput(ToolInput):
    sweep_id: str = Field(..., min_length=1, pattern=r"^[A-Za-z0-9_-]+$")
    target_value: float
    samples: list[SweepSampleInput] = Field(..., min_length=1)


class CompareSweepResultsOutput(ToolOutput):
    sweep_id: str
    best_friction_angle_degrees: float
    best_final_front: float
    best_runout_extension: float
    best_absolute_error: float = Field(..., ge=0.0)
