"""Run the SPHinXsim CLI package as a module."""

from . import main as cli_main

raise SystemExit(cli_main.main())
