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
import shlex
import subprocess
import sys
import tempfile
import time
import warnings
from pathlib import Path
from threading import Thread
from typing import Any, Tuple

from .common import (
    PROJECT_ROOT,
    __version__,
    _apply_payload_diff,
    _collect_change_lines,
    _load_config,
    _short_repr,
    _write_validated_config,
)

try:
    import readline
except ImportError:
    readline = None

# Set up sys.path BEFORE any sphinxsim imports.
PROJECT_ROOT = Path(PROJECT_ROOT)
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "build-integrated"))
sys.path.insert(0, str(PROJECT_ROOT / "sphinxsim" / "bindings" / "native"))

from pydantic import ValidationError

from sphinxsim.bindings.loader import load_sphinxsys_core_nd
from sphinxsim.config.schemas import PhysicalCorrectionWarning, SimulationConfig
from sphinxsim.config.update_patch import UpdatePatch, apply_update_patch
from sphinxsim.json_format import (
    find_prettier_options,
    format_json,
    load_json_preserving_floats,
    object_layout,
)
from sphinxsim.llm import get_llm
from sphinxsim.llm.common import LLMRepairWarning, dump_simulation_config_json

_LIVE_PREVIEW_MAX_FPS = 25

# ---------------------------------------------------------------------------
# Shared helper
# ---------------------------------------------------------------------------


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


