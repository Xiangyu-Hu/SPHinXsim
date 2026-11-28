"""Offline adversarial and hardening tests for the CLI-first agentic shell."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import time
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from sphinxsim.cli import _ShellPreviewRuntime, cmd_ask, cmd_shell, main
from sphinxsim.llm.chat_attachments import UnsupportedAttachmentError, message_attachments
from sphinxsim.llm.mcp_tools import (
    InProcessOperationsClient,
    MCPToolConversation,
    _sanitize_observation_data,
)
from sphinxsim.llm.structured_provider import (
    ExistingLLMStructuredBackend,
    StructuredProviderError,
)
from sphinxsim.runtime.export_vtp import atomic_json


class _SequenceBackend:
    """Scripted backend supporting exceptions and arbitrary dict responses."""

    def __init__(self, steps: list[object]) -> None:
        self._steps = iter(steps)
        self.payloads: list[dict] = []

    def complete_json(self, *, system_prompt: str, payload: dict) -> dict:
        self.payloads.append(payload)
        item = next(self._steps)
        if isinstance(item, Exception):
            raise item
        return item  # type: ignore[return-value]


def test_dry_run_enforces_read_only_in_code(tmp_path, capsys):
    case = tmp_path / "case.json"
    assert main(["init", "2d/column_collapse", str(case)]) == 0
    capsys.readouterr()
    original_bytes = case.read_bytes()

    # Adversarial model ignores prompt hint: omits dry_run=True on config_patch
    # and attempts template_init mutation!
    backend = _SequenceBackend(
        [
            {
                "tool": "config_patch",
                "arguments": {
                    "config_file": "case.json",
                    "patch": {
                        "operations": [
                            {"op": "set_value", "path": "solver_parameters.end_time", "value": 0.42}
                        ]
                    },
                },
            },
            {
                "tool": "template_init",
                "arguments": {"template": "2d/dambreak", "output": "unauthorized.json"},
            },
            {"tool": None, "arguments": {}},
        ]
    )

    rc = cmd_ask(
        argparse.Namespace(
            prompt="Preview changing end_time to 0.42",
            config=str(case),
            output_dir=None,
            root=str(tmp_path),
            dry_run=True,
            json=True,
            _backend=backend,
        )
    )
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["ok"] is True
    obs = out["data"]["observations"]

    # 1. config_patch was forced into dry_run=True and computed diff without touching disk
    patch_res = json.loads(obs[0]["result"])
    assert patch_res["ok"] is True
    assert patch_res["data"]["dry_run"] is True
    assert patch_res["data"]["applied"] is False
    assert "0.42" in patch_res["data"]["diff"]
    assert case.read_bytes() == original_bytes
    assert not (tmp_path / "case.json.bak").exists()

    # 2. template_init was hard-blocked in code
    init_res = json.loads(obs[1]["result"])
    assert init_res["ok"] is False
    assert "blocked in --dry-run mode" in init_res["error"]
    assert not (tmp_path / "unauthorized.json").exists()


def test_config_read_repairs_broken_json_and_restore_round_trips(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("SPHINXSIM_LLM_PROVIDER", "mock")

    # Get valid column_collapse payload, then corrupt case.json syntax on disk
    ref = tmp_path / "ref.json"
    assert main(["init", "2d/column_collapse", str(ref)]) == 0
    valid_payload = json.loads(ref.read_text(encoding="utf-8"))
    valid_payload["solver_parameters"]["end_time"] = 0.33
    ref.unlink()
    capsys.readouterr()

    broken = tmp_path / "broken.json"
    broken.write_text('{\n  "simulation_type": "continuum_dynamics",\n', encoding="utf-8")

    # Agent reads broken.json via config_read (valid=False) and repairs it via config_patch(configuration=...)
    backend = _SequenceBackend(
        [
            {"tool": "config_read", "arguments": {"config_file": "broken.json"}},
            {
                "tool": "config_patch",
                "arguments": {
                    "config_file": "broken.json",
                    "configuration": valid_payload,
                },
            },
            {"tool": None, "arguments": {}},
        ]
    )
    assert cmd_ask(
        argparse.Namespace(
            prompt="Fix broken.json",
            config=str(broken),
            output_dir=None,
            root=str(tmp_path),
            dry_run=False,
            json=True,
            _backend=backend,
        )
    ) == 0
    res = json.loads(capsys.readouterr().out)
    read_obs = json.loads(res["data"]["observations"][0]["result"])["data"]
    assert read_obs["valid"] is False
    assert "JSONDecodeError" in read_obs["error"]
    assert json.loads(broken.read_text(encoding="utf-8"))["solver_parameters"]["end_time"] == pytest.approx(0.33)
    assert (tmp_path / "broken.json.bak").is_file()

    # Now test atomic restore / redo on a valid patched file
    clean = tmp_path / "clean.json"
    assert main(["init", "2d/column_collapse", str(clean)]) == 0
    orig_end_time = json.loads(clean.read_text(encoding="utf-8"))["solver_parameters"]["end_time"]
    client = InProcessOperationsClient(tmp_path)
    client.call(
        "config_patch",
        {
            "config_file": "clean.json",
            "patch": {"operations": [{"op": "set_value", "path": "solver_parameters.end_time", "value": 0.99}]},
        },
    )
    assert json.loads(clean.read_text(encoding="utf-8"))["solver_parameters"]["end_time"] == pytest.approx(0.99)
    capsys.readouterr()

    # First restore -> reverts to orig_end_time
    assert main(["restore", str(clean), "--root", str(tmp_path), "--json"]) == 0
    r1 = json.loads(capsys.readouterr().out)
    assert r1["ok"] is True
    assert json.loads(clean.read_text(encoding="utf-8"))["solver_parameters"]["end_time"] == pytest.approx(orig_end_time)

    # Second restore -> acts as redo back to 0.99!
    inputs = [f"load {clean}", "undo", "exit"]
    with patch("builtins.input", side_effect=inputs):
        assert main(["shell"]) == 0
    assert json.loads(clean.read_text(encoding="utf-8"))["solver_parameters"]["end_time"] == pytest.approx(0.99)


def test_malformed_tool_choice_retries_and_recovers(tmp_path):
    client = InProcessOperationsClient(tmp_path)
    # Verify UpdatePatch $defs schema on config_patch validates cleanly under Draft202012
    patch_tool = next(t for t in client.inventory() if t["name"] == "config_patch")
    assert "$defs" in patch_tool["input_schema"]

    backend = _SequenceBackend(
        [
            StructuredProviderError("LLM provider returned invalid JSON"),
            {"tool": "hallucinated_tool", "arguments": {}},
            {"tool": "templates", "arguments": {}},
            {"tool": None, "arguments": {}},
        ]
    )

    class _StubLLM:
        def chat(self, messages):
            return "Recovered after 2 repair retries."

    conv = MCPToolConversation(client, _StubLLM(), "openai", backend=backend, max_retries_per_step=2)
    reply = conv.respond([{"role": "user", "content": "List templates"}])
    assert reply == "Recovered after 2 repair retries."
    assert "repair_error" not in backend.payloads[0]
    assert "invalid JSON" in backend.payloads[1]["repair_error"]
    assert "hallucinated_tool" in backend.payloads[2]["repair_error"]
    assert len(conv.last_observations) == 1
    assert conv.last_observations[0]["tool"] == "templates"


def test_openai_structured_backend_json_mode_and_fallback():
    recorded_kwargs: list[dict] = []

    class _FallbackCompletions:
        def create(self, **kwargs):
            recorded_kwargs.append(kwargs)
            if "response_format" in kwargs:
                raise RuntimeError("response_format is not supported by this model")
            msg = SimpleNamespace(content='{"tool": null, "arguments": {}}')
            return SimpleNamespace(choices=[SimpleNamespace(message=msg)])

    fake_openai = SimpleNamespace(
        model="custom-vllm",
        client=SimpleNamespace(chat=SimpleNamespace(completions=_FallbackCompletions())),
    )
    backend = ExistingLLMStructuredBackend("openai", llm=fake_openai)
    result = backend.complete_json(system_prompt="Return JSON", payload={"x": 1})
    assert result == {"tool": None, "arguments": {}}
    assert len(recorded_kwargs) == 2
    assert recorded_kwargs[0]["response_format"] == {"type": "json_object"}
    assert "response_format" not in recorded_kwargs[1]


def test_job_wait_and_search_docs(tmp_path, capsys):
    client = InProcessOperationsClient(tmp_path)

    # 1. Offline search_docs finds tutorials and templates
    docs_res = client.call("search_docs", {"query": "column collapse friction angle", "limit": 4})
    assert len(docs_res["matches"]) >= 1
    assert any("friction" in m["excerpt"].lower() or "column" in m["title"].lower() for m in docs_res["matches"])

    # 2. Fake persisted job reaching failed state -> job_wait returns terminal status + logs_tail
    job_id = "11111111-2222-4333-8444-555555555555"
    work = client.operations.jobs.directory / job_id
    work.mkdir(parents=True)
    atomic_json(work / "request.json", {"version": 1, "kind": "run"})
    (work / "solver.log").write_text("C++ fatal error: particle spacing must be positive\n", encoding="utf-8")
    atomic_json(
        work / "job.json",
        {
            "version": 1,
            "id": job_id,
            "kind": "run",
            "state": "failed",
            "error": "Native execution failed",
            "created_at": time.time(),
            "heartbeat": time.time(),
            "wall_seconds": 60,
        },
    )
    ref = client.operations.jobs.reference(job_id)
    ref_file = tmp_path / "ref.json"
    ref_file.write_text(json.dumps(ref), encoding="utf-8")

    assert main(["job", "wait", str(ref_file), "--timeout", "5", "--root", str(tmp_path), "--json"]) == 0
    waited = json.loads(capsys.readouterr().out)
    assert waited["ok"] is True
    assert waited["data"]["state"] == "failed"
    assert waited["data"]["timed_out"] is False
    assert "particle spacing must be positive" in waited["data"]["logs_tail"]


def test_base64_stripping_and_multimodal_attachment_with_fallback(tmp_path):
    big_b64 = "A" * 5000
    sanitized = _sanitize_observation_data(
        {"ok": True, "data": {"file": "shot.png", "content": [{"type": "image", "mimeType": "image/png", "data": big_b64}]}}
    )
    assert "omitted 5000 base64 chars" in sanitized["data"]["content"][0]["data"]

    # Write a minimal valid PNG header/file
    png = tmp_path / "shot.png"
    png.write_bytes(b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR")

    class _ImgClient:
        root = tmp_path

        def inventory(self):
            return [{"name": "results_render", "input_schema": {"type": "object"}}]

        def call(self, name, arguments):
            return {
                "file": "shot.png",
                "content": [{"type": "image", "mimeType": "image/png", "data": big_b64}],
            }

    # Vision-capable LLM receives ChatAttachment
    captured_attachments = []

    class _VisionLLM:
        def chat(self, messages):
            captured_attachments.extend(message_attachments(messages[-1]))
            return "Inspected rendered PNG."

    conv1 = MCPToolConversation(
        _ImgClient(),
        _VisionLLM(),
        "openai",
        backend=_SequenceBackend([{"tool": "results_render", "arguments": {}}, {"tool": None, "arguments": {}}]),
    )
    assert conv1.respond([{"role": "user", "content": "Render result"}]) == "Inspected rendered PNG."
    assert len(captured_attachments) == 1
    assert captured_attachments[0].path == png.resolve()
    assert "AAAAA" not in conv1.last_observations[0]["result"]

    # Text-only LLM raising UnsupportedAttachmentError falls back cleanly!
    calls = []

    class _TextOnlyLLM:
        def chat(self, messages):
            atts = message_attachments(messages[-1])
            calls.append(len(atts))
            if atts:
                raise UnsupportedAttachmentError("Mock/text model does not accept images")
            return "Fallback text answer."

    conv2 = MCPToolConversation(
        _ImgClient(),
        _TextOnlyLLM(),
        "openai",
        backend=_SequenceBackend([{"tool": "results_render", "arguments": {}}, {"tool": None, "arguments": {}}]),
    )
    assert conv2.respond([{"role": "user", "content": "Render result"}]) == "Fallback text answer."
    assert calls == [1, 0]


def test_results_frame_selector_and_watch_output_dir_autosync(tmp_path, monkeypatch, capsys):
    pytest.importorskip("pyvista")
    monkeypatch.chdir(tmp_path)
    case = tmp_path / "case.json"
    assert main(["init", "2d/column_collapse", str(case)]) == 0
    capsys.readouterr()

    out_dir = tmp_path / "sim_frames"
    vtp_dir = out_dir / "output"
    vtp_dir.mkdir(parents=True)
    for idx, t_val in enumerate((0.0, 0.1), start=1):
        (vtp_dir / f"GranularBody_{idx:04d}.vtp").write_text(
            f"""<VTKFile type="PolyData" version="1.0"><PolyData>
            <FieldData><DataArray type="Float64" Name="TimeValue" format="ascii">{t_val}</DataArray></FieldData>
            <Piece Name="GranularBody" NumberOfPoints="2">
              <PointData><DataArray type="Float64" Name="Pressure" format="ascii">1.0 2.0</DataArray></PointData>
              <Points><DataArray type="Float64" Name="Position" NumberOfComponents="3" format="ascii">0.1 0.1 0 0.2 0.1 0</DataArray></Points>
            </Piece></PolyData></VTKFile>""",
            encoding="utf-8",
        )

    shot_first = tmp_path / "first.png"
    assert main([
        "results", str(case), "--output-dir", str(out_dir),
        "--screenshot", str(shot_first), "--frame", "first",
        "--root", str(tmp_path), "--json",
    ]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["data"]["rendered"]["frame"] == "first"
    assert shot_first.is_file()

    # Out-of-range frame index fails cleanly
    assert main([
        "results", str(case), "--output-dir", str(out_dir),
        "--screenshot", str(tmp_path / "bad.png"), "--frame", "99",
    ]) == 1
    assert "out of range" in capsys.readouterr().err

    # Verify shell auto-syncs preview_runtime._watch_output_dir when `run --output-dir` runs after `preview --watch`
    runtime = _ShellPreviewRuntime()
    with (
        patch.dict("sys.modules", {"pyvista": MagicMock()}),
        patch("sphinxsim.cli._ShellPreviewRuntime", return_value=runtime),
        patch.object(_ShellPreviewRuntime, "show_or_update", return_value=0),
        patch("sphinxsim.cli.load_sphinxsys_core_nd", side_effect=ImportError("no native ext")),
        patch("sphinxsim.cli.cmd_run", return_value=0),
        patch("builtins.input", side_effect=[f"load {case}", "preview --watch", f"run --output-dir {out_dir}", "exit"]),
    ):
        assert main(["shell"]) == 0
        # Before close() resets state, check during run or patch close
    # Re-verify with close patched so we inspect _watch_output_dir directly
    runtime2 = _ShellPreviewRuntime()
    with (
        patch.dict("sys.modules", {"pyvista": MagicMock()}),
        patch("sphinxsim.cli._ShellPreviewRuntime", return_value=runtime2),
        patch.object(_ShellPreviewRuntime, "show_or_update", return_value=0),
        patch.object(_ShellPreviewRuntime, "close", lambda self=None: None),
        patch("sphinxsim.cli.load_sphinxsys_core_nd", side_effect=ImportError("no native ext")),
        patch("sphinxsim.cli.cmd_run", return_value=0),
        patch("builtins.input", side_effect=[f"load {case}", "preview --watch", f"run --output-dir {out_dir}", "exit"]),
    ):
        assert main(["shell"]) == 0
    assert runtime2._watch_output_dir == out_dir.resolve()
