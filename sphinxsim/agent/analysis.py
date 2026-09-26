"""Deterministic readers and metrics for SPHinXsim Agent run artifacts."""

from __future__ import annotations

from enum import Enum
import json
import math
from pathlib import Path
from typing import Sequence

from pydantic import BaseModel, ConfigDict, Field, model_validator


from sphinxsim.results import AnalysisError, PositionSummary, VTPPositionReader


class AnalysisMetric(str, Enum):
    RUNOUT = "runout"


class TimeSelection(str, Enum):
    FINAL = "final"


class InitialFrontSource(str, Enum):
    GEOMETRY = "geometry"


class FrontMethod(str, Enum):
    MAXIMUM = "maximum"


class AnalysisSpec(BaseModel):
    """Validated instructions for one deterministic analysis tool."""

    model_config = ConfigDict(extra="forbid")

    metric: AnalysisMetric = AnalysisMetric.RUNOUT
    body_name: str = Field(..., min_length=1)
    time_selection: TimeSelection = TimeSelection.FINAL
    direction_vector: tuple[float, ...] = Field(..., min_length=2, max_length=3)
    initial_front_source: InitialFrontSource = InitialFrontSource.GEOMETRY
    front_method: FrontMethod = FrontMethod.MAXIMUM
    unit: str = Field(default="m", pattern=r"^m$")

    @model_validator(mode="after")
    def _validate_direction(self) -> "AnalysisSpec":
        if not all(math.isfinite(value) for value in self.direction_vector):
            raise ValueError("direction_vector must contain finite values")
        magnitude = math.sqrt(sum(value * value for value in self.direction_vector))
        if magnitude == 0.0:
            raise ValueError("direction_vector must not be zero")
        return self


class RunoutResult(BaseModel):
    """Structured runout metrics produced by deterministic analysis."""

    model_config = ConfigDict(extra="forbid")

    metric: AnalysisMetric = AnalysisMetric.RUNOUT
    body_name: str
    state_file: str
    simulation_time: float
    particle_count: int = Field(..., ge=1)
    direction_vector: tuple[float, ...]
    initial_front: float
    final_front: float
    runout_extension: float
    unit: str = "m"


class FrictionSweepSample(BaseModel):
    """One friction-angle sample and its persisted runout metrics."""

    model_config = ConfigDict(extra="forbid")

    friction_angle_degrees: float
    run_id: str
    analysis_id: str
    final_front: float
    runout_extension: float
    absolute_error: float = Field(..., ge=0.0)


class FrictionAngleSweepResult(BaseModel):
    """Deterministic comparison of a fixed friction-angle sweep."""

    model_config = ConfigDict(extra="forbid")

    sweep_id: str = Field(..., min_length=1, pattern=r"^[A-Za-z0-9_-]+$")
    target_metric: str = Field(
        default="runout_extension",
        pattern=r"^runout_extension$",
    )
    target_value: float
    unit: str = Field(default="m", pattern=r"^m$")
    samples: list[FrictionSweepSample] = Field(..., min_length=1)
    best_friction_angle_degrees: float
    best_final_front: float
    best_runout_extension: float
    best_absolute_error: float = Field(..., ge=0.0)


class InitialGeometryReader:
    """Read an initial directional front from a supported JSON geometry."""

    def read_front(
        self,
        config_path: str | Path,
        body_name: str,
        direction_vector: Sequence[float],
    ) -> float:
        path = Path(config_path).resolve()
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise AnalysisError(f"unable to read analysis configuration {path}: {exc}") from exc
        geometries = payload.get("geometries") if isinstance(payload, dict) else None
        shapes = geometries.get("shapes") if isinstance(geometries, dict) else None
        if not isinstance(shapes, list):
            raise AnalysisError("configuration must contain geometries.shapes")
        matches = [
            shape
            for shape in shapes
            if isinstance(shape, dict) and shape.get("name") == body_name
        ]
        if len(matches) != 1:
            raise AnalysisError(
                f"expected exactly one geometry named {body_name!r}, found {len(matches)}"
            )
        shape = matches[0]
        if shape.get("type") != "bounding_box":
            raise AnalysisError(
                "geometry initial-front analysis currently supports bounding_box only"
            )
        lower = shape.get("lower_bound")
        upper = shape.get("upper_bound")
        if not isinstance(lower, list) or not isinstance(upper, list) or len(lower) != len(upper):
            raise AnalysisError("bounding_box must contain matching lower_bound and upper_bound")
        direction = VTPPositionReader._normalized_projection(direction_vector)
        assert direction is not None
        if len(direction) != len(lower):
            raise AnalysisError(
                "analysis direction dimension must match the initial geometry dimension"
            )
        try:
            bounds = [(float(lo), float(hi)) for lo, hi in zip(lower, upper)]
        except (TypeError, ValueError) as exc:
            raise AnalysisError("bounding_box bounds must contain numeric values") from exc
        if any(
            not math.isfinite(lo) or not math.isfinite(hi) or lo > hi
            for lo, hi in bounds
        ):
            raise AnalysisError("bounding_box bounds must be finite and ordered")
        return sum(
            weight * (hi if weight >= 0.0 else lo)
            for weight, (lo, hi) in zip(direction, bounds)
        )


