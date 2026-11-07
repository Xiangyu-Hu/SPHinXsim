"""Versioned configuration admission shared by manual JSON, CAD and solver jobs."""
from copy import deepcopy
import hashlib
import json
import math
import os
from pathlib import Path
import warnings
from functools import lru_cache
from importlib.util import find_spec
import re

VERSION = "1"
SOURCE = Path(next(iter(find_spec("sphinxsim").submodule_search_locations)))

# Admission follows the native builders, whose schema models contain a superset
# of fields for different types. Schema acceptance alone cannot detect no-ops.
MATERIAL_FIELDS = {
    "weakly_compressible_fluid": "density",
    "weakly_compressible_multi_species": "species",
    "weakly_compressible_multi_phase": "pure_phases multi_species_phases",
    "rigid_body": "",
    "j2_plasticity": "density sound_speed youngs_modulus poisson_ratio yield_stress hardening_modulus",
    "plastic_continuum": "density sound_speed youngs_modulus poisson_ratio friction_angle cohesion dilatancy_angle",
    "composite_solid": "density poisson_ratio youngs_modulus_active youngs_modulus_1 youngs_modulus_2 material_id_regions active_strain",
}
SHAPE_FIELDS = {
    "box": "primitive half_size transform", "bounding_box": "lower_bound upper_bound",
    "expanded_box": "original expansion", "extrude_shape": "original thickness",
    "complex_shape": "sub_shapes operations", "multipolygon": "polygons",
    "cylinder": "radius half_height transform", "triangle_mesh": "file_name translation scale",
}


def reject_inactive_fields(payload):
    def admit(item, fields, path):
        ignored = [key for key, value in item.items()
                   if key not in fields and value is not None and value != []]
        if ignored:
            raise ValueError(f"Fields not consumed for {path} ({item.get('type')}): {', '.join(ignored)}")

    for group in ("fluid_bodies", "continuum_bodies", "solid_bodies"):
        for index, body in enumerate(payload.get(group, [])):
            item = body.get("material", {})
            kind = item.get("type")
            if kind not in MATERIAL_FIELDS:
                raise ValueError(f"Material {kind} has no supported native material-builder branch")
            admit(item, {"type", "viscosity", "thermal_properties", *MATERIAL_FIELDS[kind].split()}, f"{group}[{index}].material")
    geometries = payload.get("geometries", {})
    for index, shape in enumerate(geometries.get("shapes", [])):
        fields = {"name", "type", *SHAPE_FIELDS.get(shape.get("type"), "").split()}
        if shape.get("primitive") and shape.get("type") == "box":
            fields -= {"half_size", "transform"}
        admit(shape, fields, f"geometries.shapes[{index}]")
    for index, box in enumerate(geometries.get("oriented_boxes", [])):
        fields = "center normal radius surface_half_size" if box.get("type") == "boundary" else "primitive" if box.get("primitive") else "half_size transform"
        admit(box, {"name", "type", *fields.split()}, f"geometries.oriented_boxes[{index}]")
        if box.get("radius") is not None and box.get("surface_half_size") is not None:
            raise ValueError("Boundary radius and surface_half_size are mutually exclusive")
    for index, boundary in enumerate(payload.get("fluid_boundary_conditions", [])):
        fields = {"emitter": "inflow_speed on_schedule multi_species_phases volume_fractions",
                  "bi_directional": "pressure velocity mass_fractions",
                  "free_stream": "buffer_box disposer_box target_speed t_ref"}.get(boundary.get("type"), "")
        admit(boundary, {"body_name", "oriented_box", "type", *fields.split()}, f"fluid_boundary_conditions[{index}]")
    for index, constraint in enumerate(payload.get("body_constraints", [])):
        if constraint.get("type") == "fixed":
            admit(constraint, {"body_name", "type", "region"}, f"body_constraints[{index}]")


