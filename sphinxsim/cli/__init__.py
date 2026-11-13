"""CLI package for SPHinXsim.

The legacy import path `sphinxsim.cli` remains available as a package while the
command layer is gradually split into focused modules under `sphinxsim/cli/`.
"""

from __future__ import annotations

from importlib import import_module
from pathlib import Path

from sphinxsim.bindings.loader import load_sphinxsys_core_nd
from sphinxsim.llm import get_llm

from .preview_runtime import _ShellPreviewRuntime

for _module_name in (
    ".generate",
    ".update",
    ".validate",
    ".run",
    ".explore",
    ".shell",
    ".preview",
    ".preview_runtime",
    ".main",
):
    _module = import_module(_module_name, __name__)
    for _name in dir(_module):
        if not _name.startswith("__"):
            globals()[_name] = getattr(_module, _name)

__all__ = [name for name in globals() if not name.startswith("__")]
