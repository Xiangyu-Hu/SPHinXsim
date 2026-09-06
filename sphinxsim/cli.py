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
sys.path.insert(0, os.path.join(PROJECT_ROOT, "sphinxsim", "bindings", "native"))

from pydantic import ValidationError

from sphinxsim.bindings.loader import load_sphinxsys_core_nd
from sphinxsim.config.schemas import PhysicalCorrectionWarning, SimulationConfig
from sphinxsim.config.update_patch import UpdatePatch, apply_update_patch
from sphinxsim.llm import get_llm
from sphinxsim.llm.common import LLMRepairWarning, dump_simulation_config_json

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
        return SimulationConfig(**data), 0
    except json.JSONDecodeError as exc:
        print(f"Invalid JSON: {exc}", file=sys.stderr)
        return None, 1
    except ValidationError as exc:
        print(f"Config validation failed:\n{exc}", file=sys.stderr)
        return None, 1


def _write_validated_config(path: Path, config: SimulationConfig) -> str:
    """Atomically replace *path* with the canonical validated JSON payload."""
    content = dump_simulation_config_json(config, indent=2)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
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


def _resolve_preview_config_path(config_file: str) -> Path:
    """Resolve preview config path using cwd-first then .build-temp fallback."""
    config_path = Path(config_file)
    if config_path.is_absolute():
        return config_path
    cwd_path = Path.cwd() / config_path
    build_temp_path = PROJECT_ROOT / ".build-temp" / config_path
    return cwd_path if cwd_path.exists() else build_temp_path


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


