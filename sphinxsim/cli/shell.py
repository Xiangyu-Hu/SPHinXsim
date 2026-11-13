"""Interactive shell command layer for the SPHinXsim CLI."""

from __future__ import annotations

import glob
import os
import shlex
import subprocess
import sys
import time
from pathlib import Path

from .common import PROJECT_ROOT, _load_config
from .explore import cmd_explore
from .generate import _generate_with_physical_correction_feedback
from .preview import _ShellPreviewRuntime, cmd_preview
from .run import cmd_run
from .update import cmd_update
from .validate import cmd_validate

try:
    import readline
except ImportError:  # pragma: no cover
    readline = None


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


def _shell_resolve_config_path(config_file: str) -> Path:
    path = Path(config_file)
    if path.is_absolute():
        return path

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


def _parse_shell_ai_cli_style(text: str) -> list[str] | None:
    """Translate slash-prefixed shell commands to the shell command parser."""
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


def cmd_shell(args: argparse.Namespace) -> int:
    """Interactive shell for load/generate/update/validate/run workflow."""
    import argparse

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
            file_arg = parts[-1]
            description = " ".join(parts[1:-1]).strip()
            if not description or not file_arg:
                print("Usage: generate DESCRIPTION FILE", file=sys.stderr)
                continue
            config_path = _shell_resolve_config_path(file_arg)

            import sphinxsim.cli as cli_pkg
            from sphinxsim.config.schemas import SimulationConfig
            from sphinxsim.llm.common import dump_simulation_config_json

            get_llm = getattr(cli_pkg, "get_llm", None)
            if get_llm is None:
                from sphinxsim.llm import get_llm as get_llm
            llm = get_llm()
            try:
                config = llm.generate(description)
                if isinstance(config, SimulationConfig):
                    config_path.parent.mkdir(parents=True, exist_ok=True)
                    config_path.write_text(dump_simulation_config_json(config, indent=2))
                    print(f"✅ Config generated and written to {config_path}")
                    shell_sim = None
                    _shell_auto_validate(config_path)
                else:
                    print("Error generating config: invalid config object returned.", file=sys.stderr)
                    config_path = None
            except (ValueError, Exception) as exc:
                print(f"Error generating config: {exc}", file=sys.stderr)
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
            cfg, rc = _load_config(config_path)
            if rc != 0 or cfg is None:
                print(f"❌ Validation failed for {config_path}", file=sys.stderr)
                continue
            print(f"✅ Reloaded and validated config: {config_path}")
            _ = cmd_validate(argparse.Namespace(config_file=str(config_path)))
            continue

        if cmd == "preview":
            if config_path is None:
                print("No config loaded. Run 'load FILE' or 'generate' first.", file=sys.stderr)
                continue
            with_particles = "--with-particles" in parts
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
                print("Usage: continue-to-run [--refresh-interval SECONDS]", file=sys.stderr)
                continue
            if refresh_interval <= 0.0:
                print("Refresh interval must be greater than zero.", file=sys.stderr)
                continue
            _ = preview_runtime.continue_to_run(refresh_interval)
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

            if preview_runtime.plotter is not None:
                print("ℹ️ Running simulation in isolated subprocess (persistent preview is active).", flush=True)
                try:
                    env = os.environ.copy()
                    env.setdefault("PYTHONUNBUFFERED", "1")
                    process = subprocess.Popen(
                        [sys.executable, "-m", "sphinxsim", "run", str(config_path)],
                        cwd=str(config_path.parent),
                        env=env,
                    )
                    while True:
                        preview_runtime.pump_ui_events()
                        return_code = process.poll()
                        if return_code is not None:
                            break
                        import time
                        time.sleep(0.05)
                    if return_code != 0:
                        print(f"❌ Run failed with exit code {return_code}", file=sys.stderr)
                except Exception as exc:
                    print(f"❌ Run failed: {exc}", file=sys.stderr)
                continue

            try:
                import sphinxsim.cli as cli_pkg

                load_sphinxsys_core_nd = getattr(cli_pkg, "load_sphinxsys_core_nd", None)
                if load_sphinxsys_core_nd is None:
                    from sphinxsim.bindings.loader import load_sphinxsys_core_nd as load_sphinxsys_core_nd

                ndim = len(cfg.geometries.system_domain.lower_bound) if cfg.geometries.system_domain else 3
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


__all__ = [
    "_SHELL_COMMANDS",
    "_configure_shell_readline",
    "_parse_shell_ai_cli_style",
    "_resolve_preview_config_path",
    "_shell_auto_validate",
    "_shell_completion_matches",
    "_shell_readline_completer",
    "_shell_resolve_config_path",
    "cmd_shell",
]
