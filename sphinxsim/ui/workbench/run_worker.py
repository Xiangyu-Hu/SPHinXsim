"""Reuse the CLI run path while isolating native constructor output directories."""
import argparse
import os


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("project_root")
    parser.add_argument("case")
    parser.add_argument("output")
    args = parser.parse_args()
    # Resolve the existing CLI's project context before moving to the isolated
    # run directory (which may live outside the source tree).
    os.chdir(args.project_root)
    from sphinxsim.cli import main as run_cli
    os.chdir(args.output)
    return run_cli(["run", args.case, "--output-dir", args.output])


if __name__ == "__main__":
    raise SystemExit(main())
