"""Subprocess acceptance tests for the durable AgentCAE CLI."""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
from uuid import uuid4

from sphinxsim.agent import (
    AgentCAE,
    AgentCAEAgentService,
    AgentRuntimeSettings,
    MockColumnCollapseCalibrationPlanner,
)
from tests.test_deterministic_calibration_workflow import DeterministicArtifactAdapter


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
    monkeypatch,
) -> None:
    monkeypatch.setenv("SPHINXSIM_LLM_PROVIDER", "mock")
    workspace = build_temp_path / f"agent_cli_processes_{uuid4().hex}"
    common = ("agent", "--workspace", str(workspace))

    started = run_cli(
        *common,
        "start",
        "Calibrate a 2D column-collapse runout extension to 0.2 m.",
        "--workflow-id",
        "cli_process_workflow",
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
    assert start_status["phase"] == "awaiting_plan_approval"
    assert start_status["plan_approved"] is False
    assert start_status["case_prepared"] is False
    assert start_status["approval_request"]["action"] == (
        "approve_calibration_plan"
    )
    assert start_status["plan_summary"]["target_value"] == 0.2
    assert start_status["plan_summary"]["parameter_bounds"] == [0.0, 40.0]
    assert inspect_status == start_status
    assert cancel_status["status"] == "cancelled"
    assert cancel_status["awaiting_approval"] is False
    assert (workspace / "checkpoints.sqlite").is_file()
    manifest = workspace / "runtime" / "cli_process_workflow.json"
    assert manifest.is_file()
    settings = json.loads(manifest.read_text(encoding="utf-8"))
    assert settings["planner_provider"] == "mock"
    assert settings["replanner_provider"] == "mock"
    assert settings["generation_provider"] == "mock"


def test_agent_cli_prints_readable_plan_before_approval(
    build_temp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("SPHINXSIM_LLM_PROVIDER", "mock")
    workspace = build_temp_path / f"agent_cli_plan_{uuid4().hex}"

    completed = run_cli(
        "agent",
        "--workspace",
        str(workspace),
        "start",
        "Calibrate a 2D column-collapse runout extension to 0.2 m.",
        "--workflow-id",
        "cli_readable_plan",
    )

    assert completed.returncode == 0, completed.stderr
    assert "Phase: awaiting_plan_approval" in completed.stdout
    assert "Plan:" in completed.stdout
    assert "Simulation: column_collapse" in completed.stdout
    assert "Parameter: friction_angle_degrees [0, 40] degree" in completed.stdout
    assert "Target: runout_extension = 0.2 m" in completed.stdout
    assert "Maximum simulations: 8" in completed.stdout
    assert "Plan approved: no" in completed.stdout
    assert "Case prepared: no" in completed.stdout


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


def test_agent_cli_builds_json_report_from_persisted_result(
    build_temp_path: Path,
) -> None:
    workspace = build_temp_path / f"agent_report_{uuid4().hex}"
    facade = AgentCAE(PROJECT_ROOT, workspace)
    facade.runtime.save_settings(
        AgentRuntimeSettings(workflow_id="cli_report", require_approval=False)
    )
    service = AgentCAEAgentService(
        facade.runtime.store,
        facade.runtime.database_path,
        MockColumnCollapseCalibrationPlanner(),
        DeterministicArtifactAdapter(),
        require_approval=False,
    )
    service.start("Reach 0.2 m runout extension.", workflow_id="cli_report")

    completed = run_cli(
        "agent",
        "--workspace",
        str(workspace),
        "report",
        "cli_report",
        "--json",
    )

    assert completed.returncode == 0, completed.stderr
    report = json.loads(completed.stdout)
    assert report["status"] == "converged"
    assert report["target_value"] == 0.2
    assert report["best_result"]["absolute_error"] == 0.0


def test_agent_cli_plans_lists_and_runs_declarative_extractor(
    build_temp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("SPHINXSIM_LLM_PROVIDER", "mock")
    workspace = build_temp_path / f"agent_extractor_{uuid4().hex}"
    response_path = build_temp_path / f"extractor_response_{uuid4().hex}.json"
    response_path.write_text(
        json.dumps(
            {
                "extractor_id": "final_height",
                "description": "Extract final height.",
                "body_name": "GranularBody",
                "outputs": [
                    {
                        "output_name": "height_y",
                        "component_index": 1,
                        "reduction": "maximum",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    run = build_temp_path / f"extractor_run_{uuid4().hex}"
    output = run / "output"
    output.mkdir(parents=True)
    (output / "GranularBody_0000001000.vtp").write_text(
        '<VTKFile type="PolyData"><PolyData><FieldData><DataArray '
        'Name="TimeValue" format="ascii">1.0</DataArray></FieldData><Piece '
        'NumberOfPoints="2"><Points><DataArray Name="Position" '
        'NumberOfComponents="2" format="ascii">0.0 0.1 0.4 0.6'
        '</DataArray></Points></Piece></PolyData></VTKFile>',
        encoding="utf-8",
    )
    common = ("agent", "--workspace", str(workspace), "extractor")

    planned = run_cli(
        *common,
        "plan",
        "Measure final height.",
        "--config",
        str(
            PROJECT_ROOT
            / "tests"
            / "test_simulation"
            / "test_2d_simulation"
            / "data"
            / "column_collapse.json"
        ),
        "--response-file",
        str(response_path),
        "--json",
    )
    listed = run_cli(*common, "list", "--json")
    analyzed = run_cli(
        *common,
        "analyze",
        "final_height",
        "unused.json",
        str(run),
        "--json",
    )

    assert planned.returncode == 0, planned.stderr
    assert listed.returncode == 0, listed.stderr
    assert analyzed.returncode == 0, analyzed.stderr
    assert json.loads(planned.stdout)["extractor_id"] == "final_height"
    assert json.loads(listed.stdout)["extractors"][0]["extractor_id"] == "final_height"
    assert json.loads(analyzed.stdout)["values"] == {"height_y": 0.6}


def test_agent_cli_hides_legacy_provider_overrides_from_user_help() -> None:
    start_help = run_cli("agent", "start", "--help")
    extractor_help = run_cli("agent", "extractor", "plan", "--help")

    assert start_help.returncode == 0
    assert extractor_help.returncode == 0
    assert "--planner" not in start_help.stdout
    assert "--replanner" not in start_help.stdout
    assert "--generation-provider" not in start_help.stdout
    assert "--provider" not in extractor_help.stdout
