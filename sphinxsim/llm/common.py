"""Shared utilities for SPHinXsim LLM providers.

This module centralizes provider-agnostic logic used by multiple LLM backends:
- robust JSON text cleanup
- dict merge/diff helpers
- fixture-backed example config selection
- config sanitization and typo canonicalization
- instruction intent helpers (simulation type and shape rename)
"""

from __future__ import annotations

import json
import math
import re
import warnings
from difflib import get_close_matches
from pathlib import Path
from typing import Any, Dict

from sphinxsim.config.schemas import SimulationConfig


class LLMRepairWarning(UserWarning):
    """An LLM repair retry changed a generated configuration."""


def report_llm_repair(before: Any, after: Any, *, max_changes: int = 12) -> None:
    """Report leaf-level changes made by a successful LLM repair retry."""
    changes: list[str] = []

    def _walk(old: Any, new: Any, path: str) -> None:
        if len(changes) >= max_changes:
            return
        if isinstance(old, dict) and isinstance(new, dict):
            for key in sorted(set(old) | set(new)):
                child = f"{path}.{key}" if path else str(key)
                if key not in old:
                    changes.append(f"{child}: added {new[key]!r}")
                elif key not in new:
                    changes.append(f"{child}: removed {old[key]!r}")
                else:
                    _walk(old[key], new[key], child)
            return
        if isinstance(old, list) and isinstance(new, list):
            for index in range(max(len(old), len(new))):
                child = f"{path}[{index}]"
                if index >= len(old):
                    changes.append(f"{child}: added {new[index]!r}")
                elif index >= len(new):
                    changes.append(f"{child}: removed {old[index]!r}")
                else:
                    _walk(old[index], new[index], child)
            return
        if old != new:
            changes.append(f"{path}: {old!r} -> {new!r}")

    _walk(before, after, "")
    if not changes:
        return
    suffix = "" if len(changes) < max_changes else f"; showing first {max_changes} changes"
    warnings.warn(
        "LLM repaired the generated config after validation failed: "
        + "; ".join(changes)
        + suffix,
        LLMRepairWarning,
        stacklevel=2,
    )

BODY_TYPE_RULES: str = (
    "STRICT RULES - you must follow these exactly: "
    "(1) fluid_bodies may ONLY contain entries whose material.type is 'weakly_compressible_fluid'. "
    "(2) solid_bodies may ONLY contain entries whose material.type is 'rigid_body'. "
    "(3) continuum_bodies may contain 'general_continuum', 'j2_plasticity', or 'plastic_continuum'. "
    "(4) For granular soil, landslide, slope, column collapse, Drucker-Prager, friction angle, "
    "cohesion, dilatancy, plastic material, or PlasticContinuum requests, "
    "use simulation_type 'continuum_dynamics' with a continuum_bodies material.type of "
    "'plastic_continuum'. "
    "(5) plastic_continuum material requires density, youngs_modulus, poisson_ratio, "
    "and friction_angle; sound_speed, cohesion, and dilatancy_angle are optional. "
    "When sound_speed is omitted, it is calculated at runtime. "
    "(6) friction_angle and dilatancy_angle in JSON are always radians, not degrees; "
    "convert degree values to radians before returning JSON. For plastic_continuum, "
    "use 0 <= poisson_ratio < 0.5, 0 <= friction_angle < pi/2, and, when present, "
    "0 <= dilatancy_angle <= friction_angle. "
    "(7) observers[].variable.real_type must be a plain string such as 'Pressure', never a list. "
    "(8) If the user mentions an STL file or .stl path, represent that geometry as a "
    "triangle_mesh shape with file_name, translation, and scale. "
    "(9) Return ONLY the JSON object - no markdown fences, no comments, no extra keys. "
    "(10) For plastic_continuum, preserve an explicitly supplied sound_speed. When it is "
    "omitted, runtime calculates sqrt(youngs_modulus / (3 * density * (1 - 2 * poisson_ratio))). "
    "(11) Store dimensional JSON values in SI units: length in m, time in s, density "
    "in kg/m^3, speed in m/s, acceleration in m/s^2, and stress-like values such as "
    "youngs_modulus and cohesion in Pa. Convert units explicitly stated by the user "
    "(for example mm or cm to m, kPa/MPa/GPa to Pa, and degrees to radians) before "
    "returning JSON. "
    "(12) For every generated 2D particle wall represented by a multipolygon "
    "container_box, if the user does not explicitly specify wall thickness, set it "
    "to four times the final particle_spacing. An explicitly requested wall thickness "
    "always takes precedence. "
)

PLASTIC_CONTINUUM_KEYWORDS = re.compile(
    r"\b("
    r"plastic\s*continu(?:um|umn)|plasticcontinuum|"
    r"plastic\s+material|plastic\s+soil|plastic\s+column|"
    r"material\s*type\s*(?:is|=)?\s*plastic[_\s-]?continu(?:um|umn)|"
    r"matertial\s*type\s*(?:is|=)?\s*plastic[_\s-]?continu(?:um|umn)|"
    r"granular|soil|landslide|landsldie|slope|column\s+collapse|column-collapse|"
    r"repose\s+angle|angle\s+of\s+repose|"
    r"drucker[-\s]?prager|friction\s+angle|cohesion|dilatancy"
    r")\b",
    re.IGNORECASE,
)

