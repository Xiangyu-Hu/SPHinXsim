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
import copy
import glob
import json
import os
import queue
import shlex
import subprocess
import sys
import tempfile
import time
import warnings
from pathlib import Path
from threading import Thread
from typing import Any, Tuple

try:
    import readline
except ImportError:
    readline = None

# Set up sys.path FIRST, before any sphinxsim imports
def _find_project_root(start=None):
    start = start or str(Path(__file__).resolve().parent)
    current = start
    while current != os.path.dirname(current):  # Not at root
        if os.path.exists(os.path.join(current, "pyproject.toml")) and os.path.isfile(os.path.join(current, "sphinxsim", "cli.py")):
            return current
        current = os.path.dirname(current)
    return os.getcwd()

PROJECT_ROOT = _find_project_root()
sys.path.insert(0, PROJECT_ROOT)
sys.path.insert(0, os.path.join(PROJECT_ROOT, "build-integrated"))
sys.path.insert(0, os.path.join(PROJECT_ROOT, "sphinxsim", "bindings", "native"))

from pydantic import ValidationError

from sphinxsim.bindings.loader import load_sphinxsys_core_nd
from sphinxsim.config.schemas import PhysicalCorrectionWarning, SimulationConfig
from sphinxsim.config.update_patch import UpdatePatch, apply_update_patch
from sphinxsim.json_editor import (
    _apply_payload_diff,
    _config_spatial_dim,
    _new_json_editor_list_item,
)
from sphinxsim.llm import get_llm
from sphinxsim.llm.common import LLMRepairWarning, dump_simulation_config_json
from sphinxsim.visualization.preview_runtime import (
    _ShellPreviewRuntime,
    _remove_temporary_file,
    _write_validated_config,
)

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
    # Prefer user-provided relative paths from the current working directory.
    # Fall back to .build-temp for backward compatibility with existing workflows.
    if not path.is_absolute():
        cwd_path = Path.cwd() / path
        build_temp_path = PROJECT_ROOT / ".build-temp" / path
        path = cwd_path if cwd_path.exists() else build_temp_path
    
    if not path.exists():
        print(f"File not found: {path}", file=sys.stderr)
        return None, 1
    try:
        data = json.loads(path.read_text())
        from sphinxsim.runtime.physics import check_config
        normalized, _ = check_config(data)
        return SimulationConfig(**normalized), 0
    except json.JSONDecodeError as exc:
        print(f"Invalid JSON: {exc}", file=sys.stderr)
        return None, 1
    except (ValidationError, ValueError) as exc:
        print(f"Config validation failed:\n{exc}", file=sys.stderr)
        return None, 1


def _read_shell_input(prompt: str, preview_runtime: _ShellPreviewRuntime) -> str:
    """Read shell input while servicing Qt events for a persistent preview."""
    if (
        preview_runtime.plotter is None
        or not preview_runtime._using_background_plotter
    ):
        return input(prompt)

    result: queue.Queue[tuple[str | None, Exception | None]] = queue.Queue(maxsize=1)

    def read_input() -> None:
        try:
            result.put((input(prompt), None))
        except Exception as exc:
            result.put((None, exc))

    Thread(target=read_input, daemon=True).start()
    while True:
        try:
            line, error = result.get(timeout=0.01)
        except queue.Empty:
            preview_runtime.pump_ui_events()
            continue
        if error is not None:
            raise error
        if line is None:
            raise RuntimeError("Shell input returned no command.")
        return line


def _short_repr(value: Any, max_len: int = 80) -> str:
    text = repr(value)
    if len(text) <= max_len:
        return text
    return text[: max_len - 3] + "..."


def _collect_change_lines(before: Any, after: Any, path: str = "") -> list[str]:
    if isinstance(before, dict) and isinstance(after, dict):
        lines: list[str] = []
        keys = sorted(set(before.keys()) | set(after.keys()))
        for key in keys:
            key_path = f"{path}.{key}" if path else key
            if key not in before:
                lines.append(f"{key_path}: added {_short_repr(after[key])}")
                continue
            if key not in after:
                lines.append(f"{key_path}: removed")
                continue
            lines.extend(_collect_change_lines(before[key], after[key], key_path))
        return lines

    if isinstance(before, list) and isinstance(after, list):
        if before == after:
            return []
        return [f"{path or '<root>'}: list changed (size {len(before)} -> {len(after)})"]

    if before != after:
        return [f"{path or '<root>'}: {_short_repr(before)} -> {_short_repr(after)}"]
    return []


def _print_update_summary(before_cfg: SimulationConfig, after_cfg: SimulationConfig) -> None:
    before = before_cfg.model_dump(exclude_none=True)
    after = after_cfg.model_dump(exclude_none=True)
    changes = _collect_change_lines(before, after)
    if not changes:
        print("ℹ️ No effective changes were applied to the config.")
        return

    print("Changes applied:")
    for line in changes[:12]:
        print(f"  - {line}")
    if len(changes) > 12:
        print(f"  - ... and {len(changes) - 12} more changes")


# ---------------------------------------------------------------------------
# Sub-command handlers
# ---------------------------------------------------------------------------


def _generate_with_physical_correction_feedback(llm: Any, description: str) -> Any:
    """Generate a config and report deterministic corrections and LLM repairs."""
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always", PhysicalCorrectionWarning)
        warnings.simplefilter("always", LLMRepairWarning)
        config = llm.generate(description)

    for warning in caught:
        if issubclass(warning.category, PhysicalCorrectionWarning):
            print(f"Physical correction applied: {warning.message}", file=sys.stderr)
        elif issubclass(warning.category, LLMRepairWarning):
            print(f"LLM repair applied: {warning.message}", file=sys.stderr)
        else:
            warnings.showwarning(
                warning.message,
                warning.category,
                warning.filename,
                warning.lineno,
            )
    return config


def cmd_generate(args: argparse.Namespace) -> int:
    """Generate a SimulationConfig from a natural-language *description*."""
    llm = get_llm()
    try:
        config = _generate_with_physical_correction_feedback(llm, args.description)
    except (ValueError, ValidationError) as exc:
        print(f"Error generating config: {exc}", file=sys.stderr)
        return 1

    output = dump_simulation_config_json(config, indent=2)
    if args.output:
        output_path = Path(args.output)
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