class _ShellPreviewRuntime:
    """Persistent shell preview runtime.

    Keeps a non-blocking plotter window alive across `preview` commands and
    updates its scene in place.
    """

    def __init__(self) -> None:
        self.plotter: Any | None = None
        self.last_signature: str | None = None
        self._using_background_plotter = False
        self._hover_interactor: Any | None = None
        self._hover_observer_tag: Any | None = None
        self._json_editor: Any | None = None

    def _reset_preview_state(self) -> None:
        """Forget all state associated with a closed preview window."""
        self._remove_hover_observer()
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

    def _install_json_editor(
        self,
        config: SimulationConfig,
        *,
        config_path: Path,
        with_particles: bool,
    ) -> bool:
        """Create or rebind the save-only property editor for a preview config.

        Returns ``False`` when the user cancels a refresh that would discard
        unapplied editor changes.
        """
        if self.plotter is None:
            return True

        if self._json_editor is not None:
            refresh = self._json_editor.get("refresh")
            if callable(refresh):
                return refresh(config, config_path, with_particles) is not False
            return True

        try:
            from qtpy import QtCore, QtGui, QtWidgets
        except Exception:
            return True

        app_window = getattr(self.plotter, "app_window", None)
        if app_window is None:
            return True
        _set_windows_dark_title_bar(app_window)
        app_window.setStyleSheet(
            "QMainWindow { background: #181a1f; }"
            "QMenuBar { background: #202329; color: #e6e8ec; border-bottom: 1px solid #3b4049; }"
            "QMenuBar::item { padding: 5px 9px; background: transparent; }"
            "QMenuBar::item:selected { background: #30343c; color: #ffffff; }"
            "QMenu { background: #24272d; color: #e6e8ec; border: 1px solid #454b55; }"
            "QMenu::item { padding: 5px 24px 5px 10px; }"
            "QMenu::item:selected { background: #315f8c; color: #ffffff; }"
            "QToolBar { background: #202329; border: 0; border-bottom: 1px solid #3b4049; spacing: 3px; }"
            "QToolBar QToolButton { min-width: 26px; min-height: 26px; padding: 2px; "
            "background: #24272d; color: #f0f2f5; border: 1px solid #4a505b; border-radius: 3px; }"
            "QToolBar QToolButton:hover { background: #30343c; color: #ffffff; border-color: #75a9d0; }"
            "QToolBar QToolButton:pressed { background: #315f8c; color: #ffffff; border-color: #80bde8; }"
            "QToolBar QToolButton:disabled { background: #202329; color: #6f7680; border-color: #343a43; }"
            "QStatusBar { background: #202329; color: #c8ccd4; border-top: 1px solid #3b4049; }"
            "QSplitter::handle { background: #3b4049; }"
            "QSplitter::handle:hover { background: #5794cf; }"
        )

        dock = QtWidgets.QDockWidget("Simulation Properties", app_window)
        dock.setObjectName("sphinxsim-json-editor")
        dock.setStyleSheet(
            "QDockWidget { color: #e6e8ec; background: #181a1f; }"
            "QDockWidget::title { padding: 7px 8px; background: #202329; color: #f0f2f5; "
            "border-bottom: 1px solid #3b4049; text-align: left; }"
        )
        dock.setAllowedAreas(QtCore.Qt.DockWidgetArea.RightDockWidgetArea)
        content = QtWidgets.QWidget(dock)
        content.setObjectName("sphinxsim-json-editor-content")
        content.setStyleSheet(
            "QWidget#sphinxsim-json-editor-content {"
            "    background: #181a1f;"
            "    color: #e6e8ec;"
            "}"
            "QLabel {"
            "    color: #d7dae0;"
            "}"
            "QToolButton {"
            "    min-height: 24px;"
            "    padding: 2px 5px;"
            "    border: 1px solid #4a505b;"
            "    border-radius: 3px;"
            "    background: #24272d;"
            "    color: #d7dae0;"
            "}"
            "QToolButton:hover {"
            "    background: #30343c;"
            "    border: 1px solid #61707e;"
            "    color: #ffffff;"
            "}"
            "QToolButton:pressed {"
            "    background: #3a404a;"
            "    border: 1px solid #7b8997;"
            "    color: #ffffff;"
            "}"
            "QToolButton:disabled {"
            "    background: #202329;"
            "    border: 1px solid #343a43;"
            "    color: #686e79;"
            "}"
            "QToolButton#sphinxsim-json-editor-list-action {"
            "    min-height: 28px;"
            "    font-weight: 600;"
            "    background: #2f6f9f;"
            "    border: 1px solid #235a84;"
            "    color: #ffffff;"
            "}"
            "QToolButton#sphinxsim-json-editor-list-action:hover {"
            "    background: #245d88;"
            "    border-color: #164969;"
            "}"
            "QToolButton#sphinxsim-json-editor-list-action:pressed {"
            "    background: #1c4c70;"
            "    border-color: #123b59;"
            "}"
            "QToolButton#sphinxsim-json-editor-list-action:disabled {"
            "    background: #25313b;"
            "    border: 1px solid #374956;"
            "    color: #788895;"
            "}"
        )
        layout = QtWidgets.QVBoxLayout(content)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(8)
        filter_box = QtWidgets.QLineEdit(content)
        filter_box.setObjectName("sphinxsim-json-editor-filter")
        filter_box.setPlaceholderText("Filter settings…")
        filter_box.setStyleSheet(
            "min-height: 27px; padding: 1px 7px; border: 1px solid #454b55; border-radius: 3px; "
            "background: #24272d; color: #e6e8ec; selection-background-color: #315f8c;"
        )
        tools_grid = QtWidgets.QGridLayout()
        tools_grid.setHorizontalSpacing(4)
        tools_grid.setVerticalSpacing(4)
        tools_grid.setContentsMargins(0, 0, 0, 0)
        expand_button = QtWidgets.QToolButton(content)
        expand_button.setText("Expand")
        collapse_button = QtWidgets.QToolButton(content)
        collapse_button.setText("Collapse")
        revert_button = QtWidgets.QToolButton(content)
        revert_button.setText("Revert")
        reload_button = QtWidgets.QToolButton(content)
        reload_button.setText("Reload")
        add_item_button = QtWidgets.QToolButton(content)
        add_item_button.setText("Add")
        duplicate_item_button = QtWidgets.QToolButton(content)
        duplicate_item_button.setText("Duplicate")
        remove_item_button = QtWidgets.QToolButton(content)
        remove_item_button.setText("Remove")
        move_up_button = QtWidgets.QToolButton(content)
        move_up_button.setText("Move up")
        move_down_button = QtWidgets.QToolButton(content)
        move_down_button.setText("Move down")
        list_action_buttons = (
            add_item_button,
            duplicate_item_button,
            remove_item_button,
            move_up_button,
            move_down_button,
        )
        for button in list_action_buttons:
            button.setObjectName("sphinxsim-json-editor-list-action")
        tool_buttons = (
            expand_button,
            collapse_button,
            revert_button,
            reload_button,
            add_item_button,
            duplicate_item_button,
            remove_item_button,
            move_up_button,
            move_down_button,
        )
        for index, button in enumerate(tool_buttons):
            button.setToolButtonStyle(
                QtCore.Qt.ToolButtonStyle.ToolButtonTextOnly
            )
            button.setMinimumWidth(0)
            button.setSizePolicy(
                QtWidgets.QSizePolicy.Policy.Expanding,
                QtWidgets.QSizePolicy.Policy.Fixed,
            )
            row = 0 if index < 5 else 1
            column = index if index < 5 else index - 5
            tools_grid.addWidget(button, row, column)
            button.setVisible(True)

        for column in range(5):
            tools_grid.setColumnStretch(column, 1)
        tree = QtWidgets.QTreeWidget(content)
        tree.setObjectName("sphinxsim-json-editor-tree")
        tree.setColumnCount(2)
        tree.setHeaderLabels(["Setting", "Value"])
        tree.setRootIsDecorated(True)
        tree.setAlternatingRowColors(True)
        tree.setUniformRowHeights(True)
        tree.setIndentation(16)
        tree.setColumnWidth(0, 145)
        tree.header().setStretchLastSection(True)
        tree.setStyleSheet(
            "QTreeWidget { border: 1px solid #454b55; border-radius: 3px; background: #202329; color: #e1e4e8; }"
            "QHeaderView::section { padding: 5px 7px; border: 0; border-bottom: 1px solid #454b55; "
            "background: #292d34; color: #f0f2f5; font-weight: 600; }"
            "QTreeWidget::item { min-height: 30px; color: #e1e4e8; }"
            "QTreeWidget::item:alternate { background: #252930; }"
            "QTreeWidget::item:selected { background: #315f8c; color: #ffffff; }"
            "QLineEdit, QComboBox { min-height: 25px; padding: 1px 6px; "
            "border: 1px solid #4b515c; border-radius: 3px; background: #181a1f; color: #e6e8ec; }"
            "QLineEdit:focus, QComboBox:focus { border: 1px solid #5794cf; }"
            "QLineEdit::selection { background: #315f8c; color: #ffffff; }"
            "QComboBox QAbstractItemView { background: #24272d; color: #e6e8ec; border: 1px solid #4b515c; "
            "selection-background-color: #315f8c; selection-color: #ffffff; outline: 0; }"
        )
        status = QtWidgets.QLabel("Loaded configuration. Press Ctrl+S to save changes.")
        status.setWordWrap(True)
        status.setAlignment(
            QtCore.Qt.AlignmentFlag.AlignLeft | QtCore.Qt.AlignmentFlag.AlignVCenter
        )
        status.setStyleSheet(
            "padding: 7px 9px; border: 1px solid #3d6f9e; border-radius: 4px; "
            "background: #1f3042; color: #d9e8f5;"
        )
        layout.addWidget(filter_box)
        layout.addLayout(tools_grid)
        layout.addWidget(tree, 1)
        layout.addWidget(status)
        dock.setWidget(content)
        # Give the right dock both right-hand corners so it spans the same full
        # top-to-bottom extent as the preview area instead of appearing offset.
        app_window.setCorner(
            QtCore.Qt.Corner.TopRightCorner,
            QtCore.Qt.DockWidgetArea.RightDockWidgetArea,
        )
        app_window.setCorner(
            QtCore.Qt.Corner.BottomRightCorner,
            QtCore.Qt.DockWidgetArea.RightDockWidgetArea,
        )
        app_window.addDockWidget(QtCore.Qt.DockWidgetArea.RightDockWidgetArea, dock)

        json_editor_initial_width = 360
        json_editor_minimum_width = 260

        dock.setMinimumWidth(json_editor_minimum_width)
        dock.setMaximumWidth(16777215)
        content.setMinimumWidth(0)
        content.setMaximumWidth(16777215)
        tree.setMinimumWidth(0)
        tree.setMaximumWidth(16777215)

        dock.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Preferred,
            QtWidgets.QSizePolicy.Policy.Expanding,
        )
        content.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Expanding,
            QtWidgets.QSizePolicy.Policy.Expanding,
        )
        tree.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Expanding,
            QtWidgets.QSizePolicy.Policy.Expanding,
        )

        def _set_initial_json_editor_width() -> None:
            try:
                app_window.resizeDocks(
                    [dock],
                    [json_editor_initial_width],
                    QtCore.Qt.Orientation.Horizontal,
                )
            except Exception:
                pass

        QtCore.QTimer.singleShot(0, _set_initial_json_editor_width)
        QtCore.QTimer.singleShot(100, _set_initial_json_editor_width)

        editor_state: dict[str, Any] = {
            "payload": {},
            "config_path": config_path,
            "with_particles": with_particles,
            "fields": [],
            "original_json": "",
            "undo_stack": [],
            "dirty": False,
            "expanded_paths": set(),
            "item_paths": {},
            "list_paths": set(),
        }

        def _new_tree_item(
            parent: Any | None,
            label: str,
            summary: str = "",
        ) -> Any:
            item = QtWidgets.QTreeWidgetItem([label, summary])
            if parent is None:
                tree.addTopLevelItem(item)
            else:
                parent.addChild(item)
            return item

        field_labels = {
            "simulation_type": "Simulation type",
            "particle_spacing": "Particle spacing [m]",
            "characteristic_length_particles": "Characteristic particles",
            "lower_bound": "Lower bound [m]",
            "upper_bound": "Upper bound [m]",
            "half_size": "Half size [m]",
            "translation": "Translation [m]",
            "center": "Center [m]",
            "radius": "Radius [m]",
            "density": "Density [kg/m³]",
            "sound_speed": "Sound speed [m/s]",
            "viscosity": "Viscosity [Pa·s]",
            "youngs_modulus": "Young's modulus [Pa]",
            "yield_stress": "Yield stress [Pa]",
            "cohesion": "Cohesion [Pa]",
            "friction_angle": "Friction angle [rad]",
            "dilatancy_angle": "Dilatancy angle [rad]",
            "end_time": "End time [s]",
            "output_interval": "Output interval [s]",
            "screen_interval": "Screen interval",
            "inflow_speed": "Inflow speed [m/s]",
            "gravity": "Gravity [m/s²]",
        }
        enum_choices = {
            "simulation_type": ["fluid_dynamics", "continuum_dynamics"],
            "surface_type": ["free_surface", "confined", "open_boundary", "free_stream"],
            "kernel_correction": ["linear", "none"],
        }

        def _display_label(key: str) -> str:
            if key.startswith("["):
                return key
            return field_labels.get(key, key.replace("_", " ").capitalize())

        def _entry_label(value: Any, index: int) -> str:
            if isinstance(value, dict):
                for identity_key in ("name", "body_name", "observed_body", "oriented_box"):
                    identity = value.get(identity_key)
                    if isinstance(identity, str) and identity:
                        return identity
            return f"Item {index + 1}"

        def _mark_dirty(item: Any | None = None) -> None:
            editor_state["dirty"] = True
            revert_button.setEnabled(True)
            if item is not None:
                item.setBackground(0, QtGui.QColor("#594a24"))
            status.setText("Unsaved changes. Press Ctrl+S to validate, save, and refresh.")

        def _clear_field_errors() -> None:
            for _, _, widget, item in editor_state["fields"]:
                widget.setStyleSheet("")
                widget.setToolTip("")
                item.setToolTip(0, "")

        def _value_widget(value: Any, key: str) -> Any:
            if isinstance(value, bool):
                widget = QtWidgets.QCheckBox(tree)
                widget.setChecked(value)
                return widget

            if key in enum_choices and isinstance(value, str):
                widget = QtWidgets.QComboBox(tree)
                widget.addItems(enum_choices[key])
                if value not in enum_choices[key]:
                    widget.addItem(value)
                widget.setCurrentText(value)
                widget.view().setStyleSheet(
                    "QAbstractItemView { background: #24272d; color: #e6e8ec; "
                    "selection-background-color: #315f8c; selection-color: #ffffff; }"
                )
                return widget

            if isinstance(value, int):
                widget = QtWidgets.QLineEdit(tree)
                widget.setText(str(value))
                return widget

            if isinstance(value, float):
                widget = QtWidgets.QLineEdit(tree)
                widget.setText(str(value))
                return widget

            widget = QtWidgets.QLineEdit(tree)
            if isinstance(value, str):
                widget.setText(value)
            else:
                widget.setText(json.dumps(value, ensure_ascii=False))
            return widget

        def populate_editor(
            text: str,
            *,
            reset_history: bool = False,
            expand_defaults: bool = True,
        ) -> None:
            iterator = QtWidgets.QTreeWidgetItemIterator(tree)
            while iterator.value() is not None:
                previous_item = iterator.value()
                path_key = previous_item.data(0, QtCore.Qt.ItemDataRole.UserRole)
                if path_key is not None and previous_item.isExpanded():
                    editor_state["expanded_paths"].add(path_key)
                iterator += 1

            payload = json.loads(text)
            editor_state["payload"] = payload
            editor_state["fields"] = []
            editor_state["item_paths"] = {}
            editor_state["list_paths"] = set()
            if reset_history:
                editor_state["original_json"] = text
                editor_state["undo_stack"] = []
            editor_state["dirty"] = False
            revert_button.setEnabled(bool(editor_state["undo_stack"]))
            tree.clear()

            def add_value(parent: Any | None, label: str, value: Any, path: tuple[Any, ...], depth: int) -> None:
                path_key = ".".join(str(part) for part in path)
                if isinstance(value, dict) and value:
                    item = _new_tree_item(parent, _display_label(label), f"Object · {len(value)}")
                    item.setData(0, QtCore.Qt.ItemDataRole.UserRole, path_key)
                    editor_state["item_paths"][id(item)] = path
                    item.setExpanded(
                        path_key in editor_state["expanded_paths"]
                        or (expand_defaults and depth < 2)
                    )
                    font = item.font(0)
                    font.setBold(True)
                    item.setFont(0, font)
                    for child_key, child_value in value.items():
                        add_value(item, str(child_key), child_value, (*path, child_key), depth + 1)
                    return
                if isinstance(value, list) and value:
                    item = _new_tree_item(parent, _display_label(label), f"List · {len(value)}")
                    item.setData(0, QtCore.Qt.ItemDataRole.UserRole, path_key)
                    editor_state["item_paths"][id(item)] = path
                    editor_state["list_paths"].add(path)
                    item.setExpanded(
                        path_key in editor_state["expanded_paths"]
                        or (expand_defaults and depth < 2)
                    )
                    font = item.font(0)
                    font.setBold(True)
                    item.setFont(0, font)
                    for index, child_value in enumerate(value):
                        add_value(
                            item,
                            _entry_label(child_value, index),
                            child_value,
                            (*path, index),
                            depth + 1,
                        )
                    return

                if isinstance(value, list):
                    item = _new_tree_item(parent, _display_label(label), "List · 0")
                    item.setData(0, QtCore.Qt.ItemDataRole.UserRole, path_key)
                    editor_state["item_paths"][id(item)] = path
                    editor_state["list_paths"].add(path)
                    return

                item = _new_tree_item(parent, _display_label(label))
                item.setData(0, QtCore.Qt.ItemDataRole.UserRole, path_key)
                editor_state["item_paths"][id(item)] = path
                if isinstance(value, list):
                    editor_state["list_paths"].add(path)
                widget = _value_widget(value, label)
                tree.setItemWidget(item, 1, widget)
                tooltip = path_key
                item.setToolTip(0, tooltip)
                widget.setToolTip(tooltip)
                if hasattr(widget, "textChanged"):
                    widget.textChanged.connect(lambda *_args, entry=item: _mark_dirty(entry))
                elif hasattr(widget, "valueChanged"):
                    widget.valueChanged.connect(lambda *_args, entry=item: _mark_dirty(entry))
                elif hasattr(widget, "toggled"):
                    widget.toggled.connect(lambda *_args, entry=item: _mark_dirty(entry))
                editor_state["fields"].append((path, value, widget, item))

            if isinstance(payload, dict):
                for key, value in payload.items():
                    add_value(None, str(key), value, (key,), 0)
            else:
                add_value(None, "Configuration", payload, (), 0)

        def filter_items(query: str) -> None:
            query = query.strip().lower()

            def visit(item: Any) -> bool:
                own_match = not query or query in item.text(0).lower() or query in item.text(1).lower()
                child_match = False
                for index in range(item.childCount()):
                    child_match = visit(item.child(index)) or child_match
                item.setHidden(not (own_match or child_match))
                if child_match and query:
                    item.setExpanded(True)
                return own_match or child_match

            for index in range(tree.topLevelItemCount()):
                visit(tree.topLevelItem(index))

        def collect_payload() -> dict[str, Any]:
            payload = editor_state["payload"]
            for path, original_value, widget, _ in editor_state["fields"]:
                if isinstance(original_value, bool):
                    value = widget.isChecked()
                else:
                    if isinstance(original_value, str):
                        value = widget.currentText() if isinstance(widget, QtWidgets.QComboBox) else widget.text()
                    elif isinstance(original_value, int):
                        value = int(widget.text())
                    elif isinstance(original_value, float):
                        value = float(widget.text())
                    else:
                        value = json.loads(widget.text())

                target = payload
                for part in path[:-1]:
                    target = target[part]
                if path:
                    target[path[-1]] = value
            return payload

        def reset_to_json(text: str, message: str) -> None:
            editor_state["expanded_paths"] = set()
            populate_editor(text)
            status.setText(message)

        def undo_changes() -> None:
            if not editor_state["undo_stack"]:
                reset_to_json(editor_state["original_json"], "Restored the last saved configuration.")
                return
            reset_to_json(editor_state["undo_stack"].pop(), "Reverted the latest local edit.")
            revert_button.setEnabled(bool(editor_state["undo_stack"]))

        def confirm_discard_changes(message: str) -> bool:
            if not editor_state["dirty"]:
                return True
            buttons = (
                QtWidgets.QMessageBox.StandardButton.Yes
                | QtWidgets.QMessageBox.StandardButton.No
            )
            answer = QtWidgets.QMessageBox.warning(
                content,
                "Discard unapplied changes?",
                message,
                buttons,
                QtWidgets.QMessageBox.StandardButton.No,
            )
            return answer == QtWidgets.QMessageBox.StandardButton.Yes

        def reload_from_disk() -> None:
            if not confirm_discard_changes(
                "The editor contains unapplied changes. Reloading from disk will discard them. Continue?"
            ):
                status.setText("Reload canceled. Your unapplied changes are preserved.")
                return
            active_config_path = editor_state["config_path"]
            try:
                source = active_config_path.read_text(encoding="utf-8")
                SimulationConfig(**json.loads(source))
            except (OSError, ValidationError, json.JSONDecodeError) as exc:
                status.setText(f"Could not reload configuration: {exc}")
                return
            editor_state["expanded_paths"] = set()
            populate_editor(source, reset_history=True)
            status.setText("Reloaded the saved configuration from disk.")

        def _payload_at(path: tuple[Any, ...]) -> Any:
            target = editor_state["payload"]
            for part in path:
                target = target[part]
            return target

        def update_array_actions() -> None:
            item = tree.currentItem()
            path = editor_state["item_paths"].get(id(item)) if item is not None else None
            is_list = path in editor_state["list_paths"] if path is not None else False
            is_list_entry = bool(path) and path[:-1] in editor_state["list_paths"] and isinstance(path[-1], int)
            add_item_button.setEnabled(is_list)
            duplicate_item_button.setEnabled(is_list_entry)
            remove_item_button.setEnabled(is_list_entry)
            move_up_button.setEnabled(is_list_entry and path[-1] > 0)
            if is_list_entry:
                try:
                    move_down_button.setEnabled(path[-1] < len(_payload_at(path[:-1])) - 1)
                except (KeyError, IndexError, TypeError):
                    move_down_button.setEnabled(False)
            else:
                move_down_button.setEnabled(False)

        def edit_array(operation: str) -> None:
            item = tree.currentItem()
            path = editor_state["item_paths"].get(id(item)) if item is not None else None
            if path is None:
                return
            try:
                collect_payload()
                before_edit = json.dumps(editor_state["payload"], ensure_ascii=False)
                if operation == "add" and path in editor_state["list_paths"]:
                    target_list = _payload_at(path)
                    if not target_list:
                        target_list.append(
                            _new_json_editor_list_item(path, _config_spatial_dim(config))
                        )
                    else:
                        target_list.append(copy.deepcopy(target_list[-1]))
                elif path[:-1] in editor_state["list_paths"] and isinstance(path[-1], int):
                    target_list = _payload_at(path[:-1])
                    index = path[-1]
                    if operation == "duplicate":
                        target_list.insert(index + 1, copy.deepcopy(target_list[index]))
                    elif operation == "remove":
                        target_list.pop(index)
                    elif operation == "up" and index > 0:
                        target_list[index - 1], target_list[index] = target_list[index], target_list[index - 1]
                    elif operation == "down" and index < len(target_list) - 1:
                        target_list[index + 1], target_list[index] = target_list[index], target_list[index + 1]
                    else:
                        return
                else:
                    return
            except (KeyError, IndexError, TypeError, ValueError, json.JSONDecodeError) as exc:
                status.setText(f"Could not update list: {exc}")
                return

            editor_state["undo_stack"].append(before_edit)
            editor_state["expanded_paths"].add(".".join(str(part) for part in path))
            populate_editor(
                json.dumps(editor_state["payload"], indent=2, ensure_ascii=False),
                expand_defaults=False,
            )
            _mark_dirty()
            update_array_actions()

        def apply_changes() -> None:
            active_config_path = editor_state["config_path"]
            active_with_particles = editor_state["with_particles"]
            _clear_field_errors()
            try:
                before_edit = json.dumps(editor_state["payload"], ensure_ascii=False)
                payload = collect_payload()
            except (TypeError, ValueError, json.JSONDecodeError) as exc:
                status.setText(f"Invalid value: {exc}")
                return

            try:
                updated_config = SimulationConfig(**payload)
            except ValidationError as exc:
                first_error = exc.errors(include_url=False)[0]
                location = ".".join(str(part) for part in first_error.get("loc", ("config",)))
                error_path = tuple(first_error.get("loc", ()))
                for path, _, widget, item in editor_state["fields"]:
                    if path == error_path:
                        message = str(first_error.get("msg"))
                        widget.setStyleSheet(
                            "border: 2px solid #ff6b5f; background: #472522; color: #ffffff;"
                        )
                        widget.setToolTip(message)
                        item.setToolTip(0, message)
                        tree.scrollToItem(item)
                        break
                status.setText(f"Configuration validation failed at {location}: {first_error.get('msg')}")
                return

            try:
                canonical_json = _write_validated_config(active_config_path, updated_config)
            except OSError as exc:
                status.setText(f"Could not save {active_config_path.name}: {exc}")
                return

            editor_state["undo_stack"].append(before_edit)
            populate_editor(canonical_json)
            editor_state["original_json"] = canonical_json
            status.setText("Saved and rebuilding preview geometry…")
            result = self.show_or_update(
                updated_config,
                resolved_config_path=active_config_path,
                with_particles=active_with_particles,
                force=True,
            )
            status.setText(
                "Saved successfully." if result == 0 else "Saved, but preview refresh failed; see terminal output."
            )

        shortcut = QtGui.QShortcut(QtGui.QKeySequence.StandardKey.Save, tree)
        shortcut.activated.connect(apply_changes)
        undo_shortcut = QtGui.QShortcut(QtGui.QKeySequence.StandardKey.Undo, content)
        undo_shortcut.activated.connect(undo_changes)
        expand_button.clicked.connect(tree.expandAll)
        collapse_button.clicked.connect(tree.collapseAll)
        revert_button.clicked.connect(undo_changes)
        reload_button.clicked.connect(reload_from_disk)
        add_item_button.clicked.connect(lambda: edit_array("add"))
        duplicate_item_button.clicked.connect(lambda: edit_array("duplicate"))
        remove_item_button.clicked.connect(lambda: edit_array("remove"))
        move_up_button.clicked.connect(lambda: edit_array("up"))
        move_down_button.clicked.connect(lambda: edit_array("down"))
        tree.currentItemChanged.connect(lambda *_args: update_array_actions())
        filter_box.textChanged.connect(filter_items)
        def refresh_editor(
            current_config: SimulationConfig,
            current_config_path: Path,
            current_with_particles: bool,
        ) -> bool:
            """Replace the editor session when shell ``preview`` changes files."""
            if not confirm_discard_changes(
                "The editor contains unapplied changes. Continuing Preview will discard them. Continue?"
            ):
                status.setText("Preview canceled. Your unapplied changes are preserved.")
                return False
            editor_state["config_path"] = current_config_path
            editor_state["with_particles"] = current_with_particles
            editor_state["expanded_paths"] = set()
            filter_box.clear()
            populate_editor(dump_simulation_config_json(current_config, indent=2), reset_history=True)
            update_array_actions()
            return True

        refresh_editor(config, config_path, with_particles)
        self._json_editor = {
            "dock": dock,
            "tree": tree,
            "filter": filter_box,
            "status": status,
            "shortcut": shortcut,
            "undo_shortcut": undo_shortcut,
            "refresh": refresh_editor,
        }
        return True

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
            if configure_layout is not None:
                configure_layout(self.plotter)
            visualizer._configure_default_view(self.plotter, ndim)
            self.plotter.add_axes()
            self.plotter.show_grid(font_size=10, color=(0.25, 0.25, 0.25), bold=False)

            if vtp_dir:
                mode_label = "VTP geometry"
            elif visualizer._bounds_sim is not None:
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
    provider = os.getenv("SPHINXSIM_LLM_PROVIDER", "mock")
    print("SPHinXsim interactive shell")
    print(f"LLM provider: {provider}")
    print(
            "Commands: load FILE, generate DESCRIPTION FILE, "
            "update [--patch-mode] [--dry-run] [--strict true|false] INSTRUCTION, "
                    "validate, run, preview [--with-particles] [--screenshot FILE], explore QUESTION, exit"
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
            print("  run                             - Run simulation from loaded config")
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

        if cmd == "run":
            if config_path is None:
                print("No config loaded. Run 'load FILE' or 'generate' first.", file=sys.stderr)
                continue

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
                cfg, rc = _load_config(config_path)
                if rc != 0 or cfg is None:
                    print(f"❌ Validation failed for {config_path}", file=sys.stderr)
                    continue
                ndim = _config_spatial_dim(cfg)
                sph = load_sphinxsys_core_nd(ndim)

                shell_sim = sph.SPHSimulation(str(config_path))
                output_dir = PROJECT_ROOT / ".build-temp" / "test_simulation"
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
    opts="generate validate update run preview explore shell --help --version --generate-completion"
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
complete -c sphinxsim -f -a "shell" -d "Interactive shell workflow" """
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
    valid_commands = {"generate", "validate", "update", "run", "preview", "explore", "shell"}
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


def main(argv: list[str] | None = None) -> int:
    """Entry point for the ``sphinxsim`` CLI."""
    parser = _build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