THREE_D_KEYWORDS = re.compile(
    r"\b(3d|3-d|three[-\s]?d|three[-\s]?dimensional|3\s*dimensional)\b",
    re.IGNORECASE,
)


def strip_code_fences(text: str) -> str:
    stripped = (text or "").strip()
    if stripped.startswith("```"):
        lines = stripped.splitlines()
        if len(lines) >= 3:
            return "\n".join(lines[1:-1]).strip()
    return stripped


def json_safe_errors(errors: Any) -> Any:
    return json.loads(json.dumps(errors, default=str))


def dict_diff(base: Any, updated: Any) -> Any:
    if isinstance(base, dict) and isinstance(updated, dict):
        changed: Dict[str, Any] = {}
        for key in updated.keys():
            if key not in base:
                changed[key] = updated[key]
                continue
            child = dict_diff(base[key], updated[key])
            if child is not None:
                changed[key] = child
        return changed if changed else None

    if isinstance(base, list) and isinstance(updated, list):
        if base != updated:
            return updated
        return None

    if base != updated:
        return updated
    return None


def merge_dicts(base: Dict[str, Any], updates: Dict[str, Any]) -> Dict[str, Any]:
    merged = dict(base)
    for key, value in updates.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = merge_dicts(merged[key], value)
        elif isinstance(value, list) and isinstance(merged.get(key), list):
            base_list = merged[key]
            if all(isinstance(item, dict) for item in value) and all(
                isinstance(item, dict) for item in base_list[: len(value)]
            ):
                merged[key] = [
                    merge_dicts(base_item, update_item)
                    for base_item, update_item in zip(base_list, value)
                ] + base_list[len(value) :]
            else:
                merged[key] = value
        else:
            merged[key] = value
    return merged


def is_all_plastic_continuum_config(config: SimulationConfig) -> bool:
    """Return whether every configured continuum body is PlasticContinuum."""
    return bool(config.continuum_bodies) and all(
        body.material.type.value == "plastic_continuum"
        for body in config.continuum_bodies
    )


def is_all_plastic_continuum_dict(config: Dict[str, Any]) -> bool:
    """Dict equivalent used before schema validation and by fallback logic."""
    bodies = config.get("continuum_bodies")
    return isinstance(bodies, list) and bool(bodies) and all(
        isinstance(body, dict)
        and isinstance(body.get("material"), dict)
        and body["material"].get("type") == "plastic_continuum"
        for body in bodies
    )


def dump_simulation_config_json(
    config: SimulationConfig, *, indent: int | None = None
) -> str:
    """Serialize compactly only for PlasticContinuum field-case configs."""
    return config.model_dump_json(
        indent=indent,
        exclude_none=True,
        exclude_defaults=is_all_plastic_continuum_config(config),
    )


def example_config(description: str) -> Dict[str, Any]:
    project_root = Path(__file__).resolve().parents[2]
    fluid_fixture = (
        project_root
        / "tests"
        / "test_simulation"
        / "test_2d_simulation"
        / "data"
        / "dambreak.json"
    )
    fluid_3d_fixture = (
        project_root
        / "tests"
        / "test_simulation"
        / "test_3d_simulation"
        / "data"
        / "dambreak.json"
    )
    solid_fixture = (
        project_root
        / "tests"
        / "test_simulation"
        / "test_2d_simulation"
        / "data"
        / "milling.json"
    )
    plastic_continuum_fixture = (
        project_root
        / "tests"
        / "test_simulation"
        / "test_2d_simulation"
        / "data"
        / "column_collapse.json"
    )
    plastic_continuum_3d_fixture = (
        project_root
        / "tests"
        / "test_simulation"
        / "test_3d_simulation"
        / "data"
        / "repose_angle.json"
    )
    desc = _description_without_stl_paths(description).lower()
    is_3d_like = bool(THREE_D_KEYWORDS.search(desc))
    is_plastic_continuum_like = bool(PLASTIC_CONTINUUM_KEYWORDS.search(desc))
    is_solid_like = any(token in desc for token in ("solid", "elastic", "beam", "continuum", "milling"))
    if is_3d_like and is_plastic_continuum_like:
        fixtures = (plastic_continuum_3d_fixture, plastic_continuum_fixture, solid_fixture, fluid_3d_fixture)
    elif is_3d_like and not is_plastic_continuum_like and not is_solid_like:
        fixtures = (fluid_3d_fixture, fluid_fixture, plastic_continuum_fixture, solid_fixture)
    elif is_plastic_continuum_like:
        fixtures = (plastic_continuum_fixture, solid_fixture, fluid_fixture)
    elif is_solid_like:
        fixtures = (solid_fixture, plastic_continuum_fixture, fluid_fixture)
    else:
        fixtures = (fluid_fixture, plastic_continuum_fixture, solid_fixture)

    for fixture in fixtures:
        try:
            payload = json.loads(fixture.read_text())
            validated = SimulationConfig.model_validate(payload)
            config = json.loads(dump_simulation_config_json(validated))
            return suppress_implicit_plastic_observers(config, description)
        except Exception:
            continue

    from sphinxsim.llm.mock_llm import MockLLM

    config = json.loads(dump_simulation_config_json(MockLLM().generate(description)))
    return suppress_implicit_plastic_observers(config, description)


