"""Public JSON CLI; the CLI and MCP delegate to the exact same service."""
import argparse
import json
from pathlib import Path
import sys

from .service import Service, TOOLS


def main():
    parser = argparse.ArgumentParser(description="SPHinXsim agent integration (native package required)")
    parser.add_argument("--root", required=True, help="Owner-selected workspace root")
    parser.add_argument("--desktop", action="store_true", help="Allow access to the local native desktop")
    parser.add_argument("--automation-scope", help="Owner-selected CLI automation session; reuse for Stop, choose a new value after Stop")
    commands = parser.add_subparsers(dest="operation", required=True)
    commands.add_parser("tools", help="Print public input schemas")
    commands.add_parser("mcp", help="Serve MCP over stdio; stdout is reserved for JSON-RPC")
    for name, (description, _) in TOOLS.items():
        command = commands.add_parser(name.replace("_", "-"), help=description)
        command.add_argument("--input", help="JSON arguments file, or - for stdin; defaults to {}")
    args = parser.parse_args()
    if args.operation == "tools":
        print(json.dumps({name: {"description": description, "inputSchema": schema} for name, (description, schema) in TOOLS.items()}))
        return
    if args.operation == "mcp":
        import asyncio
        from .mcp import serve
        asyncio.run(serve(Service(args.root, desktop=args.desktop)))
        return
    try:
        if args.input == "-":
            raw = sys.stdin.buffer.read((1 << 20) + 1)
        elif args.input:
            with Path(args.input).open("rb") as stream:
                raw = stream.read((1 << 20) + 1)
        else:
            raw = b"{}"
        if len(raw) > 1 << 20:
            raise ValueError("Input exceeds 1 MiB")
        if args.operation == "desktop" and not args.automation_scope:
            raise ValueError("CLI desktop operations require --automation-scope; reuse the same value for Stop")
        result = Service(args.root, desktop=args.desktop, automation_scope=args.automation_scope).call(args.operation.replace("-", "_"), json.loads(raw))
        print(json.dumps({"ok": True, "data": result}, allow_nan=False))
    except Exception as exc:
        print(json.dumps({"ok": False, "error": {"code": "operation_failed", "message": str(exc)[:1000]}}))
        raise SystemExit(1)


if __name__ == "__main__":
    main()
