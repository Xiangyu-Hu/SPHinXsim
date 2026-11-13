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

import sys
from pathlib import Path
from typing import Any

from .common import PROJECT_ROOT, __version__, _load_config
from .explore import _schema_explore_context, cmd_explore
from .generate import _generate_with_physical_correction_feedback, cmd_generate
from .interactive import (
    _configure_shell_readline,
    _parse_shell_ai_cli_style,
    _resolve_preview_config_path,
    _shell_auto_validate,
    _shell_completion_matches,
    _shell_readline_completer,
    _shell_resolve_config_path,
)
from .parser import _build_parser
from .preview import cmd_preview
from .preview_runtime import _ShellPreviewRuntime
from .run import _config_spatial_dim, cmd_run
from .shell import cmd_shell
from .update import _print_update_summary, cmd_update
from .validate import cmd_validate

# Set up sys.path BEFORE any sphinxsim imports.
PROJECT_ROOT = Path(PROJECT_ROOT)
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "build-integrated"))
sys.path.insert(0, str(PROJECT_ROOT / "sphinxsim" / "bindings" / "native"))

# Compatibility alias for older direct imports of the legacy shell helper names.
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


def _set_windows_dark_title_bar(window: Any) -> None:
    """Compatibility helper kept for older preview runtime callers."""
    if sys.platform != "win32":
        return
    try:
        import ctypes

        hwnd = int(window.winId())
        enabled = ctypes.c_int(1)
        dwmapi = ctypes.windll.dwmapi
        for attribute in (20, 19):
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


__all__ = [
    "PROJECT_ROOT",
    "__version__",
    "_SHELL_COMMANDS",
    "_ShellPreviewRuntime",
    "_build_parser",
    "_config_spatial_dim",
    "_generate_with_physical_correction_feedback",
    "_load_config",
    "_parse_shell_ai_cli_style",
    "_print_update_summary",
    "_resolve_preview_config_path",
    "_schema_explore_context",
    "_set_windows_dark_title_bar",
    "_shell_auto_validate",
    "_shell_completion_matches",
    "_shell_readline_completer",
    "_shell_resolve_config_path",
    "cmd_explore",
    "cmd_generate",
    "cmd_preview",
    "cmd_run",
    "cmd_shell",
    "cmd_update",
    "cmd_validate",
    "main",
]


def main(argv: list[str] | None = None, *, publish_json=None) -> int:
    """Entry point for the ``sphinxsim`` CLI."""
    parser = _build_parser()
    args = parser.parse_args(argv)
    if publish_json is not None:
        if not callable(publish_json) or not getattr(args, "request", None):
            parser.error("The trusted publisher hook requires a frozen --request execution")
        args.publish_json = publish_json
    if args.command in ("run", "preview") and not getattr(args, "request", None) and getattr(args, "config_file", None) is None:
        args.config_file = "config.json"
    structured = any(getattr(args, key, None) for key in ("json", "detach", "request", "root", "output", "submission_id"))
    policy_changed = any(
        getattr(args, key, default) != default
        for key, default in (("lease_seconds", 600), ("refresh_seconds", 10), ("wall_seconds", 3600))
    )
    if args.command in ("validate", "run", "preview") and (structured or policy_changed):
        from sphinxsim.commands import execute
        return execute(args)
    return args.func(args)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
