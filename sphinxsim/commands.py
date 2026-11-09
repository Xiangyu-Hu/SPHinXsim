"""Native CLI registration for structured application operations."""
import asyncio
import json
import os
from pathlib import Path
import sys
import time


def read_json(filename):
    if filename == "-":
        raw = sys.stdin.buffer.read((1 << 20) + 1)
    else:
        with Path(filename).open("rb") as stream:
            raw = stream.read((1 << 20) + 1)
    if len(raw) > 1 << 20:
        raise ValueError("JSON input exceeds 1 MiB")
    return json.loads(raw)


def emit(value, args):
    text = json.dumps(value, allow_nan=False, indent=None if getattr(args, "json", False) else 2)
    print(text)


def execute(args):
    try:
        return _execute(args)
    except (ValueError, OSError, ImportError, KeyError) as exc:
        message = str(exc)
        if isinstance(exc, ModuleNotFoundError):
            message += "; install the corresponding sphinxsim[cad], [results], [mcp] or [visualization] extra"
        emit({"ok": False, "error": {"message": message}}, args)
        return 1
    except Exception as exc:
        # Schema/CAD libraries expose their own exception types. No traceback
        # or non-JSON library output belongs on a machine-readable stdout.
        emit({"ok": False, "error": {"message": str(exc)}}, args)
        return 1


def _execute(args):
    from .operations import Operations
    root = Path(getattr(args, "root", None) or Path.cwd()).resolve(strict=True)
    if args.command == "geometry" and not args.detach and not args.root:
        root = Path(args.output).resolve().parent
        root.mkdir(parents=True, exist_ok=True)
    operations = Operations(root, desktop_reference=getattr(args, "desktop_reference", None))
    command = args.command
    if command == "mcp":
        from .runtime.mcp import serve
        asyncio.run(serve(operations))
        return 0
    if command == "desktop" and args.action == "serve":
        from .desktop.session import serve
        return serve(root, args.config_file, args.reference_file, locale=args.locale, theme=args.theme)
    if command == "geometry":
        payload = {"recipe": read_json(args.recipe_file), "detach": args.detach}
        if not args.detach:
            payload["output"] = Path(args.output).resolve().relative_to(root).as_posix()
        result = operations.call("geometry", payload)
    elif command in ("run", "preview"):
        if command == "preview" and (args.screenshot or args.off_screen):
            raise ValueError("Structured job previews do not render screenshots; use native preview without --json/--detach or desktop screenshot")
        if args.output and not args.request:
            raise ValueError("--output is the work directory for --request; local jobs return their own immutable result references")
        if args.request:
            if args.config_file is not None or getattr(args, "with_particles", False) or args.submission_id:
                raise ValueError("Frozen requests specify their own inputs and kind; do not combine them with a config file, --with-particles or --submission-id")
            if any(getattr(args, key) != default for key, default in (("lease_seconds", 600), ("refresh_seconds", 10), ("wall_seconds", 3600))):
                raise ValueError("Frozen request policy belongs to its JSON and external supervisor")
            if args.detach or not args.output:
                raise ValueError("Frozen requests need --output and are supervised by their caller; do not use --detach")
            request = read_json(args.request)
            if request.get("kind", "run") not in (("run",) if command == "run" else ("geometry", "particles")):
                raise ValueError("Frozen execution kind differs from the CLI command")
            from .runtime.runner import execute as execute_frozen
            # A native binding may write directly to fd 1. Route its diagnostics
            # to stderr, preserving one JSON result on the public CLI's stdout.
            sys.stdout.flush()
            saved_stdout = os.dup(1)
            try:
                os.dup2(2, 1)
                execute_frozen(request, args.output, publish_json=getattr(args, "publish_json", None))
                sys.stdout.flush()
            finally:
                os.dup2(saved_stdout, 1)
                os.close(saved_stdout)
            pointer = Path(args.output) / "published.json"
            emit({"ok": True, "data": json.loads(pointer.read_text())}, args)
            return 0
        path = Path(args.config_file).resolve(strict=True)
        config_file = path.relative_to(root).as_posix()
        kind = "run" if command == "run" else "particles" if args.with_particles else "geometry"
        project = operations.call("project_inspect", {"config_file": config_file})
        result = operations.call("submit", {"config_file": config_file, "project": project, "kind": kind,
            **({"submission_id": args.submission_id} if args.submission_id else {}), "lease_seconds": args.lease_seconds, "refresh_seconds": args.refresh_seconds, "wall_seconds": args.wall_seconds})
        if not args.detach:
            from .runtime.jobs import TERMINAL
            reference = result["reference"]
            while result["state"] not in TERMINAL and result["state"] != "waiting":
                time.sleep(.1)
                result = operations.call("job_status", {"reference": reference})
            result = {**result, "reference": reference}
            if result["state"] in ("failed", "canceled", "interrupted"):
                emit({"ok": False, "data": result}, args)
                return 1
    elif command == "validate":
        result = operations.call("validate", {"config_file": Path(args.config_file).resolve(strict=True).relative_to(root).as_posix()})
    elif command == "compile":
        result = operations.call("compile", {"specification": read_json(args.specification_file)})
        if args.output:
            from .runtime.export_vtp import atomic_json
            output = Path(args.output).resolve()
            if output == Path(args.specification_file).resolve() or output.exists():
                raise ValueError("Compile output must be a new file; source configurations are preserved")
            atomic_json(output, result["configuration"])
    elif command == "capabilities":
        result = operations.call(command, {"include_schema": args.include_schema})
    elif command == "project":
        result = operations.call("project_inspect", {"config_file": args.config_file})
    elif command == "job":
        name = "results_inspect" if args.action == "results" else "job_" + args.action
        result = operations.call(name, {"reference": read_json(args.request_file)})
    elif command == "desktop":
        payload = read_json(args.request_file)
        if "operation" in payload and payload["operation"] != args.action:
            raise ValueError("JSON operation differs from the requested CLI action")
        result = operations.call("desktop", {**payload, "operation": args.action})
    else:
        raise ValueError("Unknown command")
    emit({"ok": True, "data": result}, args)
    return 0


