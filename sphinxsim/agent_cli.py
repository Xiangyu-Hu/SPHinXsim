"""English command-line lifecycle for durable AgentCAE workflows."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from uuid import uuid4

from .agent import AgentRuntime, AgentRuntimeSettings, AgentServiceError


def configure_agent_parser(subparsers, project_root: Path) -> None:
    agent = subparsers.add_parser(
        "agent",
        help="Manage durable AgentCAE calibration workflows.",
    )
    agent.add_argument(
        "--workspace",
        default=str(project_root / ".build-temp" / "agent"),
        help="Persistent Agent workspace containing state and SQLite checkpoints.",
    )
    agent.set_defaults(agent_project_root=str(project_root))
    commands = agent.add_subparsers(dest="agent_command", required=True)

    start = commands.add_parser("start", help="Plan and start a new Agent workflow.")
    start.add_argument("prompt", help="English simulation objective.")
    start.add_argument("--workflow-id", default=None)
    start.add_argument("--planner", choices=("mock", "nvidia"), default="mock")
    start.add_argument(
        "--replanner",
        choices=("none", "mock", "nvidia"),
        default="mock",
    )
    start.add_argument(
        "--generation-provider",
        choices=("mock", "nvidia"),
        default="mock",
    )
    start.add_argument("--timeout", type=float, default=300.0)
    start.add_argument("--no-approval", action="store_true")
    start.add_argument("--json", action="store_true")
    start.set_defaults(func=cmd_agent_start)

    for name, help_text, handler in (
        ("status", "Show persisted workflow status.", cmd_agent_status),
        ("approve", "Approve the pending solver execution.", cmd_agent_approve),
        ("resume", "Resume a non-approval checkpoint.", cmd_agent_resume),
        ("cancel", "Cancel the workflow.", cmd_agent_cancel),
    ):
        command = commands.add_parser(name, help=help_text)
        command.add_argument("workflow_id")
        command.add_argument("--json", action="store_true")
        command.set_defaults(func=handler)


def cmd_agent_start(args: argparse.Namespace) -> int:
    runtime = AgentRuntime(args.agent_project_root, args.workspace)
    workflow_id = args.workflow_id or f"agent_{uuid4().hex[:12]}"
    settings = AgentRuntimeSettings(
        workflow_id=workflow_id,
        planner_provider=args.planner,
        replanner_provider=args.replanner,
        generation_provider=args.generation_provider,
        require_approval=not args.no_approval,
        command_timeout=args.timeout,
    )
    try:
        runtime.save_settings(settings)
        status = runtime.create_service(settings, starting=True).start(
            args.prompt,
            workflow_id=workflow_id,
        )
        _print_status(status, as_json=args.json)
        return _status_code(status.status.value)
    except (AgentServiceError, OSError, ValueError) as exc:
        print(f"Agent start failed: {exc}", file=sys.stderr)
        return 1


def cmd_agent_status(args: argparse.Namespace) -> int:
    return _run_existing(args, "status", execution_enabled=False)


def cmd_agent_approve(args: argparse.Namespace) -> int:
    return _run_existing(args, "approve", execution_enabled=True)


def cmd_agent_resume(args: argparse.Namespace) -> int:
    return _run_existing(args, "resume", execution_enabled=True)


def cmd_agent_cancel(args: argparse.Namespace) -> int:
    return _run_existing(args, "cancel", execution_enabled=False)


def _run_existing(
    args: argparse.Namespace,
    operation: str,
    *,
    execution_enabled: bool,
) -> int:
    runtime = AgentRuntime(args.agent_project_root, args.workspace)
    try:
        settings = runtime.load_settings(args.workflow_id)
        service = runtime.create_service(
            settings,
            starting=False,
            execution_enabled=execution_enabled,
        )
        status = getattr(service, operation)(args.workflow_id)
        _print_status(status, as_json=args.json)
        return _status_code(status.status.value)
    except (AgentServiceError, OSError, ValueError) as exc:
        print(f"Agent {operation} failed: {exc}", file=sys.stderr)
        return 1


def _print_status(status, *, as_json: bool) -> None:
    if as_json:
        print(status.model_dump_json(indent=2))
        return
    print(f"Workflow ID: {status.workflow_id}")
    print(f"Status: {status.status.value}")
    print(f"Target: {status.target_metric} = {status.target_value:g}")
    print(f"Iterations: {status.iteration_count}")
    print(f"Replans: {status.replan_count}")
    if status.awaiting_approval and status.approval_request:
        print("Awaiting approval: yes")
        print(f"Candidate: {status.approval_request.get('candidate_value')}")
    else:
        print("Awaiting approval: no")
    if status.best_parameter_value is not None:
        print(f"Best parameter: {status.best_parameter_value:g}")
        print(f"Best metric: {status.best_metric_value:g}")
        print(f"Best absolute error: {status.best_absolute_error:g}")
    if status.error_message:
        print(f"Error: {status.error_message}")


def _status_code(status: str) -> int:
    return 1 if status == "failed" else 0
