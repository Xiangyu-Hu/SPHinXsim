"""CLI package for SPHinXsim.

The legacy import path `sphinxsim.cli` remains available as a package, while the
actual command implementation lives under `sphinxsim/cli/main.py`.
"""

from importlib import import_module

_main_module = import_module(".main", __name__)
for _name in dir(_main_module):
    if not _name.startswith("__"):
        globals()[_name] = getattr(_main_module, _name)

__all__ = [name for name in globals() if not name.startswith("__")]