_OBSERVER_REQUEST_TERMS = (
    r"(?:observer|observers|probe|probes|sensor|sensors|monitor|monitors)"
)
_EXPLICIT_OBSERVER_REQUEST_RE = re.compile(
    rf"\b{_OBSERVER_REQUEST_TERMS}\b",
    re.IGNORECASE,
)
_NEGATIVE_OBSERVER_REQUEST_RE = re.compile(
    rf"\b(?:no|without)\b[^.!?;,:]{{0,24}}\b{_OBSERVER_REQUEST_TERMS}\b|"
    rf"\b{_OBSERVER_REQUEST_TERMS}\b[^.!?;,:]{{0,12}}\b(?:no|without)\b",
    re.IGNORECASE,
)


def suppress_implicit_plastic_observers(
    config: Dict[str, Any], description: str,
) -> Dict[str, Any]:
    """Remove fixture/LLM-invented observers from new plastic cases.

    Observers remain opt-in: an explicitly requested observer/probe/sensor is
    preserved, while a default observer copied from a plastic fixture is not.
    This is applied only during generation/example construction, not updates.
    """
    is_plastic = any(
        isinstance(body, dict)
        and isinstance(body.get("material"), dict)
        and body["material"].get("type") == "plastic_continuum"
        for body in config.get("continuum_bodies", [])
    )
    if not is_plastic:
        return config

    request_text = description or ""
    explicitly_requested = bool(_EXPLICIT_OBSERVER_REQUEST_RE.search(request_text))
    explicitly_disabled = bool(_NEGATIVE_OBSERVER_REQUEST_RE.search(request_text))
    if not explicitly_requested or explicitly_disabled:
        config.pop("observers", None)
    return config


