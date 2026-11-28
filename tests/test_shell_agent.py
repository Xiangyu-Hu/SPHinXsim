"""Tests for CLI/shell isomorphism, agent promote, and in-process ask loop."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from sphinxsim.cli import cmd_ask, cmd_shell, main


class _ScriptedBackend:
    """Deterministic structured backend driving MCPToolConversation in tests."""

    def __init__(self, turns: list[list[dict]]) -> None:
        self._turns = [iter(turn) for turn in turns]
        self._turn_idx = -1
        self.payloads: list[dict] = []

    def complete_json(self, *, system_prompt: str, payload: dict) -> dict:
        if not payload["observations"] or self._turn_idx < 0 or self._current_exhausted:
            self._turn_idx += 1
            self._current_exhausted = False
        self.payloads.append(payload)
        choice = next(self._turns[self._turn_idx])
        if choice["tool"] is None:
            self._current_exhausted = True
        return choice


def test_shell_isomorphic_commands_and_results(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    out_dir = tmp_path / "out_run"
    vtp_dir = out_dir / "output"
    vtp_dir.mkdir(parents=True)
    (vtp_dir / "GranularBody_0001.vtp").write_text(
        """<VTKFile xmlns="urn:vtk"><PolyData>
        <FieldData><DataArray Name="TimeValue">0.05</DataArray></FieldData>
        <Piece Name="GranularBody" NumberOfPoints="2"><Points>
        <DataArray Name="Position" NumberOfComponents="3" format="ascii">0.1 0.1 0 0.35 0.05 0</DataArray>
        </Points></Piece></PolyData></VTKFile>""",
        encoding="utf-8",
    )

    inputs = [
        "templates",
        "init 2d/column_collapse local_case.json",
        "capabilities --json",
        "project --json",
        f"results --output-dir {out_dir} --json",
        "exit",
    ]
    with patch("builtins.input", side_effect=inputs):
        assert main(["shell"]) == 0

    assert (tmp_path / "local_case.json").is_file()
    stdout = capsys.readouterr().out
    assert "Loaded config:" in stdout
    assert '"GranularBody"' in stdout
    assert '"solver_frames": 1' in stdout


@pytest.mark.skipif(
    __import__("os").name != "posix",
    reason="Detached local supervisor test requires POSIX",
)
def test_shell_detached_run_saves_default_job_reference(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    inputs = [
        "init 2d/column_collapse detached_case.json",
        "run --detach",
        "job status --json",
        "job cancel --json",
        "exit",
    ]
    with patch("builtins.input", side_effect=inputs):
        assert main(["shell"]) == 0

    ref_file = tmp_path / ".sphinxsim" / "last-job.json"
    assert ref_file.is_file()
    ref = json.loads(ref_file.read_text(encoding="utf-8"))
    assert set(ref) == {"job_id", "request_sha256"}
    out = capsys.readouterr().out
    assert "Detached job submitted:" in out


def test_agent_promote_writes_best_candidate_and_backup(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    ws = tmp_path / "agent_ws"
    wf_dir = ws / "store" / "workflows" / "wf_demo"
    cases_dir = wf_dir / "cases"
    cases_dir.mkdir(parents=True)

    target_case = tmp_path / "active_case.json"
    assert main(["init", "2d/column_collapse", str(target_case)]) == 0
    capsys.readouterr()

    cand_1 = cases_dir / "cand_1.json"
    cand_2 = cases_dir / "cand_2.json"
    base_data = json.loads(target_case.read_text(encoding="utf-8"))

    d1 = json.loads(json.dumps(base_data))
    d1["continuum_bodies"][0]["material"]["friction_angle"] = 0.35
    cand_1.write_text(json.dumps(d1, indent=2), encoding="utf-8")

    d2 = json.loads(json.dumps(base_data))
    d2["continuum_bodies"][0]["material"]["friction_angle"] = 0.52
    cand_2.write_text(json.dumps(d2, indent=2), encoding="utf-8")

    (wf_dir / "workflow.json").write_text(
        json.dumps(
            {
                "workflow_id": "wf_demo",
                "status": "completed",
                "iterations": [
                    {
                        "iteration": 1,
                        "parameter_value": 20.0,
                        "metric_value": 0.28,
                        "absolute_error": 0.08,
                        "case_path": str(cand_1),
                    },
                    {
                        "iteration": 2,
                        "parameter_value": 29.8,
                        "metric_value": 0.201,
                        "absolute_error": 0.001,
                        "case_path": str(cand_2),
                    },
                ],
            }
        ),
        encoding="utf-8",
    )

    # Test inside shell with default active config_path
    inputs = [
        f"load {target_case}",
        f"agent --workspace {ws} promote wf_demo",
        "exit",
    ]
    with patch("builtins.input", side_effect=inputs):
        assert main(["shell"]) == 0

    promoted = json.loads(target_case.read_text(encoding="utf-8"))
    assert promoted["continuum_bodies"][0]["material"]["friction_angle"] == pytest.approx(0.52)
    backup = target_case.with_name("active_case.json.bak")
    assert backup.is_file()
    orig = json.loads(backup.read_text(encoding="utf-8"))
    assert orig["continuum_bodies"][0]["material"]["friction_angle"] != pytest.approx(0.52)


def test_cli_ask_and_shell_ask_multi_turn_in_process(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("SPHINXSIM_LLM_PROVIDER", "mock")

    # 1. Explicit /mcp tool invocation via standalone `sphinxsim ask --json`
    assert main(["ask", "/mcp templates {}", "--root", str(tmp_path), "--json"]) == 0
    mcp_res = json.loads(capsys.readouterr().out)
    assert mcp_res["ok"] is True
    assert "2d/column_collapse" in mcp_res["data"]["answer"]

    # 2. Multi-turn scripted agent inside `sphinxsim shell`:
    #    Turn 1 initializes a case from template (auto-loading it in shell);
    #    Turn 2 patches solver_parameters.end_time via config_patch (writing .bak) and validates!
    backend = _ScriptedBackend(
        [
            [
                {"tool": "template_init", "arguments": {"template": "2d/column_collapse", "output": "agent_case.json"}},
                {"tool": None, "arguments": {}},
            ],
            [
                {
                    "tool": "config_patch",
                    "arguments": {
                        "config_file": "agent_case.json",
                        "patch": {
                            "operations": [
                                {"op": "set_value", "path": "solver_parameters.end_time", "value": 0.15}
                            ]
                        },
                    },
                },
                {"tool": "validate", "arguments": {"config_file": "agent_case.json"}},
                {"tool": None, "arguments": {}},
            ],
        ]
    )

    inputs = [
        "ask Initialize a 2D column collapse case in agent_case.json",
        "/ask Set end_time to 0.15 seconds and validate it",
        "validate",
        "exit",
    ]
    with patch("builtins.input", side_effect=inputs):
        rc = cmd_shell(argparse.Namespace(config_file=None, root=str(tmp_path), _backend=backend))
    assert rc == 0

    case_file = tmp_path / "agent_case.json"
    backup_file = tmp_path / "agent_case.json.bak"
    assert case_file.is_file()
    assert backup_file.is_file()
    updated = json.loads(case_file.read_text(encoding="utf-8"))
    assert updated["solver_parameters"]["end_time"] == pytest.approx(0.15)

    # Turn 2 received Turn 1's prior_observations and active_config_file context
    turn2_first_payload = backend.payloads[2]
    assert "agent_case.json" in turn2_first_payload["context"]
    assert any(obs["tool"] == "template_init" for obs in turn2_first_payload["observations"])

    out = capsys.readouterr().out
    assert "[tool] template_init" in out
    assert "[tool] config_patch" in out
    assert "Reloaded and validated config:" in out
