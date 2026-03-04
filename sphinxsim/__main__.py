"""Entry point for running sphinxsim as a package.

Allows running: python3 -m sphinxsim <command> [args]
or:             python3 sphinxsim <command> [args]
"""

import sys

from sphinxsim.cli import main

if __name__ == "__main__":
    sys.exit(main())
