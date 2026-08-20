"""Subprocess acceptance tests for the durable AgentCAE CLI."""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
from uuid import uuid4


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def run_cli(*arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "sphinxsim", *arguments],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
        check=False,
    )


def test_agent_cli_start_status_cancel_across_processes(
    build_temp_path: Path,
) -> None:
    workspace = build_temp_path / f"agent_cli_processes_{uuid4().hex}"
    common = ("agent", "--workspace", str(workspace))

    started = run_cli(
        *common,
        "start",
        "Calibrate a 2D column-collapse runout extension to 0.2 m.",
        "--workflow-id",
        "cli_process_workflow",
        "--planner",
        "mock",
        "--replanner",
        "mock",
        "--generation-provider",
        "mock",
        "--json",
    )
    inspected = run_cli(*common, "status", "cli_process_workflow", "--json")
    cancelled = run_cli(*common, "cancel", "cli_process_workflow", "--json")

    assert started.returncode == 0, started.stderr
    assert inspected.returncode == 0, inspected.stderr
    assert cancelled.returncode == 0, cancelled.stderr
    start_status = json.loads(started.stdout)
    inspect_status = json.loads(inspected.stdout)
    cancel_status = json.loads(cancelled.stdout)
    assert start_status["awaiting_approval"] is True
    assert start_status["approval_request"]["candidate_value"] == 0.0
    assert inspect_status == start_status
    assert cancel_status["status"] == "cancelled"
    assert cancel_status["awaiting_approval"] is False
    assert (workspace / "checkpoints.sqlite").is_file()
    assert (workspace / "runtime" / "cli_process_workflow.json").is_file()


def test_agent_cli_reports_unknown_workflow_without_traceback(
    build_temp_path: Path,
) -> None:
    completed = run_cli(
        "agent",
        "--workspace",
        str(build_temp_path / "unknown_agent"),
        "status",
        "missing_workflow",
        "--json",
    )

    assert completed.returncode == 1
    assert "runtime settings not found" in completed.stderr
    assert "Traceback" not in completed.stderr
