"""Preview command-layer module for the SPHinXsim CLI.

This module exposes the preview entrypoint and shell preview runtime under the
package namespace while keeping the legacy implementation bridged to the
existing main module during the migration.
"""

from __future__ import annotations

from typing import Any

from .main import _ShellPreviewRuntime as _LegacyShellPreviewRuntime
from .main import cmd_preview as _legacy_cmd_preview


class _ShellPreviewRuntime(_LegacyShellPreviewRuntime):
    """Compatibility wrapper for the preview runtime in the new module layout."""

    pass


def cmd_preview(args: Any) -> int:
    """Preview command entrypoint exposed by the preview module."""
    return _legacy_cmd_preview(args)


__all__ = ["_ShellPreviewRuntime", "cmd_preview"]
