"""English command-line lifecycle for durable AgentCAE workflows."""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import json
import sys
import time
from pathlib import Path
from uuid import uuid4

from .agent import (
    AgentCAE,
    AgentRuntime,
    AgentRuntimeSettings,
    AgentServiceError,
    ExtractorPlanningContext,
    MockExtractorPlannerClient,
    ProviderExtractorPlannerClient,
)
from .llm import normalize_llm_provider
from .llm.structured_provider import create_structured_backend


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
    start.add_argument("--planner", default=None, help=argparse.SUPPRESS)
    start.add_argument(
        "--replanner",
        default=None,
        help=argparse.SUPPRESS,
    )
    start.add_argument(
        "--generation-provider",
        default=None,
        help=argparse.SUPPRESS,
    )
    start.add_argument("--timeout", type=float, default=300.0)
    start.add_argument("--no-approval", action="store_true")
    start.add_argument("--json", action="store_true")
    start.set_defaults(func=cmd_agent_start)

    for name, help_text, handler in (
        ("status", "Show persisted workflow status.", cmd_agent_status),
        (
            "approve",
            "Approve the complete Plan and run it to a terminal state.",
            cmd_agent_approve,
        ),
        ("resume", "Resume a non-approval checkpoint.", cmd_agent_resume),
        ("cancel", "Cancel the workflow.", cmd_agent_cancel),
    ):
        command = commands.add_parser(name, help=help_text)
        command.add_argument("workflow_id")
        command.add_argument("--json", action="store_true")
        command.set_defaults(func=handler)

    report = commands.add_parser("report", help="Build an auditable workflow report.")
    report.add_argument("workflow_id")
    report.add_argument("--json", action="store_true")
    report.set_defaults(func=cmd_agent_report)

    extractor = commands.add_parser(
        "extractor",
        help="Plan and run safe declarative VTP extractors.",
    )
    extractor_commands = extractor.add_subparsers(
        dest="extractor_command",
        required=True,
    )
    plan_extractor = extractor_commands.add_parser(
        "plan",
        help="Ask a structured planner for one data-only extractor definition.",
    )
    plan_extractor.add_argument("request")
    plan_extractor.add_argument(
        "--config",
        required=True,
        help="Validated case JSON used to derive body and dimension allow-lists.",
    )
    plan_extractor.add_argument("--provider", default=None, help=argparse.SUPPRESS)
    plan_extractor.add_argument(
        "--response-file",
        help="Mock provider JSON response used for deterministic evaluation.",
    )
    plan_extractor.add_argument("--json", action="store_true")
    plan_extractor.set_defaults(func=cmd_agent_extractor_plan)

    list_extractors = extractor_commands.add_parser(
        "list",
        help="List persisted safe extractor definitions.",
    )
    list_extractors.add_argument("--json", action="store_true")
    list_extractors.set_defaults(func=cmd_agent_extractor_list)

    analyze = extractor_commands.add_parser(
        "analyze",
        help="Apply a persisted extractor to existing SPHinXsim artifacts.",
    )
    analyze.add_argument("extractor_id")
    analyze.add_argument("config_path")
    analyze.add_argument("output_dir")
    analyze.add_argument("--json", action="store_true")
    analyze.set_defaults(func=cmd_agent_extractor_analyze)


def cmd_agent_start(args: argparse.Namespace) -> int:
    runtime = AgentRuntime(args.agent_project_root, args.workspace)
    workflow_id = args.workflow_id or f"agent_{uuid4().hex[:12]}"
    try:
        configured_provider = normalize_llm_provider()
        settings = AgentRuntimeSettings(
            workflow_id=workflow_id,
            planner_provider=args.planner or configured_provider,
            replanner_provider=args.replanner or configured_provider,
            generation_provider=args.generation_provider or configured_provider,
            require_approval=not args.no_approval,
            command_timeout=args.timeout,
        )
        runtime.save_settings(settings)
        status = runtime.create_service(settings, starting=True).start(
            args.prompt,
            workflow_id=workflow_id,
        )
        if not args.json:
            print(f"[Agent] Project root: {runtime.project_root}")
            print(f"[Agent] Workspace: {runtime.workspace}")
        _print_status(status, as_json=args.json)
        return _status_code(status.status.value)
    except (AgentServiceError, OSError, ValueError, RuntimeError) as exc:
        print(f"Agent start failed: {exc}", file=sys.stderr)
        return 1


def cmd_agent_status(args: argparse.Namespace) -> int:
    return _run_existing(args, "status", execution_enabled=False)