def apply_explicit_instruction_overrides(cfg: Dict[str, Any], description: str) -> Dict[str, Any]:
    """Apply explicitly stated scalar values without relying on LLM compliance.

    Generation still uses the LLM to adapt the overall configuration, but values
    that are straightforward to parse are authoritative.  This prevents a
    template default from silently winning when a model omits one requested
    field during full-object generation.
    """
    updated = json.loads(json.dumps(cfg))

    # Normalize common Unicode typography before applying deterministic
    # overrides, so provider output cannot silently replace explicit values.
    normalized = (
        description.replace("‘", "'")
        .replace("’", "'")
        .replace("′", "'")
        .replace("³", "3")
        .replace("²", "2")
    )
    number = r"(?P<value>[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?)"

    def find_value(label: str, unit: str = "") -> tuple[float, str] | None:
        match = re.search(
            rf"(?:{label})\s*(?:of|=|:|to|is|at)?\s*{number}\s*(?P<unit>{unit})",
            normalized,
            re.IGNORECASE,
        )
        if match is None:
            return None
        return float(match.group("value")), match.groupdict().get("unit", "") or ""

    def length_in_metres(value_and_unit: tuple[float, str]) -> float:
        value, unit = value_and_unit
        factor = {"mm": 1.0e-3, "cm": 1.0e-2, "m": 1.0}.get(unit.lower(), 1.0)
        return value * factor

    def stress_in_pascals(value_and_unit: tuple[float, str]) -> float:
        value, unit = value_and_unit
        factor = {"pa": 1.0, "kpa": 1.0e3, "mpa": 1.0e6, "gpa": 1.0e9}.get(
            unit.lower(), 1.0
        )
        return value * factor

    solver = updated.setdefault("solver_parameters", {})
    end_time = find_value(r"end[\s_-]*time", r"s|sec(?:ond)?s?")
    if end_time is not None:
        solver["end_time"] = end_time[0]
    output_interval = find_value(r"output[\s_-]*interval", r"s|sec(?:ond)?s?")
    if output_interval is not None:
        solver["output_interval"] = output_interval[0]

    particle_spacing = find_value(r"particle[\s_-]*spacing", r"mm|cm|m")
    if particle_spacing is not None:
        updated.setdefault("geometries", {}).setdefault("global_resolution", {})[
            "particle_spacing"
        ] = length_in_metres(particle_spacing)
    else:
        resolution = find_value(r"resolution", r"mm|cm|m")
        if resolution is not None:
            updated.setdefault("geometries", {}).setdefault("global_resolution", {})[
                "particle_spacing"
            ] = length_in_metres(resolution)

    # Every 2D particle wall needs several particle layers. Apply the same
    # four-dp default to every generated multipolygon container, independent of
    # the named simulation case. Explicit user dimensions remain authoritative.
    requested_inner_length = find_value(r"inner[\s_-]*length", r"mm|cm|m")
    requested_inner_height = find_value(r"inner[\s_-]*height", r"mm|cm|m")
    inner_length = (
        length_in_metres(requested_inner_length)
        if requested_inner_length is not None
        else None
    )
    inner_height = (
        length_in_metres(requested_inner_height)
        if requested_inner_height is not None
        else None
    )
    requested_wall_thickness = find_value(
        r"(?:wall|boundary)[\s_-]*thickness", r"mm|cm|m"
    )
    if requested_wall_thickness is not None:
        wall_thickness = length_in_metres(requested_wall_thickness)
    else:
        resolution_cfg = updated.get("geometries", {}).get("global_resolution", {})
        spacing = (
            resolution_cfg.get("particle_spacing")
            if isinstance(resolution_cfg, dict)
            else None
        )
        wall_thickness = (
            4.0 * float(spacing)
            if isinstance(spacing, (int, float))
            and not isinstance(spacing, bool)
            and math.isfinite(float(spacing))
            and float(spacing) > 0.0
            else None
        )
    if wall_thickness is not None:
        outer_bounds: tuple[list[float], list[float]] | None = None
        for shape in updated.get("geometries", {}).get("shapes", []):
            if not isinstance(shape, dict) or shape.get("type") != "multipolygon":
                continue
            for polygon in shape.get("polygons", []):
                if isinstance(polygon, dict) and polygon.get("type") == "container_box":
                    inner_lower = polygon.get("inner_lower_bound")
                    inner_upper = polygon.get("inner_upper_bound")
                    if (
                        isinstance(inner_lower, list)
                        and isinstance(inner_upper, list)
                        and len(inner_lower) == len(inner_upper) == 2
                    ):
                        if inner_length is not None:
                            inner_upper[0] = float(inner_lower[0]) + inner_length
                        if inner_height is not None:
                            inner_upper[1] = float(inner_lower[1]) + inner_height
                        candidate_lower = [
                            float(value) - wall_thickness for value in inner_lower
                        ]
                        candidate_upper = [
                            float(value) + wall_thickness for value in inner_upper
                        ]
                        if outer_bounds is None:
                            outer_bounds = (candidate_lower, candidate_upper)
                        else:
                            outer_bounds = (
                                [
                                    min(outer_bounds[0][i], candidate_lower[i])
                                    for i in range(len(candidate_lower))
                                ],
                                [
                                    max(outer_bounds[1][i], candidate_upper[i])
                                    for i in range(len(candidate_upper))
                                ],
                            )
                    polygon["thickness"] = wall_thickness
        explicit_domain_requested = bool(
            re.search(r"\b(?:system[\s_-]*)?domain\b", normalized, re.IGNORECASE)
        )
        if outer_bounds is not None and not explicit_domain_requested:
            geometries = updated.setdefault("geometries", {})
            explicit_container_size = inner_length is not None or inner_height is not None
            domain = geometries.get("system_domain")
            if explicit_container_size or not isinstance(domain, dict):
                geometries["system_domain"] = {
                    "lower_bound": outer_bounds[0],
                    "upper_bound": outer_bounds[1],
                }
            else:
                lower = domain.get("lower_bound")
                upper = domain.get("upper_bound")
                if (
                    isinstance(lower, list)
                    and isinstance(upper, list)
                    and len(lower) == len(upper) == len(outer_bounds[0])
                ):
                    domain["lower_bound"] = [
                        min(float(lower[i]), outer_bounds[0][i])
                        for i in range(len(lower))
                    ]
                    domain["upper_bound"] = [
                        max(float(upper[i]), outer_bounds[1][i])
                        for i in range(len(upper))
                    ]

    material_values: dict[str, float] = {}
    density = find_value(r"density", r"kg\s*/\s*m3|kg\s*m-3")
    if density is not None:
        material_values["density"] = density[0]
    youngs_modulus = find_value(
        r"young(?:'s|s)?[\s_-]*modulus|elastic[\s_-]*modulus",
        r"gpa|mpa|kpa|pa",
    )
    if youngs_modulus is not None:
        material_values["youngs_modulus"] = stress_in_pascals(youngs_modulus)
    poisson_ratio = find_value(r"poisson(?:'s|s)?[\s_-]*ratio", "")
    if poisson_ratio is not None:
        material_values["poisson_ratio"] = poisson_ratio[0]
    friction_angle = find_value(
        r"friction[\s_-]*angle", r"degrees?|deg|°|radians?|rad"
    )
    if friction_angle is not None:
        value, unit = friction_angle
        material_values["friction_angle"] = (
            math.radians(value) if unit.lower() in {"degree", "degrees", "deg", "°"} else value
        )
    dilatancy_angle = find_value(
        r"dilatancy[\s_-]*angle", r"degrees?|deg|°|radians?|rad"
    )
    if dilatancy_angle is not None:
        value, unit = dilatancy_angle
        material_values["dilatancy_angle"] = (
            math.radians(value)
            if unit.lower() in {"degree", "degrees", "deg", "°"}
            else value
        )
    cohesion = find_value(r"cohesion", r"gpa|mpa|kpa|pa")
    if cohesion is not None:
        material_values["cohesion"] = stress_in_pascals(cohesion)

    if material_values:
        for body in updated.get("continuum_bodies", []):
            if not isinstance(body, dict):
                continue
            material = body.get("material")
            if isinstance(material, dict) and material.get("type") == "plastic_continuum":
                material.update(material_values)

    return updated