def _new_json_editor_list_item(path: tuple[Any, ...], spatial_dim: int) -> Any:
    """Return an editable starter value for an empty JSON editor list."""
    normalized_path = tuple(part for part in path if not isinstance(part, int))
    vector = [0.0] * spatial_dim
    unit_vector = [1.0] * spatial_dim
    transform = {
        "translation": vector,
        "rotation_angle": 0.0,
    }
    if spatial_dim == 3:
        transform["rotation_axis"] = [0.0, 0.0, 1.0]

    templates: dict[tuple[Any, ...], Any] = {
        ("characteristic_dimensions",): {
            "name": "Length",
            "value": 1.0,
            "hint": "Characteristic length",
        },
        ("geometries", "primitives"): {
            "name": "New primitive",
            "type": "box",
            "half_size": unit_vector,
            "transform": transform,
        },
        ("geometries", "shapes"): {
            "name": "New shape",
            "type": "bounding_box",
            "lower_bound": vector,
            "upper_bound": unit_vector,
        },
        ("geometries", "shapes", "polygons"): {
            "operation": "union",
            "type": "bounding_box",
            "lower_bound": vector,
            "upper_bound": unit_vector,
        },
        ("geometries", "oriented_boxes"): {
            "name": "New region",
            "type": "region",
            "half_size": unit_vector,
            "transform": transform,
        },
        ("particle_generation", "settings", "bodies"): {"name": "New body"},
        ("particle_generation", "settings", "relaxation_constraints"): {
            "body_name": "",
            "oriented_box": "",
            "type": "",
        },
        ("fluid_bodies",): {
            "name": "New fluid body",
            "material": {"type": "weakly_compressible_fluid", "density": 1000.0},
        },
        ("continuum_bodies",): {
            "name": "New continuum body",
            "material": {
                "type": "general_continuum",
                "density": 1000.0,
                "sound_speed": 100.0,
                "youngs_modulus": 1000000.0,
                "poisson_ratio": 0.3,
            },
        },
        ("solid_bodies",): {
            "name": "New solid body",
            "material": {"type": "rigid_body"},
        },
        ("observers",): {
            "name": "New observer",
            "observed_body": "",
            "variable": {"real_type": "Pressure"},
            "positions": [vector],
        },
        ("fluid_boundary_conditions",): {
            "body_name": "",
            "oriented_box": "",
            "type": "emitter",
            "inflow_speed": 1.0,
        },
        ("body_constraints",): {"body_name": "", "type": "fixed"},
        ("initial_conditions",): {
            "body_name": "",
            "assignments": [{"variable": {"real_type": "Pressure"}, "value": 0.0}],
        },
        ("initial_conditions", "assignments"): {
            "variable": {"real_type": "Pressure"},
            "value": 0.0,
        },
        ("extra_state_recording",): {
            "name": "New recording",
            "variables": [{"real_type": ["Pressure"]}],
        },
        ("extra_state_recording", "variables"): {"real_type": ["Pressure"]},
        ("energy_recording",): {"name": "New energy recording", "body": ""},
    }
    return copy.deepcopy(templates.get(normalized_path, {}))


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
    validated_config_path: str | None = None
    tmp_cfg = tempfile.NamedTemporaryFile(
        mode="w", suffix=".json", delete=False, prefix="sphinxsim_run_",
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

        # Create temp directory in project root, not relative to cwd
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
        safe_name = first_body_name.replace(' ', '_').replace('/', '_')[:50]
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
        # Always clean up the validated temp config.
        if validated_config_path:
            try:
                os.unlink(validated_config_path)
            except OSError:
                pass


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

    # Screenshot mode implies off-screen rendering.
    if screenshot_path:
        off_screen = True

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


def _shell_resolve_config_path(config_file: str) -> Path:
    from .interactive import _shell_resolve_config_path as _impl
    return _impl(config_file)


def _shell_auto_validate(config_path: Path) -> bool:
    from .interactive import _shell_auto_validate as _impl
    return _impl(config_path)


_SHELL_COMMANDS = (
    "load",
    "generate",
    "update",
    "validate",
    "run",
    "preview",
    "continue-to-run",
    "replay",
    "explore",
    "help",
    "exit",
    "quit",
)


def _shell_completion_matches(prefix: str, text: str) -> list[str]:
    from .interactive import _shell_completion_matches as _impl
    return _impl(prefix, text)


def _shell_readline_completer(text: str, state: int) -> str | None:
    from .interactive import _shell_readline_completer as _impl
    return _impl(text, state)


def _configure_shell_readline() -> None:
    from .interactive import _configure_shell_readline as _impl
    return _impl()


def _resolve_preview_config_path(config_file: str) -> Path:
    """Resolve preview config path using cwd-first then .build-temp fallback."""
    from .interactive import _resolve_preview_config_path as _impl
    return _impl(config_file)


def _set_windows_dark_title_bar(window: Any) -> None:
    """Request Windows dark mode for a Qt top-level window's native title bar."""
    if sys.platform != "win32":
        return

    try:
        import ctypes

        hwnd = int(window.winId())
        enabled = ctypes.c_int(1)
        dwmapi = ctypes.windll.dwmapi
        for attribute in (20, 19):  # Windows 10 build-dependent dark-mode IDs.
            result = dwmapi.DwmSetWindowAttribute(
                hwnd,
                attribute,
                ctypes.byref(enabled),
                ctypes.sizeof(enabled),
            )
            if result == 0:
                break
    except Exception:
        pass



from .preview_runtime import _ShellPreviewRuntime



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
                "continue-to-run [--refresh-interval SECONDS], replay, explore QUESTION, exit"
    )
    print("Note: relative paths are resolved from the current directory first, then .build-temp/.")

    config_path: Path | None = None
    shell_sim = None
    preview_runtime = _ShellPreviewRuntime()

    while True:
        try:
            line = input("sphinxsim> ").strip()
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
            print("  load FILE                       - Load an existing config file")
            print("  generate DESCRIPTION FILE       - Generate new config via LLM and save to FILE")
            print("  update INSTRUCTION              - Modify loaded config via LLM")
            print("  update --patch-mode INSTRUCTION - Apply operation-based patch update")
            print("  update --patch-mode --dry-run INSTRUCTION")
            print("                                 - Preview patch update without writing")
            print("  update --patch-mode --strict false INSTRUCTION")
            print("                                 - Patch update with non-strict behavior")
            print("  explore QUESTION                - Ask about schema")
            print("  validate                        - Reload and validate config from disk")
            print("  preview                         - Render geometry/BC preview (requires pyvista)")
            print("  preview --with-particles        - Run particle generation and overlay particles")
            print("  preview --screenshot FILE        - Save a screenshot to FILE instead of interactive window")
            print("  continue-to-run [--refresh-interval SECONDS]")
            print("                                 - Finish and run the retained particle preview simulation")
            print("  replay                          - Replay recorded particle snapshots at up to 25 FPS")
            print("  run [--with-animation]          - Run config; optionally preview particles and animate")
            print("  exit                            - Exit shell")
            continue

        try:
            translated_parts = _parse_shell_ai_cli_style(line)
            if translated_parts is not None:
                parts = translated_parts
            else:
                parts = shlex.split(line)
        except ValueError as exc:
            print(f"Invalid command syntax: {exc}", file=sys.stderr)
            continue

        if not parts:
            continue

        cmd = parts[0]

        # A retained simulation has exactly one follow-up command. Any other
        # command abandons it and lets the native wrapper release its state.
        if cmd != "continue-to-run":
            preview_runtime.discard_pending_simulation()

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
                print("ℹ️ Preview window is persistent in shell mode; run `preview` again after edits.")
            continue

        if cmd == "continue-to-run":
            refresh_interval = 10.0
            if len(parts) == 3 and parts[1] == "--refresh-interval":
                try:
                    refresh_interval = float(parts[2])
                except ValueError:
                    refresh_interval = 0.0
            elif len(parts) != 1:
                print(
                    "Usage: continue-to-run [--refresh-interval SECONDS]",
                    file=sys.stderr,
                )
                continue
            if refresh_interval <= 0.0:
                print("Refresh interval must be greater than zero.", file=sys.stderr)
                continue
            _ = preview_runtime.continue_to_run(refresh_interval)
            continue

        if cmd == "replay":
            if len(parts) != 1:
                print("Usage: replay", file=sys.stderr)
                continue
            _ = preview_runtime.replay_animation()
            continue

        if cmd == "run":
            with_animation = len(parts) == 2 and parts[1] == "--with-animation"
            if len(parts) > 1 and not with_animation:
                print("Usage: run [--with-animation]", file=sys.stderr)
                continue
            if config_path is None:
                print("No config loaded. Run 'load FILE' or 'generate' first.", file=sys.stderr)
                continue

            cfg, rc = _load_config(config_path)
            if rc != 0 or cfg is None:
                print(f"❌ Validation failed for {config_path}", file=sys.stderr)
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
                if preview_rc == 0:
                    _ = preview_runtime.continue_to_run()
                continue

            preview_runtime._animation_output_dir = None
            preview_runtime._animation_particle_body_names = set()
            preview_runtime._animation_particle_colours = {}
            preview_runtime._animation_config = None
            preview_runtime._animation_config_path = None
            preview_runtime._animation_run_completed = False
            output_dir = PROJECT_ROOT / ".build-temp" / "test_simulation"

            # Persistent preview keeps native UI/runtime state alive in-process.
            # Run simulation in an isolated subprocess to avoid state leakage.
            if preview_runtime.plotter is not None:
                print(
                    "ℹ️ Running simulation in isolated subprocess (persistent preview is active).",
                    flush=True,
                )
                try:
                    env = os.environ.copy()
                    env.setdefault("PYTHONUNBUFFERED", "1")

                    process = subprocess.Popen(
                        [sys.executable, "-m", "sphinxsim", "run", str(resolved_config_path)],
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
                rc = cmd_run(argparse.Namespace(config_file=str(config_path)))
            except Exception as exc:
                print(f"❌ Run failed: {exc}", file=sys.stderr)
            continue

        print(f"Unknown command: {cmd}. Type 'help' for commands.", file=sys.stderr)

# ---------------------------------------------------------------------------
# Argument parser
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def _parse_shell_ai_cli_style(text: str) -> list[str] | None:
    """Backward-compatible wrapper for slash-prefixed shell command parsing."""
    from .interactive import _parse_shell_ai_cli_style as _impl
    return _impl(text)


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
    if args.command in ("validate", "run", "preview") and (structured or policy_changed):
        from sphinxsim.commands import execute
        return execute(args)
    return args.func(args)


from .parser import _build_parser

if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