class RunoutAnalyzer:
    """Compose generic readers into the currently registered runout metric."""

    def __init__(
        self,
        position_reader: VTPPositionReader | None = None,
        geometry_reader: InitialGeometryReader | None = None,
    ) -> None:
        self.position_reader = position_reader or VTPPositionReader()
        self.geometry_reader = geometry_reader or InitialGeometryReader()

    def analyze(
        self,
        config_path: str | Path,
        output_dir: str | Path,
        spec: AnalysisSpec,
    ) -> RunoutResult:
        direction = VTPPositionReader._normalized_projection(spec.direction_vector)
        assert direction is not None
        initial_front = self.geometry_reader.read_front(
            config_path,
            spec.body_name,
            direction,
        )
        summary = self.position_reader.read_final_summary(
            output_dir,
            spec.body_name,
            projection_direction=direction,
        )
        if summary.maximum_projection is None:
            raise AnalysisError("final particle front projection was not calculated")
        final_front = summary.maximum_projection
        return RunoutResult(
            body_name=spec.body_name,
            state_file=str(summary.state_file),
            simulation_time=summary.time,
            particle_count=summary.particle_count,
            direction_vector=direction,
            initial_front=initial_front,
            final_front=final_front,
            runout_extension=final_front - initial_front,
            unit=spec.unit,
        )


class FrictionAngleSweepAnalyzer:
    """Compare completed runout analyses against one runout-extension target."""

    def summarize(
        self,
        *,
        sweep_id: str,
        target_value: float,
        samples: Sequence[tuple[float, str, str, dict]],
    ) -> FrictionAngleSweepResult:
        target = float(target_value)
        if not math.isfinite(target):
            raise AnalysisError("sweep target_value must be finite")
        summarized: list[FrictionSweepSample] = []
        for angle_value, run_id, analysis_id, metrics in samples:
            if isinstance(angle_value, bool):
                raise AnalysisError("sweep friction angles must be numeric")
            angle = float(angle_value)
            if not math.isfinite(angle) or not 0.0 <= angle < 90.0:
                raise AnalysisError(
                    "sweep friction angles must satisfy 0 <= angle < 90"
                )
            if not isinstance(metrics, dict):
                raise AnalysisError("sweep sample metrics must be an object")
            if metrics.get("unit") != "m":
                raise AnalysisError("sweep runout metrics must use metres")
            try:
                final_front = float(metrics["final_front"])
                extension = float(metrics["runout_extension"])
            except (KeyError, TypeError, ValueError) as exc:
                raise AnalysisError(
                    "sweep sample requires numeric final_front and runout_extension"
                ) from exc
            if not math.isfinite(final_front) or not math.isfinite(extension):
                raise AnalysisError("sweep runout metrics must be finite")
            summarized.append(
                FrictionSweepSample(
                    friction_angle_degrees=angle,
                    run_id=run_id,
                    analysis_id=analysis_id,
                    final_front=final_front,
                    runout_extension=extension,
                    absolute_error=abs(extension - target),
                )
            )
        if not summarized:
            raise AnalysisError("sweep requires at least one completed sample")
        best = min(
            summarized,
            key=lambda sample: (
                sample.absolute_error,
                sample.friction_angle_degrees,
            ),
        )
        return FrictionAngleSweepResult(
            sweep_id=sweep_id,
            target_value=target,
            samples=summarized,
            best_friction_angle_degrees=best.friction_angle_degrees,
            best_final_front=best.final_front,
            best_runout_extension=best.runout_extension,
            best_absolute_error=best.absolute_error,
        )