def cmd_agent_approve(args: argparse.Namespace) -> int:
    if not args.json:
        return _run_approve_with_progress(args)
    return _run_existing(args, "approve", execution_enabled=True)


def cmd_agent_resume(args: argparse.Namespace) -> int:
    return _run_existing(args, "resume", execution_enabled=True)


def cmd_agent_cancel(args: argparse.Namespace) -> int:
    return _run_existing(args, "cancel", execution_enabled=False)


def cmd_agent_report(args: argparse.Namespace) -> int:
    facade = AgentCAE(args.agent_project_root, args.workspace)
    try:
        report = facade.report(args.workflow_id)
        if args.json:
            print(report.model_dump_json(indent=2))
        else:
            print(f"Workflow ID: {report.workflow_id}")
            print(f"Status: {report.status.value}")
            print(f"Target: {report.target_metric} = {report.target_value:g}")
            print(f"Iterations: {len(report.iterations)}")
            if report.best_result is not None:
                print(f"Best parameter: {report.best_result.parameter_value:g}")
                print(f"Best metric: {report.best_result.metric_value:g}")
                print(f"Best absolute error: {report.best_result.absolute_error:g}")
        return _status_code(report.status.value)
    except (AgentServiceError, OSError, ValueError) as exc:
        print(f"Agent report failed: {exc}", file=sys.stderr)
        return 1


def cmd_agent_extractor_plan(args: argparse.Namespace) -> int:
    facade = AgentCAE(args.agent_project_root, args.workspace)
    try:
        provider = normalize_llm_provider(args.provider)
        if provider == "mock":
            if not args.response_file:
                raise AgentServiceError(
                    "mock extractor planning requires --response-file"
                )
            raw = json.loads(Path(args.response_file).read_text(encoding="utf-8"))
            if not isinstance(raw, dict):
                raise AgentServiceError("mock extractor response must be a JSON object")
            client = MockExtractorPlannerClient(raw)
        else:
            client = ProviderExtractorPlannerClient(
                create_structured_backend(provider)
            )
        context = ExtractorPlanningContext.from_case(args.config)
        spec = facade.plan_extractor(args.request, context, client)
        if args.json:
            print(spec.model_dump_json(indent=2))
        else:
            print(f"Extractor ID: {spec.extractor_id}")
            print(f"Body: {spec.body_name}")
            print(f"Outputs: {len(spec.outputs)}")
            print(f"Fingerprint: {spec.fingerprint}")
        return 0
    except (
        AgentServiceError,
        OSError,
        ValueError,
        RuntimeError,
        json.JSONDecodeError,
    ) as exc:
        print(f"Agent extractor plan failed: {exc}", file=sys.stderr)
        return 1


def cmd_agent_extractor_list(args: argparse.Namespace) -> int:
    facade = AgentCAE(args.agent_project_root, args.workspace)
    try:
        catalog = facade.load_extractors()
        if args.json:
            print(catalog.model_dump_json(indent=2))
        elif not catalog.extractors:
            print("No extractors are registered.")
        else:
            for spec in catalog.extractors:
                print(f"{spec.extractor_id}: {spec.description}")
        return 0
    except (OSError, ValueError) as exc:
        print(f"Agent extractor list failed: {exc}", file=sys.stderr)
        return 1


def cmd_agent_extractor_analyze(args: argparse.Namespace) -> int:
    facade = AgentCAE(args.agent_project_root, args.workspace)
    try:
        result = facade.analyze(
            args.extractor_id,
            args.config_path,
            args.output_dir,
        )
        if args.json:
            print(result.model_dump_json(indent=2))
        else:
            print(f"Extractor ID: {result.metric}")
            print(f"State file: {result.state_file}")
            print(f"Particles: {result.particle_count}")
            for name, value in sorted(result.values.items()):
                print(f"{name}: {value:g} {result.unit}")
        return 0
    except (OSError, ValueError) as exc:
        print(f"Agent extractor analyze failed: {exc}", file=sys.stderr)
        return 1


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
    except (AgentServiceError, OSError, ValueError, RuntimeError) as exc:
        print(f"Agent {operation} failed: {exc}", file=sys.stderr)
        return 1


