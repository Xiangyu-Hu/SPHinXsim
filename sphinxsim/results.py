"""Shared, GUI-independent streaming access to SPHinXsim particle results."""
from __future__ import annotations
from dataclasses import dataclass
import json
import math
from pathlib import Path
from typing import Sequence
import xml.etree.ElementTree as ET
import xml.sax
from xml.sax.handler import ContentHandler

class AnalysisError(ValueError):
    """An output file is missing, incomplete, or violates the result contract."""


def referenced_job(output_dir):
    """Resolve a complete MCP job reference stored with a workflow run."""
    pointer = Path(output_dir) / "job-reference.json"
    if not pointer.is_file():
        return None
    from sphinxsim.runtime.jobs import JobStore
    content = json.loads(pointer.read_text(encoding="utf-8"))
    jobs = JobStore(content["workspace"])
    return jobs, jobs.resolve(content["reference"])


@dataclass(frozen=True)
class ResultFrame:
    body_name: str
    path: Path
    time: float
    generation: bool = False
    version: tuple[int, int] = (0, 0)


class ResultCatalog:
    """Discover complete frames; incomplete live writes are retried on the next poll.

    Solver frames are ordered by TimeValue, generation frames by iteration.
    Metadata is cached by size/mtime so polling does not reparse unchanged files.
    """

    def __init__(self) -> None:
        self._cache: dict[Path, tuple[tuple[int, int], ResultFrame]] = {}

    def latest(self, output_dir: str | Path, body_names: Sequence[str], *,
               include_generation: bool = False) -> dict[str, ResultFrame]:
        root = Path(output_dir)
        job = referenced_job(root)
        if job:
            jobs, job_id = job
            root = jobs.job_path(job_id) / "solver"
        root = root / "output" if (root / "output").is_dir() else root
        latest: dict[str, ResultFrame] = {}
        for path in root.glob("*.vtp"):
            for body in sorted(body_names, key=len, reverse=True):
                if not path.stem.startswith(body + "_"):
                    continue
                suffix = path.stem[len(body) + 1:]
                generation = suffix.startswith("ite_")
                if generation and not include_generation:
                    break
                try:
                    sequence = float(suffix[4:] if generation else suffix)
                    stat = path.stat()
                    version = (stat.st_size, stat.st_mtime_ns)
                    cached = self._cache.get(path)
                    if cached and cached[0] == version:
                        frame = cached[1]
                    else:
                        time_value = None
                        for _, element in ET.iterparse(path, events=("end",)):
                            if (element.tag.rsplit("}", 1)[-1] == "DataArray"
                                    and element.get("Name") == "TimeValue"):
                                time_value = float(element.text or "")
                            element.clear()
                        after = path.stat()
                        if version != (after.st_size, after.st_mtime_ns):
                            break
                        if time_value is None and not generation:
                            break
                        selected = sequence if generation else time_value
                        if not math.isfinite(selected):
                            break
                        frame = ResultFrame(body, path.resolve(), selected, generation, version)
                        self._cache[path] = (version, frame)
                    previous = latest.get(body)
                    if previous is None or (not frame.generation, frame.time, frame.path.name) > (
                        not previous.generation, previous.time, previous.path.name
                    ):
                        latest[body] = frame
                except (OSError, ValueError, ET.ParseError):
                    pass
                break
        return latest


def read_particle_mesh(path: str | Path):
    """Optional PyVista reader shared by desktop and headless presentation."""
    import pyvista as pv
    source = Path(path)
    before = source.stat()
    mesh = pv.read(str(source))
    after = source.stat()
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        raise AnalysisError("VTP changed while loading the particle mesh")
    if not mesh.n_points:
        raise AnalysisError("VTP contains no particle points")
    return mesh

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
        job = referenced_job(root)
        if job:
            jobs, job_id = job
            publication = jobs.results(job_id)
            if not publication.get("available"):
                raise AnalysisError("No published MCP job results available")
            directory = jobs.job_path(job_id) / "result"
            manifest = json.loads((directory / publication["manifest"]).read_text(encoding="utf-8"))
            frames = manifest.get("bodies", {}).get(body_name, [])
            if not frames:
                raise AnalysisError(f"no state VTP files found for body {body_name!r}")
            frame = max(frames, key=lambda item: item["time"])
            return directory / frame["source"], frame["time"]
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
        return self.read_summary(state_file, body_name, projection_direction=projection_direction,
                                 expected_time=selected_time)

    def read_summary(self, state_file: str | Path, body_name: str, *,
                     projection_direction: Sequence[float] | None = None,
                     expected_time: float | None = None) -> PositionSummary:
        state_file = Path(state_file)
        before = state_file.stat()
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
        after = state_file.stat()
        if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
            raise AnalysisError("VTP changed while reading the state")
        if expected_time is not None and not math.isclose(handler.time, expected_time, rel_tol=0.0, abs_tol=1e-12):
            raise AnalysisError("VTP TimeValue changed while selecting the final state")
        if handler.component_count is None or handler.value_count == 0:
            raise AnalysisError("VTP state has no ASCII Position values")
        if handler.body_name is not None and handler.body_name != body_name:
            raise AnalysisError("VTP body name does not match the requested body")
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


