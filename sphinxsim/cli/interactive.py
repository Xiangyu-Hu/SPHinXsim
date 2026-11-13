"""Interactive shell helpers for the SPHinXsim CLI.

This keeps the shell parsing and completion logic separate from the top-level
CLI entrypoint while preserving compatibility for the legacy import path.
"""

from __future__ import annotations

import glob
import os
import shlex
import sys
from pathlib import Path

from .common import PROJECT_ROOT, _load_config

try:
    import readline
except ImportError:  # pragma: no cover
    readline = None


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
            [f"/{command}" for command in ("load", "generate", "update", "validate", "run", "preview", "continue-to-run", "replay", "explore", "help", "exit", "quit")]
            if text.startswith("/")
            else ("load", "generate", "update", "validate", "run", "preview", "continue-to-run", "replay", "explore", "help", "exit", "quit")
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
    config_path = Path(config_file)
    if config_path.is_absolute():
        return config_path
    cwd_path = Path.cwd() / config_path
    build_temp_path = PROJECT_ROOT / ".build-temp" / config_path
    return cwd_path if cwd_path.exists() else build_temp_path


def _parse_shell_ai_cli_style(text: str) -> list[str] | None:
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


__all__ = [
    "_configure_shell_readline",
    "_parse_shell_ai_cli_style",
    "_resolve_preview_config_path",
    "_shell_auto_validate",
    "_shell_completion_matches",
    "_shell_readline_completer",
    "_shell_resolve_config_path",
]