_STL_PATH_RE = re.compile(
    r"(?P<path>(?:[./\\\w-]+[/\\])?[\w.-]+\.stl)",
    re.IGNORECASE,
)


def _description_without_stl_paths(description: str) -> str:
    return _STL_PATH_RE.sub(" ", description or "")


def _primary_body_name(cfg: Dict[str, Any], section: str, fallback_keywords: tuple[str, ...]) -> str | None:
    bodies = cfg.get(section)
    if isinstance(bodies, list):
        for body in bodies:
            if isinstance(body, dict) and isinstance(body.get("name"), str):
                return body["name"]

    shapes = cfg.get("geometries", {}).get("shapes", [])
    if isinstance(shapes, list):
        for shape in shapes:
            if not isinstance(shape, dict) or not isinstance(shape.get("name"), str):
                continue
            name = shape["name"]
            lowered = name.lower()
            if any(keyword in lowered for keyword in fallback_keywords):
                return name
    return None


def _infer_stl_shape_name(cfg: Dict[str, Any], path: str, context: str) -> str | None:
    context_without_path = context.replace(path, " ")
    context_lowered = context_without_path.lower()
    shapes = cfg.get("geometries", {}).get("shapes", [])
    shape_names = [
        shape.get("name")
        for shape in shapes
        if isinstance(shape, dict) and isinstance(shape.get("name"), str)
    ]

    for name in shape_names:
        if name and name.lower() in context_lowered:
            return name

    soil_keywords = ("landslide", "landsldie", "soil", "granular", "moving")
    boundary_keywords = ("channel", "terrain", "boundary", "wall", "fixed", "bed")
    if any(keyword in context_lowered for keyword in boundary_keywords):
        return _primary_body_name(cfg, "solid_bodies", ("wall", "boundary", "terrain", "channel"))
    if any(keyword in context_lowered for keyword in soil_keywords):
        return _primary_body_name(cfg, "continuum_bodies", ("granular", "soil", "slide", "body"))

    return None


def _triangle_mesh_shape(
    name: str, file_name: str, *, include_default_transform: bool
) -> Dict[str, Any]:
    shape: Dict[str, Any] = {
        "name": name,
        "type": "triangle_mesh",
        "file_name": file_name,
    }
    if include_default_transform:
        shape["translation"] = [0.0, 0.0, 0.0]
        shape["scale"] = 1.0
    return shape


def _shape_reference_names(shape: Dict[str, Any]) -> set[str]:
    refs: set[str] = set()
    original = shape.get("original")
    if isinstance(original, str):
        refs.add(original)
    sub_shapes = shape.get("sub_shapes")
    if isinstance(sub_shapes, list):
        refs.update(item for item in sub_shapes if isinstance(item, str))
    return refs


def apply_stl_geometry_overrides(cfg: Dict[str, Any], description: str) -> Dict[str, Any]:
    """Map natural-language STL references to triangle_mesh shape definitions."""
    if not description or ".stl" not in description.lower():
        return cfg

    updated = json.loads(json.dumps(cfg))
    is_plastic_stl_case = is_all_plastic_continuum_dict(updated)
    include_default_transform = not is_plastic_stl_case
    geometries = updated.setdefault("geometries", {})
    if is_plastic_stl_case:
        # Plastic-continuum STL field cases derive their domain from shape
        # bounds in the native GeometryBuilder. Do not retain a template- or
        # LLM-authored duplicate. Other STL simulations may intentionally use
        # an explicit domain that extends beyond their initial shape bounds.
        geometries.pop("system_domain", None)
    shapes = geometries.setdefault("shapes", [])
    if not isinstance(shapes, list):
        geometries["shapes"] = []
        shapes = geometries["shapes"]

    replacements: Dict[str, str] = {}
    matches = list(_STL_PATH_RE.finditer(description))
    for index, match in enumerate(matches):
        path = match.group("path").replace("\\", "/")
        previous_delimiters = [
            description.rfind(delimiter, 0, match.start())
            for delimiter in (".", ";", ",")
        ]
        window_start = max(max(previous_delimiters) + 1, match.start() - 80)
        next_match_start = matches[index + 1].start() if index + 1 < len(matches) else len(description)
        next_delimiters = [
            pos
            for delimiter in (".", ";", ",")
            for pos in [description.find(delimiter, match.end(), next_match_start)]
            if pos != -1
        ]
        window_end = min(next_delimiters) if next_delimiters else min(next_match_start, match.end() + 120)
        context = description[window_start:window_end]
        shape_name = _infer_stl_shape_name(updated, path, context)
        if shape_name:
            replacements[shape_name] = path

    if not replacements:
        return updated

    shapes_by_name = {
        shape.get("name"): shape
        for shape in shapes
        if isinstance(shape, dict) and isinstance(shape.get("name"), str)
    }
    removable_helpers: set[str] = set()
    pending_helpers: list[str] = []
    for name in replacements:
        old_shape = shapes_by_name.get(name)
        if not isinstance(old_shape, dict):
            continue
        pending_helpers.extend(_shape_reference_names(old_shape))

    while pending_helpers:
        helper_name = pending_helpers.pop()
        if helper_name in removable_helpers:
            continue
        removable_helpers.add(helper_name)
        helper_shape = shapes_by_name.get(helper_name)
        if isinstance(helper_shape, dict):
            pending_helpers.extend(_shape_reference_names(helper_shape))

    seen: set[str] = set()
    for index, shape in enumerate(shapes):
        if not isinstance(shape, dict):
            continue
        name = shape.get("name")
        if isinstance(name, str) and name in replacements:
            shapes[index] = _triangle_mesh_shape(
                name,
                replacements[name],
                include_default_transform=include_default_transform,
            )
            seen.add(name)

    for name, path in replacements.items():
        if name not in seen:
            shapes.append(
                _triangle_mesh_shape(
                    name,
                    path,
                    include_default_transform=include_default_transform,
                )
            )

    referenced_after_replace: set[str] = set()
    body_names: set[str] = set()
    for section in ("fluid_bodies", "continuum_bodies", "solid_bodies"):
        for body in updated.get(section, []):
            if isinstance(body, dict) and isinstance(body.get("name"), str):
                body_names.add(body["name"])
    settings = updated.get("particle_generation", {}).get("settings", {})
    for body in settings.get("bodies", []):
        if isinstance(body, dict) and isinstance(body.get("name"), str):
            body_names.add(body["name"])
            if is_plastic_stl_case and body["name"] in replacements:
                # Irregular STL surfaces need level-set bounding during particle
                # relaxation.  The repose-angle template uses simple boxes and
                # therefore does not carry this mesh-specific setting.
                relaxation = body.setdefault("relaxation", {})
                if isinstance(relaxation, dict):
                    relaxation.setdefault("level_set", {})

    for shape in shapes:
        if isinstance(shape, dict) and shape.get("name") not in removable_helpers:
            referenced_after_replace.update(_shape_reference_names(shape))

    geometries["shapes"] = [
        shape
        for shape in shapes
        if not (
            isinstance(shape, dict)
            and isinstance(shape.get("name"), str)
            and shape["name"] in removable_helpers
            and shape["name"] not in referenced_after_replace
            and shape["name"] not in body_names
        )
    ]

    return updated


