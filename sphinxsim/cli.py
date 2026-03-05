"""Command-line interface for SPHinXsys.

Usage examples
--------------
Generate a config from a natural-language description::

    sphinxsim generate "water flowing through a pipe at 2 m/s"

Validate an existing JSON config file::

    sphinxsim validate path/to/config.json

Run a simulation from a JSON config file::

    sphinxsim run path/to/config.json
"""

from __future__ import annotations

import argparse
import json
import sys
import os
from pathlib import Path
from typing import Tuple

from pydantic import ValidationError

import sphinxsim
from sphinxsim.config.schemas import SimulationConfig
from sphinxsim.llm import get_llm, run_from_config

def find_project_root(start: Path | None = None):
    start = start or Path.cwd()
    for path in [start] + list(start.parents):
        if (path / "pyproject.toml").exists():
            return path
    raise RuntimeError("Project root not found")

PROJECT_ROOT = find_project_root()

# Add parent directory for imports
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "build-integrated"))
original_dir = Path.cwd()

# ---------------------------------------------------------------------------
# Shared helper
# ---------------------------------------------------------------------------


def _load_config(path: Path) -> Tuple[SimulationConfig | None, int]:
    """Load and validate a SimulationConfig from *path*.

    Returns ``(config, 0)`` on success or ``(None, 1)`` after printing an
    error message to stderr.
    """
    if not path.exists():
        print(f"File not found: {path}", file=sys.stderr)
        return None, 1
    try:
        data = json.loads(path.read_text())
        return SimulationConfig(**data), 0
    except json.JSONDecodeError as exc:
        print(f"Invalid JSON: {exc}", file=sys.stderr)
        return None, 1
    except ValidationError as exc:
        print(f"Config validation failed:\n{exc}", file=sys.stderr)
        return None, 1


# ---------------------------------------------------------------------------
# Sub-command handlers
# ---------------------------------------------------------------------------


def cmd_generate(args: argparse.Namespace) -> int:
    """Generate a SimulationConfig from a natural-language *description*."""
    llm = get_llm()
    try:
        config = llm.generate(args.description)
    except (ValueError, ValidationError) as exc:
        print(f"Error generating config: {exc}", file=sys.stderr)
        return 1

    output = config.model_dump_json(indent=2)
    if args.output:
        output_path = PROJECT_ROOT / ".build-temp" / args.output
        try:
            if output_path.parent and not output_path.parent.exists():
                output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text(output)
        except OSError as exc:
            print(f"Error writing config to {output_path}: {exc}", file=sys.stderr)
            return 1
        print(f"Config written to {output_path}")
    else:
        print(output)
    return 0


def cmd_validate(args: argparse.Namespace) -> int:
    """Validate a JSON config file against the SimulationConfig schema."""
    config, rc = _load_config(Path(args.config_file))
    if rc != 0:
        return rc
    print(f"✅ Generated configuration:")
    print(f"   Name: {config.name}")
    print(f"   Physics: {config.physics.value}")
    print(f"   Domain: {config.domain.bounds_min} → {config.domain.bounds_max}")
    print(f"   Resolution: {config.domain.resolution}")
    print(f"   Materials: {len(config.materials)} material(s)")
    for mat in config.materials:
        print(f"     - {mat.name}: ρ={mat.density} kg/m³")
    print(f"   Boundary conditions: {len(config.boundary_conditions)}")
    for bc in config.boundary_conditions:
        print(f"     - {bc.name} ({bc.type.value})")
    print(f"   End time: {config.time_stepping.end_time}s")
    print(f"   Output interval: {config.time_stepping.output_interval}s")
    
    # Validate config can round-trip through JSON
    config_json = config.model_dump_json(indent=2)
    print(f"\n📄 Configuration as JSON ({len(config_json)} bytes)")
    print(config_json[:200] + "..." if len(config_json) > 200 else config_json)
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    """Run a simulation defined by a JSON config file."""
    config, rc = _load_config(Path(args.config_file))
    if rc != 0:
        return rc

    try:
        builder, result = run_from_config(config_reloaded)
        
        print("✅ Simulation completed successfully!")
        print(f"\n📊 Results:")
        print(f"   End time: {result.end_time}s")
        print(f"   Fluid blocks: {len(result.fluid_blocks)}")
        for fluid in result.fluid_blocks:
            print(f"     - {fluid}")
        print(f"   Wall boundaries: {len(result.walls)}")
        for wall in result.walls:
            print(f"     - {wall}")
        if result.gravity:
            print(f"   Gravity: {result.gravity}")
        print(f"   Observers: {len(result.observers)}")
        for name, pos in result.observers:
            print(f"     - {name} at position {pos}")
        
        # Show output location
        safe_name = config.name.replace(' ', '_').replace('/', '_')[:50]
        output_dir = PROJECT_ROOT / ".build-temp" / "simulations" / safe_name
        print(f"\n📁 Simulation output saved to:")
        print(f"   {output_dir}")
        
        return True
        
    except RuntimeError as e:
        if "C++ extension" in str(e):
            print("❌ C++ extension not available")
            print("\n🔧 Please build the C++ extension:")
            print("   cd sphinxsim/sphinxsys")
            print("   cmake --preset integrated-build")
            print("   ninja -C ../../build-integrated")
            return False
        else:
            raise
    
    except NotImplementedError as e:
        print(f"❌ Feature not yet implemented: {e}")
        print("\n💡 Tip: Try a fluid-only simulation like:")
        print('   "water dam break for 1 second"')
        return False
    
    except Exception as e:
        print(f"❌ Unexpected error: {e}")
        import traceback
        traceback.print_exc()
        return False
    finally:
        # Restore original directory
        os.chdir(original_dir)

# ---------------------------------------------------------------------------
# Argument parser
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="sphinxsim",
        description="Python UI for the SPHinXsys multi-physics C++ library.",
    )
    parser.add_argument("--version", action="version", version=f"sphinxsim {sphinxsim.__version__}")

    subparsers = parser.add_subparsers(dest="command", required=True)

    # generate
    gen = subparsers.add_parser(
        "generate",
        help="Generate a simulation config from a natural-language description.",
    )
    gen.add_argument("description", help="Natural-language simulation description.")
    gen.add_argument(
        "-o", "--output", metavar="FILE", default="config.json", help="Write JSON config to FILE instead of stdout."
    )
    gen.set_defaults(func=cmd_generate)

    # validate
    val = subparsers.add_parser(
        "validate", help="Validate a JSON simulation config against the schema."
    )
    val.add_argument("config_file", help="Path to JSON config file.")
    val.set_defaults(func=cmd_validate)

    # run
    run = subparsers.add_parser("run", help="Run a simulation from a JSON config file.")
    run.add_argument("config_file", help="Path to JSON config file.")
    run.set_defaults(func=cmd_run)

    return parser


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    """Entry point for the ``sphinxsim`` CLI."""
    parser = _build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