def cmd_update(args: argparse.Namespace) -> int:
    """Update an existing SimulationConfig from a natural-language instruction."""
    config_path = Path(args.config_file)
    config, rc = _load_config(config_path)
    if rc != 0:
        return rc
    assert config is not None

    llm = get_llm()
    try:
        if not hasattr(llm, "update"):
            print(
                "The selected LLM provider does not support config updates. "
                "Please use a provider implementing update().",
                file=sys.stderr,
            )
            return 1
        strict_mode = str(getattr(args, "strict", "true")).lower() != "false"
        if getattr(args, "patch_mode", False):
            if not hasattr(llm, "update_patch"):
                print(
                    "The selected LLM provider does not support patch-mode updates. "
                    "Please use a provider implementing update_patch().",
                    file=sys.stderr,
                )
                return 1
            patch_payload = llm.update_patch(config, args.description, strict=strict_mode)
            if isinstance(patch_payload, UpdatePatch):
                parsed_patch = patch_payload
            else:
                parsed_patch = UpdatePatch.model_validate(patch_payload)

            patch_result = apply_update_patch(
                config.model_dump(exclude_none=True), parsed_patch, strict=strict_mode
            )
            if patch_result.errors:
                print("Error applying update patch:", file=sys.stderr)
                for error in patch_result.errors:
                    print(f"  - {error}", file=sys.stderr)
                return 1

            try:
                updated_config = SimulationConfig.model_validate(patch_result.updated)
            except ValidationError as exc:
                print(f"Patched config validation failed:\n{exc}", file=sys.stderr)
                return 1

            print("Patch summary:")
            print(f"  Applied: {patch_result.applied}")
            print(f"  Changed: {patch_result.changed}")
            print(f"  Operations: {patch_result.summary}")
            print(f"  Diff stats: {patch_result.diff_stats}")
            if patch_result.warnings:
                print("  Warnings:")
                for warning in patch_result.warnings:
                    print(f"    - {warning}")

            if getattr(args, "dry_run", False):
                print("Dry run: no files were written.")
                print("Generated patch:")
                print(parsed_patch.model_dump_json(indent=2, exclude_none=True))
                return 0
        else:
            updated_config = llm.update(config, args.description)
    except (ValueError, ValidationError) as exc:
        print(f"Error updating config: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"Unexpected error updating config: {exc}", file=sys.stderr)
        return 1

    output_path = Path(args.output) if args.output else config_path

    output = updated_config.model_dump_json(indent=2, exclude_none=True)
    try:
        if output_path.parent and not output_path.parent.exists():
            output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(output)
    except OSError as exc:
        print(f"Error writing updated config to {output_path}: {exc}", file=sys.stderr)
        return 1

    if args.output:
        print(f"Updated config written to {output_path}")
    else:
        print(f"Updated config in place: {output_path}")

    _print_update_summary(config, updated_config)
    return 0


def cmd_validate(args: argparse.Namespace) -> int:
    """Validate a JSON config file against the SimulationConfig schema."""
    config, rc = _load_config(Path(args.config_file))
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
        print(
            "     - "
            f"{body.name}: "
            f"material={body.material.type.value}"
        )
    print(f"   Continuum bodies: {len(config.continuum_bodies)}")
    for body in config.continuum_bodies:
        print(
            "     - "
            f"{body.name}: "
            f"material={body.material.type.value}"
        )
    print(f"   Solid bodies: {len(config.solid_bodies)}")
    for body in config.solid_bodies:
        print(
            "     - "
            f"{body.name}: "
            f"material={body.material.type.value}"
        )
    if config.gravity is not None:
        print(f"   Gravity: {config.gravity}")
    print(f"   Observers: {len(config.observers)}")
    end_time = config.solver_parameters.end_time
    print(f"   End time: {end_time if end_time is not None else '(set by solver defaults)'}")
    
    # Validate config can round-trip through JSON
    config_json = config.model_dump_json(indent=2, exclude_none=True)
    print(f"\n📄 Configuration as JSON ({len(config_json)} bytes)")
    print(config_json[:200] + "..." if len(config_json) > 200 else config_json)
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    """Run a simulation defined by a JSON config file."""
    config_path = Path(args.config_file)
    config, rc = _load_config(config_path)
    if rc != 0:
        return rc
    assert config is not None

    ndim = _config_spatial_dim(config)
    try:
        sph = load_sphinxsys_core_nd(ndim)
    except ImportError:
        print(f"❌ {ndim}D C++ extension not available", file=sys.stderr)
        print("\n🔧 Please build the C++ extension:", file=sys.stderr)
        print("   cd sphinxsim/sphinxsys", file=sys.stderr)
        print("   cmake --preset integrated-build", file=sys.stderr)
        print("   ninja -C ../../build-integrated", file=sys.stderr)
        return 1

    if not config_path.is_absolute():
        config_path = PROJECT_ROOT / ".build-temp" / config_path

    # Write the Pydantic-validated config to a temp file before passing to C++.
    validated_config_path: Path | None = None
    config_written = False
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            suffix=".json",
            delete=False,
            prefix="sphinxsim_run_",
            dir=str(config_path.parent),
        ) as tmp_cfg:
            validated_config_path = Path(tmp_cfg.name)
            tmp_cfg.write(config.model_dump_json(indent=2, exclude_none=True))
        config_written = True
    except OSError as exc:
        print(f"Error writing validated config: {exc}", file=sys.stderr)
        return 1
    finally:
        if not config_written:
            _remove_temporary_file(validated_config_path, "validated run config")

    assert validated_config_path is not None
    try:
        sim = sph.SPHSimulation(str(validated_config_path))
        print(f"Preview runtime config written to: {validated_config_path}")

        # Preserve the legacy shared root unless an isolated run directory is requested.
        requested_output_dir = getattr(args, "output_dir", None)
        output_dir = (
            Path(requested_output_dir).resolve()
            if requested_output_dir
            else PROJECT_ROOT / ".build-temp" / "test_simulation"
        )
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

        # Run simulation
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

        # Show output location
        # Keep output_dir unchanged: it is the actual root passed to SPHinXsys.
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
        # Always clean up the validated temp config.
        _remove_temporary_file(validated_config_path, "validated run config")


