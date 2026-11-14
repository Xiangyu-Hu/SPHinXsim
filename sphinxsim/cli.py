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
    try:
        words = shlex.split(prefix)
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
            candidate = "~" + os.path.relpath(match, os.path.expanduser("~"))
        if os.path.isdir(match):
            candidate += "/"
        results.append(candidate)
    return sorted(results)


def _shell_readline_completer(text: str, state: int) -> str | None:
    if readline is None:
        return None
    matches = _shell_completion_matches(
        readline.get_line_buffer()[: readline.get_begidx()], text
    )
    return matches[state] if state < len(matches) else None


def _configure_shell_readline() -> None:
    if readline is None:
        return
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

COMPLETION_SCRIPTS = {
    "bash": """_sphinxsim_completion() {
    local cur opts
    COMPREPLY=()
    cur="${COMP_WORDS[COMP_CWORD]}"
    opts="generate validate update run preview explore shell capabilities compile geometry project job desktop mcp --help --version --generate-completion"
    COMPREPLY=( $(compgen -W "${opts}" -- ${cur}) )
    return 0
}
complete -F _sphinxsim_completion sphinxsim""",

    "zsh": """#compdef sphinxsim
_sphinxsim() {
    local -a subcmds
    subcmds=(
        'generate:Generate a simulation config from a natural-language description'
        'validate:Validate a JSON simulation config against the schema'
        'update:Update an existing simulation config from an instruction'
        'run:Run a simulation from a JSON config file'
        'preview:Render an interactive geometry/BC preview of a JSON config file'
        'explore:Ask schema/functionality questions using LLM'
        'shell:Interactive shell for config workflow'
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

    "fish": """complete -c sphinxsim -f -a "generate" -d "Generate simulation config"
complete -c sphinxsim -f -a "validate" -d "Validate JSON simulation config"
complete -c sphinxsim -f -a "update" -d "Update existing simulation config"
complete -c sphinxsim -f -a "run" -d "Run simulation from JSON config"
complete -c sphinxsim -f -a "preview" -d "Render interactive geometry/BC preview"
complete -c sphinxsim -f -a "explore" -d "Ask schema/functionality questions"
complete -c sphinxsim -f -a "shell" -d "Interactive shell workflow"
complete -c sphinxsim -f -a "capabilities compile geometry project job desktop mcp" -d "Native application operations" """
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
    shell.set_defaults(func=cmd_shell)

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

    parts = shlex.split(stripped)
    if not parts:
        return None

    command = parts[0][1:]
    valid_commands = {
        "generate", "validate", "update", "run", "preview", "continue-to-run", "explore", "shell"
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
    if args.command in ("validate", "run", "preview") and (structured or policy_changed):
        from sphinxsim.commands import execute
        return execute(args)
    return args.func(args)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
