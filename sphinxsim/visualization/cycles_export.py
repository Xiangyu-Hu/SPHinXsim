"""Portable, content-addressed Cycles Scene Bundle exporter for SPHinXsim.

Exports SPHinXsim geometries (``Shape*.vtp`` or C++ bounding-box fallbacks),
particle snapshots (``<Body>_*.vtp``), camera state, clip planes, and scalar
or vector-magnitude colormaps into a self-contained directory containing
``scene.json`` and immutable little-endian ``.bin`` buffers.

Designed to work both with ``vtkmodules`` / ``pyvista`` installed (fast path
supporting ASCII, binary, and appended VTP files) and in pure Python + NumPy
(parsing SPHinXsim's native ASCII XML ``.vtp`` files directly), with zero
dependency on Blender itself.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import json
import math
from pathlib import Path
from typing import Any, Sequence
import xml.etree.ElementTree as ET

import numpy as np

from sphinxsim.runtime.export_vtp import (
    atomic_json,
    box_mesh,
    complete_vtp,
    hash_file,
    write_buffer,
)
from sphinxsim.visualization.particle_visibility import (
    _DEFAULT_PALETTE,
    _PARTICLE_PALETTES,
)
from sphinxsim.visualization.preview import (
    PREVIEW_MAIN_BACKGROUND,
    _PARTICLE_DEFAULT_OPACITY,
    _SOLID_COLOUR,
    _body_colour,
    _legend_entries_for_config,
)


SCHEMA_VERSION = "1.0"


@dataclass
class VTPBundleData:
    """Geometry and attribute arrays extracted from a single VTP file."""

    points: np.ndarray  # (N, 3) float32
    triangles: np.ndarray  # (M, 3) uint32
    point_data: dict[str, np.ndarray] = field(default_factory=dict)
    field_data: dict[str, np.ndarray] = field(default_factory=dict)
    time_value: float | None = None


@dataclass
class CyclesSceneState:
    """Snapshot of interactive or CLI visual state for Cycles export."""

    camera_state: dict[str, Any] | None = None
    hidden_bodies: set[str] = field(default_factory=set)
    colouring: dict[str, str] = field(default_factory=dict)
    palettes: dict[str, str] = field(default_factory=dict)
    rescale_ranges: dict[tuple[str, str], tuple[float, float]] = field(default_factory=dict)
    opaque_bodies: set[str] = field(default_factory=set)
    clipped_bodies: set[str] = field(default_factory=set)
    clip_origins: dict[str, tuple[float, float, float]] = field(default_factory=dict)
    clip_normals: dict[str, tuple[float, float, float]] = field(default_factory=dict)
    inverted_clip_bodies: set[str] = field(default_factory=set)

    @classmethod
    def from_particle_visibility(
        cls,
        visibility: Any | None,
        *,
        camera_state: dict[str, Any] | None = None,
    ) -> "CyclesSceneState":
        if visibility is None:
            return cls(camera_state=camera_state)
        clip_origins = dict(getattr(visibility, "clip_origins", {}))
        clip_normals = dict(getattr(visibility, "clip_normals", {}))
        clipped = set(getattr(visibility, "clipped_bodies", set()))
        for body in clipped:
            if body not in clip_origins or body not in clip_normals:
                try:
                    origin, normal, _ = visibility.clip_plane_settings(body)
                    clip_origins.setdefault(body, tuple(float(v) for v in origin))
                    clip_normals.setdefault(body, tuple(float(v) for v in normal))
                except Exception:
                    pass
        return cls(
            camera_state=camera_state,
            hidden_bodies=set(getattr(visibility, "hidden", set())),
            colouring=dict(getattr(visibility, "colouring", {})),
            palettes=dict(getattr(visibility, "palettes", {})),
            rescale_ranges=dict(getattr(visibility, "rescale_ranges", {})),
            opaque_bodies=set(getattr(visibility, "opaque_bodies", set())),
            clipped_bodies=clipped,
            clip_origins=clip_origins,
            clip_normals=clip_normals,
            inverted_clip_bodies=set(getattr(visibility, "inverted_clip_bodies", set())),
        )


def _pad_points_3d(points: np.ndarray) -> np.ndarray:
    pts = np.asarray(points, dtype=np.float32)
    if pts.size == 0:
        return np.zeros((0, 3), dtype=np.float32)
    if pts.ndim == 1:
        pts = pts.reshape(-1, 3)
    if pts.shape[1] == 2:
        pts = np.column_stack((pts, np.zeros(len(pts), dtype=np.float32)))
    elif pts.shape[1] > 3:
        pts = pts[:, :3]
    return np.ascontiguousarray(pts, dtype=np.float32)


def _extract_time_from_fields(field_data: dict[str, np.ndarray]) -> float | None:
    raw = field_data.get("TimeValue")
    if raw is None:
        return None
    arr = np.asarray(raw).ravel()
    if arr.size >= 1 and np.issubdtype(arr.dtype, np.number):
        val = float(arr[0])
        if math.isfinite(val):
            return val
    return None


def _read_vtp_with_vtk(path: Path) -> VTPBundleData:
    from vtkmodules.util.numpy_support import vtk_to_numpy
    from vtkmodules.vtkFiltersCore import vtkTriangleFilter
    from vtkmodules.vtkIOXML import vtkXMLPolyDataReader

    reader = vtkXMLPolyDataReader()
    reader.SetFileName(str(path))
    reader.Update()
    poly = reader.GetOutput()
    if reader.GetErrorCode() or poly is None:
        raise ValueError(f"VTK could not parse VTP file: {path}")

    n_points = int(poly.GetNumberOfPoints())
    if n_points > 0 and poly.GetPoints() is not None:
        points = _pad_points_3d(vtk_to_numpy(poly.GetPoints().GetData()))
    else:
        points = np.zeros((0, 3), dtype=np.float32)

    triangles_arr = np.zeros((0, 3), dtype=np.uint32)
    if poly.GetNumberOfPolys() > 0 or poly.GetNumberOfStrips() > 0:
        tri_filter = vtkTriangleFilter()
        tri_filter.SetInputData(poly)
        tri_filter.PassLinesOff()
        tri_filter.PassVertsOff()
        tri_filter.Update()
        tri_out = tri_filter.GetOutput()
        polys = tri_out.GetPolys()
        if polys is not None and polys.GetNumberOfCells() > 0:
            raw_cells = vtk_to_numpy(polys.GetData())
            indices: list[int] = []
            offset = 0
            total = len(raw_cells)
            while offset < total:
                count = int(raw_cells[offset])
                if count >= 3:
                    v0 = int(raw_cells[offset + 1])
                    for k in range(1, count - 1):
                        indices.extend(
                            (
                                v0,
                                int(raw_cells[offset + 1 + k]),
                                int(raw_cells[offset + 2 + k]),
                            )
                        )
                offset += count + 1
            if indices:
                triangles_arr = np.asarray(indices, dtype=np.uint32).reshape(-1, 3)

    point_data: dict[str, np.ndarray] = {}
    pd = poly.GetPointData()
    if pd is not None:
        for idx in range(pd.GetNumberOfArrays()):
            arr = pd.GetArray(idx)
            if arr is None or not arr.GetName():
                continue
            np_arr = np.asarray(vtk_to_numpy(arr))
            if np.issubdtype(np_arr.dtype, np.number):
                point_data[arr.GetName()] = np_arr

    field_data: dict[str, np.ndarray] = {}
    fd = poly.GetFieldData()
    if fd is not None:
        for idx in range(fd.GetNumberOfArrays()):
            arr = fd.GetArray(idx)
            if arr is None or not arr.GetName():
                continue
            np_arr = np.asarray(vtk_to_numpy(arr))
            if np.issubdtype(np_arr.dtype, np.number):
                field_data[arr.GetName()] = np_arr

    return VTPBundleData(
        points=points,
        triangles=triangles_arr,
        point_data=point_data,
        field_data=field_data,
        time_value=_extract_time_from_fields(field_data),
    )


def _parse_numeric_data_array(elem: ET.Element, *, dtype: Any = np.float64) -> np.ndarray | None:
    fmt = (elem.attrib.get("format") or "ascii").strip().lower()
    if fmt != "ascii":
        raise ValueError(
            f"Non-ASCII VTP DataArray ({elem.attrib.get('Name', '<unnamed>')}, format={fmt!r}) "
            "requires vtkmodules/pyvista; install sphinxsim[visualization]."
        )
    vtp_type = (elem.attrib.get("type") or "Float32").strip()
    if vtp_type.lower() == "string":
        return None
    text = (elem.text or "").strip()
    if not text:
        n_comp = int(elem.attrib.get("NumberOfComponents", "1"))
        return np.zeros((0, n_comp) if n_comp > 1 else (0,), dtype=dtype)
    try:
        values = np.fromstring(text, sep=" ", dtype=dtype)
    except ValueError:
        return None
    n_comp = int(elem.attrib.get("NumberOfComponents", "1"))
    if n_comp > 1:
        if values.size % n_comp != 0:
            raise ValueError(
                f"DataArray {elem.attrib.get('Name', '<unnamed>')!r} length {values.size} "
                f"is not divisible by NumberOfComponents={n_comp}"
            )
        values = values.reshape(-1, n_comp)
    return values


def _read_vtp_ascii_xml(path: Path) -> VTPBundleData:
    try:
        tree = ET.parse(path)
    except ET.ParseError as exc:
        raise ValueError(f"Invalid XML in VTP file {path}: {exc}") from exc

    root = tree.getroot()

    def _local(tag: str) -> str:
        return tag.rsplit("}", 1)[-1]

    poly_elem = next((el for el in root.iter() if _local(el.tag) == "PolyData"), None)
    if poly_elem is None:
        raise ValueError(f"VTP file {path} has no <PolyData> element")

    field_data: dict[str, np.ndarray] = {}
    for child in poly_elem:
        if _local(child.tag) == "FieldData":
            for da in child:
                if _local(da.tag) != "DataArray":
                    continue
                name = da.attrib.get("Name")
                if not name:
                    continue
                parsed = _parse_numeric_data_array(da, dtype=np.float64)
                if parsed is not None:
                    field_data[name] = parsed

    piece = next((el for el in poly_elem if _local(el.tag) == "Piece"), None)
    if piece is None:
        return VTPBundleData(
            points=np.zeros((0, 3), dtype=np.float32),
            triangles=np.zeros((0, 3), dtype=np.uint32),
            field_data=field_data,
            time_value=_extract_time_from_fields(field_data),
        )

    expected_points = int(piece.attrib.get("NumberOfPoints", "0"))
    points = np.zeros((0, 3), dtype=np.float32)
    point_data: dict[str, np.ndarray] = {}
    triangles = np.zeros((0, 3), dtype=np.uint32)

    for section in piece:
        sec_name = _local(section.tag)
        if sec_name == "Points":
            da = next((el for el in section if _local(el.tag) == "DataArray"), None)
            if da is not None:
                raw_pts = _parse_numeric_data_array(da, dtype=np.float32)
                if raw_pts is not None:
                    if raw_pts.ndim == 1 and expected_points > 0 and raw_pts.size in (2 * expected_points, 3 * expected_points):
                        raw_pts = raw_pts.reshape(expected_points, -1)
                    points = _pad_points_3d(raw_pts)
        elif sec_name == "PointData":
            for da in section:
                if _local(da.tag) != "DataArray":
                    continue
                name = da.attrib.get("Name")
                if not name:
                    continue
                parsed = _parse_numeric_data_array(da, dtype=np.float64)
                if parsed is not None:
                    point_data[name] = parsed
        elif sec_name == "Polys":
            conn: np.ndarray | None = None
            offsets: np.ndarray | None = None
            for da in section:
                if _local(da.tag) != "DataArray":
                    continue
                da_name = da.attrib.get("Name", "")
                if da_name == "connectivity":
                    conn = _parse_numeric_data_array(da, dtype=np.int64)
                elif da_name == "offsets":
                    offsets = _parse_numeric_data_array(da, dtype=np.int64)
            if conn is not None and offsets is not None and offsets.size > 0:
                conn_flat = conn.ravel()
                off_flat = offsets.ravel()
                tri_list: list[int] = []
                start = 0
                for end_idx in off_flat:
                    end = int(end_idx)
                    count = end - start
                    if count >= 3 and end <= conn_flat.size:
                        v0 = int(conn_flat[start])
                        for k in range(1, count - 1):
                            tri_list.extend(
                                (
                                    v0,
                                    int(conn_flat[start + k]),
                                    int(conn_flat[start + k + 1]),
                                )
                            )
                    start = end
                if tri_list:
                    triangles = np.asarray(tri_list, dtype=np.uint32).reshape(-1, 3)

    if expected_points and len(points) != expected_points:
        raise ValueError(
            f"VTP {path.name} Piece NumberOfPoints={expected_points} does not match parsed points ({len(points)})"
        )

    return VTPBundleData(
        points=points,
        triangles=triangles,
        point_data=point_data,
        field_data=field_data,
        time_value=_extract_time_from_fields(field_data),
    )


def _has_non_3d_points_header(path: Path) -> bool:
    """Detect ASCII <Points> arrays with NumberOfComponents != 3 (rejected by vtkPoints)."""
    try:
        with path.open("rb") as handle:
            head = handle.read(4096).decode("utf-8", errors="ignore")
    except OSError:
        return False
    pts_idx = head.find("<Points")
    if pts_idx < 0:
        return False
    end_idx = head.find("</Points>", pts_idx)
    snippet = head[pts_idx : (end_idx if end_idx > pts_idx else pts_idx + 512)]
    return 'NumberOfComponents="2"' in snippet or "NumberOfComponents='2'" in snippet


def read_vtp_bundle_data(path: str | Path, *, prefer_vtk: bool = True) -> VTPBundleData:
    """Read geometry, point fields, and field metadata from a complete VTP file."""
    source = Path(path)
    if not source.is_file():
        raise FileNotFoundError(f"VTP file does not exist: {source}")
    before = source.stat()
    if not complete_vtp(source):
        raise ValueError(f"VTP file is incomplete (missing closing </VTKFile>): {source}")

    data: VTPBundleData | None = None
    if prefer_vtk and not _has_non_3d_points_header(source):
        try:
            data = _read_vtp_with_vtk(source)
        except (ImportError, ValueError):
            data = None

    if data is None:
        data = _read_vtp_ascii_xml(source)

    after = source.stat()
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        raise ValueError(f"VTP file changed while reading: {source}")
    return data


def estimate_particle_radius(points: np.ndarray, config: Any) -> float:
    """Compute particle sphere radius matching ``ParticleVisibility._sphere_splat_radius``."""
    spacing = None
    if getattr(config, "geometries", None) is not None and getattr(config.geometries, "global_resolution", None) is not None:
        spacing = config.geometries.global_resolution.particle_spacing
    if spacing is not None and float(spacing) > 0:
        return float(spacing) * 0.5

    if len(points) == 0:
        return 0.01
    mins = points.min(axis=0)
    maxs = points.max(axis=0)
    extents = [float(abs(maxs[i] - mins[i])) for i in range(3)]
    largest = max(extents)
    active = [ext for ext in extents if ext > largest * 1e-8]
    if not active or not np.all(np.isfinite(active)):
        return 0.01
    return float(np.prod(active) / max(len(points), 1)) ** (1.0 / len(active)) * 0.5


def infer_up_axis(config: Any, spatial_dim: int) -> str:
    """Infer vertical axis ('Y' or 'Z') for 3D auto-framing and floor placement."""
    if spatial_dim == 2:
        return "Y"
    gravity = getattr(config, "gravity", None)
    if gravity is not None and len(gravity) >= 3:
        gy, gz = abs(float(gravity[1])), abs(float(gravity[2]))
        if gy > gz and gy > 0:
            return "Y"
        if gz >= gy and gz > 0:
            return "Z"
    return "Y"


def _normalize(vec: Sequence[float], fallback: tuple[float, float, float]) -> np.ndarray:
    arr = np.asarray(vec, dtype=np.float64)
    norm = float(np.linalg.norm(arr))
    if not math.isfinite(norm) or norm < 1e-12:
        return np.asarray(fallback, dtype=np.float64)
    return arr / norm


def build_cycles_camera(
    spatial_dim: int,
    bounds_min: Sequence[float],
    bounds_max: Sequence[float],
    *,
    camera_state: dict[str, Any] | None = None,
    up_axis: str = "Y",
    width: int = 1600,
    height: int = 900,
) -> dict[str, Any]:
    """Build a right-handed Blender camera specification from live VTK state or bounds."""
    lo = np.asarray(bounds_min, dtype=np.float64)
    hi = np.asarray(bounds_max, dtype=np.float64)
    if not (np.all(np.isfinite(lo)) and np.all(np.isfinite(hi))):
        lo = np.array([-0.5, -0.5, -0.5], dtype=np.float64)
        hi = np.array([0.5, 0.5, 0.5], dtype=np.float64)

    center = 0.5 * (lo + hi)
    span = np.maximum(hi - lo, 0.0)
    diagonal = max(float(np.linalg.norm(span)), 1e-4)
    aspect = max(float(width) / max(float(height), 1.0), 1e-3)

    if camera_state is not None and len(camera_state.get("position", ())) == 3:
        pos_raw, focal_raw, up_raw = camera_state["position"]
        pos = np.asarray(pos_raw, dtype=np.float64)
        focal = np.asarray(focal_raw, dtype=np.float64)
        up_hint = np.asarray(up_raw, dtype=np.float64)
        is_ortho = bool(camera_state.get("parallel_projection", spatial_dim == 2))
        parallel_scale = max(float(camera_state.get("parallel_scale", 0.6 * diagonal)), 1e-6)
        fov_y_deg = float(np.clip(float(camera_state.get("view_angle", 30.0)), 1.0, 160.0))
    else:
        focal = center.copy()
        fov_y_deg = 30.0
        if spatial_dim == 2:
            is_ortho = True
            dx = max(float(span[0]), 1e-4)
            dy = max(float(span[1]), 1e-4)
            # VTK parallel_scale is half the vertical height in world units.
            parallel_scale = 0.5 * max(dy, dx / aspect) * 1.18
            pos = np.array([center[0], center[1], center[2] + max(2.5 * diagonal, 1.0)], dtype=np.float64)
            up_hint = np.array([0.0, 1.0, 0.0], dtype=np.float64)
        else:
            is_ortho = False
            half_fov_y = math.radians(fov_y_deg * 0.5)
            half_fov_x = math.atan(math.tan(half_fov_y) * aspect)
            limiting_half_angle = max(min(half_fov_x, half_fov_y), math.radians(5.0))
            dist = (0.58 * diagonal) / math.sin(limiting_half_angle)
            if up_axis == "Z":
                view_dir = _normalize((1.30, -1.35, 0.90), (0.0, -1.0, 0.0))
                up_hint = np.array([0.0, 0.0, 1.0], dtype=np.float64)
            else:
                view_dir = _normalize((1.25, 0.85, 1.45), (0.0, 0.0, 1.0))
                up_hint = np.array([0.0, 1.0, 0.0], dtype=np.float64)
            pos = focal + view_dir * dist
            parallel_scale = 0.6 * diagonal

    forward = focal - pos
    dist_to_focal = float(np.linalg.norm(forward))
    if not math.isfinite(dist_to_focal) or dist_to_focal < 1e-12:
        forward = np.array([0.0, 0.0, -1.0], dtype=np.float64)
        dist_to_focal = max(2.0 * diagonal, 1.0)
        pos = focal - forward * dist_to_focal
    else:
        forward = forward / dist_to_focal

    right = np.cross(forward, up_hint)
    if float(np.linalg.norm(right)) < 1e-9:
        alt_up = np.array([0.0, 1.0, 0.0]) if abs(forward[1]) < 0.9 else np.array([1.0, 0.0, 0.0])
        right = np.cross(forward, alt_up)
    right = _normalize(right, (1.0, 0.0, 0.0))
    true_up = _normalize(np.cross(right, forward), (0.0, 1.0, 0.0))

    # Blender cameras look down local -Z, with +X right and +Y up.
    back = -forward
    matrix_world = [
        [float(right[0]), float(true_up[0]), float(back[0]), float(pos[0])],
        [float(right[1]), float(true_up[1]), float(back[1]), float(pos[1])],
        [float(right[2]), float(true_up[2]), float(back[2]), float(pos[2])],
        [0.0, 0.0, 0.0, 1.0],
    ]

    clip_start = max(min(dist_to_focal * 0.01, diagonal * 0.01), diagonal * 1e-4, 1e-6)
    clip_end = max(dist_to_focal + 8.0 * diagonal, clip_start * 1000.0)

    return {
        "type": "ORTHO" if is_ortho else "PERSP",
        "sensor_fit": "VERTICAL",
        "ortho_scale": float(2.0 * parallel_scale),
        "fov_y_degrees": float(fov_y_deg),
        "clip_start": float(clip_start),
        "clip_end": float(clip_end),
        "location": [float(v) for v in pos],
        "focal_point": [float(v) for v in focal],
        "up": [float(v) for v in true_up],
        "matrix_world": matrix_world,
    }


def resolve_scalar_mapping(
    body_name: str,
    field_name: str,
    point_data: dict[str, np.ndarray],
    field_data: dict[str, np.ndarray],
    *,
    palette_name: str = _DEFAULT_PALETTE,
    prescribed_range: tuple[float, float] | None = None,
) -> dict[str, Any] | None:
    """Resolve scalar/vector-magnitude values, range, and palette stops matching ``ParticleVisibility``."""
    if field_name not in point_data:
        return None
    raw = np.asarray(point_data[field_name])
    if not np.issubdtype(raw.dtype, np.number):
        return None
    is_vector = False
    if raw.ndim == 2 and raw.shape[1] in (2, 3):
        is_vector = True
        values = np.linalg.norm(raw.astype(np.float64), axis=1)
    elif raw.ndim == 1:
        values = raw.astype(np.float64)
    else:
        return None

    finite = values[np.isfinite(values)]
    if finite.size == 0:
        return None

    reference: float | None = None
    ref_arr = field_data.get(f"ScalingReference_{field_name}")
    if ref_arr is not None:
        flat_ref = np.asarray(ref_arr).ravel()
        if (
            flat_ref.size == 1
            and np.issubdtype(flat_ref.dtype, np.number)
            and math.isfinite(float(flat_ref[0]))
            and float(flat_ref[0]) > 0.0
        ):
            reference = float(flat_ref[0])

    if prescribed_range is not None:
        vmin, vmax = float(prescribed_range[0]), float(prescribed_range[1])
    elif reference is not None:
        vmin, vmax = (0.0 if is_vector else -reference), reference
    else:
        vmin, vmax = float(finite.min()), float(finite.max())
        if vmin == vmax:
            pad = max(abs(vmin) * 0.01, 1e-12)
            vmin, vmax = vmin - pad, vmax + pad

    chosen_palette = palette_name if palette_name in _PARTICLE_PALETTES else _DEFAULT_PALETTE
    colours = list(_PARTICLE_PALETTES[chosen_palette])
    if chosen_palette == _DEFAULT_PALETTE and is_vector:
        colours = [colours[0], colours[-1]]

    stops = [
        [
            float(idx / max(len(colours) - 1, 1)),
            [float(c[0]), float(c[1]), float(c[2])],
        ]
        for idx, c in enumerate(colours)
    ]

    clean_values = np.where(np.isfinite(values), values, vmin).astype(np.float32)
    title = f"{body_name}: {field_name}" + (" magnitude" if is_vector else "")
    return {
        "field": field_name,
        "is_vector_magnitude": is_vector,
        "title": title,
        "vmin": float(vmin),
        "vmax": float(vmax),
        "palette": chosen_palette,
        "stops": stops,
        "array": clean_values,
    }


def _filter_particles_by_clip(
    body_name: str,
    points: np.ndarray,
    point_data: dict[str, np.ndarray],
    state: CyclesSceneState,
) -> tuple[np.ndarray, dict[str, np.ndarray]]:
    if body_name not in state.clipped_bodies or len(points) == 0:
        return points, point_data

    if body_name in state.clip_origins:
        origin = np.asarray(state.clip_origins[body_name], dtype=np.float64)
    else:
        origin = 0.5 * (points.min(axis=0).astype(np.float64) + points.max(axis=0).astype(np.float64))
    normal = _normalize(state.clip_normals.get(body_name, (1.0, 0.0, 0.0)), (1.0, 0.0, 0.0))
    inverted = body_name in state.inverted_clip_bodies
    signed = (points.astype(np.float64) - origin) @ normal
    keep = signed >= 0.0 if inverted else signed <= 0.0
    filtered_pd = {
        key: np.asarray(val)[keep]
        for key, val in point_data.items()
        if len(val) == len(points)
    }
    return np.ascontiguousarray(points[keep], dtype=np.float32), filtered_pd


def _length_scale_for_config(config: Any) -> float:
    dims = getattr(config, "characteristic_dimensions", None) or []
    for item in dims:
        if getattr(item, "name", None) == "Length":
            return float(item.value)
    return 1.0


def _build_world_rig(
    spatial_dim: int,
    bounds_min: np.ndarray,
    bounds_max: np.ndarray,
    *,
    up_axis: str,
    particle_radius: float,
    transparent_bg: bool,
) -> dict[str, Any]:
    span = np.maximum(bounds_max - bounds_min, 1e-4)
    diag = max(float(np.linalg.norm(span)), 1e-4)
    center = 0.5 * (bounds_min + bounds_max)

    if spatial_dim == 2:
        lights = [
            {"name": "KeySun", "direction": [0.25, -0.35, -1.0], "energy": 3.0, "angle_deg": 10.0, "color": [1.0, 0.99, 0.97]},
            {"name": "FillSun", "direction": [-0.35, 0.30, -1.0], "energy": 1.1, "angle_deg": 35.0, "color": [0.88, 0.93, 1.0]},
        ]
        ground = {"enabled": False, "axis": "Z", "level": 0.0, "center": [float(v) for v in center], "size": float(4.0 * diag), "shadow_catcher": True}
    elif up_axis == "Z":
        lights = [
            {"name": "KeySun", "direction": [-0.55, 0.45, -0.85], "energy": 3.2, "angle_deg": 7.5, "color": [1.0, 0.98, 0.95]},
            {"name": "FillSun", "direction": [0.65, 0.55, -0.55], "energy": 1.1, "angle_deg": 35.0, "color": [0.85, 0.92, 1.0]},
            {"name": "RimSun", "direction": [-0.35, -0.75, -0.45], "energy": 0.85, "angle_deg": 15.0, "color": [1.0, 0.96, 0.90]},
        ]
        ground = {
            "enabled": True,
            "axis": "Z",
            "level": float(bounds_min[2] - max(particle_radius, diag * 0.002)),
            "center": [float(center[0]), float(center[1]), float(bounds_min[2] - max(particle_radius, diag * 0.002))],
            "size": float(6.0 * diag),
            "shadow_catcher": True,
        }
    else:
        lights = [
            {"name": "KeySun", "direction": [-0.55, -0.85, -0.45], "energy": 3.2, "angle_deg": 7.5, "color": [1.0, 0.98, 0.95]},
            {"name": "FillSun", "direction": [0.65, -0.55, -0.55], "energy": 1.1, "angle_deg": 35.0, "color": [0.85, 0.92, 1.0]},
            {"name": "RimSun", "direction": [-0.35, -0.45, 0.75], "energy": 0.85, "angle_deg": 15.0, "color": [1.0, 0.96, 0.90]},
        ]
        ground = {
            "enabled": True,
            "axis": "Y",
            "level": float(bounds_min[1] - max(particle_radius, diag * 0.002)),
            "center": [float(center[0]), float(bounds_min[1] - max(particle_radius, diag * 0.002)), float(center[2])],
            "size": float(6.0 * diag),
            "shadow_catcher": True,
        }

    return {
        "background_color": [float(c) for c in PREVIEW_MAIN_BACKGROUND],
        "sky_zenith": [0.30, 0.36, 0.46],
        "sky_horizon": [0.72, 0.75, 0.80],
        "env_strength": 0.95,
        "transparent_bg": bool(transparent_bg),
        "lights": lights,
        "ground_plane": ground,
    }


def _export_static_meshes(
    bundle_dir: Path,
    config: Any,
    shape_dirs: Sequence[Path],
    *,
    shape_bounds: dict[str, Any] | None = None,
    has_particle_bodies: set[str] | None = None,
    show_domain: bool = False,
) -> tuple[list[dict[str, Any]], np.ndarray, np.ndarray]:
    """Export solid/fluid/continuum shape meshes into ``bundle_dir``."""
    particle_set = has_particle_bodies or set()
    fluids = {b.name for b in config.fluid_bodies}
    continua = {b.name for b in config.continuum_bodies}
    solids = {b.name for b in config.solid_bodies}
    has_active_media = bool(fluids or continua)

    shapes_by_name = {s.name: s for s in config.geometries.shapes}
    length_scale = _length_scale_for_config(config)

    low = np.full(3, np.inf, dtype=np.float64)
    high = np.full(3, -np.inf, dtype=np.float64)
    meshes_out: list[dict[str, Any]] = []

    def _find_shape_vtp(name: str) -> Path | None:
        target = f"Shape{name}.vtp"
        for directory in shape_dirs:
            direct = directory / target
            if direct.is_file():
                return direct
            nested = directory / "output" / target
            if nested.is_file():
                return nested
        return None

    candidate_names = [
        s.name for s in config.geometries.shapes if s.name in (fluids | continua | solids)
    ]

    for name in candidate_names:
        role = "fluid" if name in fluids else "continuum" if name in continua else "solid"
        # When particle snapshots exist for a fluid/continuum/deformable body, hide its static setup shape.
        if name in particle_set:
            continue

        positions: np.ndarray | None = None
        triangles: np.ndarray | None = None
        vtp_path = _find_shape_vtp(name)
        if vtp_path is not None:
            try:
                vtp_data = read_vtp_bundle_data(vtp_path)
                if len(vtp_data.points) > 0 and len(vtp_data.triangles) > 0:
                    positions = vtp_data.points
                    triangles = vtp_data.triangles
            except Exception:
                positions = None

        if positions is None and shape_bounds and name in shape_bounds:
            lo_b, hi_b = shape_bounds[name]
            scaled_lo = [float(v) * length_scale for v in lo_b]
            scaled_hi = [float(v) * length_scale for v in hi_b]
            shape_cfg = shapes_by_name.get(name)
            if (
                shape_cfg is not None
                and shape_cfg.type.value == "complex_shape"
                and list(shape_cfg.operations or []) == ["union", "subtraction"]
                and len(shape_cfg.sub_shapes or []) == 2
                and all(sub in shape_bounds for sub in (shape_cfg.sub_shapes or []))
            ):
                outer_name, inner_name = shape_cfg.sub_shapes
                op, oi = box_mesh(
                    [float(v) * length_scale for v in shape_bounds[outer_name][0]],
                    [float(v) * length_scale for v in shape_bounds[outer_name][1]],
                )
                ip, ii = box_mesh(
                    [float(v) * length_scale for v in shape_bounds[inner_name][0]],
                    [float(v) * length_scale for v in shape_bounds[inner_name][1]],
                )
                positions = np.concatenate((op, ip)).astype(np.float32)
                flipped_inner = (ii.reshape(-1, 3)[:, ::-1] + len(op)).reshape(-1)
                triangles = np.concatenate((oi, flipped_inner)).astype(np.uint32).reshape(-1, 3)
            else:
                pts, idx = box_mesh(scaled_lo, scaled_hi)
                positions = np.asarray(pts, dtype=np.float32)
                triangles = np.asarray(idx, dtype=np.uint32).reshape(-1, 3)

        if positions is None or triangles is None or len(positions) == 0 or len(triangles) == 0:
            continue

        low = np.minimum(low, positions.min(axis=0))
        high = np.maximum(high, positions.max(axis=0))
        pos_buf = write_buffer(bundle_dir, "geometry", positions, dtype="<f4")
        idx_buf = write_buffer(bundle_dir, "indices", triangles.reshape(-1), dtype="<u4")

        body_rgb = [float(c) for c in (_SOLID_COLOUR if role == "solid" else _body_colour(name, config))]
        if role == "solid":
            mat_spec = {
                "kind": "acrylic_wall" if has_active_media else "matte_solid",
                "color": body_rgb,
                "alpha": 0.22 if has_active_media else 1.0,
                "roughness": 0.14 if has_active_media else 0.45,
                "ior": 1.49,
            }
        else:
            mat_spec = {
                "kind": "translucent_shape",
                "color": body_rgb,
                "alpha": 0.62,
                "roughness": 0.18,
                "ior": 1.333 if role == "fluid" else 1.45,
            }

        meshes_out.append(
            {
                "id": name,
                "role": role,
                "wireframe": False,
                "positions": {**pos_buf, "count": int(len(positions))},
                "indices": {**idx_buf, "count": int(len(triangles))},
                "material": mat_spec,
            }
        )

    if show_domain and config.geometries.system_domain is not None:
        dom = config.geometries.system_domain
        pts, idx = box_mesh(list(dom.lower_bound), list(dom.upper_bound))
        positions = np.asarray(pts, dtype=np.float32)
        triangles = np.asarray(idx, dtype=np.uint32).reshape(-1, 3)
        low = np.minimum(low, positions.min(axis=0))
        high = np.maximum(high, positions.max(axis=0))
        meshes_out.append(
            {
                "id": "SystemDomain",
                "role": "domain",
                "wireframe": True,
                "positions": {**write_buffer(bundle_dir, "geometry", positions, dtype="<f4"), "count": int(len(positions))},
                "indices": {**write_buffer(bundle_dir, "indices", triangles.reshape(-1), dtype="<u4"), "count": int(len(triangles))},
                "material": {
                    "kind": "wireframe",
                    "color": [0.55, 0.59, 0.64],
                    "alpha": 0.6,
                    "roughness": 0.5,
                    "ior": 1.0,
                },
            }
        )

    return meshes_out, low, high


def _collect_sequence_frames(
    data_dir: Path,
    body_names: Sequence[str],
    frame_selector: str,
) -> list[tuple[str, dict[str, Any]]]:
    """Return ordered list of ``(frame_label, {body: ResultFrame})`` for single or ``all`` mode."""
    from sphinxsim.results import AnalysisError, ResultFrame, VTPPositionReader, _select_body_frames

    selector = frame_selector.strip().lower()
    if selector != "all":
        selected = _select_body_frames(data_dir, body_names, frame_selector)
        return [(frame_selector, selected)] if selected else []

    reader = VTPPositionReader()
    vtp_files = sorted(data_dir.glob("*.vtp"))
    per_body: dict[str, list[ResultFrame]] = {b: [] for b in body_names}
    for path in vtp_files:
        matched = None
        for cand in sorted(body_names, key=len, reverse=True):
            if path.stem.startswith(f"{cand}_"):
                matched = cand
                break
        if matched is None:
            continue
        suffix = path.stem[len(matched) + 1 :]
        if suffix.startswith("ite_"):
            continue
        try:
            t_val = reader._read_time_value(path)
            if math.isfinite(t_val):
                stat = path.stat()
                per_body[matched].append(
                    ResultFrame(matched, path.resolve(), t_val, False, (stat.st_size, stat.st_mtime_ns))
                )
        except AnalysisError:
            continue

    for body in per_body:
        per_body[body].sort(key=lambda item: (item.time, item.path.name))

    active_lists = {b: frames for b, frames in per_body.items() if frames}
    if not active_lists:
        return []

    max_count = max(len(frames) for frames in active_lists.values())
    sequence: list[tuple[str, dict[str, Any]]] = []
    for idx in range(max_count):
        step_map: dict[str, Any] = {}
        for body, frames in active_lists.items():
            chosen = frames[min(idx, len(frames) - 1)]
            step_map[body] = chosen
        sequence.append((f"frame_{idx:04d}", step_map))
    return sequence


def export_cycles_scene_bundle(
    config: Any,
    bundle_dir: str | Path,
    *,
    spatial_dim: int,
    config_path: str | Path | None = None,
    data_dir: str | Path | None = None,
    shape_dirs: Sequence[str | Path] = (),
    shape_bounds: dict[str, Any] | None = None,
    explicit_particle_vtps: dict[str, Path] | None = None,
    frame: str = "latest",
    color_by: str | None = None,
    scene_state: CyclesSceneState | None = None,
    width: int = 1600,
    height: int = 900,
    samples: int = 64,
    fluid_surface: bool = False,
    transparent_bg: bool = False,
    use_denoising: bool = True,
    show_domain: bool = False,
) -> dict[str, Any]:
    """Export a complete Cycles Scene Bundle (``scene.json`` + ``.bin`` buffers)."""
    dest = Path(bundle_dir).resolve()
    dest.mkdir(parents=True, exist_ok=True)
    state = scene_state or CyclesSceneState()

    fluids = {b.name for b in config.fluid_bodies}
    continua = {b.name for b in config.continuum_bodies}
    all_bodies = [b.name for b in (config.fluid_bodies + config.continuum_bodies + config.solid_bodies)]

    resolved_shape_dirs: list[Path] = [Path(d).resolve() for d in shape_dirs if d is not None]
    resolved_data_dir: Path | None = None
    if data_dir is not None:
        raw_dir = Path(data_dir).resolve()
        resolved_data_dir = raw_dir / "output" if (raw_dir / "output").is_dir() else raw_dir
        if resolved_data_dir not in resolved_shape_dirs:
            resolved_shape_dirs.insert(0, resolved_data_dir)
        if raw_dir not in resolved_shape_dirs:
            resolved_shape_dirs.append(raw_dir)

    frame_entries_raw: list[tuple[str, dict[str, tuple[Path, float | None, bool]]]] = []
    if explicit_particle_vtps is not None:
        step_dict = {
            body: (Path(vtp_path).resolve(), None, "_ite_" in Path(vtp_path).stem)
            for body, vtp_path in explicit_particle_vtps.items()
        }
        if step_dict:
            frame_entries_raw.append(("preview", step_dict))
    elif resolved_data_dir is not None and resolved_data_dir.is_dir():
        seq = _collect_sequence_frames(resolved_data_dir, all_bodies, frame)
        for label, body_frames in seq:
            frame_entries_raw.append(
                (
                    label,
                    {
                        body: (rf.path, rf.time, rf.generation)
                        for body, rf in body_frames.items()
                    },
                )
            )

    particle_bodies_present: set[str] = set()
    for _, step_map in frame_entries_raw:
        particle_bodies_present.update(b for b in step_map if b not in state.hidden_bodies)

    meshes_out, low, high = _export_static_meshes(
        dest,
        config,
        resolved_shape_dirs,
        shape_bounds=shape_bounds,
        has_particle_bodies=particle_bodies_present,
        show_domain=show_domain,
    )

    frames_out: list[dict[str, Any]] = []
    bodies_rendered_any: set[str] = set()
    coloured_bodies_any: set[str] = set()
    available_fields_anywhere: set[str] = set()
    representative_radius = 0.01

    if not frame_entries_raw:
        legend_items = [
            {"label": label, "color": [float(c) for c in col]}
            for label, col in _legend_entries_for_config(config, set())
        ]
        frames_out.append(
            {
                "index": 0,
                "label": "setup",
                "time": None,
                "is_relaxation": False,
                "output_image": None,
                "particles": [],
                "overlay": {
                    "time": None,
                    "is_relaxation": False,
                    "colorbars": [],
                    "legend": legend_items,
                },
            }
        )
    else:
        is_multi = len(frame_entries_raw) > 1
        for frame_idx, (frame_label, step_map) in enumerate(frame_entries_raw):
            particles_out: list[dict[str, Any]] = []
            colorbars_out: list[dict[str, Any]] = []
            frame_times: list[float] = []
            any_relaxation = False

            for body_name in all_bodies:
                if body_name not in step_map or body_name in state.hidden_bodies:
                    continue
                vtp_path, known_time, is_gen = step_map[body_name]
                vtp_data = read_vtp_bundle_data(vtp_path)
                t_val = known_time if known_time is not None else vtp_data.time_value
                if t_val is not None and not is_gen:
                    frame_times.append(float(t_val))
                if is_gen:
                    any_relaxation = True

                for fname, farr in vtp_data.point_data.items():
                    if fname.startswith("_sphinxsim_colour_"):
                        continue
                    arr_np = np.asarray(farr)
                    if np.issubdtype(arr_np.dtype, np.number) and (
                        arr_np.ndim == 1 or (arr_np.ndim == 2 and arr_np.shape[1] in (2, 3))
                    ):
                        available_fields_anywhere.add(fname)

                pts_filtered, pd_filtered = _filter_particles_by_clip(
                    body_name,
                    vtp_data.points,
                    vtp_data.point_data,
                    state,
                )
                if len(pts_filtered) == 0:
                    continue

                low = np.minimum(low, pts_filtered.min(axis=0))
                high = np.maximum(high, pts_filtered.max(axis=0))
                radius = estimate_particle_radius(pts_filtered, config)
                representative_radius = radius
                role = "fluid" if body_name in fluids else "continuum" if body_name in continua else "solid"

                requested_field = color_by if color_by is not None else state.colouring.get(body_name)
                scalar_spec: dict[str, Any] | None = None
                if requested_field is not None:
                    mapping = resolve_scalar_mapping(
                        body_name,
                        requested_field,
                        pd_filtered,
                        vtp_data.field_data,
                        palette_name=state.palettes.get(body_name, _DEFAULT_PALETTE),
                        prescribed_range=state.rescale_ranges.get((body_name, requested_field)),
                    )
                    if mapping is not None:
                        val_buf = write_buffer(dest, "scalar", mapping["array"], dtype="<f4")
                        scalar_spec = {
                            "field": mapping["field"],
                            "is_vector_magnitude": mapping["is_vector_magnitude"],
                            "title": mapping["title"],
                            "vmin": mapping["vmin"],
                            "vmax": mapping["vmax"],
                            "palette": mapping["palette"],
                            "stops": mapping["stops"],
                            "values": val_buf,
                        }
                        coloured_bodies_any.add(body_name)
                        colorbars_out.append(
                            {
                                "title": mapping["title"],
                                "vmin": mapping["vmin"],
                                "vmax": mapping["vmax"],
                                "palette": mapping["palette"],
                                "stops": mapping["stops"],
                            }
                        )

                pos_buf = write_buffer(dest, "points", pts_filtered, dtype="<f4")
                use_fluid_mesh = bool(fluid_surface and role == "fluid" and spatial_dim == 3)
                # Note: in 2D, fluid_surface can also extrude thin volume, or spheres; keep 3D/2D smooth!
                if fluid_surface and role == "fluid":
                    use_fluid_mesh = True

                particles_out.append(
                    {
                        "body": body_name,
                        "role": role,
                        "count": int(len(pts_filtered)),
                        "radius": float(radius),
                        "particle_spacing": float(radius * 2.0),
                        "fluid_surface": use_fluid_mesh,
                        "positions": pos_buf,
                        "base_color": [float(c) for c in _body_colour(body_name, config)],
                        "opacity": 1.0 if body_name in state.opaque_bodies else float(_PARTICLE_DEFAULT_OPACITY),
                        "scalar": scalar_spec,
                    }
                )
                bodies_rendered_any.add(body_name)

            uncoloured_bodies = {
                p["body"] for p in particles_out if p["scalar"] is None
            }
            legend_items = [
                {"label": label, "color": [float(c) for c in col]}
                for label, col in _legend_entries_for_config(config, uncoloured_bodies)
            ] if uncoloured_bodies else []

            resolved_time = max(frame_times) if frame_times else None
            frames_out.append(
                {
                    "index": frame_idx,
                    "label": frame_label,
                    "time": resolved_time,
                    "is_relaxation": any_relaxation and resolved_time is None,
                    "output_image": f"frame_{frame_idx:04d}.png" if is_multi else None,
                    "particles": particles_out,
                    "overlay": {
                        "time": resolved_time,
                        "is_relaxation": any_relaxation and resolved_time is None,
                        "colorbars": colorbars_out,
                        "legend": legend_items,
                    },
                }
            )

    if color_by is not None and frame_entries_raw and not coloured_bodies_any:
        avail_str = ", ".join(sorted(available_fields_anywhere)) or "none"
        raise ValueError(
            f"Field {color_by!r} is not available in particle frames (available: {avail_str})"
        )

    if not (np.all(np.isfinite(low)) and np.all(np.isfinite(high))):
        dom = config.geometries.system_domain
        if dom is not None:
            lo_list = list(dom.lower_bound) + [0.0] * (3 - len(dom.lower_bound))
            hi_list = list(dom.upper_bound) + [0.0] * (3 - len(dom.upper_bound))
            low = np.asarray(lo_list, dtype=np.float64)
            high = np.asarray(hi_list, dtype=np.float64)
        else:
            low = np.array([-0.5, -0.5, -0.5], dtype=np.float64)
            high = np.array([0.5, 0.5, 0.5], dtype=np.float64)

    up_axis = infer_up_axis(config, spatial_dim)
    camera_spec = build_cycles_camera(
        spatial_dim,
        low,
        high,
        camera_state=state.camera_state,
        up_axis=up_axis,
        width=width,
        height=height,
    )
    world_spec = _build_world_rig(
        spatial_dim,
        low,
        high,
        up_axis=up_axis,
        particle_radius=representative_radius,
        transparent_bg=transparent_bg,
    )

    spacing_cfg = (
        config.geometries.global_resolution.particle_spacing
        if getattr(config, "geometries", None) and getattr(config.geometries, "global_resolution", None)
        else None
    )
    scene_payload = {
        "schema_version": SCHEMA_VERSION,
        "title": "SPHinXsim Cycles Scene",
        "spatial_dim": int(spatial_dim),
        "up_axis": up_axis,
        "config_file": str(config_path) if config_path is not None else None,
        "render": {
            "width": int(width),
            "height": int(height),
            "samples": int(max(samples, 1)),
            "use_denoising": bool(use_denoising),
            "transparent_bg": bool(transparent_bg),
            "fluid_surface": bool(fluid_surface),
            "particle_spacing": float(spacing_cfg) if spacing_cfg is not None else None,
        },
        "scene_bounds": {
            "min": [float(v) for v in low],
            "max": [float(v) for v in high],
            "center": [float(v) for v in 0.5 * (low + high)],
            "diagonal": float(max(np.linalg.norm(np.maximum(high - low, 0.0)), 1e-4)),
        },
        "camera": camera_spec,
        "world": world_spec,
        "meshes": meshes_out,
        "frames": frames_out,
    }

    scene_file = dest / "scene.json"
    atomic_json(scene_file, scene_payload)
    return {
        "bundle_dir": str(dest),
        "scene_file": str(scene_file),
        "scene_sha256": hash_file(scene_file),
        "meshes_exported": len(meshes_out),
        "frames_exported": len(frames_out),
        "bodies_rendered": sorted(bodies_rendered_any),
        "color_by": color_by,
        "coloured_bodies": sorted(coloured_bodies_any),
    }


def export_job_manifest_cycles_bundle(
    result_dir: str | Path,
    manifest: dict[str, Any],
    config: Any,
    bundle_dir: str | Path,
    *,
    color_by: str | None = None,
    width: int = 1600,
    height: int = 900,
    samples: int = 64,
    fluid_surface: bool = False,
    transparent_bg: bool = False,
) -> dict[str, Any]:
    """Export a Cycles Scene Bundle directly from an immutable published MCP job manifest."""
    src_dir = Path(result_dir).resolve()
    dest = Path(bundle_dir).resolve()
    dest.mkdir(parents=True, exist_ok=True)
    spatial_dim = int(manifest.get("dimension", 3))

    particle_vtps: dict[str, Path] = {}
    for body_name, frames in manifest.get("bodies", {}).items():
        if frames:
            particle_vtps[body_name] = (src_dir / frames[-1]["source"]).resolve()

    summary = export_cycles_scene_bundle(
        config,
        dest,
        spatial_dim=spatial_dim,
        explicit_particle_vtps=particle_vtps or None,
        color_by=color_by,
        width=width,
        height=height,
        samples=samples,
        fluid_surface=fluid_surface,
        transparent_bg=transparent_bg,
    )

    # If the published manifest includes pre-triangulated geometry buffers not covered by VTPs, attach them.
    scene_file = Path(summary["scene_file"])
    scene_data = json.loads(scene_file.read_text(encoding="utf-8"))
    existing_ids = {m["id"] for m in scene_data["meshes"]}
    low = np.asarray(scene_data["scene_bounds"]["min"], dtype=np.float64)
    high = np.asarray(scene_data["scene_bounds"]["max"], dtype=np.float64)
    added_any = False

    for geom in manifest.get("geometry", []):
        gid = geom["id"]
        role = geom.get("role", "solid")
        if gid in existing_ids or role in ("domain", "boundary") or gid in particle_vtps:
            continue
        if "positions" not in geom or "indices" not in geom:
            continue
        pos = np.fromfile(src_dir / geom["positions"]["file"], dtype="<f4").reshape(-1, 3)
        tri = np.fromfile(src_dir / geom["indices"]["file"], dtype="<u4").reshape(-1, 3)
        if len(pos) == 0 or len(tri) == 0:
            continue
        low = np.minimum(low, pos.min(axis=0))
        high = np.maximum(high, pos.max(axis=0))
        pos_buf = write_buffer(dest, "geometry", pos, dtype="<f4")
        idx_buf = write_buffer(dest, "indices", tri.reshape(-1), dtype="<u4")
        is_solid = role == "solid"
        scene_data["meshes"].append(
            {
                "id": gid,
                "role": role,
                "wireframe": False,
                "positions": {**pos_buf, "count": int(len(pos))},
                "indices": {**idx_buf, "count": int(len(tri))},
                "material": {
                    "kind": "acrylic_wall" if is_solid and particle_vtps else ("matte_solid" if is_solid else "translucent_shape"),
                    "color": list(_SOLID_COLOUR if is_solid else _body_colour(gid, config)),
                    "alpha": 0.22 if is_solid and particle_vtps else (1.0 if is_solid else 0.62),
                    "roughness": 0.16,
                    "ior": 1.49 if is_solid else 1.333,
                },
            }
        )
        added_any = True

    if added_any:
        up_axis = scene_data.get("up_axis", "Y")
        scene_data["scene_bounds"] = {
            "min": [float(v) for v in low],
            "max": [float(v) for v in high],
            "center": [float(v) for v in 0.5 * (low + high)],
            "diagonal": float(max(np.linalg.norm(np.maximum(high - low, 0.0)), 1e-4)),
        }
        scene_data["camera"] = build_cycles_camera(
            spatial_dim,
            low,
            high,
            up_axis=up_axis,
            width=width,
            height=height,
        )
        atomic_json(scene_file, scene_data)
        summary["scene_sha256"] = hash_file(scene_file)
        summary["meshes_exported"] = len(scene_data["meshes"])

    return summary
