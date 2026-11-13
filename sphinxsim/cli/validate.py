"""Validate command logic for the SPHinXsim CLI."""

from __future__ import annotations

import argparse

from .common import _load_config


def cmd_validate(args: argparse.Namespace) -> int:
    """Validate a JSON config file against the SimulationConfig schema."""
    config, rc = _load_config(args.config_file)
    if rc != 0:
        return rc
    assert config is not None
    print(f"✅ Generated configuration:")
    print(f"   Simulation type: {config.simulation_type.value}")
    print(f"   Shapes: {len(config.geometries.shapes)}")
    print(f"   oriented boxes: {len(config.geometries.oriented_boxes)}")
    if config.geometries.system_domain is not None:
        print(f"   Domain lower bound: {config.geometries.system_domain.lower_bound}")
        print(f"   Domain upper bound: {config.geometries.system_domain.upper_bound}")
    if config.geometries.global_resolution is not None:
        print(f"   Global resolution: {config.geometries.global_resolution.model_dump(exclude_none=True)}")

    print(f"   Fluid bodies: {len(config.fluid_bodies)}")
    for body in config.fluid_bodies:
        print("     - " f"{body.name}: " f"material={body.material.type.value}")
    print(f"   Continuum bodies: {len(config.continuum_bodies)}")
    for body in config.continuum_bodies:
        print("     - " f"{body.name}: " f"material={body.material.type.value}")
    print(f"   Solid bodies: {len(config.solid_bodies)}")
    for body in config.solid_bodies:
        print("     - " f"{body.name}: " f"material={body.material.type.value}")
    if config.gravity is not None:
        print(f"   Gravity: {config.gravity}")
    print(f"   Observers: {len(config.observers)}")
    end_time = config.solver_parameters.end_time
    print(f"   End time: {end_time if end_time is not None else '(set by solver defaults)'}")

    config_json = config.model_dump_json(indent=2, exclude_none=True)
    print(f"\n📄 Configuration as JSON ({len(config_json)} bytes)")
    print(config_json[:200] + "..." if len(config_json) > 200 else config_json)
    return 0


__all__ = ["cmd_validate"]
