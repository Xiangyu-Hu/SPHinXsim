"""Shared, GUI-independent streaming access to SPHinXsim particle results."""
from __future__ import annotations
from dataclasses import dataclass
import json
import math
from pathlib import Path
from typing import Any, Sequence
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
               include_generation: bool = False,
               generation_only: bool = False) -> dict[str, ResultFrame]:
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
                if (generation and not (include_generation or generation_only)
                        or generation_only and not generation):
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


def _resolve_run_data_dir(output_dir: str | Path) -> tuple[Path, dict[str, Any] | None]:
    """Return ``(vtp_or_dat_dir, job_publication_or_none)`` for a run directory."""
    root = Path(output_dir).resolve()
    job = referenced_job(root)
    if job is not None:
        jobs, job_id = job
        solver_dir = jobs.job_path(job_id) / "solver"
        data_dir = solver_dir / "output" if (solver_dir / "output").is_dir() else solver_dir
        try:
            publication = jobs.results(job_id)
        except Exception:
            publication = None
        return data_dir, publication
    if (root / "solver").is_dir():
        solver_dir = root / "solver"
        data_dir = solver_dir / "output" if (solver_dir / "output").is_dir() else solver_dir
        return data_dir, None
    data_dir = root / "output" if (root / "output").is_dir() else root
    return data_dir, None


def _inspect_vtp_point_fields(path: Path) -> list[str]:
    """Extract PointData array names from a VTP file without requiring PyVista."""
    fields: list[str] = []
    in_point_data = False
    try:
        for event, element in ET.iterparse(path, events=("start", "end")):
            tag = element.tag.rsplit("}", 1)[-1]
            if event == "start" and tag == "PointData":
                in_point_data = True
            elif event == "end" and tag == "PointData":
                in_point_data = False
                element.clear()
                break
            elif event == "start" and in_point_data and tag == "DataArray":
                name = element.attrib.get("Name")
                if name and not name.startswith("_sphinxsim_"):
                    fields.append(name)
            elif event == "end":
                element.clear()
    except (OSError, ET.ParseError):
        return []
    return sorted(set(fields))


def summarize_dat_series(data_dir: str | Path) -> list[dict[str, Any]]:
    """Summarize observer and reduced-quantity ``.dat`` time-series files in *data_dir*."""
    import shlex

    directory = Path(data_dir)
    if not directory.is_dir():
        return []

    summaries: list[dict[str, Any]] = []
    for dat_path in sorted(directory.glob("*.dat")):
        try:
            with dat_path.open("r", encoding="utf-8", errors="replace") as handle:
                header_line = ""
                for raw_line in handle:
                    stripped = raw_line.strip()
                    if stripped:
                        header_line = stripped
                        break
                if not header_line:
                    continue
                try:
                    headers = shlex.split(header_line)
                except ValueError:
                    headers = [token.strip('"') for token in header_line.split()]
                if len(headers) < 2 or headers[0].lower() not in {"run_time", "time"}:
                    continue

                col_count = len(headers)
                rows = 0
                start_time: float | None = None
                end_time: float | None = None
                mins = [math.inf] * (col_count - 1)
                maxs = [-math.inf] * (col_count - 1)
                initials: list[float | None] = [None] * (col_count - 1)
                finals: list[float | None] = [None] * (col_count - 1)

                for raw_line in handle:
                    parts = raw_line.split()
                    if len(parts) < col_count:
                        continue
                    try:
                        values = [float(token) for token in parts[:col_count]]
                    except ValueError:
                        continue
                    if not all(math.isfinite(val) for val in values):
                        continue
                    t_val = values[0]
                    if start_time is None:
                        start_time = t_val
                    end_time = t_val
                    for idx, val in enumerate(values[1:]):
                        if initials[idx] is None:
                            initials[idx] = val
                        finals[idx] = val
                        mins[idx] = min(mins[idx], val)
                        maxs[idx] = max(maxs[idx], val)
                    rows += 1

                if rows == 0 or start_time is None or end_time is None:
                    continue

                series: dict[str, dict[str, float]] = {}
                for idx, col_name in enumerate(headers[1:]):
                    series[col_name] = {
                        "initial": float(initials[idx]),  # type: ignore[arg-type]
                        "final": float(finals[idx]),  # type: ignore[arg-type]
                        "min": float(mins[idx]),
                        "max": float(maxs[idx]),
                    }
                summaries.append(
                    {
                        "file": dat_path.name,
                        "rows": rows,
                        "start_time": start_time,
                        "end_time": end_time,
                        "series": series,
                    }
                )
        except OSError:
            continue
    return summaries


