"""Run command logic for the SPHinXsim CLI."""

from __future__ import annotations

import argparse
import os
import sys
import tempfile
from pathlib import Path

from sphinxsim.bindings.loader import load_sphinxsys_core_nd as _default_load_sphinxsys_core_nd
from sphinxsim.config.schemas import SimulationConfig

from .common import PROJECT_ROOT, _load_config


def _resolve_load_sphinxsys_core_nd():
    """Return the package-level native loader if tests monkeypatch the CLI root."""
    try:
        import sphinxsim.cli as cli_pkg
    except Exception:
        return _default_load_sphinxsys_core_nd
    candidate = getattr(cli_pkg, "load_sphinxsys_core_nd", None)
    return candidate if callable(candidate) else _default_load_sphinxsys_core_nd


def _config_spatial_dim(config: SimulationConfig) -> int:
    """Infer whether a validated config should use the 2-D or 3-D native module."""
    geo = config.geometries

    if geo.system_domain is not None:
        return len(geo.system_domain.lower_bound)

    if config.gravity is not None:
        return len(config.gravity)

    for constraint in config.body_constraints:
        if constraint.type.value == "simbody":
            if (constraint.mobilized_body or "").lower() == "planar":
                return 2
            if constraint.velocity is not None:
                return len(constraint.velocity)

    for shape in geo.shapes:
        for vec in (shape.lower_bound, shape.upper_bound, shape.half_size):
            if vec is not None:
                return len(vec)
        if shape.transform is not None:
            return len(shape.transform.translation)
        if shape.translation is not None:
            return len(shape.translation)

    for oriented_box in geo.oriented_boxes:
        for vec in (oriented_box.center, oriented_box.normal, oriented_box.half_size):
            if vec is not None:
                return len(vec)
        if oriented_box.transform is not None:
            return len(oriented_box.transform.translation)

    return 3


def cmd_run(args: argparse.Namespace) -> int:
    """Run a simulation defined by a JSON config file."""
    config_path = Path(args.config_file)
    config, rc = _load_config(config_path)
    if rc != 0:
        return rc
    assert config is not None

    ndim = _config_spatial_dim(config)
    try:
        sph = _resolve_load_sphinxsys_core_nd()(ndim)
    except ImportError:
        print(f"❌ {ndim}D C++ extension not available", file=sys.stderr)
        print("\n🔧 Please build the C++ extension:", file=sys.stderr)
        print("   cd sphinxsim/sphinxsys", file=sys.stderr)
        print("   cmake --preset integrated-build", file=sys.stderr)
        print("   ninja -C ../../build-integrated", file=sys.stderr)
        return 1

    if not config_path.is_absolute():
        config_path = PROJECT_ROOT / ".build-temp" / config_path

    validated_config_path: str | None = None
    tmp_cfg = tempfile.NamedTemporaryFile(
        mode="w",
        suffix=".json",
        delete=False,
        prefix="sphinxsim_run_",
        dir=str(config_path.parent),
    )
    try:
        tmp_cfg.write(config.model_dump_json(indent=2, exclude_none=True))
        tmp_cfg.close()
        validated_config_path = tmp_cfg.name
    except OSError as exc:
        print(f"Error writing validated config: {exc}", file=sys.stderr)
        return 1

    try:
        sim = sph.SPHSimulation(validated_config_path)
        print(f"Preview runtime config written to: {validated_config_path}")

        output_dir = PROJECT_ROOT / ".build-temp" / "test_simulation"
        output_dir.mkdir(exist_ok=True, parents=True)
        sim.resetOutputRoot(str(output_dir))
        print(f"📁 Now, the output folder is changed to: {output_dir}")

        sim.buildGeometries()
        print("✅ Geometries built")
        sim.generateParticles()
        print("✅ Particles generated")
        sim.buildSimulation()
        print("✅ Simulation built")
        sim.initializeSimulation()
        print("✅ Simulation initialized")

        print("\n🚀 Running simulation...")
        sim.run()

        print("✅ Simulation completed successfully!")
        print(f"\n📊 Run summary:")
        configured_end_time = config.solver_parameters.end_time
        print(f"   End time: {configured_end_time if configured_end_time is not None else '(solver default)'}")
        if config.fluid_bodies:
            first_body_name = config.fluid_bodies[0].name
            print(f"   Fluid body: {first_body_name}")
        elif config.continuum_bodies:
            first_body_name = config.continuum_bodies[0].name
            print(f"   Continuum body: {first_body_name}")
        else:
            first_body_name = "simulation"
        print(f"   Run config: {config_path}")

        safe_name = first_body_name.replace(" ", "_").replace("/", "_")[:50]
        output_dir = PROJECT_ROOT / ".build-temp" / "simulations" / safe_name
        print(f"\n📁 Simulation output saved to:")
        print(f"   {output_dir}")
        return 0

    except RuntimeError as exc:
        if "C++ extension" in str(exc):
            print("❌ C++ extension not available")
            print("\n🔧 Please build the C++ extension:")
            print("   cd sphinxsim/sphinxsys")
            print("   cmake --preset integrated-build")
            print("   ninja -C ../../build-integrated")
            return 1
        raise

    except NotImplementedError as exc:
        print(f"❌ Feature not yet implemented: {exc}")
        print("\n💡 Tip: Try a fluid-only simulation like:")
        print('   "water dam break for 1 second"')
        return 1

    except Exception as exc:
        print(f"❌ Unexpected error: {exc}")
        import traceback
        traceback.print_exc()
        return 1

    finally:
        if validated_config_path:
            try:
                os.unlink(validated_config_path)
            except OSError:
                pass


__all__ = ["_config_spatial_dim", "cmd_run"]