def register(subparsers):
    """Extend the existing parser; never create a second command-line program."""
    def structured(parser):
        parser.add_argument("--json", action="store_true", help="Return a structured JSON result.")
        parser.add_argument("--root", default=None, help="Explicit workspace root (default: current directory).")
        parser.set_defaults(func=execute)

    for name in ("validate", "run", "preview"):
        parser = subparsers.choices[name]
        parser.add_argument("--json", action="store_true", help="Use the structured application operation.")
        parser.add_argument("--root", default=None, help="Explicit workspace root (default: current directory).")
        if name != "validate":
            parser.set_defaults(config_file=None)
            parser.add_argument("--request", help="Execute an explicit frozen JSON request under an external supervisor.")
            parser.add_argument("-o", "--output", help="Work directory containing the frozen project/ for --request.")
            parser.add_argument("--detach", action="store_true", help="Return a persistent job reference immediately (POSIX).")
            parser.add_argument("--submission-id", help="Reuse a UUID to retry the same frozen submission without creating another job.")
            parser.add_argument("--lease-seconds", type=int, default=600)
            parser.add_argument("--refresh-seconds", type=int, default=10)
            parser.add_argument("--wall-seconds", type=int, default=3600)
    capabilities = subparsers.add_parser("capabilities", help="Inspect schema and native routing evidence.")
    capabilities.add_argument("--include-schema", action="store_true")
    structured(capabilities)
    compile_parser = subparsers.add_parser("compile", help="Compile explicit quantities into a new native JSON configuration.")
    compile_parser.add_argument("specification_file")
    compile_parser.add_argument("-o", "--output")
    structured(compile_parser)
    geometry = subparsers.add_parser("geometry", help="Generate STL/STEP using a data-only CadQuery recipe.")
    geometry.add_argument("recipe_file")
    geometry.add_argument("-o", "--output", default="cad-output")
    geometry.add_argument("--detach", action="store_true")
    structured(geometry)
    project = subparsers.add_parser("project", help="Inspect a project's explicit file inventory before submission.")
    project.add_argument("config_file")
    structured(project)
    job = subparsers.add_parser("job", help="Operate on a JSON job reference returned by submission.")
    job.add_argument("action", choices=("status", "logs", "continue", "cancel", "results"))
    job.add_argument("request_file", help="JSON job reference file, or - for stdin.")
    structured(job)
    desktop = subparsers.add_parser("desktop", help="Serve or operate the native application; remote input is an external integration.")
    actions = desktop.add_subparsers(dest="action", required=True)
    serve = actions.add_parser("serve")
    serve.add_argument("config_file")
    serve.add_argument("--reference-file", required=True, help="New owner-only file receiving this instance's connection reference.")
    serve.add_argument("--locale", choices=("en", "zh"), default="en")
    serve.add_argument("--theme", choices=("light", "dark"), default="dark")
    structured(serve)
    for name in ("status", "apply", "save", "preview", "particles", "continue", "cancel", "result", "edit", "screenshot", "stop"):
        parser = actions.add_parser(name)
        parser.add_argument("request_file")
        parser.add_argument("--desktop-reference", required=True)
        structured(parser)
    mcp = subparsers.add_parser("mcp", help="Serve the same operations over MCP stdio.")
    mcp.add_argument("--desktop-reference", help="Owner-provided native desktop reference file.")
    mcp.add_argument("--root", required=True)
    mcp.set_defaults(func=execute)
