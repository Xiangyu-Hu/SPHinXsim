"""Deterministic readers and metrics for SPHinXsim Agent run artifacts."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import json
import math
from pathlib import Path
from typing import Sequence
import xml.etree.ElementTree as ET
import xml.sax
from xml.sax.handler import ContentHandler

from pydantic import BaseModel, ConfigDict, Field, model_validator


class AnalysisError(ValueError):
    """Raised when simulation output cannot be analyzed deterministically."""


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


@dataclass(frozen=True)
class PositionSummary:
    """Streaming summary of one body's particle positions at one state."""

    body_name: str
    state_file: Path
    time: float
    particle_count: int
    component_count: int
    minimum: tuple[float, ...]
    maximum: tuple[float, ...]
    maximum_projection: float | None = None


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


class _PositionHandler(ContentHandler):
    def __init__(self, projection: tuple[float, ...] | None) -> None:
        super().__init__()
        self.projection = projection
        self.in_points = False
        self.active: str | None = None
        self.buffer = ""
        self.time: float | None = None
        self.body_name: str | None = None
        self.expected_points: int | None = None
        self.component_count: int | None = None
        self.value_count = 0
        self.current_position: list[float] = []
        self.minimum: list[float] = []
        self.maximum: list[float] = []
        self.maximum_projection: float | None = None

    def startElement(self, name: str, attrs) -> None:  # noqa: N802
        if name == "Piece":
            self.body_name = attrs.get("Name")
            raw_count = attrs.get("NumberOfPoints")
            if raw_count is not None:
                try:
                    self.expected_points = int(raw_count)
                except ValueError as exc:
                    raise AnalysisError("VTP Piece NumberOfPoints must be an integer") from exc
        elif name == "Points":
            self.in_points = True
        elif name == "DataArray":
            data_name = attrs.get("Name")
            if data_name == "TimeValue":
                self.active = "time"
                self.buffer = ""
            elif self.in_points and data_name == "Position":
                if attrs.get("format", "ascii").lower() != "ascii":
                    raise AnalysisError("only ASCII VTP Position arrays are currently supported")
                raw_components = attrs.get("NumberOfComponents", "3")
                try:
                    self.component_count = int(raw_components)
                except ValueError as exc:
                    raise AnalysisError(
                        "VTP Position NumberOfComponents must be an integer"
                    ) from exc
                if self.component_count not in {2, 3}:
                    raise AnalysisError("VTP Position must contain 2 or 3 components")
                if self.projection is not None and len(self.projection) > self.component_count:
                    raise AnalysisError(
                        "projection dimension exceeds VTP Position component count"
                    )
                self.minimum = [math.inf] * self.component_count
                self.maximum = [-math.inf] * self.component_count
                self.active = "position"
                self.buffer = ""

    def characters(self, content: str) -> None:
        if self.active is None:
            return
        combined = self.buffer + content
        tokens = combined.split()
        if combined and not combined[-1].isspace():
            self.buffer = tokens.pop() if tokens else combined
        else:
            self.buffer = ""
        for token in tokens:
            self._consume_token(token)

    def endElement(self, name: str) -> None:  # noqa: N802
        if name == "DataArray" and self.active is not None:
            if self.buffer.strip():
                self._consume_token(self.buffer.strip())
            self.buffer = ""
            self.active = None
        elif name == "Points":
            self.in_points = False

    def _consume_token(self, token: str) -> None:
        try:
            value = float(token)
        except ValueError as exc:
            raise AnalysisError(f"VTP DataArray contains a non-numeric value: {token!r}") from exc
        if not math.isfinite(value):
            raise AnalysisError("VTP DataArray contains a non-finite value")
        if self.active == "time":
            if self.time is not None:
                raise AnalysisError("VTP TimeValue must contain exactly one value")
            self.time = value
            return
        if self.active != "position" or self.component_count is None:
            return

        component = self.value_count % self.component_count
        self.minimum[component] = min(self.minimum[component], value)
        self.maximum[component] = max(self.maximum[component], value)
        self.current_position.append(value)
        self.value_count += 1
        if len(self.current_position) == self.component_count:
            if self.projection is not None:
                projected = sum(
                    self.current_position[index] * weight
                    for index, weight in enumerate(self.projection)
                )
                if self.maximum_projection is None:
                    self.maximum_projection = projected
                else:
                    self.maximum_projection = max(self.maximum_projection, projected)
            self.current_position.clear()