def summarize_run_results(
    config_path: str | Path,
    output_dir: str | Path,
) -> dict[str, Any]:
    """Summarize VTP frames, ``.dat`` time series, and saved-output checks for a run."""
    from sphinxsim.config.migrations import migrate_legacy_configuration
    from sphinxsim.config.schemas import SimulationConfig
    from sphinxsim.runtime.physics import check_config

    resolved_config = Path(config_path).resolve()
    if not resolved_config.is_file():
        raise AnalysisError(f"Configuration file not found: {resolved_config}")
    resolved_output = Path(output_dir).resolve()
    if not resolved_output.exists():
        raise AnalysisError(f"Output directory not found: {resolved_output}")

    raw_payload = migrate_legacy_configuration(
        json.loads(resolved_config.read_text(encoding="utf-8"))
    )
    normalized, report = check_config(raw_payload)
    config = SimulationConfig.model_validate(normalized)
    dimension = report["dimension"]

    data_dir, publication = _resolve_run_data_dir(resolved_output)
    if not data_dir.is_dir():
        raise AnalysisError(f"Simulation output directory does not exist: {data_dir}")

    all_bodies = (
        [(b.name, "fluid") for b in config.fluid_bodies]
        + [(b.name, "continuum") for b in config.continuum_bodies]
        + [(b.name, "solid") for b in config.solid_bodies]
    )
    body_names = [name for name, _ in all_bodies]
    reader = VTPPositionReader()
    vtp_files = sorted(data_dir.glob("*.vtp"))

    bodies_summary: dict[str, dict[str, Any]] = {}
    findings: list[dict[str, Any]] = []
    tolerance = (
        (config.geometries.global_resolution.particle_spacing or 0.0) * 0.5
        if config.geometries.global_resolution is not None
        else 0.0
    )
    domain = config.geometries.system_domain
    has_open_bc = bool(config.fluid_boundary_conditions)
    has_scaling = bool(config.characteristic_dimensions)

    for body_name, kind in all_bodies:
        solver_frames: list[tuple[float, Path]] = []
        relaxation_count = 0
        prefix = f"{body_name}_"
        for path in vtp_files:
            # Match longest body prefix unambiguously
            matched_body = None
            for candidate in sorted(body_names, key=len, reverse=True):
                if path.stem.startswith(f"{candidate}_"):
                    matched_body = candidate
                    break
            if matched_body != body_name:
                continue
            suffix = path.stem[len(prefix):]
            if suffix.startswith("ite_"):
                relaxation_count += 1
                continue
            try:
                t_val = reader._read_time_value(path)
                solver_frames.append((t_val, path))
            except AnalysisError:
                continue

        solver_frames.sort(key=lambda item: (item[0], item[1].name))
        entry: dict[str, Any] = {
            "kind": kind,
            "solver_frames": len(solver_frames),
            "relaxation_frames": relaxation_count,
        }
        if solver_frames:
            first_time, first_file = solver_frames[0]
            last_time, last_file = solver_frames[-1]
            entry["first_time"] = first_time
            entry["last_time"] = last_time
            entry["latest_file"] = last_file.name
            entry["fields"] = _inspect_vtp_point_fields(last_file)

            try:
                first_pos = reader.read_summary(first_file, body_name, expected_time=first_time)
                last_pos = reader.read_summary(last_file, body_name, expected_time=last_time)
                entry["particle_count"] = last_pos.particle_count
                entry["min_particles"] = min(first_pos.particle_count, last_pos.particle_count)
                entry["max_particles"] = max(first_pos.particle_count, last_pos.particle_count)
                entry["bounds_min"] = list(last_pos.minimum[:dimension])
                entry["bounds_max"] = list(last_pos.maximum[:dimension])

                if kind == "fluid" and not has_open_bc and first_pos.particle_count != last_pos.particle_count:
                    findings.append(
                        {
                            "code": "closed_body_count_changed",
                            "severity": "warning",
                            "body": body_name,
                            "min_count": entry["min_particles"],
                            "max_count": entry["max_particles"],
                            "message": "Saved particle count changed between initial and final frames without open boundaries.",
                        }
                    )
                if (
                    kind == "fluid"
                    and domain is not None
                    and not has_open_bc
                    and not has_scaling
                ):
                    dim = len(domain.lower_bound)
                    out_low = any(
                        last_pos.minimum[i] < domain.lower_bound[i] - tolerance
                        for i in range(min(dim, len(last_pos.minimum)))
                    )
                    out_high = any(
                        last_pos.maximum[i] > domain.upper_bound[i] + tolerance
                        for i in range(min(dim, len(last_pos.maximum)))
                    )
                    if out_low or out_high:
                        findings.append(
                            {
                                "code": "outside_domain",
                                "severity": "warning",
                                "body": body_name,
                                "message": "Final particle bounds exceed configured system_domain.",
                            }
                        )
            except AnalysisError:
                pass

            end_time = config.solver_parameters.end_time
            interval = config.solver_parameters.output_interval
            if (
                kind == "fluid"
                and end_time is not None
                and interval is not None
                and not has_scaling
                and last_time + interval * 1.1 < end_time
            ):
                findings.append(
                    {
                        "code": "saved_output_coverage_gap",
                        "severity": "warning",
                        "body": body_name,
                        "last_saved_time": last_time,
                        "requested_end_time": end_time,
                        "message": "Latest saved VTP time is earlier than requested end_time.",
                    }
                )
        bodies_summary[body_name] = entry

    time_series = summarize_dat_series(data_dir)
    quality_summary: dict[str, Any]
    if publication is not None and publication.get("quality"):
        quality_summary = publication["quality"]
    else:
        any_frames = any(b["solver_frames"] > 0 for b in bodies_summary.values())
        quality_summary = {
            "state": "warning" if findings else ("checked" if any_frames else "no_solver_frames"),
            "findings": findings,
            "scope": "Saved VTP and .dat outputs; not physical accuracy certification.",
        }

    log_path = resolved_output / "solver.log"
    log_tail: str | None = None
    if log_path.is_file():
        with log_path.open("rb") as stream:
            stream.seek(max(0, log_path.stat().st_size - 4096))
            log_tail = stream.read(4096).decode("utf-8", errors="replace")

    result: dict[str, Any] = {
        "config_file": str(resolved_config),
        "output_dir": str(resolved_output),
        "data_dir": str(data_dir),
        "dimension": dimension,
        "simulation_type": config.simulation_type.value,
        "bodies": bodies_summary,
        "time_series": time_series,
        "quality": quality_summary,
    }
    if log_tail is not None:
        result["solver_log_tail"] = log_tail
    return result


