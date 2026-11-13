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
    JsonEditorMixin,
    _apply_payload_diff,
    _config_spatial_dim,
    _new_json_editor_list_item,
)
from sphinxsim.llm import get_llm
from sphinxsim.llm.common import LLMRepairWarning, dump_simulation_config_json

# Convert PROJECT_ROOT to Path after imports
PROJECT_ROOT = Path(PROJECT_ROOT)
_LIVE_PREVIEW_MAX_FPS = 25

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


def _write_validated_config(
    path: Path, config: SimulationConfig, content: str | None = None
) -> str:
    """Atomically replace *path* with *content* (default: canonical validated JSON)."""
    if content is None:
        content = dump_simulation_config_json(config, indent=2)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="",
            suffix=".json",
            prefix=f".{path.stem}.",
            dir=path.parent,
            delete=False,
        ) as temporary_file:
            temporary_file.write(content)
            temporary_path = Path(temporary_file.name)
        os.replace(temporary_path, path)
    except Exception:
        if temporary_path is not None:
            try:
                temporary_path.unlink(missing_ok=True)
            except OSError:
                pass
        raise
    return content


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


class _ShellPreviewRuntime(JsonEditorMixin):
    """Persistent shell preview runtime.

    Keeps a non-blocking plotter window alive across `preview` commands and
    updates its scene in place.
    """

    def __init__(self, *, config_type=None, write_config=None) -> None:
        self.config_type = config_type or SimulationConfig
        self.write_config = write_config or _write_validated_config
        self.plotter: Any | None = None
        self.last_signature: str | None = None
        self._using_background_plotter = False
        self._hover_interactor: Any | None = None
        self._hover_observer_tag: Any | None = None
        self._json_editor: Any | None = None
        self._pending_simulation: Any | None = None
        self._pending_runtime_config_path: Path | None = None
        self._live_run_thread: Thread | None = None
        self._live_refresh_timer: Any | None = None
        self._live_output_dir: Path | None = None
        self._live_particle_body_names: set[str] = set()
        self._live_particle_colours: dict[str, Any] = {}
        self._live_particle_versions: dict[str, float] = {}
        self._live_particle_file_states: dict[Path, tuple[int, int]] = {}
        self._live_playback_queue: list[tuple[float, dict[str, Path]]] = []
        self._live_refresh_interval_ms = round(10.0 * 1000)
        self._live_replay_mode = False
        self._animation_output_dir: Path | None = None
        self._animation_particle_body_names: set[str] = set()
        self._animation_particle_colours: dict[str, Any] = {}
        self._animation_config: SimulationConfig | None = None
        self._animation_config_path: Path | None = None
        self._animation_run_completed = False

    def _reset_preview_state(self) -> None:
        """Forget all state associated with a closed preview window."""
        self._remove_hover_observer()
        self._stop_live_refresh()
        self.discard_pending_simulation()
        self.plotter = None
        self._json_editor = None
        self.last_signature = None
        self._using_background_plotter = False

    def _on_preview_window_closed(self, *_args: Any) -> None:
        """Handle a user closing the Qt window outside the shell command loop."""
        self._reset_preview_state()

    def _watch_preview_window_close(self) -> None:
        """Synchronize pyvistaqt's native close signal with this runtime."""
        if self.plotter is None:
            return
        app_window = getattr(self.plotter, "app_window", None)
        signal_close = getattr(app_window, "signal_close", None)
        if signal_close is None:
            return
        try:
            signal_close.connect(self._on_preview_window_closed)
        except Exception:
            pass

    def close(self) -> None:
        plotter = self.plotter
        if plotter is not None:
            try:
                plotter.close()
            except Exception:
                pass
        self._reset_preview_state()

    def discard_pending_simulation(self) -> None:
        """Release a simulation retained by ``preview --with-particles``."""
        self._pending_simulation = None
        runtime_config_path = self._pending_runtime_config_path
        self._pending_runtime_config_path = None
        if runtime_config_path is not None:
            try:
                runtime_config_path.unlink(missing_ok=True)
            except OSError:
                pass

    def _stop_live_refresh(self) -> None:
        timer = self._live_refresh_timer
        self._live_refresh_timer = None
        if timer is not None:
            try:
                timer.stop()
            except Exception:
                pass
        self._live_output_dir = None
        self._live_particle_body_names = set()
        self._live_particle_colours = {}
        self._live_particle_versions = {}
        self._live_particle_file_states = {}
        self._live_playback_queue = []
        self._live_replay_mode = False

    def _refresh_live_particles(self) -> None:
        """Play a stable snapshot batch or an explicitly requested replay."""
        if self.plotter is None or self._live_output_dir is None:
            return

        from sphinxsim.visualization.preview import _PARTICLE_POINT_SIZE, _particle_label

        try:
            import pyvista as pv
        except ImportError:
            return

        if self._live_replay_mode and not self._live_playback_queue:
            self._stop_live_refresh()
            return

        if not self._live_replay_mode and not self._live_playback_queue:
            batch: dict[float, dict[str, Path]] = {}
            has_unstable_snapshot = False
            for vtp_path in self._live_output_dir.glob("*.vtp"):
                for body_name in self._live_particle_body_names:
                    prefix = f"{body_name}_"
                    if not vtp_path.stem.startswith(prefix):
                        continue
                    suffix = vtp_path.stem[len(prefix):]
                    if suffix.startswith("ite_"):
                        suffix = suffix[len("ite_"):]
                    try:
                        sequence = float(suffix)
                    except ValueError:
                        break
                    if sequence <= self._live_particle_versions.get(body_name, float("-inf")):
                        break
                    try:
                        stat = vtp_path.stat()
                    except OSError:
                        break
                    file_state = (stat.st_size, stat.st_mtime_ns)
                    if self._live_particle_file_states.get(vtp_path) != file_state:
                        self._live_particle_file_states[vtp_path] = file_state
                        has_unstable_snapshot = True
                        break
                    batch.setdefault(sequence, {})[body_name] = vtp_path
                    break

            self._live_playback_queue = [
                (sequence, body_snapshots)
                for sequence, body_snapshots in sorted(batch.items())
            ]
            for sequence, body_snapshots in self._live_playback_queue:
                for body_name in body_snapshots:
                    self._live_particle_versions[body_name] = sequence

            timer = self._live_refresh_timer
            if self._live_playback_queue and timer is not None:
                timer.setInterval(round(1000 / _LIVE_PREVIEW_MAX_FPS))
            elif not self._live_playback_queue:
                thread = self._live_run_thread
                if thread is not None and not thread.is_alive() and not has_unstable_snapshot:
                    self._stop_live_refresh()
                    self._live_run_thread = None
                return

        _, body_snapshots = self._live_playback_queue.pop(0)
        for body_name, vtp_path in body_snapshots.items():
            try:
                with warnings.catch_warnings():
                    warnings.filterwarnings("ignore", message=".*vtkXMLPolyDataReader.*")
                    mesh = pv.read(str(vtp_path))
            except Exception:
                continue
            if getattr(mesh, "n_points", None) == 0:
                continue
            self.plotter.add_mesh(
                mesh,
                name=f"particle-preview-{body_name}",
                color=self._live_particle_colours.get(body_name),
                opacity=0.95,
                style="points",
                point_size=_PARTICLE_POINT_SIZE,
                label=_particle_label(body_name),
            )

        if self._live_replay_mode and not self._live_playback_queue:
            self._stop_live_refresh()
            print("✅ Particle animation replay completed.")
        elif not self._live_playback_queue:
            timer = self._live_refresh_timer
            if timer is not None:
                timer.setInterval(self._live_refresh_interval_ms)

            thread = self._live_run_thread
            if thread is not None and not thread.is_alive():
                self._stop_live_refresh()
                self._live_run_thread = None

    def _start_live_refresh(self, refresh_interval: float) -> None:
        timer = self._live_refresh_timer
        if timer is not None:
            try:
                timer.stop()
            except Exception:
                pass
        self._live_refresh_timer = None
        self._live_particle_versions = {}
        self._live_particle_file_states = {}
        self._live_playback_queue = []
        self._live_replay_mode = False
        self._live_refresh_interval_ms = round(refresh_interval * 1000)
        if not self._using_background_plotter or self.plotter is None:
            return
        try:
            from qtpy import QtCore
        except ImportError:
            return
        timer = QtCore.QTimer()
        timer.setInterval(round(refresh_interval * 1000))
        timer.timeout.connect(self._refresh_live_particles)
        timer.start()
        self._live_refresh_timer = timer
        if self._live_output_dir is not None:
            for vtp_path in self._live_output_dir.glob("*.vtp"):
                try:
                    stat = vtp_path.stat()
                except OSError:
                    continue
                self._live_particle_file_states[vtp_path] = (stat.st_size, stat.st_mtime_ns)

    def replay_animation(self) -> int:
        """Replay all recorded particle snapshots at no more than 25 FPS."""
        thread = self._live_run_thread
        if thread is not None and thread.is_alive():
            print("❌ Wait for the simulation to finish before replaying.", file=sys.stderr)
            return 1
        if not self._animation_run_completed:
            print("❌ Complete a successful animated run before replaying.", file=sys.stderr)
            return 1
        if self._animation_output_dir is None or not self._animation_particle_body_names:
            print(
                "No particle animation is available. Run 'run --with-animation', "
                "or run 'preview --with-particles' followed by 'continue-to-run'.",
                file=sys.stderr,
            )
            return 1
        if self.plotter is None:
            if self._animation_config is None or self._animation_config_path is None:
                print("❌ The configuration for this particle animation is unavailable.", file=sys.stderr)
                return 1
            rc = self.show_or_update(
                self._animation_config,
                resolved_config_path=self._animation_config_path,
                with_particles=False,
                force=True,
            )
            if rc != 0:
                return rc
        elif not self._using_background_plotter:
            print("❌ Animation replay requires the Qt-backed persistent preview.", file=sys.stderr)
            return 1

        try:
            from qtpy import QtCore
        except ImportError as exc:
            print(
                f"❌ Animation replay requires Qt (`pyvistaqt` with `PySide6` or `PyQt5`): {exc}",
                file=sys.stderr,
            )
            return 1

        snapshots: dict[float, dict[str, Path]] = {}
        for vtp_path in self._animation_output_dir.glob("*.vtp"):
            for body_name in self._animation_particle_body_names:
                prefix = f"{body_name}_"
                if not vtp_path.stem.startswith(prefix):
                    continue
                suffix = vtp_path.stem[len(prefix):]
                if suffix.startswith("ite_"):
                    suffix = suffix[len("ite_"):]
                try:
                    sequence = float(suffix)
                except ValueError:
                    break
                snapshots.setdefault(sequence, {})[body_name] = vtp_path
                break

        if not snapshots:
            print(f"❌ No particle snapshots found in {self._animation_output_dir}.", file=sys.stderr)
            return 1

        self._stop_live_refresh()
        self._live_output_dir = self._animation_output_dir
        self._live_particle_body_names = set(self._animation_particle_body_names)
        self._live_particle_colours = dict(self._animation_particle_colours)
        self._live_particle_versions = {
            body_name: float("-inf")
            for body_name in self._live_particle_body_names
        }
        self._live_playback_queue = [
            (sequence, body_snapshots)
            for sequence, body_snapshots in sorted(snapshots.items())
        ]
        self._live_replay_mode = True
        timer = QtCore.QTimer()
        timer.setInterval(round(1000 / _LIVE_PREVIEW_MAX_FPS))
        timer.timeout.connect(self._refresh_live_particles)
        timer.start()
        self._live_refresh_timer = timer
        print(
            f"▶️ Replaying {len(self._live_playback_queue)} particle frames "
            f"at up to {_LIVE_PREVIEW_MAX_FPS} FPS."
        )
        return 0

    def continue_to_run(self, refresh_interval: float = 10.0) -> int:
        """Run the retained simulation and play its particle snapshots at a capped rate."""
        simulation = self._pending_simulation
        runtime_config_path = self._pending_runtime_config_path
        self._pending_simulation = None
        self._pending_runtime_config_path = None
        if simulation is None:
            if runtime_config_path is not None:
                try:
                    runtime_config_path.unlink(missing_ok=True)
                except OSError:
                    pass
            print(
                "No particle-preview simulation is available. Run 'preview --with-particles' first.",
                file=sys.stderr,
            )
            return 1

        try:
            self._animation_run_completed = False
            simulation.buildSimulation()
            print("✅ Simulation built")
            simulation.initializeSimulation()
            print("✅ Simulation initialized")
            print("\n🚀 Running simulation...")
            self._start_live_refresh(refresh_interval)

            def run_simulation() -> None:
                try:
                    simulation.run()
                    self._animation_run_completed = True
                    print("✅ Simulation completed successfully!")
                except Exception as exc:
                    print(f"❌ Continued run failed: {exc}", file=sys.stderr)
                finally:
                    if runtime_config_path is not None:
                        try:
                            runtime_config_path.unlink(missing_ok=True)
                        except OSError:
                            pass

            self._live_run_thread = Thread(target=run_simulation, daemon=True)
            self._live_run_thread.start()
            print(
                f"ℹ️ Preview checks for new snapshots every {refresh_interval:g} seconds "
                f"and plays each batch at up to {_LIVE_PREVIEW_MAX_FPS} FPS."
            )
            return 0
        except Exception as exc:
            print(f"❌ Continued run failed: {exc}", file=sys.stderr)
            return 1
        finally:
            if self._live_run_thread is None:
                del simulation
                if runtime_config_path is not None:
                    try:
                        runtime_config_path.unlink(missing_ok=True)
                    except OSError:
                        pass


    def pump_ui_events(self) -> None:
        """Keep the persistent preview responsive while shell work blocks."""
        if self.plotter is None:
            return

        try:
            if self._using_background_plotter:
                app = getattr(self.plotter, "app", None)
                if app is not None and hasattr(app, "processEvents"):
                    app.processEvents()
                    return

                try:
                    from PySide6.QtWidgets import QApplication  # type: ignore[import]
                except Exception:
                    try:
                        from PyQt5.QtWidgets import QApplication  # type: ignore[import]
                    except Exception:
                        QApplication = None  # type: ignore[misc,assignment]

                if QApplication is not None:
                    app = QApplication.instance()
                    if app is not None:
                        app.processEvents()
                        return

            if hasattr(self.plotter, "render"):
                self.plotter.render()
        except Exception:
            pass

    @staticmethod
    def _set_label_font_size(actor: Any, size: int) -> bool:
        try:
            mapper = actor.GetMapper()
            text_prop = mapper.GetLabelTextProperty()
            text_prop.SetFontSize(int(size))
            if hasattr(mapper, "Modified"):
                mapper.Modified()
            if hasattr(actor, "Modified"):
                actor.Modified()
            return True
        except Exception:
            return False

    def _rebuild_label_actor(self, entry: dict[str, Any], font_size: int) -> Any | None:
        if self.plotter is None:
            return entry.get("actor")

        actor = entry.get("actor")
        if actor is not None:
            try:
                self.plotter.remove_actor(actor, render=False)
            except TypeError:
                try:
                    self.plotter.remove_actor(actor)
                except Exception:
                    pass
            except Exception:
                pass

        points = entry.get("points")
        labels = entry.get("labels")
        text_color = entry.get("text_color", "white")
        if not points or not labels:
            return actor

        try:
            new_actor = self.plotter.add_point_labels(
                points,
                labels,
                point_size=0,
                font_size=int(font_size),
                text_color=text_color,
                always_visible=True,
            )
        except Exception:
            return actor

        entry["actor"] = new_actor
        return new_actor

    def _remove_hover_observer(self) -> None:
        if self._hover_interactor is not None and self._hover_observer_tag is not None:
            try:
                self._hover_interactor.RemoveObserver(self._hover_observer_tag)
            except Exception:
                pass
        self._hover_interactor = None
        self._hover_observer_tag = None

    def _install_annotation_hover(self, visualizer: Any) -> None:
        self._remove_hover_observer()

        if self.plotter is None:
            return

        # Hover effects are intended for the persistent Qt-backed preview.
        # The plain Plotter fallback can become less responsive under extra
        # interactor callbacks while the shell waits for input.
        if not self._using_background_plotter:
            return

        entries = getattr(visualizer, "annotation_label_actors", None) or []
        if not entries:
            return

        iren_wrapper = getattr(self.plotter, "iren", None)
        interactor = getattr(iren_wrapper, "interactor", None)
        renderer = getattr(self.plotter, "renderer", None)
        if interactor is None or renderer is None:
            return

        try:
            import vtk  # type: ignore[import]
        except Exception:
            return

        spec_entries: list[dict[str, Any]] = []
        hover_coordinate = vtk.vtkCoordinate()
        hover_coordinate.SetCoordinateSystemToWorld()

        def _apply_size(spec_index: int, font_size: int) -> None:
            entry = spec_entries[spec_index]
            actor = entry.get("actor")
            if actor is not None and self._set_label_font_size(actor, font_size):
                return

            rebuilt_actor = self._rebuild_label_actor(entry, font_size)
            if rebuilt_actor is not None:
                entry["actor"] = rebuilt_actor

        for entry in entries:
            actor = entry.get("actor") if isinstance(entry, dict) else None
            if actor is None:
                continue
            base_size = int(entry.get("font_size", 8))
            hover_size = max(base_size + 4, 12)
            spec_index = len(spec_entries)
            entry["base_size"] = base_size
            entry["hover_size"] = hover_size
            spec_entries.append(entry)
            _apply_size(spec_index, base_size)

        if not spec_entries:
            return

        active_spec_index: int | None = None

        def _estimate_label_bounds(entry: dict[str, Any], dx: float, dy: float) -> tuple[float, float, float, float]:
            labels = entry.get("labels") or []
            text = str(labels[0]) if labels else ""
            lines = text.splitlines() or [text]
            max_chars = max((len(line) for line in lines), default=1)

            # Approximate VTK text bounds; labels are anchored near lower-left.
            font_size = int(entry.get("base_size", 8))
            if active_spec_index is not None and spec_entries[active_spec_index] is entry:
                font_size = int(entry.get("hover_size", font_size))
            width = max(12.0, float(max_chars) * float(font_size) * 0.62 + 8.0)
            height = max(12.0, float(len(lines)) * float(font_size) * 1.35 + 4.0)
            pad = 6.0
            return (dx - pad, dy - pad, dx + width + pad, dy + height + pad)

        def _on_mouse_move(caller: Any, event: Any) -> None:
            nonlocal active_spec_index
            try:
                x, y = interactor.GetEventPosition()
            except Exception:
                return

            new_spec_index: int | None = None
            best_inside_score = float("inf")
            best_anchor_distance_sq = float(16 * 16)
            for idx, entry in enumerate(spec_entries):
                points = entry.get("points") or []
                if not points:
                    continue

                anchor = points[0]
                try:
                    if len(anchor) >= 3:
                        hover_coordinate.SetValue(float(anchor[0]), float(anchor[1]), float(anchor[2]))
                    else:
                        hover_coordinate.SetValue(float(anchor[0]), float(anchor[1]), 0.0)
                    dx, dy = hover_coordinate.GetComputedDisplayValue(renderer)
                except Exception:
                    continue

                left, bottom, right, top = _estimate_label_bounds(entry, float(dx), float(dy))
                if left <= float(x) <= right and bottom <= float(y) <= top:
                    center_x = 0.5 * (left + right)
                    center_y = 0.5 * (bottom + top)
                    inside_score = (center_x - float(x)) ** 2 + (center_y - float(y)) ** 2
                    if inside_score < best_inside_score:
                        best_inside_score = inside_score
                        new_spec_index = idx
                    continue

                if new_spec_index is not None:
                    continue

                # Fallback around anchor when cursor is close but outside text box.
                distance_sq = (float(dx) - float(x)) ** 2 + (float(dy) - float(y)) ** 2
                if distance_sq <= best_anchor_distance_sq:
                    best_anchor_distance_sq = distance_sq
                    new_spec_index = idx

            if new_spec_index == active_spec_index:
                return

            if active_spec_index is not None:
                prev_entry = spec_entries[active_spec_index]
                _apply_size(active_spec_index, int(prev_entry.get("base_size", 8)))

            if new_spec_index is not None:
                next_entry = spec_entries[new_spec_index]
                _apply_size(new_spec_index, int(next_entry.get("hover_size", 12)))

            active_spec_index = new_spec_index

        try:
            tag = interactor.AddObserver("MouseMoveEvent", _on_mouse_move)
        except Exception:
            return

        self._hover_interactor = interactor
        self._hover_observer_tag = tag

    def _is_unchanged(
        self,
        config: SimulationConfig,
        *,
        resolved_config_path: Path,
        with_particles: bool,
    ) -> bool:
        if with_particles:
            return False

        payload = {
            "config": config.model_dump(exclude_none=True),
            "config_path": str(resolved_config_path.resolve()),
            "with_particles": with_particles,
        }
        signature = json.dumps(payload, sort_keys=True)
        unchanged = signature == self.last_signature
        self.last_signature = signature
        return unchanged

    def show_or_update(
        self,
        config: SimulationConfig,
        *,
        resolved_config_path: Path,
        with_particles: bool,
        force: bool = False,
    ) -> int:
        try:
            import pyvista as pv  # noqa: F401
        except ImportError:
            print(
                "❌ PyVista is not installed.\n"
                "   Install it with:  pip install sphinxsim[visualization]",
                file=sys.stderr,
            )
            return 1

        if self.plotter is not None and self._using_background_plotter:
            editor_refreshed = self._install_json_editor(
                config,
                config_path=resolved_config_path,
                with_particles=with_particles,
            )
            if not editor_refreshed:
                print("Preview canceled; unapplied editor changes were preserved.")
                return 0
        unchanged = self._is_unchanged(
            config,
            resolved_config_path=resolved_config_path,
            with_particles=with_particles,
        )
        if not force and unchanged:
            print("ℹ️ Preview unchanged; keeping existing window.")
            return 0

        from sphinxsim.visualization.preview import ConfigVisualizer

        visualizer = ConfigVisualizer(
            config,
            PROJECT_ROOT,
            config_path=resolved_config_path,
            off_screen=False,
        )

        ndim = visualizer._spatial_dim()
        vtp_dir: Path | None = None
        latest_particle_vtps: dict[str, Path] = {}

        # Always rebuild geometry; if VTPs are unavailable the preview falls back
        # to cached bounds and still renders the scene.
        vtp_dir = visualizer._try_build_geometries(ndim, with_particles=with_particles)
        if with_particles:
            latest_particle_vtps = visualizer._discover_latest_particle_vtps(vtp_dir)

        try:
            if sys.platform.startswith("linux"):
                os.environ.setdefault("QT_QPA_PLATFORM", "xcb")
            import pyvista as pv
            pyvistaqt_error: Exception | None = None
            created_plotter = False
            try:
                from pyvistaqt import BackgroundPlotter  # type: ignore[import]
            except Exception as exc:
                BackgroundPlotter = None
                pyvistaqt_error = exc

            if self.plotter is None:
                created_plotter = True
                if BackgroundPlotter is not None:
                    self.plotter = BackgroundPlotter(
                        title="SPHinXsim - Configuration Preview",
                        show=True,
                        shape=(1, 1),
                        border=False,
                        window_size=(1400, 800),
                        # Avoid loading pyvistaqt's bundled PNG icon, whose
                        # invalid ICC metadata causes noisy libpng warnings on
                        # some Windows builds.
                        update_app_icon=False,
                    )
                    self._using_background_plotter = True
                else:
                    self.plotter = pv.Plotter(
                        title="SPHinXsim - Configuration Preview",
                        off_screen=False,
                        shape=(1, 2),
                        border=False,
                        window_size=(1400, 800),
                    )
                    self._using_background_plotter = False
                    detail = f" ({pyvistaqt_error})" if pyvistaqt_error is not None else ""
                    print(
                        "ℹ️ pyvistaqt background mode is unavailable"
                        f"{detail}; persistent preview may become unresponsive while shell waits for input.\n"
                        "   Install dependencies with: pip install pyvistaqt PySide6\n"
                        "   (or use PyQt5 instead of PySide6)",
                        file=sys.stderr,
                    )

            if created_plotter and self._using_background_plotter:
                self._watch_preview_window_close()
                self._install_json_editor(
                    config,
                    config_path=resolved_config_path,
                    with_particles=with_particles,
                )

            self.plotter.clear()
            configure_layout = getattr(visualizer, "_configure_layout", None)
            if configure_layout is not None:
                configure_layout(self.plotter)
            draw_sidebar = getattr(visualizer, "_draw_preview_sidebar", None)
            if draw_sidebar is not None:
                draw_sidebar(self.plotter)
            visualizer._populate_plotter(self.plotter, vtp_dir, latest_particle_vtps)
            if with_particles:
                self._animation_run_completed = False
                pending_simulation = visualizer.take_particle_simulation()
                if pending_simulation is not None:
                    from sphinxsim.visualization.preview import _body_colour

                    (
                        self._pending_simulation,
                        self._pending_runtime_config_path,
                    ) = pending_simulation
                    self._live_output_dir = vtp_dir or (
                        PROJECT_ROOT / ".build-temp" / "test_simulation" / "output"
                    )
                    self._live_particle_body_names = visualizer._particle_generation_body_names()
                    self._live_particle_colours = {
                        body_name: _body_colour(body_name, config)
                        for body_name in self._live_particle_body_names
                    }
                    self._animation_output_dir = self._live_output_dir
                    self._animation_particle_body_names = set(self._live_particle_body_names)
                    self._animation_particle_colours = dict(self._live_particle_colours)
                    self._animation_config = config
                    self._animation_config_path = resolved_config_path
                else:
                    self._animation_output_dir = None
                    self._animation_particle_body_names = set()
                    self._animation_particle_colours = {}
                    self._animation_run_completed = False
            if configure_layout is not None:
                configure_layout(self.plotter)
            visualizer._configure_default_view(self.plotter, ndim)
            self.plotter.add_axes()
            self.plotter.show_grid(font_size=10, color=(0.25, 0.25, 0.25), bold=False)

            if vtp_dir:
                mode_label = "VTP geometry"
            elif visualizer.used_cpp_bounds:
                mode_label = "C++ bounds fallback"
            else:
                mode_label = "No C++ geometry"
            dim_label = "2-D" if ndim == 2 else "3-D"
            sim_type_label = config.simulation_type.value.replace("_", " ").title()
            config_info = f"{dim_label}  •  {sim_type_label}  •  {mode_label}"
            visualizer._add_config_info_text(self.plotter, config_info, ndim)
            self._install_annotation_hover(visualizer)

            if not self._using_background_plotter:
                try:
                    # Non-blocking window path: returns immediately to shell.
                    self.plotter.show(auto_close=False, interactive_update=True)
                except TypeError:
                    # Some mocked plotters do not accept these kwargs.
                    self.plotter.show()
            try:
                self.plotter.render()
            except Exception:
                pass
            return 0
        except ImportError as exc:
            print(f"❌ {exc}", file=sys.stderr)
            return 1
        except Exception as exc:
            print(f"❌ Preview failed: {exc}", file=sys.stderr)
            return 1


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