def cmd_preview(args: argparse.Namespace) -> int:
    """Render an interactive geometry/BC preview of a JSON config file."""
    try:
        import pyvista  # noqa: F401
    except ImportError:
        print(
            "❌ PyVista is not installed.\n"
            "   Install it with:  pip install sphinxsim[visualization]",
            file=sys.stderr,
        )
        return 1

    config_path = Path(args.config_file)
    config, rc = _load_config(config_path)
    if rc != 0:
        return rc
    assert config is not None

    resolved_config_path = _resolve_preview_config_path(args.config_file)

    from sphinxsim.visualization.preview import ConfigVisualizer

    off_screen = getattr(args, "off_screen", False)
    screenshot_path = getattr(args, "screenshot", None)
    with_particles = getattr(args, "with_particles", False)
    watch_mode = getattr(args, "watch", False)
    watch_output_dir = getattr(args, "watch_output_dir", None)

    # Screenshot mode implies off-screen rendering.
    if screenshot_path:
        off_screen = True

    if watch_mode and not off_screen:
        runtime = _ShellPreviewRuntime()
        rc = runtime.show_or_update(
            config,
            resolved_config_path=resolved_config_path,
            with_particles=with_particles,
        )
        if rc != 0:
            return rc
        out_dir = Path(watch_output_dir).resolve() if watch_output_dir else None
        runtime.start_file_watch(
            resolved_config_path,
            config=config,
            output_dir=out_dir,
            with_particles=with_particles,
        )
        print(f"👀 Watching {resolved_config_path} for changes...")
        if runtime._using_background_plotter and runtime.plotter is not None:
            try:
                from qtpy import QtWidgets

                app = QtWidgets.QApplication.instance()
                if app is not None:
                    return int(app.exec())
            except ImportError:
                pass
        return 0

    print(f"🖼  Building configuration preview for: {resolved_config_path}")
    print("   Building C++ geometry; bounds cache fallback will be used if VTP meshes are unavailable.")
    if with_particles:
        print("   Particle generation overlay is enabled (--with-particles).")

    visualizer = ConfigVisualizer(
        config,
        PROJECT_ROOT,
        config_path=resolved_config_path,
        off_screen=off_screen,
    )
    try:
        visualizer.preview(
            screenshot_path=screenshot_path,
            with_particles=with_particles,
        )
        if visualizer.used_cpp_geometry:
            print("✅ Preview used C++ geometry (VTP meshes).")
        else:
            print("ℹ️ Preview used C++ bounds fallback (no VTP meshes produced).")
        if screenshot_path:
            print(f"📸 Screenshot saved to: {screenshot_path}")
    except ImportError as exc:
        print(f"❌ {exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"❌ Preview failed: {exc}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        return 1

    return 0


def _schema_explore_context() -> str:
    schema = json.dumps(SimulationConfig.model_json_schema(), indent=2)
    return (
        "You are helping a user understand the SPHinXsim simulator schema and capabilities. "
        "Answer clearly and concisely using the schema as the source of truth. "
        "When relevant, include practical command examples.\n\n"
        "CLI capabilities:\n"
        "- generate: create a config from natural language\n"
        "- update: revise an existing config with natural language\n"
        "- validate: schema-validate and summarize a config\n"
        "- run: execute simulation from validated config\n"
        "- shell: interactive mode for generate/update/validate/run\n\n"
        "SimulationConfig JSON schema:\n"
        f"{schema}"
    )


def cmd_explore(args: argparse.Namespace) -> int:
    """Answer schema/functionality questions using the configured LLM provider."""
    question = args.question.strip() if args.question else ""
    if not question:
        print("question must not be empty", file=sys.stderr)
        return 1

    llm = get_llm()
    if not hasattr(llm, "explore"):
        print(
            "The selected LLM provider does not support schema exploration.",
            file=sys.stderr,
        )
        return 1

    try:
        answer = llm.explore(question, context=_schema_explore_context())
    except Exception as exc:
        print(f"Error exploring schema: {exc}", file=sys.stderr)
        return 1

    print("Top-level SimulationConfig fields and guidance:")
    print(answer)
    return 0


def cmd_templates(args: argparse.Namespace) -> int:
    """List verified 2D and 3D reference templates."""
    from sphinxsim.templates import list_templates

    items = list_templates(PROJECT_ROOT)
    if not items:
        print("No reference templates found.", file=sys.stderr)
        return 1
    print(f"Available reference templates ({len(items)}):")
    for item in items:
        assets = f" [assets: {', '.join(item['assets'])}]" if item["assets"] else ""
        bodies = ", ".join(item["bodies"])
        print(
            f"  {item['id']:<28} {item['dimension']}D  {item['simulation_type']:<20} "
            f"bodies: {bodies}{assets}"
        )
    return 0


def cmd_init(args: argparse.Namespace) -> int:
    """Initialize a configuration file from a verified reference template."""
    from sphinxsim.templates import init_template

    try:
        result = init_template(
            args.template,
            Path(args.config_file),
            project_root=PROJECT_ROOT,
            overwrite=bool(getattr(args, "overwrite", False)),
        )
    except (OSError, ValueError) as exc:
        print(f"Error initializing template: {exc}", file=sys.stderr)
        return 1

    print(
        f"✅ Initialized {result['template']} ({result['dimension']}D {result['simulation_type']}) "
        f"-> {result['config_file']}"
    )
    if result["assets"]:
        print(f"   Staged assets: {', '.join(result['assets'])}")
    return 0


def cmd_restore(args: argparse.Namespace) -> int:
    """Restore a configuration file atomically from its .bak backup."""
    from sphinxsim.operations import Operations

    config_path = _resolve_preview_config_path(args.config_file).resolve()
    if not config_path.is_file():
        print(f"File not found: {config_path}", file=sys.stderr)
        return 1
    root = _workspace_root_for(config_path, getattr(args, "root", None))
    try:
        ops = Operations(root)
        result = ops.call(
            "config_restore",
            {
                "config_file": config_path.relative_to(root).as_posix(),
                "dry_run": bool(getattr(args, "dry_run", False)),
            },
        )
    except (OSError, ValueError) as exc:
        print(f"Error restoring config: {exc}", file=sys.stderr)
        return 1

    action = "Dry run restore for" if result["dry_run"] else "✅ Restored"
    print(f"{action} {config_path} from {result['backup_file']}")
    if result["diff"]:
        print(result["diff"].rstrip())
    return 0


def cmd_results(args: argparse.Namespace) -> int:
    """Summarize VTP frames, observer/energy .dat files, and optional screenshots."""
    from sphinxsim.results import AnalysisError, render_run_results, summarize_run_results

    config_path = _resolve_preview_config_path(args.config_file)
    output_dir = (
        Path(args.output_dir).resolve()
        if getattr(args, "output_dir", None)
        else PROJECT_ROOT / ".build-temp" / "test_simulation"
    )
    try:
        summary = summarize_run_results(config_path, output_dir)
        rendered = None
        if getattr(args, "screenshot", None):
            rendered = render_run_results(
                config_path,
                output_dir,
                Path(args.screenshot),
                color_by=getattr(args, "color_by", None),
                frame=getattr(args, "frame", None) or "latest",
                project_root=PROJECT_ROOT,
            )
    except (AnalysisError, ImportError, OSError, ValueError, RuntimeError) as exc:
        print(f"Error inspecting results: {exc}", file=sys.stderr)
        return 1

    print(f"📊 Results summary for {summary['config_file']}")
    print(f"   Output directory: {summary['output_dir']}")
    print(f"   Quality state:    {summary['quality'].get('state', 'unknown')}")
    for body_name, info in summary["bodies"].items():
        line = (
            f"   - {body_name} ({info['kind']}): "
            f"{info['solver_frames']} solver frames, "
            f"{info['relaxation_frames']} relaxation frames"
        )
        if info.get("solver_frames"):
            line += f", t=[{info['first_time']:g}, {info['last_time']:g}]"
            if "particle_count" in info:
                line += f", N={info['particle_count']}"
            if info.get("bounds_min") is not None:
                line += f", bounds={info['bounds_min']}..{info['bounds_max']}"
        print(line)
        if info.get("fields"):
            print(f"       fields: {', '.join(info['fields'])}")
    if summary["time_series"]:
        print("   Time-series (.dat):")
        for ts in summary["time_series"]:
            cols = ", ".join(
                f"{col}=[min={vals['min']:g}, max={vals['max']:g}, final={vals['final']:g}]"
                for col, vals in ts["series"].items()
            )
            print(
                f"     - {ts['file']} ({ts['rows']} rows, "
                f"t=[{ts['start_time']:g}, {ts['end_time']:g}]): {cols}"
            )
    for finding in summary["quality"].get("findings", []):
        print(f"   ⚠️ [{finding.get('code')}] {finding.get('body', '')}: {finding.get('message')}")
    if rendered is not None:
        print(f"📸 Result screenshot saved to: {rendered['file']}")
    return 0


def _workspace_root_for(
    config_path: Path | None,
    explicit_root: str | Path | None = None,
) -> Path:
    if explicit_root is not None:
        return Path(explicit_root).resolve()
    cwd = Path.cwd().resolve()
    if config_path is not None:
        resolved = config_path.resolve()
        if not resolved.is_relative_to(cwd):
            return resolved.parent
    return cwd


def _run_agentic_turn(
    prompt: str,
    *,
    root: Path,
    config_path: Path | None = None,
    output_dir: Path | None = None,
    dry_run: bool = False,
    messages: list[dict[str, Any]] | None = None,
    prior_observations: list[dict[str, Any]] | None = None,
    as_json: bool = False,
    backend: Any | None = None,
) -> tuple[str, list[dict[str, Any]], list[dict[str, Any]]]:
    from sphinxsim.llm import normalize_llm_provider
    from sphinxsim.llm.mcp_tools import InProcessOperationsClient, MCPToolConversation

    provider = normalize_llm_provider()
    llm = get_llm(provider)
    client = InProcessOperationsClient(root, read_only=dry_run)

    history = list(
        messages
        or [
            {
                "role": "system",
                "content": (
                    "You are the SPHinXsim CLI assistant. JSON configuration files on disk "
                    "are the single source of truth. Use the discovered SPHinXsim operations "
                    "to inspect templates/capabilities/docs, read or patch config.json, "
                    "validate inputs, submit/wait/inspect jobs, and summarize VTP/.dat results. "
                    "Report exact file paths and numerical evidence."
                ),
            }
        ]
    )
    history.append({"role": "user", "content": prompt})

    ctx_payload: dict[str, Any] = {
        "workspace_root": str(root),
        "dry_run": dry_run,
    }
    if config_path is not None and config_path.resolve().is_relative_to(root):
        rel_cfg = config_path.resolve().relative_to(root).as_posix()
        ctx_payload["active_config_file"] = rel_cfg
        if config_path.is_file():
            try:
                from sphinxsim.config.migrations import migrate_legacy_configuration
                from sphinxsim.runtime.physics import check_config

                normalized, _ = check_config(
                    migrate_legacy_configuration(json.loads(config_path.read_text(encoding="utf-8")))
                )
                SimulationConfig.model_validate(normalized)
                ctx_payload["active_config_valid"] = True
            except Exception as exc:
                ctx_payload["active_config_valid"] = False
                ctx_payload["active_config_error"] = f"{type(exc).__name__}: {exc}"
    if output_dir is not None and output_dir.resolve().is_relative_to(root):
        ctx_payload["active_output_dir"] = output_dir.resolve().relative_to(root).as_posix()
    last_job = root / ".sphinxsim" / "last-job.json"
    if last_job.is_file():
        try:
            ctx_payload["last_job_reference"] = json.loads(last_job.read_text(encoding="utf-8"))
        except Exception:
            pass

    step_stream = sys.stderr if as_json else sys.stdout

    def on_step(tool_name: str, tool_args: dict[str, Any], outcome: dict[str, Any]) -> None:
        status = "ok" if outcome.get("ok") else f"error: {outcome.get('error')}"
        compact_args = json.dumps(tool_args, ensure_ascii=False)
        if len(compact_args) > 80:
            compact_args = compact_args[:77] + "..."
        print(f"  [tool] {tool_name}({compact_args}) -> {status}", file=step_stream)

    conversation = MCPToolConversation(
        client,
        llm,
        provider,
        backend=backend,
        on_step=on_step,
    )
    answer = conversation.respond(
        history,
        context="Session context:\n" + json.dumps(ctx_payload, ensure_ascii=False),
        prior_observations=prior_observations,
    )
    history.append({"role": "assistant", "content": answer})
    return answer, history, conversation.last_observations


def cmd_ask(args: argparse.Namespace) -> int:
    """Run one agentic turn grounded on in-process SPHinXsim CLI operations."""
    prompt = (args.prompt or "").strip()
    if not prompt:
        print("prompt must not be empty", file=sys.stderr)
        return 1
    cfg_path = _resolve_preview_config_path(args.config) if getattr(args, "config", None) else None
    out_dir = Path(args.output_dir).resolve() if getattr(args, "output_dir", None) else None
    root = _workspace_root_for(cfg_path, getattr(args, "root", None))
    as_json = bool(getattr(args, "json", False))
    try:
        answer, _, observations = _run_agentic_turn(
            prompt,
            root=root,
            config_path=cfg_path,
            output_dir=out_dir,
            dry_run=bool(getattr(args, "dry_run", False)),
            as_json=as_json,
            backend=getattr(args, "_backend", None),
        )
    except Exception as exc:
        if as_json:
            print(json.dumps({"ok": False, "error": {"message": str(exc)}}))
        else:
            print(f"Error in ask: {exc}", file=sys.stderr)
        return 1

    if as_json:
        print(
            json.dumps(
                {"ok": True, "data": {"answer": answer, "observations": observations}},
                ensure_ascii=False,
            )
        )
    else:
        print(answer)
    return 0


def _shell_resolve_config_path(config_file: str) -> Path:
    path = Path(config_file)
    if path.is_absolute():
        return path

    # In shell mode, prefer paths relative to the current working directory.
    # Keep .build-temp fallback for existing workflows and tests.
    cwd_path = Path.cwd() / path
    build_temp_path = PROJECT_ROOT / ".build-temp" / path
    return cwd_path if cwd_path.exists() else build_temp_path


def _shell_auto_validate(config_path: Path) -> bool:
    cfg, rc = _load_config(config_path)
    if rc != 0 or cfg is None:
        print("❌ Auto-validation failed", file=sys.stderr)
        return False
    print(f"✅ Auto-validation passed: {config_path}")
    return True


_SHELL_COMMANDS = (
    "load",
    "templates",
    "init",
    "restore",
    "undo",
    "generate",
    "update",
    "validate",
    "run",
    "preview",
    "results",
    "capabilities",
    "compile",
    "geometry",
    "project",
    "job",
    "agent",
    "ask",
    "initialize-simulation",
    "continue-to-run",
    "replay",
    "explore",
    "help",
    "exit",
    "quit",
)


def _split_shell_command(text: str) -> list[str]:
    if sys.platform != "win32":
        return shlex.split(text)
    lexer = shlex.shlex(text, posix=True)
    lexer.whitespace_split = True
    lexer.commenters = ""
    lexer.escape = ""
    return list(lexer)


def _shell_completion_matches(prefix: str, text: str) -> list[str]:
    try:
        words = _split_shell_command(prefix)
    except ValueError:
        words = prefix.split()

    if not words:
        commands = (
            [f"/{command}" for command in _SHELL_COMMANDS]
            if text.startswith("/")
            else _SHELL_COMMANDS
        )
        return [command for command in commands if command.startswith(text)]

    command = words[0].lstrip("/")
    if not (
        (command == "load" and len(words) == 1)
        or command == "generate"
        or (command == "preview" and "--screenshot" in words)
    ):
        return []

    expanded_text = os.path.expanduser(text)
    matches = glob.glob(expanded_text + "*")
    results = []
    for match in matches:
        candidate = match
        if text.startswith("~"):
            relative = os.path.relpath(match, os.path.expanduser("~"))
            candidate = "~" if relative == "." else os.path.join("~", relative)
        if sys.platform == "win32" and "/" in text and "\\" not in text:
            candidate = candidate.replace("\\", "/")
        if os.path.isdir(match):
            candidate += "/"
        results.append(candidate)
    return sorted(results)


def _windows_shell_completion_matches(line: str) -> list[str]:
    start = 0
    quote = None
    token = []
    for index, char in enumerate(line):
        if quote:
            if char == quote:
                quote = None
            else:
                token.append(char)
        elif char in "\"'":
            quote = char
        elif char.isspace():
            start = index + 1
            token = []
        else:
            token.append(char)

    text = "".join(token)
    prefix = line[:start]
    matches = _shell_completion_matches(prefix, text)
    if not prefix:
        return matches

    quoted = quote or line[start:start + 1] in ("'", '"') or any(
        char.isspace() or char == "'" for match in matches for char in match
    )
    quote_char = line[start:start + 1]
    if quote_char not in ("'", '"') or any(quote_char in match for match in matches):
        quote_char = '"'
    results = []
    for match in matches:
        if match.endswith("/") and "\\" in text and "/" not in text:
            match = match[:-1] + "\\"
        if quoted:
            closing = "" if match.endswith(("/", "\\")) else quote_char
            match = quote_char + match + closing
        results.append(prefix + match)
    return results


def _shell_readline_completer(text: str, state: int) -> str | None:
    if readline is None:
        return None
    if sys.platform == "win32":
        matches = _windows_shell_completion_matches(text)
    else:
        matches = _shell_completion_matches(
            readline.get_line_buffer()[: readline.get_begidx()], text
        )
    return matches[state] if state < len(matches) else None


def _configure_shell_readline() -> None:
    if readline is None:
        print("Shell completion and history are unavailable: readline is not installed.", file=sys.stderr)
        return
    if sys.platform == "win32":
        # pyreadline3 does not recognize quoted delimiters; complete the whole line.
        readline.set_completer_delims("")
    else:
        delimiters = readline.get_completer_delims().replace("/", "")
        readline.set_completer_delims(delimiters)
    readline.set_completer(_shell_readline_completer)
    readline.parse_and_bind("tab: complete")


def _resolve_preview_config_path(config_file: str) -> Path:
    """Resolve preview config path using cwd-first then .build-temp fallback."""
    config_path = Path(config_file)
    if config_path.is_absolute():
        return config_path
    cwd_path = Path.cwd() / config_path
    build_temp_path = PROJECT_ROOT / ".build-temp" / config_path
    return cwd_path if cwd_path.exists() else build_temp_path




def cmd_shell(args: argparse.Namespace) -> int:
    """Interactive shell for load/generate/update/validate/run workflow."""
    if sys.stdin.isatty():
        _configure_shell_readline()
    provider = os.getenv("SPHINXSIM_LLM_PROVIDER", "mock")
    print("SPHinXsim interactive shell")
    print(f"LLM provider: {provider}")
    print(
            "Commands: load FILE, generate DESCRIPTION FILE, "
            "update [--patch-mode] [--dry-run] [--strict true|false] INSTRUCTION, "
            "validate, run [--with-animation], preview [--with-particles] [--screenshot FILE], "
                "initialize-simulation, continue-to-run, replay [--relaxation], explore QUESTION, exit"
    )
    print("Note: relative paths are resolved from the current directory first, then .build-temp/.")

    config_path: Path | None = None
    if getattr(args, "config_file", None):
        initial_cfg = _shell_resolve_config_path(str(args.config_file))
        cfg_obj, rc_init = _load_config(initial_cfg)
        if rc_init == 0 and cfg_obj is not None:
            config_path = initial_cfg
            print(f"✅ Loaded config: {config_path}")
        else:
            return 1

    explicit_root = getattr(args, "root", None)
    active_output_dir: Path | None = None
    last_job_ref_file: Path | None = None
    agent_messages: list[dict[str, Any]] | None = None
    agent_observations: list[dict[str, Any]] = []
    shell_sim = None
    preview_runtime = _ShellPreviewRuntime()

    def _safe_cli_call(sub_argv: list[str]) -> int:
        try:
            return main(sub_argv)
        except SystemExit as exc:
            return int(exc.code) if isinstance(exc.code, int) else 0

    while True:
        try:
            line = _read_shell_input("sphinxsim> ", preview_runtime).strip()
        except (EOFError, KeyboardInterrupt):
            preview_runtime.close()
            print()
            return 0

        if not line:
            continue

        if line in {"exit", "quit"}:
            preview_runtime.close()
            return 0

        if line == "help":
            preview_runtime.discard_pending_simulation()
            print("Commands:")
            print("  templates [--json]              - List verified 2D/3D reference templates")
            print("  init TEMPLATE FILE [--overwrite]- Initialize config + assets from a template and load it")
            print("  restore [FILE] [--dry-run]      - Restore config from .bak backup (alias: undo)")
            print("  load FILE                       - Load an existing config file")
            print("  generate DESCRIPTION FILE       - Generate new config via LLM and save to FILE")
            print("  update INSTRUCTION              - Modify loaded config via LLM")
            print("  update --patch-mode INSTRUCTION - Apply operation-based patch update")
            print("  update --patch-mode --dry-run INSTRUCTION")
            print("                                 - Preview patch update without writing")
            print("  update --patch-mode --strict false INSTRUCTION")
            print("                                 - Patch update with non-strict behavior")
            print("  ask [--dry-run] PROMPT          - Multi-step agentic assistant over SPHinXsim CLI tools")
            print("  explore QUESTION                - Ask about schema")
            print("  validate                        - Reload and validate config from disk")
            print("  preview [--watch]               - Render geometry/BC preview (optional live disk watch)")
            print("  preview --with-particles        - Run particle generation and overlay particles")
            print("  preview --screenshot FILE        - Save a screenshot to FILE instead of interactive window")
            print("  initialize-simulation           - Build and initialize the retained particle preview")
            print("  continue-to-run")
            print("                                 - Run the initialized particle preview simulation")
            print("  replay [--relaxation]           - Replay simulation or relaxation snapshots at up to 25 FPS")
            print("  run [--with-animation] [--output-dir DIR] [--detach]")
            print("                                 - Run config synchronously, with animation, or detached")
            print("  job <status|logs|wait|continue|cancel|results> [REF_JSON]")
            print("                                 - Inspect, wait on, or control a detached job (defaults to latest)")
            print("  results [--output-dir DIR] [--screenshot FILE] [--color-by FIELD] [--frame FRAME] [--json]")
            print("                                 - Summarize VTP frames, .dat series, and quality checks")
            print("  capabilities / compile / geometry / project / agent ...")
            print("                                 - Direct access to native SPHinXsim CLI subcommands")
            print("  exit                            - Exit shell")
            continue

        # Preserve raw prompt text (including quotes/JSON) for ask / /ask / ? commands.
        if (
            line == "ask"
            or line == "/ask"
            or line == "?"
            or line.startswith(("ask ", "/ask ", "? "))
        ):
            preview_runtime.discard_pending_simulation()
            raw_body = line.split(None, 1)[1].strip() if len(line.split(None, 1)) > 1 else ""
            ask_dry_run = False
            if raw_body.startswith("--dry-run "):
                ask_dry_run = True
                raw_body = raw_body[len("--dry-run "):].strip()
            if not raw_body:
                print("Usage: ask [--dry-run] PROMPT", file=sys.stderr)
                continue
            root = _workspace_root_for(config_path, explicit_root)
            try:
                answer, agent_messages, agent_observations = _run_agentic_turn(
                    raw_body,
                    root=root,
                    config_path=config_path,
                    output_dir=active_output_dir,
                    dry_run=ask_dry_run,
                    messages=agent_messages,
                    prior_observations=agent_observations,
                    backend=getattr(args, "_backend", None),
                )
                print(answer)
                # Auto-load newly created config if none was loaded yet.
                if config_path is None:
                    for obs in reversed(agent_observations):
                        try:
                            parsed_obs = json.loads(obs.get("result", "{}"))
                            rel_cfg = (parsed_obs.get("data") or {}).get("config_file")
                            if rel_cfg:
                                candidate = (root / rel_cfg).resolve()
                                if candidate.is_file():
                                    config_path = candidate
                                    print(f"✅ Loaded config: {config_path}")
                                    break
                        except Exception:
                            continue
                if preview_runtime._watch_config_path is not None:
                    preview_runtime.check_watched_files()
            except Exception as exc:
                print(f"Error in ask: {exc}", file=sys.stderr)
            continue

        try:
            translated_parts = _parse_shell_ai_cli_style(line)
            if translated_parts is not None:
                parts = translated_parts
            else:
                parts = _split_shell_command(line)
        except ValueError as exc:
            print(f"Invalid command syntax: {exc}", file=sys.stderr)
            continue

        if not parts:
            continue

        cmd = parts[0]

        # Relaxation replay also retains the simulation for later initialization.
        if cmd not in {"initialize-simulation", "continue-to-run"} and parts != ["replay", "--relaxation"]:
            preview_runtime.discard_pending_simulation()

        if cmd == "templates":
            _ = _safe_cli_call(["templates", *parts[1:]])
            continue

        if cmd == "init":
            overwrite = "--overwrite" in parts
            as_json = "--json" in parts
            positional = [tok for tok in parts[1:] if tok not in {"--overwrite", "--json"}]
            if len(positional) != 2:
                print("Usage: init TEMPLATE FILE [--overwrite] [--json]", file=sys.stderr)
                continue
            template_id, file_arg = positional
            raw_target = Path(file_arg)
            if raw_target.is_absolute():
                target_path = raw_target
            elif "pytest-temp" in file_arg and (PROJECT_ROOT / ".build-temp" / raw_target).parent.exists():
                target_path = PROJECT_ROOT / ".build-temp" / raw_target
            else:
                target_path = (Path.cwd() / raw_target).resolve()
            sub_args = ["init", template_id, str(target_path)]
            if overwrite:
                sub_args.append("--overwrite")
            if as_json:
                sub_args.extend(["--root", str(_workspace_root_for(target_path, explicit_root)), "--json"])
            if _safe_cli_call(sub_args) == 0 and target_path.is_file():
                config_path = target_path
                shell_sim = None
                if not as_json:
                    print(f"✅ Loaded config: {config_path}")
            continue

        if cmd in {"restore", "undo"}:
            pos = [tok for tok in parts[1:] if not tok.startswith("-")]
            flags = [tok for tok in parts[1:] if tok.startswith("-")]
            target_cfg = _shell_resolve_config_path(pos[0]) if pos else config_path
            if target_cfg is None:
                print("No config loaded. Run 'load FILE' or pass a config file.", file=sys.stderr)
                continue
            sub_args = ["restore", str(target_cfg.resolve()), *flags]
            if "--json" in flags and "--root" not in flags:
                sub_args.extend(["--root", str(_workspace_root_for(target_cfg, explicit_root))])
            if _safe_cli_call(sub_args) == 0 and preview_runtime._watch_config_path is not None:
                preview_runtime.check_watched_files()
            continue

        if cmd == "load":
            if len(parts) < 2:
                print("Usage: load FILE", file=sys.stderr)
                continue
            file_arg = " ".join(parts[1:]).strip()
            config_path = _shell_resolve_config_path(file_arg)
            if not config_path.exists():
                print(f"File not found: {config_path}", file=sys.stderr)
                config_path = None
                continue
            # Validate the file
            cfg, rc = _load_config(config_path)
            if rc != 0 or cfg is None:
                print(f"Failed to load config from {config_path}", file=sys.stderr)
                config_path = None
                continue
            print(f"✅ Loaded config: {config_path}")
            shell_sim = None
            continue

        if cmd == "generate":
            if len(parts) < 3:
                print("Usage: generate DESCRIPTION FILE", file=sys.stderr)
                continue
            # Last part is the file, rest is description
            file_arg = parts[-1]
            description = " ".join(parts[1:-1]).strip()
            if not description or not file_arg:
                print("Usage: generate DESCRIPTION FILE", file=sys.stderr)
                continue
            config_path = _shell_resolve_config_path(file_arg)
            llm = get_llm()
            try:
                config = _generate_with_physical_correction_feedback(llm, description)
                config_path.parent.mkdir(parents=True, exist_ok=True)
                config_path.write_text(dump_simulation_config_json(config, indent=2))
                print(f"✅ Config generated and written to {config_path}")
                shell_sim = None
                _shell_auto_validate(config_path)
            except (ValueError, ValidationError) as exc:
                print(f"Error generating config: {exc}", file=sys.stderr)
                config_path = None
            except OSError as exc:
                print(f"Error writing config: {exc}", file=sys.stderr)
                config_path = None
            continue

        if cmd == "update":
            patch_mode = False
            dry_run = False
            strict = "true"
            parse_error = False
            idx = 1
            while idx < len(parts):
                token = parts[idx]
                if token == "--patch-mode":
                    patch_mode = True
                    idx += 1
                    continue
                if token == "--dry-run":
                    dry_run = True
                    idx += 1
                    continue
                if token == "--strict":
                    if idx + 1 >= len(parts) or parts[idx + 1] not in {"true", "false"}:
                        print("Usage: update [--patch-mode] [--dry-run] [--strict true|false] INSTRUCTION", file=sys.stderr)
                        parse_error = True
                        break
                    strict = parts[idx + 1]
                    idx += 2
                    continue
                break

            if parse_error:
                continue

            instruction = " ".join(parts[idx:]).strip()
            if not instruction:
                print("Usage: update [--patch-mode] [--dry-run] [--strict true|false] INSTRUCTION", file=sys.stderr)
                continue
            if config_path is None:
                print("No config loaded. Run 'load FILE' or 'generate' first.", file=sys.stderr)
                continue

            rc = cmd_update(
                argparse.Namespace(
                    config_file=str(config_path),
                    description=instruction,
                    output=None,
                    patch_mode=patch_mode,
                    dry_run=dry_run,
                    strict=strict,
                )
            )
            if rc == 0 and not dry_run:
                _shell_auto_validate(config_path)
                if preview_runtime._watch_config_path is not None:
                    preview_runtime.check_watched_files()
            continue

        if cmd == "explore":
            question = " ".join(parts[1:]).strip()
            if not question:
                print("Usage: explore QUESTION", file=sys.stderr)
                continue
            _ = cmd_explore(argparse.Namespace(question=question))
            continue

        if cmd == "validate":
            if config_path is None:
                print("No config loaded. Run 'load FILE' or 'generate' first.", file=sys.stderr)
                continue
            # Reload from disk to pick up external edits
            cfg, rc = _load_config(config_path)
            if rc != 0 or cfg is None:
                print(f"❌ Validation failed for {config_path}", file=sys.stderr)
                continue
            print(f"✅ Reloaded and validated config: {config_path}")
            # Show config summary
            _ = cmd_validate(argparse.Namespace(config_file=str(config_path)))
            continue

        if cmd == "preview":
            if config_path is None:
                print("No config loaded. Run 'load FILE' or 'generate' first.", file=sys.stderr)
                continue
            with_particles = "--with-particles" in parts
            watch_mode = "--watch" in parts
            watch_out: str | None = None
            if "--watch-output-dir" in parts:
                idx = parts.index("--watch-output-dir")
                if idx + 1 < len(parts):
                    watch_out = parts[idx + 1]
            # Extract --screenshot / -s value from shell input
            screenshot_path = None
            if "--screenshot" in parts:
                idx = parts.index("--screenshot")
                if idx + 1 < len(parts):
                    screenshot_path = parts[idx + 1]
            elif "-s" in parts:
                idx = parts.index("-s")
                if idx + 1 < len(parts):
                    screenshot_path = parts[idx + 1]
            if screenshot_path is not None:
                _ = cmd_preview(
                    argparse.Namespace(
                        config_file=str(config_path),
                        with_particles=with_particles,
                        off_screen=False,
                        screenshot=screenshot_path,
                    )
                )
                continue

            cfg, rc = _load_config(config_path)
            if rc != 0 or cfg is None:
                continue

            resolved_config_path = _resolve_preview_config_path(str(config_path))
            print(f"🖼  Building configuration preview for: {resolved_config_path}")
            print("   Building C++ geometry; bounds cache fallback will be used if VTP meshes are unavailable.")
            if with_particles:
                print("   Particle generation overlay is enabled (--with-particles).")

            rc = preview_runtime.show_or_update(
                cfg,
                resolved_config_path=resolved_config_path,
                with_particles=with_particles,
            )
            if rc == 0:
                if watch_mode:
                    out_dir = (
                        Path(watch_out).resolve()
                        if watch_out
                        else active_output_dir
                    )
                    preview_runtime.start_file_watch(
                        resolved_config_path,
                        config=cfg,
                        output_dir=out_dir,
                        with_particles=with_particles,
                    )
                    print(f"👀 Watching {resolved_config_path} for disk updates.")
                else:
                    print("ℹ️ Preview window is persistent in shell mode; run `preview` again after edits.")
            continue

        if cmd == "initialize-simulation":
            if len(parts) != 1:
                print("Usage: initialize-simulation", file=sys.stderr)
                continue
            _ = preview_runtime.initialize_simulation()
            continue

        if cmd == "continue-to-run":
            if len(parts) != 1:
                print(
                    "Usage: continue-to-run",
                    file=sys.stderr,
                )
                continue
            _ = preview_runtime.continue_to_run()
            continue

        if cmd == "replay":
            if len(parts) == 1:
                _ = preview_runtime.replay_animation()
            elif parts == ["replay", "--relaxation"]:
                _ = preview_runtime.replay_animation(relaxation=True)
            else:
                print("Usage: replay [--relaxation]", file=sys.stderr)
                continue
            continue

        if cmd == "run":
            with_animation = "--with-animation" in parts
            detach_run = "--detach" in parts
            as_json = "--json" in parts
            custom_out_dir: str | None = None
            parse_ok = True
            idx = 1
            while idx < len(parts):
                tok = parts[idx]
                if tok in {"--with-animation", "--detach", "--json"}:
                    idx += 1
                elif tok == "--output-dir":
                    if idx + 1 >= len(parts):
                        parse_ok = False
                        break
                    custom_out_dir = parts[idx + 1]
                    idx += 2
                else:
                    parse_ok = False
                    break
            if not parse_ok or (with_animation and (detach_run or custom_out_dir is not None)):
                print("Usage: run [--with-animation] [--output-dir DIR] [--detach] [--json]", file=sys.stderr)
                continue
            if config_path is None:
                print("No config loaded. Run 'load FILE' or 'generate' first.", file=sys.stderr)
                continue

            cfg, rc = _load_config(config_path)
            if rc != 0 or cfg is None:
                print(f"❌ Validation failed for {config_path}", file=sys.stderr)
                continue

            if detach_run:
                from sphinxsim.operations import Operations
                from sphinxsim.runtime.export_vtp import atomic_json

                resolved_cfg = _resolve_preview_config_path(str(config_path)).resolve()
                root = _workspace_root_for(resolved_cfg, explicit_root)
                try:
                    ops = Operations(root)
                    rel_cfg = resolved_cfg.relative_to(root).as_posix()
                    project = ops.call("project_inspect", {"config_file": rel_cfg})
                    submitted = ops.call("submit", {"config_file": rel_cfg, "project": project, "kind": "run"})
                    ref_file = root / ".sphinxsim" / "last-job.json"
                    atomic_json(ref_file, submitted["reference"])
                    last_job_ref_file = ref_file
                    active_output_dir = ops.jobs.job_path(submitted["reference"]["job_id"])
                    if preview_runtime._watch_config_path is not None:
                        preview_runtime._watch_output_dir = active_output_dir
                    if as_json:
                        print(json.dumps({"ok": True, "data": submitted}))
                    else:
                        print(f"🚀 Detached job submitted: {submitted['reference']['job_id']} (state={submitted['state']})")
                        print(f"   Reference saved to: {ref_file}")
                except Exception as exc:
                    print(f"❌ Detached run failed: {exc}", file=sys.stderr)
                continue

            if with_animation:
                resolved_config_path = _resolve_preview_config_path(str(config_path))
                print(f"🖼  Building particle-animation preview for: {resolved_config_path}")
                print("   Building C++ geometry; bounds cache fallback will be used if VTP meshes are unavailable.")
                preview_rc = preview_runtime.show_or_update(
                    cfg,
                    resolved_config_path=resolved_config_path,
                    with_particles=True,
                    force=True,
                )
                if preview_rc == 0 and preview_runtime.initialize_simulation() == 0:
                    _ = preview_runtime.continue_to_run()
                continue

            preview_runtime._animation_output_dir = None
            preview_runtime._animation_particle_body_names = set()
            preview_runtime._animation_particle_colours = {}
            preview_runtime._animation_config = None
            preview_runtime._animation_config_path = None
            preview_runtime._animation_run_completed = False
            output_dir = (
                Path(custom_out_dir).resolve()
                if custom_out_dir is not None
                else PROJECT_ROOT / ".build-temp" / "test_simulation"
            )
            active_output_dir = output_dir
            if preview_runtime._watch_config_path is not None:
                preview_runtime._watch_output_dir = active_output_dir

            # Persistent preview keeps native UI/runtime state alive in-process.
            # Run simulation in an isolated subprocess to avoid state leakage.
            if preview_runtime.plotter is not None:
                resolved_config_path = _resolve_preview_config_path(str(config_path))
                print(
                    "ℹ️ Running simulation in isolated subprocess (persistent preview is active).",
                    flush=True,
                )
                try:
                    env = os.environ.copy()
                    env.setdefault("PYTHONUNBUFFERED", "1")
                    run_cmd = [sys.executable, "-m", "sphinxsim", "run", str(resolved_config_path)]
                    if custom_out_dir is not None:
                        run_cmd.extend(["--output-dir", str(output_dir)])
                    process = subprocess.Popen(
                        run_cmd,
                        cwd=str(resolved_config_path.parent),
                        env=env,
                    )
                    while True:
                        preview_runtime.pump_ui_events()
                        return_code = process.poll()
                        if return_code is not None:
                            break
                        time.sleep(0.05)
                    if return_code != 0:
                        print(f"❌ Run failed with exit code {return_code}", file=sys.stderr)
                except Exception as exc:
                    print(f"❌ Run failed: {exc}", file=sys.stderr)
                continue

            try:
                ndim = _config_spatial_dim(cfg)
                sph = load_sphinxsys_core_nd(ndim)

                shell_sim = sph.SPHSimulation(str(config_path))
                output_dir.mkdir(exist_ok=True, parents=True)
                shell_sim.resetOutputRoot(str(output_dir))
                print(f"📁 Now, the output folder is changed to: {output_dir}")

                shell_sim.buildGeometries()
                print("✅ Geometries built")
                shell_sim.generateParticles()
                print("✅ Particles generated")
                shell_sim.buildSimulation()
                print("✅ Simulation built")
                shell_sim.initializeSimulation()
                print("✅ Simulation initialized")
                print("\n🚀 Running simulation...")
                shell_sim.run()
                print("✅ Simulation completed successfully!")
            except ImportError:
                rc = cmd_run(argparse.Namespace(config_file=str(config_path), output_dir=str(output_dir) if custom_out_dir else None))
            except Exception as exc:
                print(f"❌ Run failed: {exc}", file=sys.stderr)
            continue

        if cmd == "results":
            if config_path is None and not any(not tok.startswith("-") and tok.endswith(".json") for tok in parts[1:]):
                print("No config loaded. Run 'load FILE' or pass a config file.", file=sys.stderr)
                continue
            sub_args = ["results"]
            has_cfg = any(
                idx == 1 and not tok.startswith("-")
                for idx, tok in enumerate(parts[1:], start=1)
            )
            if not has_cfg and config_path is not None:
                sub_args.append(str(_resolve_preview_config_path(str(config_path)).resolve()))
            sub_args.extend(parts[1:])
            if "--output-dir" not in parts:
                default_out = active_output_dir or (PROJECT_ROOT / ".build-temp" / "test_simulation")
                sub_args.extend(["--output-dir", str(default_out)])
            if "--json" in parts and "--root" not in parts:
                cfg_for_root = config_path or Path(sub_args[1])
                out_idx = sub_args.index("--output-dir") + 1
                out_path = Path(sub_args[out_idx]).resolve()
                common = Path(os.path.commonpath([str(cfg_for_root.resolve().parent), str(out_path)]))
                sub_args.extend(["--root", str(explicit_root or common)])
            _ = _safe_cli_call(sub_args)
            continue

        if cmd == "job":
            if len(parts) < 2:
                print("Usage: job <status|logs|wait|continue|cancel|results> [REF_JSON] [--timeout S] [--json]", file=sys.stderr)
                continue
            action = parts[1]
            rest = list(parts[2:])
            root = _workspace_root_for(config_path, explicit_root)
            # Identify positional ref file (ignoring flag values like --timeout 30)
            has_ref = False
            skip_next = False
            for tok in rest:
                if skip_next:
                    skip_next = False
                    continue
                if tok in {"--timeout", "--root"}:
                    skip_next = True
                    continue
                if not tok.startswith("-"):
                    has_ref = True
                    break
            if not has_ref:
                candidate_ref = last_job_ref_file or (root / ".sphinxsim" / "last-job.json")
                if not candidate_ref.is_file():
                    print("No job reference specified and no recent detached job found.", file=sys.stderr)
                    continue
                rest.insert(0, str(candidate_ref))
            if "--root" not in rest:
                rest.extend(["--root", str(root)])
            _ = _safe_cli_call(["job", action, *rest])
            continue

        if cmd in {"capabilities", "compile", "geometry", "project"}:
            sub_args = [cmd, *parts[1:]]
            if cmd == "project" and len(parts) == 1 and config_path is not None:
                root = _workspace_root_for(config_path, explicit_root)
                rel = _resolve_preview_config_path(str(config_path)).resolve().relative_to(root).as_posix()
                sub_args = ["project", rel, "--root", str(root)]
            _ = _safe_cli_call(sub_args)
            continue

        if cmd == "agent":
            if len(parts) < 2:
                print(
                    "Usage: agent <start|status|approve|resume|cancel|report|promote|preview|extractor> ...",
                    file=sys.stderr,
                )
                continue
            if parts[1] == "preview":
                wf_args = [tok for tok in parts[2:] if not tok.startswith("-")]
                ws_dir = Path(PROJECT_ROOT / ".build-temp" / "agent")
                if "--workspace" in parts:
                    w_idx = parts.index("--workspace")
                    if w_idx + 1 < len(parts):
                        ws_dir = Path(parts[w_idx + 1]).resolve()
                if not wf_args:
                    print("Usage: agent [--workspace DIR] preview WORKFLOW_ID", file=sys.stderr)
                    continue
                try:
                    _ = preview_runtime.observe_agent_workflow(ws_dir, wf_args[0])
                    print(f"🖼  Preview window is following Agent workflow {wf_args[0]}.")
                except Exception as exc:
                    print(f"Error opening agent preview: {exc}", file=sys.stderr)
                continue
            sub_args = ["agent", *parts[1:]]
            # Allow `agent promote <workflow_id>` to default target to active config_path
            if "promote" in parts[1:]:
                p_idx = parts.index("promote")
                pos_after = [tok for tok in parts[p_idx + 1:] if not tok.startswith("-")]
                if len(pos_after) == 1 and config_path is not None:
                    sub_args.append(str(_resolve_preview_config_path(str(config_path)).resolve()))
            rc_agent = _safe_cli_call(sub_args)
            if rc_agent == 0 and "promote" in parts[1:] and preview_runtime._watch_config_path is not None:
                preview_runtime.check_watched_files()
            continue

        print(f"Unknown command: {cmd}. Type 'help' for commands.", file=sys.stderr)

# ---------------------------------------------------------------------------
# Standalone chatbot launcher
# ---------------------------------------------------------------------------


def cmd_chatbot(args: argparse.Namespace) -> int:
    """Launch the independent standalone Chainlit chatbot."""
    try:
        from sphinxsim.ui.chatbot.application import (
            ChatbotDependencyError,
            launch_chatbot,
        )
        return launch_chatbot(
            host=args.host,
            port=args.port,
            headless=args.headless,
            watch=args.watch,
        )
    except (ImportError, ChatbotDependencyError) as exc:
        print(
            "The chatbot requires Chainlit. Install the chatbot extra and try "
            "again: pip install -e .[chatbot]",
            file=sys.stderr,
        )
        print(f"Import detail: {exc}", file=sys.stderr)
        return 1


# ---------------------------------------------------------------------------
# Argument parser
# ---------------------------------------------------------------------------


def cmd_workbench(args: argparse.Namespace) -> int:
    """Open the unified Qt/PyVista/Chainlit workbench."""
    try:
        import importlib.util
        if importlib.util.find_spec("chainlit") is None:
            raise ImportError("Install sphinxsim[chatbot,visualization] to use the workbench.")
        from sphinxsim.ui.workbench.window import launch_workbench
        return launch_workbench(PROJECT_ROOT, args.workspace, args.config_file)
    except (ImportError, ValueError, OSError) as exc:
        print(f"Workbench could not start: {exc}", file=sys.stderr)
        return 1

COMPLETION_SCRIPTS = {
    "bash": """_sphinxsim_completion() {
    local cur opts
    COMPREPLY=()
    cur="${COMP_WORDS[COMP_CWORD]}"
    opts="templates init restore generate validate update ask run results preview explore shell agent capabilities compile geometry project job desktop mcp --help --version --generate-completion"
    COMPREPLY=( $(compgen -W "${opts}" -- ${cur}) )
    return 0
}
complete -F _sphinxsim_completion sphinxsim""",

    "zsh": """#compdef sphinxsim
_sphinxsim() {
    local -a subcmds
    subcmds=(
        'templates:List verified 2D and 3D reference templates'
        'init:Initialize a JSON config and assets from a template'
        'generate:Generate a simulation config from a natural-language description'
        'validate:Validate a JSON simulation config against the schema'
        'update:Update an existing simulation config from an instruction'
        'ask:Run an agentic turn over SPHinXsim CLI operations'
        'run:Run a simulation from a JSON config file'
        'results:Summarize VTP frames, .dat time series, and quality checks'
        'preview:Render an interactive geometry/BC preview of a JSON config file'
        'explore:Ask schema/functionality questions using LLM'
        'shell:Interactive shell for config workflow'
        'agent:Manage durable AgentCAE calibration workflows'
        'capabilities:Inspect schema and native routing evidence'
        'compile:Compile quantities into native configuration'
        'geometry:Generate verified CAD geometry'
        'project:Inspect project inputs'
        'job:Query or control an explicit job'
        'desktop:Serve or operate the native application'
        'mcp:Serve native operations over MCP stdio'
    )
    _describe 'sphinxsim commands' subcmds
}
_sphinxsim "$@" """,

    "fish": """complete -c sphinxsim -f -a "templates" -d "List reference templates"
complete -c sphinxsim -f -a "init" -d "Initialize config from template"
complete -c sphinxsim -f -a "generate" -d "Generate simulation config"
complete -c sphinxsim -f -a "validate" -d "Validate JSON simulation config"
complete -c sphinxsim -f -a "update" -d "Update existing simulation config"
complete -c sphinxsim -f -a "ask" -d "Run agentic CLI turn"
complete -c sphinxsim -f -a "run" -d "Run simulation from JSON config"
complete -c sphinxsim -f -a "results" -d "Summarize simulation results"
complete -c sphinxsim -f -a "preview" -d "Render interactive geometry/BC preview"
complete -c sphinxsim -f -a "explore" -d "Ask schema/functionality questions"
complete -c sphinxsim -f -a "shell" -d "Interactive shell workflow"
complete -c sphinxsim -f -a "agent capabilities compile geometry project job desktop mcp" -d "Native application operations" """
}


class GenerateCompletionAction(argparse.Action):
    """Custom argparse action to print shell auto-completion scripts and exit."""
    def __call__(self, parser, namespace, values, option_string=None):
        shell = values.lower()
        if shell in COMPLETION_SCRIPTS:
            print(COMPLETION_SCRIPTS[shell].strip())
        parser.exit(0)

def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="sphinxsim",
        description="Python UI for the SPHinXsys multi-physics C++ library.",
    )
    parser.add_argument("--version", action="version", version=f"sphinxsim {__version__}")

    parser.add_argument(
        "--generate-completion",
        choices=["bash", "zsh", "fish"],
        action=GenerateCompletionAction,
        help="Generate shell auto-completion script for bash, zsh, or fish.",
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    workbench = subparsers.add_parser("workbench", help="Open JSON editor, Preview and Chainlit in one desktop window.")
    workbench.add_argument("config_file", nargs="?", type=Path, help="JSON to edit; defaults to a workspace copy of the column-collapse demo.")
    workbench.add_argument("--workspace", type=Path, default=PROJECT_ROOT / ".build-temp" / "workbench")
    workbench.set_defaults(func=cmd_workbench)

    # templates
    tmpl = subparsers.add_parser(
        "templates",
        help="List verified 2D and 3D reference simulation templates.",
    )
    tmpl.set_defaults(func=cmd_templates)

    # init
    init_p = subparsers.add_parser(
        "init",
        help="Initialize a new JSON config (and referenced assets) from a verified template.",
    )
    init_p.add_argument("template", help="Template identifier (e.g. 2d/column_collapse or 3d/fuel_tank).")
    init_p.add_argument("config_file", help="Destination JSON configuration file path.")
    init_p.add_argument("--overwrite", action="store_true", help="Overwrite destination if it exists.")
    init_p.set_defaults(func=cmd_init)

    # restore
    rest_p = subparsers.add_parser(
        "restore",
        help="Restore a JSON config file atomically from its .bak backup.",
    )
    rest_p.add_argument("config_file", nargs="?", default="config.json", help="Path to JSON config file.")
    rest_p.add_argument("--dry-run", action="store_true", help="Preview restoration diff without writing.")
    rest_p.set_defaults(func=cmd_restore)

    # results
    res_p = subparsers.add_parser(
        "results",
        help="Summarize VTP frames, observer/energy .dat series, and quality checks for a run.",
    )
    res_p.add_argument("config_file", nargs="?", default="config.json", help="Path to JSON config file.")
    res_p.add_argument(
        "--output-dir",
        default=None,
        metavar="DIR",
        help="Simulation output directory (defaults to .build-temp/test_simulation).",
    )
    res_p.add_argument(
        "--screenshot",
        "-s",
        default=None,
        help="Render an off-screen PNG screenshot of the latest solver frames.",
    )
    res_p.add_argument(
        "--color-by",
        default=None,
        help="Point-data scalar or vector field name to color particles by when rendering.",
    )
    res_p.add_argument(
        "--frame",
        default="latest",
        help="Select 'latest' (default), 'first', or a 0-based/negative frame index for --screenshot.",
    )
    res_p.set_defaults(func=cmd_results)

    # ask
    ask_p = subparsers.add_parser(
        "ask",
        help="Run an agentic turn grounded on SPHinXsim CLI operations and JSON configs.",
    )
    ask_p.add_argument("prompt", help="Natural-language simulation or analysis request.")
    ask_p.add_argument("-c", "--config", default=None, help="Active JSON configuration file.")
    ask_p.add_argument("--output-dir", default=None, help="Active simulation output directory.")
    ask_p.add_argument("--root", default=None, help="Explicit workspace root.")
    ask_p.add_argument("--dry-run", action="store_true", help="Request dry-run previews without modifying files.")
    ask_p.add_argument("--json", action="store_true", help="Emit structured JSON output.")
    ask_p.set_defaults(func=cmd_ask)

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

    # update
    upd = subparsers.add_parser(
        "update",
        help="Update an existing simulation config from a natural-language instruction.",
    )
    upd.add_argument("config_file", help="Path to an existing JSON config file.")
    upd.add_argument("description", help="Natural-language update instruction.")
    upd.add_argument(
        "-o",
        "--output",
        metavar="FILE",
        default=None,
        help="Write updated JSON to FILE instead of updating in place.",
    )
    upd.add_argument(
        "--patch-mode",
        action="store_true",
        help="Use operation-based patch updates (provider must support update_patch()).",
    )
    upd.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview patch-mode results without writing output file.",
    )
    upd.add_argument(
        "--strict",
        choices=["true", "false"],
        default="true",
        help="Strict patch application behavior for --patch-mode (default: true).",
    )
    upd.set_defaults(func=cmd_update)

    # run
    run = subparsers.add_parser("run", help="Run a simulation from a JSON config file.")
    run.add_argument("config_file", nargs='?', default="config.json", help="Path to JSON config file.")
    run.add_argument(
        "--output-dir",
        default=None,
        metavar="DIR",
        help="Write simulation artifacts under DIR instead of the legacy shared output root.",
    )
    run.set_defaults(func=cmd_run)

    # preview
    prev = subparsers.add_parser(
        "preview",
        help="Render an interactive geometry/BC preview of a JSON config file.",
    )
    prev.add_argument(
        "config_file",
        nargs="?",
        default="config.json",
        help="Path to JSON config file.",
    )
    prev.add_argument(
        "--with-particles",
        action="store_true",
        help="Also run particle generation and overlay the latest generated particles per body.",
    )
    prev.add_argument(
        "--off-screen",
        action="store_true",
        help="Render off-screen (no window). Useful for automated testing.",
    )
    prev.add_argument(
        "--screenshot",
        "-s",
        default=None,
        help="Save a screenshot to this file path instead of opening an interactive window.",
    )
    prev.add_argument(
        "--watch",
        action="store_true",
        help="Watch config.json (and --watch-output-dir) on disk and refresh automatically.",
    )
    prev.add_argument(
        "--watch-output-dir",
        default=None,
        metavar="DIR",
        help="Optional VTP output directory to stream into a --watch preview window.",
    )
    prev.set_defaults(func=cmd_preview)

    # explore
    exp = subparsers.add_parser(
        "explore",
        help="Ask schema/functionality questions using the configured LLM provider.",
    )
    exp.add_argument("question", help="Question about the simulator schema or functionality.")
    exp.set_defaults(func=cmd_explore)

    # shell
    shell = subparsers.add_parser(
        "shell",
        help="Interactive shell for config load/modify/validate workflow.",
    )
    shell.add_argument("config_file", nargs="?", default=None, help="Optional JSON config file to load on startup.")
    shell.add_argument("--root", default=None, help="Optional workspace root for structured operations.")
    shell.set_defaults(func=cmd_shell)

    from sphinxsim.agent_cli import configure_agent_parser
    configure_agent_parser(subparsers, PROJECT_ROOT)

    from sphinxsim.commands import register
    register(subparsers)
    return parser


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def _parse_shell_ai_cli_style(text: str) -> list[str] | None:
    """Translate slash-prefixed shell commands to the shell command parser.

    Examples:
        /generate water dam break simulation cfg.json -> ["generate", "water dam break simulation", "cfg.json"]
        /update simulate for 2 s -> ["update", "simulate for 2 s"]
    """
    if not text:
        return None

    stripped = text.strip()
    if not stripped.startswith("/"):
        return None

    parts = _split_shell_command(stripped)
    if not parts:
        return None

    command = parts[0][1:]
    valid_commands = {
        "load", "templates", "init", "restore", "undo", "generate", "validate", "update", "run",
        "preview", "results", "capabilities", "compile", "geometry", "project",
        "job", "agent", "ask", "initialize-simulation", "continue-to-run",
        "replay", "explore", "shell",
    }
    if command not in valid_commands:
        return None

    if command in {"generate", "update", "explore"}:
        if len(parts) < 2:
            return [command]
        if command == "generate":
            if len(parts) == 2:
                return [command, parts[1]]
            return [command, " ".join(parts[1:-1]), parts[-1]]
        if command == "update":
            return [command, " ".join(parts[1:])]
        return [command, " ".join(parts[1:])]

    return [command] + parts[1:]


def main(argv: list[str] | None = None, *, publish_json=None) -> int:
    """Entry point for the ``sphinxsim`` CLI."""
    parser = _build_parser()
    args = parser.parse_args(argv)
    if publish_json is not None:
        if not callable(publish_json) or not getattr(args, "request", None):
            parser.error("The trusted publisher hook requires a frozen --request execution")
        args.publish_json = publish_json
    if args.command in ("run", "preview") and not args.request and args.config_file is None:
        args.config_file = "config.json"
    structured = any(getattr(args, key, None) for key in ("json", "detach", "request", "root", "output", "submission_id"))
    policy_changed = any(getattr(args, key, default) != default for key, default in
                         (("lease_seconds", 600), ("refresh_seconds", 10), ("wall_seconds", 3600)))
    if args.command in ("validate", "run", "preview", "templates", "init", "restore", "results") and (structured or policy_changed):
        if args.command == "results" and getattr(args, "output_dir", None) is None:
            args.output_dir = str(PROJECT_ROOT / ".build-temp" / "test_simulation")
        from sphinxsim.commands import execute
        return execute(args)
    return args.func(args)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
