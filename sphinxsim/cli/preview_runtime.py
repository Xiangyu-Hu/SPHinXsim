"""Preview runtime compatibility module for the CLI package.

This module provides the shell preview runtime under a dedicated file while the
legacy main module remains the compatibility entrypoint during the refactor.
"""

from .main import _ShellPreviewRuntime

__all__ = ["_ShellPreviewRuntime"]