def _select_body_frames(
    data_dir: Path,
    body_names: Sequence[str],
    frame_selector: str,
) -> dict[str, ResultFrame]:
    """Select ``latest``, ``first``, or 0-based/negative frame index per body."""
    selector = frame_selector.strip().lower()
    if selector == "latest":
        return ResultCatalog().latest(data_dir, list(body_names), include_generation=True)

    reader = VTPPositionReader()
    vtp_files = sorted(data_dir.glob("*.vtp"))
    selected_frames: dict[str, ResultFrame] = {}
    for body_name in body_names:
        candidates: list[tuple[bool, float, Path]] = []
        prefix = f"{body_name}_"
        for path in vtp_files:
            matched = None
            for cand in sorted(body_names, key=len, reverse=True):
                if path.stem.startswith(f"{cand}_"):
                    matched = cand
                    break
            if matched != body_name:
                continue
            suffix = path.stem[len(prefix):]
            generation = suffix.startswith("ite_")
            try:
                if generation:
                    t_val = float(suffix[4:])
                else:
                    t_val = reader._read_time_value(path)
                if math.isfinite(t_val):
                    candidates.append((generation, t_val, path.resolve()))
            except (AnalysisError, ValueError):
                continue

        solver_only = [item for item in candidates if not item[0]]
        pool = solver_only if solver_only else candidates
        pool.sort(key=lambda item: (item[1], item[2].name))
        if not pool:
            continue
        if selector == "first":
            gen, t_val, chosen = pool[0]
        else:
            try:
                idx = int(selector)
            except ValueError as exc:
                raise AnalysisError(
                    f"Invalid --frame selector {frame_selector!r}; use 'latest', 'first', or an integer index"
                ) from exc
            if idx < -len(pool) or idx >= len(pool):
                raise AnalysisError(
                    f"Frame index {idx} out of range for {body_name!r} ({len(pool)} frames available)"
                )
            gen, t_val, chosen = pool[idx]
        stat = chosen.stat()
        selected_frames[body_name] = ResultFrame(
            body_name, chosen, t_val, gen, (stat.st_size, stat.st_mtime_ns)
        )
    return selected_frames