_SHAPE_FIELDS_BY_TYPE = {
    "box": {"name", "type", "half_size", "transform"},
    "bounding_box": {"name", "type", "lower_bound", "upper_bound"},
    "expanded_box": {"name", "type", "original", "expansion"},
    "extrude_shape": {"name", "type", "original", "thickness"},
    "complex_shape": {"name", "type", "sub_shapes", "operations"},
    "multipolygon": {"name", "type", "polygons"},
    "cylinder": {
        "name",
        "type",
        "radius",
        "half_height",
        "transform",
        "primitive",
        "_description",
    },
    "triangle_mesh": {"name", "type", "file_name", "translation", "scale"},
}


def _strip_shape_fields_for_type(shape: Dict[str, Any]) -> Dict[str, Any]:
    shape_type = shape.get("type")
    if not isinstance(shape_type, str):
        return shape
    allowed = _SHAPE_FIELDS_BY_TYPE.get(shape_type)
    if allowed is None:
        return shape
    return {key: value for key, value in shape.items() if key in allowed}


def sanitize_config_dict(cfg: Dict[str, Any]) -> Dict[str, Any]:
    updated = json.loads(json.dumps(cfg))

    geometries = updated.get("geometries")
    if not isinstance(geometries, dict):
        geometries = {}
        updated["geometries"] = geometries

    for key in ("shapes", "oriented_boxes"):
        items = geometries.get(key, [])
        if not isinstance(items, list):
            geometries[key] = []
            continue
        geometries[key] = [item for item in items if isinstance(item, dict)]

    for key in (
        "fluid_bodies",
        "solid_bodies",
        "continuum_bodies",
        "observers",
        "fluid_boundary_conditions",
        "body_constraints",
        "extra_state_recording",
        "initial_conditions",
    ):
        items = updated.get(key, [])
        if not isinstance(items, list):
            updated[key] = []
            continue
        updated[key] = [item for item in items if isinstance(item, dict)]

    updated.pop("characteristic_dimensions", None)

    shapes = updated.get("geometries", {}).get("shapes", [])
    shape_names = {shape.get("name") for shape in shapes if isinstance(shape, dict) and shape.get("name")}

    def _normalize_wall_typo(name: str | None) -> str | None:
        if not name or not isinstance(name, str):
            return name
        if name.startswith("Wal") and not name.startswith("Wall"):
            return "Wall" + name[3:]
        return name

    def _canonical_shape_name(name: str | None) -> str | None:
        name = _normalize_wall_typo(name)
        if not name:
            return name
        if name in shape_names:
            return name
        candidates = get_close_matches(name, [n for n in shape_names if isinstance(n, str)], n=1, cutoff=0.6)
        return candidates[0] if candidates else name

    shape_rename_map: Dict[str, str] = {}
    for shape in shapes:
        if not isinstance(shape, dict):
            continue
        name = shape.get("name")
        corrected = _canonical_shape_name(name)
        if name and corrected and corrected != name:
            shape_rename_map[name] = corrected
            shape["name"] = corrected
            shape_names.discard(name)
            shape_names.add(corrected)

    for shape in shapes:
        if not isinstance(shape, dict):
            continue
        original = shape.get("original")
        if isinstance(original, str):
            shape["original"] = _canonical_shape_name(shape_rename_map.get(original, original))
        sub_shapes = shape.get("sub_shapes")
        if isinstance(sub_shapes, list):
            shape["sub_shapes"] = [
                _canonical_shape_name(shape_rename_map.get(item, item) if isinstance(item, str) else item)
                if isinstance(item, str)
                else item
                for item in sub_shapes
            ]

    geometries["shapes"] = [_strip_shape_fields_for_type(shape) for shape in shapes]
    shapes = geometries["shapes"]

    for section in ("fluid_bodies", "solid_bodies", "continuum_bodies"):
        for body in updated.get(section, []):
            if not isinstance(body, dict):
                continue
            name = body.get("name")
            normalized = _normalize_wall_typo(name)
            if name and normalized and normalized != name:
                body["name"] = normalized

    all_body_names = {
        body.get("name")
        for section in ("fluid_bodies", "solid_bodies", "continuum_bodies")
        for body in updated.get(section, [])
        if isinstance(body, dict) and body.get("name")
    }

    def _canonical_body_name(name: str | None) -> str | None:
        name = _normalize_wall_typo(name)
        if not name:
            return name
        if name in all_body_names:
            return name
        candidates = get_close_matches(name, [n for n in all_body_names if isinstance(n, str)], n=1, cutoff=0.6)
        return candidates[0] if candidates else name

    for entry in updated.get("particle_generation", {}).get("settings", {}).get("bodies", []):
        if isinstance(entry, dict):
            entry["name"] = _canonical_body_name(entry.get("name"))

    for entry in updated.get("observers", []):
        if isinstance(entry, dict):
            entry["observed_body"] = _canonical_body_name(entry.get("observed_body"))

    for entry in updated.get("fluid_boundary_conditions", []):
        if isinstance(entry, dict):
            entry["body_name"] = _canonical_body_name(entry.get("body_name"))

    for entry in updated.get("body_constraints", []):
        if isinstance(entry, dict):
            entry["body_name"] = _canonical_body_name(entry.get("body_name"))

    for entry in updated.get("extra_state_recording", []):
        if not isinstance(entry, dict):
            continue
        entry["name"] = _canonical_body_name(entry.get("name"))
        for variable in entry.get("variables", []):
            if not isinstance(variable, dict):
                continue
            if isinstance(variable.get("real_type"), str):
                variable["real_type"] = [variable["real_type"]]
            if isinstance(variable.get("vector_type"), str):
                variable["vector_type"] = [variable["vector_type"]]

    settings = updated.get("particle_generation", {}).get("settings", {})
    bodies = settings.get("bodies", [])
    fluid_names = {body.get("name") for body in updated.get("fluid_bodies", [])}
    solid_names = {body.get("name") for body in updated.get("solid_bodies", [])}

    for body in bodies:
        if not isinstance(body, dict):
            continue
        name = body.get("name")
        solid_body = body.get("solid_body")
        if name in solid_names:
            body["solid_body"] = {} if not isinstance(solid_body, dict) else solid_body
        elif name in fluid_names and not isinstance(solid_body, dict):
            body.pop("solid_body", None)

    return updated