class VTPPositionReader:
    """Locate a body's final state and stream only its ASCII Position array."""

    def find_final_state(self, output_dir: str | Path, body_name: str) -> tuple[Path, float]:
        if not isinstance(body_name, str) or not body_name.strip():
            raise AnalysisError("body_name must be a non-empty string")
        root = Path(output_dir).resolve()
        data_dir = root / "output" if (root / "output").is_dir() else root
        if not data_dir.is_dir():
            raise AnalysisError(f"run output directory does not exist: {data_dir}")

        prefix = f"{body_name}_"
        candidates: list[tuple[float, Path]] = []
        for path in data_dir.glob("*.vtp"):
            if not path.name.startswith(prefix) or "_ite_" in path.name:
                continue
            candidates.append((self._read_time_value(path), path.resolve()))
        if not candidates:
            raise AnalysisError(f"no state VTP files found for body {body_name!r}")
        time_value, state_file = max(candidates, key=lambda item: (item[0], item[1].name))
        return state_file, time_value

    def read_final_summary(
        self,
        output_dir: str | Path,
        body_name: str,
        *,
        projection_direction: Sequence[float] | None = None,
    ) -> PositionSummary:
        state_file, selected_time = self.find_final_state(output_dir, body_name)
        projection = self._normalized_projection(projection_direction)
        handler = _PositionHandler(projection)
        try:
            xml.sax.parse(str(state_file), handler)
        except AnalysisError:
            raise
        except (OSError, xml.sax.SAXException) as exc:
            raise AnalysisError(f"unable to parse VTP state {state_file}: {exc}") from exc

        if handler.time is None:
            raise AnalysisError("VTP state has no TimeValue")
        if not math.isclose(handler.time, selected_time, rel_tol=0.0, abs_tol=1e-12):
            raise AnalysisError("VTP TimeValue changed while selecting the final state")
        if handler.component_count is None or handler.value_count == 0:
            raise AnalysisError("VTP state has no ASCII Position values")
        if handler.current_position:
            raise AnalysisError("VTP Position value count is not divisible by its components")
        particle_count = handler.value_count // handler.component_count
        if handler.expected_points is not None and particle_count != handler.expected_points:
            raise AnalysisError(
                "VTP Position tuple count does not match Piece NumberOfPoints"
            )
        return PositionSummary(
            body_name=handler.body_name or body_name,
            state_file=state_file,
            time=handler.time,
            particle_count=particle_count,
            component_count=handler.component_count,
            minimum=tuple(handler.minimum),
            maximum=tuple(handler.maximum),
            maximum_projection=handler.maximum_projection,
        )

    @staticmethod
    def _read_time_value(path: Path) -> float:
        try:
            for _event, element in ET.iterparse(path, events=("end",)):
                if element.tag.rsplit("}", 1)[-1] != "DataArray":
                    continue
                if element.attrib.get("Name") != "TimeValue":
                    element.clear()
                    continue
                values = (element.text or "").split()
                if len(values) != 1:
                    raise AnalysisError(f"VTP TimeValue is invalid in {path}")
                time_value = float(values[0])
                if not math.isfinite(time_value):
                    raise AnalysisError(f"VTP TimeValue is non-finite in {path}")
                return time_value
        except AnalysisError:
            raise
        except (OSError, ET.ParseError, ValueError) as exc:
            raise AnalysisError(f"unable to read VTP TimeValue from {path}: {exc}") from exc
        raise AnalysisError(f"VTP state has no TimeValue: {path}")

    @staticmethod
    def _normalized_projection(
        direction: Sequence[float] | None,
    ) -> tuple[float, ...] | None:
        if direction is None:
            return None
        if isinstance(direction, (str, bytes)) or len(direction) not in {2, 3}:
            raise AnalysisError("projection direction must have 2 or 3 components")
        values = tuple(float(value) for value in direction)
        if not all(math.isfinite(value) for value in values):
            raise AnalysisError("projection direction must contain finite values")
        magnitude = math.sqrt(sum(value * value for value in values))
        if magnitude == 0.0:
            raise AnalysisError("projection direction must not be zero")
        return tuple(value / magnitude for value in values)


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