def render_run_results(
    config_path: str | Path,
    output_dir: str | Path,
    screenshot_path: str | Path | None = None,
    *,
    color_by: str | None = None,
    frame: str = "latest",
    project_root: str | Path | None = None,
    renderer: str = "vtk",
    samples: int = 64,
    resolution: str | tuple[int, int] | None = None,
    fluid_surface: bool = False,
    transparent_bg: bool = False,
    apply_overlay: bool = True,
    save_bundle: str | Path | None = None,
    save_blend: str | Path | None = None,
    blender_executable: str | Path | None = None,
) -> dict[str, Any]:
    """Render an off-screen PNG (or Cycles scene bundle/.blend) of a simulation output directory."""
    use_cycles = (
        str(renderer or "vtk").strip().lower() == "cycles"
        or save_bundle is not None
        or save_blend is not None
    )
    if use_cycles:
        from sphinxsim.visualization.cycles_runner import render_results_with_cycles

        return render_results_with_cycles(
            config_path,
            output_dir,
            screenshot_path,
            save_bundle=save_bundle,
            save_blend=save_blend,
            color_by=color_by,
            frame=frame,
            samples=samples,
            resolution=resolution,
            fluid_surface=fluid_surface,
            transparent_bg=transparent_bg,
            apply_overlay=apply_overlay,
            blender_executable=blender_executable,
        )

    if screenshot_path is None:
        raise AnalysisError("screenshot_path is required when rendering with the VTK backend")

    import pyvista as pv

    from sphinxsim.config.migrations import migrate_legacy_configuration
    from sphinxsim.config.schemas import SimulationConfig
    from sphinxsim.runtime.export_vtp import hash_file
    from sphinxsim.runtime.physics import check_config
    from sphinxsim.templates import _default_project_root
    from sphinxsim.visualization.live_particles import (
        add_or_update_particle_actor,
        refresh_particle_actors,
    )
    from sphinxsim.visualization.particle_time import ParticleTimeDisplay
    from sphinxsim.visualization.particle_visibility import ParticleVisibility
    from sphinxsim.visualization.preview import (
        PREVIEW_AXIS_COLOUR,
        ConfigVisualizer,
        _body_colour,
    )

    resolved_config = Path(config_path).resolve()
    resolved_output = Path(output_dir).resolve()
    data_dir, _ = _resolve_run_data_dir(resolved_output)
    if not data_dir.is_dir():
        raise AnalysisError(f"Simulation output directory does not exist: {data_dir}")

    raw_payload = migrate_legacy_configuration(
        json.loads(resolved_config.read_text(encoding="utf-8"))
    )
    normalized, _ = check_config(raw_payload)
    config = SimulationConfig.model_validate(normalized)
    root = Path(project_root).resolve() if project_root is not None else _default_project_root()

    destination = Path(screenshot_path).resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)

    plotter = pv.Plotter(
        title="SPHinXsim - Simulation Results",
        off_screen=True,
        shape=(1, 2),
        border=False,
        window_size=(1400, 800),
    )
    try:
        visualizer = ConfigVisualizer(
            config,
            root,
            config_path=resolved_config,
            off_screen=True,
        )
        visualizer.populate_existing_run(plotter, data_dir.parent if data_dir.name == "output" else data_dir)
        visibility = ParticleVisibility(plotter)
        visibility.set_scene(("results-render", resolved_config, data_dir, frame), config, visualizer.legend_actor)
        time_display = ParticleTimeDisplay(plotter)
        bodies = config.fluid_bodies + config.continuum_bodies + config.solid_bodies
        colours = {body.name: _body_colour(body.name, config) for body in bodies}
        versions: dict[str, Any] = {}
        visualizer._select_subplot(plotter, 0)
        if frame.strip().lower() == "latest":
            frames = refresh_particle_actors(
                plotter,
                data_dir,
                colours,
                versions,
                ResultCatalog(),
                visibility,
                time_display,
            )
        else:
            frames = _select_body_frames(data_dir, list(colours), frame)
            for body_name, selected_frame in frames.items():
                mesh = read_particle_mesh(selected_frame.path)
                actor = add_or_update_particle_actor(
                    plotter,
                    body_name,
                    mesh,
                    colours[body_name],
                    label=f"Particles: {body_name}",
                )
                visibility.add_actor(body_name, actor, mesh, initialized=not selected_frame.generation)
                time_display.update(body_name, mesh, generation=selected_frame.generation)
                versions[body_name] = (str(selected_frame.path), selected_frame.version)
        if not frames:
            raise AnalysisError(f"No particle VTP snapshots found in {data_dir}")

        for body in config.fluid_bodies + config.continuum_bodies:
            if body.name in versions:
                try:
                    plotter.remove_actor(f"preview-shape-{body.name}", reset_camera=False)
                except Exception:
                    pass

        coloured_bodies: list[str] = []
        if color_by is not None:
            available_anywhere: set[str] = set()
            for body_name in sorted(frames):
                fields = visibility.colourable_fields(body_name)
                available_anywhere.update(fields)
                if color_by in fields:
                    visibility.set_colour(body_name, color_by)
                    coloured_bodies.append(body_name)
            if not coloured_bodies:
                available_list = ", ".join(sorted(available_anywhere)) or "none"
                raise AnalysisError(
                    f"Field {color_by!r} is not available in solver frames (available: {available_list})"
                )

        visualizer._select_subplot(plotter, 0)
        visualizer._configure_default_view(plotter, visualizer._spatial_dim())
        plotter.reset_camera()
        plotter.show_grid(font_size=10, color=PREVIEW_AXIS_COLOUR, bold=False)
        visualizer._configure_layout(plotter)
        plotter.screenshot(str(destination))
    finally:
        plotter.close()

    return {
        "file": str(destination),
        "bytes": destination.stat().st_size,
        "sha256": hash_file(destination),
        "bodies_rendered": sorted(frames),
        "color_by": color_by,
        "coloured_bodies": coloured_bodies,
        "frame": frame,
    }
