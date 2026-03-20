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

import os
import sys

# Set up sys.path FIRST, before any sphinxsim imports
def _find_project_root(start=None):
    start = start or os.getcwd()
    current = start
    while current != os.path.dirname(current):  # Not at root
        if os.path.exists(os.path.join(current, "pyproject.toml")):
            return current
        current = os.path.dirname(current)
    raise RuntimeError("Project root not found")

PROJECT_ROOT = _find_project_root()
sys.path.insert(0, PROJECT_ROOT)
sys.path.insert(0, os.path.join(PROJECT_ROOT, "build-integrated"))
original_dir = os.getcwd()

# NOW import everything else
import argparse
import json
from pathlib import Path
from typing import Tuple

from pydantic import ValidationError

from sphinxsim.config.schemas import SimulationConfig
from sphinxsim.llm import get_llm
import _sphinxsys_core_2d as sph

# Convert PROJECT_ROOT to Path after imports
PROJECT_ROOT = Path(PROJECT_ROOT)

__version__ = "0.1.0"  # Keep in sync with sphinxsim/__init__.py

# ---------------------------------------------------------------------------
# Shared helper
# ---------------------------------------------------------------------------


def _load_config(path: Path) -> Tuple[SimulationConfig | None, int]:
    """Load and validate a SimulationConfig from *path*.

    Returns ``(config, 0)`` on success or ``(None, 1)`` after printing an
    error message to stderr.
    """
    # If path is relative, resolve it under.build-temp directory
    if not path.is_absolute():
        path = PROJECT_ROOT / ".build-temp" / path
    
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
    print(f"   Domain dimensions: {config.domain.dimensions}")
    print(f"   Particle spacing: {config.particle_spacing}")
    print(f"   Particle boundary buffer: {config.particle_boundary_buffer}")
    print(f"   Fluid blocks: {len(config.fluid_blocks)}")
    for block in config.fluid_blocks:
        print(f"     - {block.name}: dims={block.dimensions}, rho={block.density}, c={block.sound_speed}")
    print(f"   Walls: {len(config.walls)}")
    for wall in config.walls:
        print(
            f"     - {wall.name}: width={config.particle_boundary_buffer * config.particle_spacing}"
        )
    if config.gravity is not None:
        print(f"   Gravity: {config.gravity}")
    print(f"   Observers: {len(config.observers)}")
    print(f"   End time: {config.end_time if config.end_time is not None else '(set at runtime)'}")
    
    # Validate config can round-trip through JSON
    config_json = config.model_dump_json(indent=2)
    print(f"\n📄 Configuration as JSON ({len(config_json)} bytes)")
    print(config_json[:200] + "..." if len(config_json) > 200 else config_json)
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    """Run a simulation defined by a JSON config file."""
    config_path = Path(args.config_file)
    config, rc = _load_config(config_path)
    if rc != 0:
        return rc

    if not config_path.is_absolute():
        config_path = PROJECT_ROOT / ".build-temp" / config_path

    try:
        sim = sph.SPHSimulation(str(config_path))
        sim.loadConfig()
        print("✅ Simulation created and configuration loaded")
        
        # Create temp directory in project root, not relative to cwd
        work_dir = PROJECT_ROOT / ".build-temp" / "test_simulation"
        work_dir.mkdir(exist_ok=True, parents=True)
        os.chdir(work_dir)
        
        sim.run(config.end_time if config.end_time is not None else 1.0)
        
        print("✅ Simulation completed successfully!")
        print(f"\n📊 Run summary:")
        print(f"   End time: {config.end_time if config.end_time is not None else 1.0}s")
        print(f"   Fluid block: {config.fluid_blocks[0].name}")
        print(f"   Run config: {config_path}")
        
        # Show output location
        safe_name = config.fluid_blocks[0].name.replace(' ', '_').replace('/', '_')[:50]
        output_dir = PROJECT_ROOT / ".build-temp" / "simulations" / safe_name
        print(f"\n📁 Simulation output saved to:")
        print(f"   {output_dir}")
        
        return 0
        
    except RuntimeError as e:
        if "C++ extension" in str(e):
            print("❌ C++ extension not available")
            print("\n🔧 Please build the C++ extension:")
            print("   cd sphinxsim/sphinxsys")
            print("   cmake --preset integrated-build")
            print("   ninja -C ../../build-integrated")
            return 1
        else:
            raise
    
    except NotImplementedError as e:
        print(f"❌ Feature not yet implemented: {e}")
        print("\n💡 Tip: Try a fluid-only simulation like:")
        print('   "water dam break for 1 second"')
        return 1
    
    except Exception as e:
        print(f"❌ Unexpected error: {e}")
        import traceback
        traceback.print_exc()
        return 1
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
    parser.add_argument("--version", action="version", version=f"sphinxsim {__version__}")

    subparsers = parser.add_subparsers(dest="command", required=True)

    # generate
    gen = subparsers.add_parser(
        "generate",
        help="Generate a simulation config from a natural-language description.",
    )
    gen.add_argument("description", help="Natural-language simulation description.")
    gen.add_argument(
        "-o", "--output", metavar="FILE", default=None, help="Write JSON config to FILE instead of stdout."
    )
    gen.set_defaults(func=cmd_generate)

    # validate
    val = subparsers.add_parser(
        "validate", help="Validate a JSON simulation config against the schema."
    )
    val.add_argument("config_file", nargs='?', default="config.json", help="Path to JSON config file.")
    val.set_defaults(func=cmd_validate)

    # run
    run = subparsers.add_parser("run", help="Run a simulation from a JSON config file.")
    run.add_argument("config_file", nargs='?', default="config.json", help="Path to JSON config file.")
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