def _run_approve_with_progress(args: argparse.Namespace) -> int:
    """Approve in a worker while printing durable workflow progress."""
    runtime = AgentRuntime(args.agent_project_root, args.workspace)
    try:
        settings = runtime.load_settings(args.workflow_id)
        service = runtime.create_service(
            settings,
            starting=False,
            execution_enabled=True,
        )
        print("[Agent] Plan approved. Execution started.", flush=True)
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(service.approve, args.workflow_id)
            last_key = None
            while not future.done():
                try:
                    status = service.status(args.workflow_id)
                    key = _progress_key(status)
                    if key != last_key:
                        _print_progress(status)
                        last_key = key
                except (AgentServiceError, OSError, ValueError, RuntimeError) as exc:
                    print(f"[Agent] Progress temporarily unavailable: {exc}", flush=True)
                time.sleep(1.0)
            status = future.result()
        _print_status(status, as_json=False)
        return _status_code(status.status.value)
    except (AgentServiceError, OSError, ValueError, RuntimeError) as exc:
        print(f"Agent approve failed: {exc}", file=sys.stderr)
        return 1


def _progress_key(status) -> tuple[object, ...]:
    return (
        status.status.value,
        status.phase,
        status.current_node,
        status.current_candidate_value,
        status.iteration_count,
        status.best_parameter_value,
        status.best_metric_value,
        status.best_absolute_error,
        status.error_message,
    )


def _print_progress(status) -> None:
    node = status.next_nodes[0] if status.next_nodes else "workflow_complete"
    activities = {
        "prepare_case": "preparing the isolated simulation case",
        "decide": "selecting the next calibration candidate",
        "execute_candidate": "running SPHinXsim and analyzing the VTP result",
        "replan": "evaluating recovery after a failed candidate",
        "workflow_complete": "workflow reached a terminal state",
    }
    print(
        "[Agent] "
        f"phase={status.phase}; "
        f"activity={activities.get(node, node)}; "
        f"simulations={status.iteration_count}/"
        f"{status.plan_summary.maximum_simulations}; "
        f"target={status.target_metric} {status.target_value:g} "
        f"{status.plan_summary.target_unit}",
        flush=True,
    )
    if status.best_metric_value is not None:
        print(
            "[Agent] "
            f"best_parameter={status.best_parameter_value:g} "
            f"{status.plan_summary.parameter_unit}; "
            f"best_metric={status.best_metric_value:.9g} "
            f"{status.plan_summary.target_unit}; "
            f"error={status.best_absolute_error:.9g} "
            f"{status.plan_summary.target_unit}",
            flush=True,
        )
    if status.current_candidate_value is not None:
        print(
            "[Agent] "
            f"current_candidate={status.current_candidate_value:g} "
            f"{status.plan_summary.parameter_unit}",
            flush=True,
        )
    if status.error_message:
        print(f"[Agent] message={status.error_message}", flush=True)


def _print_status(status, *, as_json: bool) -> None:
    if as_json:
        print(status.model_dump_json(indent=2))
        return
    print(f"Workflow ID: {status.workflow_id}")
    print(f"Status: {status.status.value}")
    print(f"Phase: {status.phase}")
    print("Plan:")
    print(f"  Simulation: {status.plan_summary.case_name}")
    print(f"  Body: {status.plan_summary.body_name}")
    bounds = status.plan_summary.parameter_bounds
    print(
        f"  Parameter: {status.plan_summary.parameter_name} "
        f"[{bounds[0]:g}, {bounds[1]:g}] "
        f"{status.plan_summary.parameter_unit}"
    )
    print(
        f"  Target: {status.plan_summary.target_metric} = "
        f"{status.plan_summary.target_value:g} "
        f"{status.plan_summary.target_unit}"
    )
    print(f"  Tolerance: {status.plan_summary.target_tolerance:g}")
    print(f"  Maximum simulations: {status.plan_summary.maximum_simulations}")
    print(f"Plan approved: {'yes' if status.plan_approved else 'no'}")
    print(f"Case prepared: {'yes' if status.case_prepared else 'no'}")
    print(f"Iterations: {status.iteration_count}")
    print(f"Replans: {status.replan_count}")
    if status.awaiting_approval and status.approval_request:
        print("Awaiting Plan approval: yes")
        print("Run the approve command to execute the complete Plan.")
    else:
        print("Awaiting Plan approval: no")
    if status.best_parameter_value is not None:
        print(f"Best parameter: {status.best_parameter_value:g}")
        print(f"Best metric: {status.best_metric_value:g}")
        print(f"Best absolute error: {status.best_absolute_error:g}")
    if status.error_message:
        print(f"Error: {status.error_message}")


def _status_code(status: str) -> int:
    return 1 if status == "failed" else 0