def infer_requested_simulation_type(description: str) -> str | None:
    text = _description_without_stl_paths(description).lower()
    if not text:
        return None

    if PLASTIC_CONTINUUM_KEYWORDS.search(text):
        return "continuum_dynamics"

    asks_for_type_change = bool(re.search(r"\b(simulation|simulaiton|type|switch|change|convert)\b", text))
    if not asks_for_type_change:
        return None

    if "continuum" in text:
        return "continuum_dynamics"
    if "fluid" in text:
        return "fluid_dynamics"
    return None


def infer_requested_material_type(description: str) -> str | None:
    text = _description_without_stl_paths(description).lower()
    if not text:
        return None
    if PLASTIC_CONTINUUM_KEYWORDS.search(text):
        return "plastic_continuum"
    return None


def coerce_simulation_type(
    config_dict: Dict[str, Any],
    target_type: str,
    material_type: str | None = None,
) -> Dict[str, Any]:
    updated = json.loads(json.dumps(config_dict))
    updated["simulation_type"] = target_type
    updated.setdefault("solver_parameters", {})

    if target_type == "continuum_dynamics":
        updated["solver_parameters"].setdefault("continuum_dynamics", {})
        if material_type == "plastic_continuum":
            updated.pop("fluid_bodies", None)
            updated["solver_parameters"].pop("fluid_dynamics", None)
        if not updated.get("continuum_bodies"):
            shape_names = [
                shape["name"]
                for shape in updated.get("geometries", {}).get("shapes", [])
                if isinstance(shape, dict) and isinstance(shape.get("name"), str)
                and not shape.get("name", "").lower().startswith("wall")
            ]
            if not shape_names and updated.get("fluid_bodies"):
                shape_names = [
                    body.get("name")
                    for body in updated.get("fluid_bodies", [])
                    if isinstance(body, dict) and isinstance(body.get("name"), str)
                ]
            if shape_names:
                if material_type == "plastic_continuum":
                    material = {
                        "type": "plastic_continuum",
                        "density": 2600.0,
                        "youngs_modulus": 5980000.0,
                        "poisson_ratio": 0.3,
                        "friction_angle": 0.5235987755982988,
                        "cohesion": 0.0,
                        "dilatancy_angle": 0.0,
                    }
                else:
                    material = {
                        "type": "general_continuum",
                        "density": 1000.0,
                        "sound_speed": 100.0,
                        "youngs_modulus": 1000000.0,
                        "poisson_ratio": 0.3,
                    }
                updated["continuum_bodies"] = [
                    {
                        "name": shape_names[0],
                        "material": material,
                    }
                ]
        elif material_type == "plastic_continuum":
            for body in updated.get("continuum_bodies", []):
                if not isinstance(body, dict):
                    continue
                body["material"] = {
                    "type": "plastic_continuum",
                    "density": 2600.0,
                    "youngs_modulus": 5980000.0,
                    "poisson_ratio": 0.3,
                    "friction_angle": 0.5235987755982988,
                    "cohesion": 0.0,
                    "dilatancy_angle": 0.0,
                }

    if target_type == "fluid_dynamics":
        updated["solver_parameters"].setdefault("fluid_dynamics", {})
        if not updated.get("fluid_bodies"):
            shape_names = [
                shape["name"]
                for shape in updated.get("geometries", {}).get("shapes", [])
                if isinstance(shape, dict) and isinstance(shape.get("name"), str)
            ]
            if shape_names:
                updated["fluid_bodies"] = [
                    {
                        "name": shape_names[0],
                        "material": {
                            "type": "weakly_compressible_fluid",
                            "density": 1000.0,
                        },
                    }
                ]

    return updated


