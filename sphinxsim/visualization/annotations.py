"""Annotation helpers for simulation preview visualization.

Builds human-readable label strings for shapes, bodies, boundary conditions,
and initial conditions from a SimulationConfig.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sphinxsim.config.schemas import (
        BodyConstraintConfig,
        FluidBoundaryConditionConfig,
        ObserverConfig,
        OrientedBoxConfig,
        SimulationConfig,
    )


def body_label(body_name: str, config: "SimulationConfig") -> str:
    """Return a short label string for a body shape."""
    for body in config.fluid_bodies:
        if body.name == body_name:
            mat = body.material
            parts = [f"Fluid: {body_name}", f"ρ={mat.density}"]
            if mat.sound_speed is not None:
                parts.append(f"c={mat.sound_speed}")
            if mat.viscosity is not None:
                visc = mat.viscosity
                if isinstance(visc, (int, float)):
                    parts.append(f"μ={visc}")
            if mat.thermal_properties is not None:
                tp = mat.thermal_properties
                if tp.thermal_boundary is not None:
                    parts.append(f"Thermal: {tp.thermal_boundary.value}")
            return "\n".join(parts)

    for body in config.solid_bodies:
        if body.name == body_name:
            return f"Solid: {body_name}\n(rigid)"

    for body in config.continuum_bodies:
        if body.name == body_name:
            mat = body.material
            parts = [f"Continuum: {body_name}", f"material={mat.type.value}"]
            if mat.density is not None:
                parts.append(f"ρ={mat.density}")
            return "\n".join(parts)

    return body_name


def short_body_label(body_name: str, config: "SimulationConfig") -> str:
    """Return the compact in-scene label used beside a body."""
    for body in (*config.fluid_bodies, *config.solid_bodies, *config.continuum_bodies):
        if body.name == body_name:
            return body_name
    return body_name


def _material_value(material: object, *names: str) -> object | None:
    """Read a material field from the validated model, with safe aliases."""
    for name in names:
        if isinstance(material, Mapping):
            value = material.get(name)
        else:
            value = getattr(material, name, None)
        if value is not None:
            return value
    return None


def _material_type_value(material: object) -> str:
    value = _material_value(material, "type", "material_type", "model")
    return str(getattr(value, "value", value or "—"))


def _material_model_label(material: object) -> str:
    labels = {
        "plastic_continuum": "Drucker–Prager",
        "general_continuum": "General continuum",
        "j2_plasticity": "J2 plasticity",
        "rigid_body": "Rigid body",
        "weakly_compressible_fluid": "Weakly compressible fluid",
        "weakly_compressible_mixture": "Compressible mixture",
        "weakly_compressible_multi_species": "Multi-species fluid",
        "weakly_compressible_multi_phase": "Multi-phase fluid",
    }
    raw = _material_type_value(material)
    return labels.get(raw, raw.replace("_", " ").title())


def _format_density(value: object | None) -> str:
    if value is None:
        return "—"
    return f"{float(value):g} kg/m³"


def _format_friction_angle(value: object | None) -> str:
    if value is None:
        return "—"
    # MaterialConfig stores angles in radians; conversion is display-only.
    return f"{math.degrees(float(value)):.1f}°"


def _format_cohesion(value: object | None) -> str:
    if value is None:
        return "—"
    cohesion = float(value)
    if cohesion >= 1000.0:
        return f"{cohesion / 1000.0:g} kPa"
    return f"{cohesion:g} Pa"


def _format_value(value: object | None, unit: str = "") -> str:
    if value is None:
        return "—"
    return f"{float(value):g}{unit}"


def _plastic_sound_speed(material: object) -> float | None:
    """Return an explicit PlasticContinuum sound speed or its runtime default."""
    explicit = _material_value(material, "sound_speed")
    if explicit is not None:
        return float(explicit)
    values = [
        _material_value(material, "density"),
        _material_value(material, "youngs_modulus"),
        _material_value(material, "poisson_ratio"),
    ]
    if any(value is None for value in values):
        return None
    density, youngs_modulus, poisson_ratio = (float(value) for value in values)
    denominator = density * 3.0 * (1.0 - 2.0 * poisson_ratio)
    if density <= 0.0 or youngs_modulus <= 0.0 or denominator <= 0.0:
        return None
    return math.sqrt(youngs_modulus / denominator)


def _material_rows(material: object) -> list[tuple[str, str]]:
    """Return only the fields that belong to this material family."""
    material_type = _material_type_value(material)
    rows = [("Density", _format_density(_material_value(material, "density", "mass_density", "rho")))]

    if material_type == "plastic_continuum":
        rows.extend(
            [
                ("Sound speed", _format_value(_plastic_sound_speed(material), " m/s")),
                ("Young's modulus", _format_value(_material_value(material, "youngs_modulus"), " Pa")),
                ("Poisson ratio", _format_value(_material_value(material, "poisson_ratio"))),
                (
                    "Friction angle",
                    _format_friction_angle(_material_value(material, "friction_angle", "frictionAngle")),
                ),
                (
                    "Dilatancy angle",
                    _format_friction_angle(_material_value(material, "dilatancy_angle", "dilatancyAngle")),
                ),
                ("Cohesion", _format_cohesion(_material_value(material, "cohesion", "cohesive_strength"))),
            ]
        )
    elif material_type == "j2_plasticity":
        rows.extend(
            [
                ("Sound speed", _format_value(_material_value(material, "sound_speed"), " m/s")),
                ("Young's modulus", _format_value(_material_value(material, "youngs_modulus"), " Pa")),
                ("Poisson ratio", _format_value(_material_value(material, "poisson_ratio"))),
                ("Yield stress", _format_value(_material_value(material, "yield_stress"), " Pa")),
                (
                    "Hardening modulus",
                    _format_value(_material_value(material, "hardening_modulus"), " Pa"),
                ),
            ]
        )
    elif material_type == "general_continuum":
        rows.extend(
            [
                ("Sound speed", _format_value(_material_value(material, "sound_speed"), " m/s")),
                ("Young's modulus", _format_value(_material_value(material, "youngs_modulus"), " Pa")),
                ("Poisson ratio", _format_value(_material_value(material, "poisson_ratio"))),
            ]
        )

    return rows


def collect_preview_body_information(config: "SimulationConfig") -> list[dict[str, object]]:
    """Collect display-ready material information without changing config data."""
    information: list[dict[str, object]] = []
    bodies = [
        *[(body, "Fluid body") for body in getattr(config, "fluid_bodies", [])],
        *[(body, "Continuum body") for body in getattr(config, "continuum_bodies", [])],
    ]
    for body, body_type in bodies:
        material = body.material
        rows = _material_rows(material)
        information.append(
            {
                "name": body.name,
                "body_type": body_type,
                "material_model": _material_model_label(material),
                "material_type": _material_type_value(material),
                "rows": rows,
                # Retain these keys for callers that used the first sidebar API.
                "density": rows[0][1],
                "friction_angle": _format_friction_angle(
                    _material_value(material, "friction_angle", "frictionAngle")
                ),
                "cohesion": _format_cohesion(_material_value(material, "cohesion", "cohesive_strength")),
            }
        )
    return information


def particle_resolution_label(config: "SimulationConfig") -> tuple[str, str] | None:
    """Return the configured particle resolution as display text."""
    resolution = config.geometries.global_resolution
    spacing = _material_value(resolution, "particle_spacing")
    if spacing is not None:
        return ("Particle spacing", f"{float(spacing):g} m")
    count = _material_value(resolution, "characteristic_length_particles")
    if count is not None:
        return ("Characteristic particles", f"{int(count)}")
    return None


def observer_short_label(index: int) -> str:
    """Return the compact in-scene observer label."""
    return f"Observer {index}"


def observer_details(observer: "ObserverConfig") -> list[tuple[str, str]]:
    """Return observer details for the sidebar."""
    variable = observer.variable
    variable_name = variable.real_type if variable.real_type is not None else variable.vector_type
    if isinstance(variable_name, list):
        variable_text = ", ".join(str(value) for value in variable_name)
    else:
        variable_text = str(variable_name)
    positions = "; ".join(
        "(" + ", ".join(f"{float(value):g}" for value in position) + ")"
        for position in observer.positions
    )
    return [("Name", observer.name), ("Body", observer.observed_body), ("Variable", variable_text), ("Position", positions)]


def oriented_box_label(ob: "OrientedBoxConfig", config: "SimulationConfig") -> str:
    """Return an annotation label for an oriented box, including its BCs."""
    parts = [f"{ob.name} [{ob.type.value}]"]

    # Fluid boundary conditions
    for bc in config.fluid_boundary_conditions:
        if bc.oriented_box == ob.name:
            bc_parts = [f"BC → {bc.body_name}: {bc.type.value}"]
            if bc.inflow_speed is not None:
                bc_parts.append(f"v={bc.inflow_speed}")
            if bc.pressure is not None:
                bc_parts.append(f"p={bc.pressure}")
            parts.append(" ".join(bc_parts))

    # Particle-relaxation constraints reference oriented boxes directly.
    pg_settings = config.particle_generation.settings
    if pg_settings is not None:
        for constraint in pg_settings.relaxation_constraints:
            if constraint.oriented_box == ob.name:
                parts.append(
                    f"Relaxation constraint → {constraint.body_name}: {constraint.type}"
                )

    return "\n".join(parts)


def gravity_label(config: "SimulationConfig") -> str | None:
    """Return a gravity annotation string, or None if gravity is not set."""
    if config.gravity is None:
        return None
    g = config.gravity
    if len(g) == 2:
        return f"g = ({g[0]:.2f}, {g[1]:.2f}) m/s²"
    return f"g = ({g[0]:.2f}, {g[1]:.2f}, {g[2]:.2f}) m/s²"


def observer_label(observer: "ObserverConfig") -> str:
    """Return an annotation label for an observer definition."""
    variable = observer.variable
    variable_name = variable.real_type if variable.real_type is not None else variable.vector_type
    return (
        f"Observer: {observer.name}\n"
        f"body={observer.observed_body}\n"
        f"var={variable_name}"
    )


def body_constraint_label(constraint: "BodyConstraintConfig") -> str:
    """Return an annotation label for a body constraint definition.

    Covers both ``fixed`` and ``simbody`` constraint types.  When a
    ``region`` (oriented box name) is specified the label notes it; otherwise
    the constraint applies to the entire body.
    """
    parts = [f"Constraint → {constraint.body_name}", f"type={constraint.type.value}"]

    if constraint.region is not None:
        parts.append(f"region={constraint.region}")

    if constraint.type.value == "simbody":
        if constraint.mobilized_body is not None:
            parts.append(f"mob={constraint.mobilized_body}")
        if constraint.velocity is not None:
            v = constraint.velocity
            if len(v) == 2:
                parts.append(f"v=({v[0]}, {v[1]})")
            else:
                parts.append(f"v=({v[0]}, {v[1]}, {v[2]})")
        if constraint.angular_velocity is not None:
            parts.append(f"ω={constraint.angular_velocity}")

    return "\n".join(parts)