@lru_cache(maxsize=1)
def catalog():
    from sphinxsim.config import schemas
    groups = {"simulationTypes": schemas.SimulationType, "shapes": schemas.BodyShapeType,
              "materials": schemas.MaterialType, "boundaries": schemas.FluidBoundaryConditionType,
              "constraints": schemas.BodyConstraintType}
    builders = SOURCE / "sph_simulation"
    texts = [(p, p.read_text()) for p in builders.rglob("*.cpp")] if builders.exists() else []
    result = {}
    for group, enum in groups.items():
        result[group] = []
        for item in enum:
            evidence = [str(p.relative_to(SOURCE)) for p, text in texts if re.search(r'(?:==|!=)\s*"' + re.escape(item.value) + '"', text)]
            status = ("wired_unverified" if evidence else "not_wired") if texts else "not_inspected"
            if item.value == "weakly_compressible_mixture":
                status = "unsupported"
            result[group].append({"name": item.value,
                "status": status,
                "dimensions": [3] if item.value in ("triangle_mesh", "cylinder") else None,
                "verification": "Source routing evidence only; no runtime combination verification",
                "evidence": evidence[:12]})
    schema = schemas.SimulationConfig.model_json_schema()
    identity = hashlib.sha256(json.dumps(schema, sort_keys=True).encode())
    for path, text in sorted(texts):
        identity.update(str(path.relative_to(SOURCE)).encode())
        identity.update(text.encode())
    bindings = []
    from sphinxsim.bindings.loader import _candidate_search_dirs
    for directory in _candidate_search_dirs():
        for binary in sorted(directory.glob("*_sphinxsys_core*.*")):
            if binary.suffix.lower() not in (".so", ".pyd", ".dylib"):
                continue
            from sphinxsim_agent.export_vtp import hash_file
            digest = hash_file(binary)
            identity.update(digest.encode())
            bindings.append({"name": binary.name, "sha256": digest})
    return {"version": VERSION, "adapterRevision": os.environ.get("SPHINXSIM_ADAPTER_REVISION", "local-unversioned"),
            "buildFingerprint": identity.hexdigest(), "imageId": os.environ.get("SPHINXSIM_SOLVER_IMAGE_ID"), "capabilities": result, "bindings": bindings,
            "schema": schema, "combinations": [],
            "limitations": ["Schema and builder evidence do not certify a physical combination.",
                "No verified sail propulsion or free-floating six-degree-of-freedom combination.",
                "STL only supplies geometry; materials, boundaries and motion require explicit configuration."]}


def reject_unknown(value, schema, definitions, path="$", depth=0):
    if depth > 64:
        raise ValueError("Configuration nesting exceeds limit")
    if "$ref" in schema:
        schema = definitions[schema["$ref"].split("/")[-1]]
    if value is None:
        return  # Required/type checks belong to the native schema.
    if "anyOf" in schema:
        choices = [s for s in schema["anyOf"] if s.get("type") != "null"]
        matched = []
        for choice in choices:
            try:
                reject_unknown(value, choice, definitions, path, depth + 1)
                matched.append(choice)
            except ValueError:
                pass
        if not matched:
            raise ValueError(f"Unknown or invalid fields at {path}")
        return
    if isinstance(value, dict):
        if schema.get("type") != "object" and "properties" not in schema:
            raise ValueError(f"Expected a scalar or list at {path}")
        props = schema.get("properties", {})
        unknown = set(value) - set(props)
        if unknown and not isinstance(schema.get("additionalProperties"), dict):
            raise ValueError(f"Unknown fields at {path}: {sorted(unknown)}")
        for key, child in value.items():
            reject_unknown(child, props.get(key, schema.get("additionalProperties", {})), definitions, path + "." + key, depth + 1)
    elif isinstance(value, list) and "items" in schema:
        for index, child in enumerate(value):
            reject_unknown(child, schema["items"], definitions, f"{path}[{index}]", depth + 1)


def changed_inputs(before, after, path="$"):
    """Record schema coercions/removals only where the caller supplied a value."""
    changes = []
    if isinstance(before, dict) and isinstance(after, dict):
        for key, value in before.items():
            if key not in after:
                changes.append({"path": path + "." + key, "before": value, "after": None, "removed": True})
            else:
                changes.extend(changed_inputs(value, after[key], path + "." + key))
    elif isinstance(before, list) and isinstance(after, list):
        if len(before) != len(after):
            changes.append({"path": path, "before": before, "after": after})
        else:
            for index, (a, b) in enumerate(zip(before, after)):
                changes.extend(changed_inputs(a, b, f"{path}[{index}]"))
    elif before != after:
        changes.append({"path": path, "before": before, "after": after})
    return changes


