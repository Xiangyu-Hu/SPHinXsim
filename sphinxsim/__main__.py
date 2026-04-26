"""Entry point for running sphinxsim as a package.

Allows running: python3 -m sphinxsim <command> [args]
or:             python3 sphinxsim <command> [args]
"""

import os
import sys

# Set up sys.path BEFORE any sphinxsim imports
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

from sphinxsim.cli import main

if __name__ == "__main__":
    sys.exit(main())
