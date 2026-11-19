"""Run an editor snapshot through the native MCP job service."""
import argparse
import os


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("project_root")
    parser.add_argument("case")
    parser.add_argument("output")
    args = parser.parse_args()
    os.chdir(args.project_root)
    from sphinxsim.agent.mcp_execution import MCPExecutionClient
    client = MCPExecutionClient(cwd=args.project_root,
        root=os.path.commonpath([args.project_root, args.output]),
        control_dir=args.output, default_timeout=3600)
    from pathlib import Path
    staged = client.prepare_case(args.case, Path(args.output) / "project" / "case.json")
    result = client.run(staged, output_dir=args.output)
    return 0 if result.succeeded else 1


if __name__ == "__main__":
    raise SystemExit(main())