def infer_requested_shape_rename(description: str) -> tuple[str, str] | None:
    text = (description or "").strip()
    if not text:
        return None

    quoted_patterns = [
        r"(?:shape\s+name|shape|rename|change)\s+[\"']([^\"']+)[\"']\s+(?:to|as)\s+[\"']([^\"']+)[\"']",
        r"rename\s+[\"']([^\"']+)[\"']\s+to\s+[\"']([^\"']+)[\"']",
    ]
    for pattern in quoted_patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            old_name = match.group(1).strip()
            new_name = match.group(2).strip()
            if old_name and new_name and old_name != new_name:
                return old_name, new_name

    token_patterns = [
        r"(?:shape\s+name|shape|rename)\s+['\"]?([A-Za-z_][\w]*)['\"]?\s+(?:to|as)\s+['\"]?([A-Za-z_][\w]*)['\"]?",
        r"change\s+['\"]?([A-Za-z_][\w]*)['\"]?\s+to\s+['\"]?([A-Za-z_][\w]*)['\"]?",
    ]
    lowered = text.lower()
    if "shape" not in lowered and "rename" not in lowered and "change" not in lowered:
        return None
    for pattern in token_patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            old_name = match.group(1)
            new_name = match.group(2)
            if old_name != new_name:
                return old_name, new_name
    return None


def apply_shape_rename(config_dict: Dict[str, Any], old_name: str, new_name: str) -> Dict[str, Any]:
    updated = json.loads(json.dumps(config_dict))

    for shape in updated.get("geometries", {}).get("shapes", []):
        if not isinstance(shape, dict):
            continue
        if shape.get("name") == old_name:
            shape["name"] = new_name
        if shape.get("original") == old_name:
            shape["original"] = new_name
        sub_shapes = shape.get("sub_shapes")
        if isinstance(sub_shapes, list):
            shape["sub_shapes"] = [new_name if item == old_name else item for item in sub_shapes]

    for section in ("fluid_bodies", "continuum_bodies", "solid_bodies"):
        for body in updated.get(section, []):
            if isinstance(body, dict) and body.get("name") == old_name:
                body["name"] = new_name

    settings = updated.get("particle_generation", {}).get("settings", {})
    for body in settings.get("bodies", []):
        if isinstance(body, dict) and body.get("name") == old_name:
            body["name"] = new_name
    for constraint in settings.get("relaxation_constraints", []):
        if isinstance(constraint, dict) and constraint.get("body_name") == old_name:
            constraint["body_name"] = new_name

    for observer in updated.get("observers", []):
        if isinstance(observer, dict) and observer.get("observed_body") == old_name:
            observer["observed_body"] = new_name

    for bc in updated.get("fluid_boundary_conditions", []):
        if isinstance(bc, dict) and bc.get("body_name") == old_name:
            bc["body_name"] = new_name

    for constraint in updated.get("body_constraints", []):
        if isinstance(constraint, dict) and constraint.get("body_name") == old_name:
            constraint["body_name"] = new_name

    for initial_condition in updated.get("initial_conditions", []):
        if isinstance(initial_condition, dict) and initial_condition.get("body_name") == old_name:
            initial_condition["body_name"] = new_name

    for entry in updated.get("extra_state_recording", []):
        if isinstance(entry, dict) and entry.get("name") == old_name:
            entry["name"] = new_name

    return updated