def check_config(payload):
    from sphinxsim.config.schemas import SimulationConfig
    if not isinstance(payload, dict):
        raise ValueError("Expected native configuration object")
    json.dumps(payload, allow_nan=False)
    schema = SimulationConfig.model_json_schema()
    reject_unknown(payload, schema, schema.get("$defs", {}))
    reject_inactive_fields(payload)
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        config = SimulationConfig.model_validate(deepcopy(payload))
    normalized = config.model_dump(mode="json", exclude_none=True)
    changes = changed_inputs(payload, normalized)
    removed = [c["path"] for c in changes if c.get("removed") and c["before"] is not None]
    if removed:
        raise ValueError("Configuration fields would be ignored or removed: " + ", ".join(removed))
    dimension = config._infer_spatial_dim()
    if dimension not in (2, 3):
        raise ValueError("Configuration must explicitly establish 2D or 3D")
    if dimension == 2 and any(s["type"] in ("triangle_mesh", "cylinder") for s in normalized["geometries"]["shapes"]):
        raise ValueError("Triangle meshes and cylinders require a 3D configuration")
    if dimension == 3 and any(s["type"] == "multipolygon" for s in normalized["geometries"]["shapes"]):
        raise ValueError("Multipolygons require a 2D configuration")
    for group in ("fluid_bodies", "continuum_bodies", "solid_bodies"):
        for body in normalized.get(group, []):
            material = body.get("material", {}).get("type")
            if material == "general_continuum":
                raise ValueError("general_continuum has no current native material-builder branch")
    notes = [{"code": "native_normalization", "severity": "warning", "message": str(w.message)} for w in caught]
    if changes:
        notes.append({"code": "configuration_changed", "severity": "warning", "changes": changes,
                      "message": "Native validation normalized supplied values; review before applying."})
    if normalized.get("body_constraints") or any(b.get("is_moving") for b in normalized.get("solid_bodies", [])):
        notes.append({"code": "motion_unverified", "severity": "warning",
                      "message": "Motion/constraint combination requires a short trial; this is not free-floating validation."})
    notes.append({"code": "combination_unverified", "severity": "warning",
                  "message": "Configuration accepted by native schema; physical combination has not been certified."})
    spacing = normalized["geometries"]["global_resolution"].get("particle_spacing")
    if spacing:
        for shape in normalized["geometries"]["shapes"]:
            if shape.get("type") == "bounding_box":
                lengths = [b-a for a, b in zip(shape["lower_bound"], shape["upper_bound"])]
                if min(lengths) < 3 * spacing:
                    notes.append({"code": "thin_geometry", "severity": "warning", "body": shape["name"],
                                  "message": "A shape dimension spans fewer than three particle spacings; inspect particle coverage."})
    digest = hashlib.sha256(json.dumps(normalized, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    from .identity import effective_identity
    return normalized, {"version": VERSION, "valid": True, "dimension": dimension,
                        "configurationHash": digest, "effectiveConfigurationIdentity": effective_identity(normalized), "diagnostics": notes, "normalizations": changes,
                        "notEvaluated": ["physical_accuracy", "all_cross_physics_combinations"],
                        "buildFingerprint": catalog()["buildFingerprint"], "adapterRevision": catalog()["adapterRevision"], "imageId": catalog()["imageId"]}


UNITS = {
    "m": ("length", 1), "cm": ("length", .01), "mm": ("length", .001),
    "s": ("time", 1), "ms": ("time", .001), "Pa": ("pressure", 1), "kPa": ("pressure", 1000),
    "kg/m3": ("density", 1), "m/s": ("velocity", 1), "m/s2": ("acceleration", 1),
    "Pa*s": ("viscosity", 1), "rad": ("angle", 1), "deg": ("angle", math.pi / 180), "K": ("temperature", 1),
}
QUANTITIES = {
    "particle_spacing": "length", "radius": "length", "half_height": "length", "half_size": "length",
    "lower_bound": "length", "upper_bound": "length", "translation": "length", "expansion": "length",
    "end_time": "time", "output_interval": "time", "density": "density", "pressure": "pressure",
    "youngs_modulus": "pressure", "hardening_modulus": "pressure", "yield_stress": "pressure", "cohesion": "pressure",
    "viscosity": "viscosity", "gravity": "acceleration", "velocity": "velocity", "target_velocity": "velocity",
    "inflow_speed": "velocity", "target_speed": "velocity", "t_ref": "time",
    "rotation_angle": "angle", "friction_angle": "angle", "dilatancy_angle": "angle", "initial_temperature": "temperature",
}


def compile_config(spec):
    """Native structure plus explicit quantities; no second canonical physics DSL."""
    if not isinstance(spec, dict) or set(spec) != {"version", "configuration"} or spec["version"] != 1:
        raise ValueError("Compile input uses version:1 and configuration")
    conversions = []
    def convert(value, key="", path="$"):
        if isinstance(value, dict):
            if "unit" in value or "value" in value and set(value) == {"value", "unit"}:
                if set(value) != {"value", "unit"} or value["unit"] not in UNITS or QUANTITIES.get(key) != UNITS[value["unit"]][0]:
                    raise ValueError(f"Unsupported or incompatible unit at {path}")
                def scale(v):
                    if isinstance(v, list):
                        return [scale(n) for n in v]
                    if type(v) not in (int, float) or not math.isfinite(v):
                        raise ValueError(f"Invalid quantity at {path}")
                    return v * UNITS[value["unit"]][1]
                result = scale(value["value"])
                conversions.append({"path": path, "from": value, "to": result})
                return result
            return {k: convert(v, k, path + "." + k) for k, v in value.items()}
        if isinstance(value, list):
            return [convert(v, key, f"{path}[{i}]") for i, v in enumerate(value)]
        return value
    payload = convert(spec["configuration"])
    if conversions and payload.get("characteristic_dimensions"):
        raise ValueError("Explicit unit quantities cannot be combined with characteristic_dimensions; use SI or native scaling")
    config, report = check_config(payload)
    report["unitConversions"] = conversions
    return config, report


def editor_config_type():
    """Process-local replacement; validate raw Qt payload before native coercion."""
    from pydantic import model_validator
    from sphinxsim.config.schemas import SimulationConfig

    class EditorConfig(SimulationConfig):
        @model_validator(mode="before")
        @classmethod
        def admit_raw(cls, payload):
            if not isinstance(payload, dict):
                return payload
            normalized, report = check_config(payload)
            if report["normalizations"]:
                raise ValueError("Values require normalization; compile and review the changes before applying")
            return normalized

    return EditorConfig
