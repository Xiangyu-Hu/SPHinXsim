"""Workbench document integrity and the real desktop/chat loopback boundary."""
import asyncio
import importlib
import json
import os
from pathlib import Path
import sys
import subprocess
from types import SimpleNamespace

import pytest

from sphinxsim.llm import get_llm
from sphinxsim.ui.workbench.bridge import WorkbenchBridge, WorkbenchClient
from sphinxsim.ui.workbench.document import WorkbenchDocument


@pytest.fixture(params=["\n", "\r\n"], ids=["lf", "crlf"])
def document(tmp_path, request):
    source = Path(__file__).parent / "test_simulation/test_2d_simulation/data/column_collapse.json"
    case = tmp_path / "case.json"
    case.write_bytes(source.read_text(encoding="utf-8").replace("\n", request.param).encode("utf-8"))
    return WorkbenchDocument(case)


@pytest.fixture
def connection(document):
    bridge = WorkbenchBridge(document)
    try:
        yield bridge, WorkbenchClient(bridge.url, bridge.token)
    finally:
        bridge.close()


def test_proposal_is_reviewed_before_application_and_saved_explicitly(document, connection):
    _, client = connection
    original = document.path.read_bytes().decode("utf-8")
    proposal = client.propose_update(get_llm("mock"), "Change end time to 0.08 seconds")
    assert "0.08" in proposal["diff"]
    assert document.text == original
    assert document.path.read_bytes().decode("utf-8") == original
    document.apply_proposal()
    assert document.snapshot()["dirty"]
    assert json.loads(document.text)["solver_parameters"]["end_time"] == 0.08
    assert document.path.read_bytes().decode("utf-8") == original
    document.save()
    assert not document.snapshot()["dirty"]
    assert json.loads(document.path.read_text(encoding="utf-8"))["solver_parameters"]["end_time"] == 0.08


def test_draft_changed_during_llm_request_cannot_be_overwritten(document, connection):
    _, client = connection
    base = client.state()
    document.set_text(document.text + "\n")
    with pytest.raises(ValueError, match="draft changed"):
        client.request("/proposal", dict(revision=base["revision"], payload=json.loads(base["text"]), instruction="stale"))
    assert document.pending_proposal() is None


def test_old_proposal_cannot_replace_later_manual_edits(document, connection):
    _, client = connection
    client.propose_update(get_llm("mock"), "end time 0.08 seconds")
    document.set_text(document.text + "\n")
    with pytest.raises(ValueError, match="draft changed"):
        document.apply_proposal()


def test_invalid_draft_and_external_disk_edits_do_not_destroy_saved_config(document):
    original = document.text
    document.set_text('{"broken":')
    with pytest.raises(ValueError):
        document.save()
    assert document.path.read_bytes().decode("utf-8") == original
    document.set_text(original)
    document.path.write_bytes((original + "\n").encode("utf-8"))
    with pytest.raises(ValueError, match="changed on disk"):
        document.save()
    assert document.path.read_bytes().decode("utf-8") == original + "\n"


def test_bridge_requires_token_and_accepts_only_valid_workflow_ids(document, connection):
    bridge, client = connection
    with pytest.raises(ValueError, match="Unauthorized"):
        WorkbenchClient(bridge.url, "wrong").state()
    with pytest.raises(ValueError, match="invalid workflow"):
        client.observe("../../elsewhere")
    client.observe("demo_123")
    assert document.actions.get_nowait() == ("observe", "demo_123")


def test_asset_paths_follow_the_source_case_without_rewriting_other_fields(tmp_path):
    from sphinxsim.ui.workbench.document import rebase_asset_paths
    payload = {"name": "mesh.stl", "items": [{"file_name": "input/mesh.stl"}]}
    resolved = rebase_asset_paths(payload, tmp_path)
    assert resolved["name"] == "mesh.stl"
    assert resolved["items"][0]["file_name"] == str((tmp_path / "input/mesh.stl").resolve())
    assert payload["items"][0]["file_name"] == "input/mesh.stl"


def test_shared_property_editor_edits_lists_validates_and_preserves_source_format(tmp_path):
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run([sys.executable, str(root / "tests/json_editor_probe.py"), str(tmp_path)],
                            cwd=root, capture_output=True, timeout=30)
    assert result.returncode == 0, (result.stdout + result.stderr).decode("utf-8", errors="replace")


def test_chainlit_edits_and_chat_context_use_the_live_editor(document, connection, monkeypatch):
    from tests.test_chainlit_chatbot import _fake_chainlit
    bridge, _ = connection
    monkeypatch.setenv("SPHINXSIM_LLM_PROVIDER", "mock")
    monkeypatch.setenv("SPHINXSIM_WORKBENCH_URL", bridge.url)
    monkeypatch.setenv("SPHINXSIM_WORKBENCH_TOKEN", bridge.token)
    fake = _fake_chainlit()
    monkeypatch.setitem(sys.modules, "chainlit", fake)
    monkeypatch.delitem(sys.modules, "sphinxsim.ui.chatbot.chainlit_app", raising=False)
    app = importlib.import_module("sphinxsim.ui.chatbot.chainlit_app")
    captured = []

    async def exercise():
        await app.on_chat_start()
        controller = fake.user_session.get(app.CONTROLLER_SESSION_KEY)
        original = controller.llm.chat
        def capture(messages):
            captured.extend(messages)
            return original(messages)
        controller.llm.chat = capture
        await app.on_message(SimpleNamespace(content="Explain my current model"))
        await app.on_message(SimpleNamespace(content="/config change end time to 0.08 seconds"))

    try:
        asyncio.run(exercise())
        context = next(message["content"] for message in captured if message["content"].startswith("Current workbench state"))
        assert json.loads(context.split("\n", 1)[1])["config_path"] == str(document.path)
        assert document.pending_proposal() is not None
        assert "Validated proposal" in fake.sent_messages[-1].content
        assert not document.snapshot()["dirty"]
    finally:
        # Do not leak a fake Chainlit module into the desktop integration test.
        sys.modules.pop("sphinxsim.ui.chatbot.chainlit_app", None)


@pytest.mark.integration
@pytest.mark.skipif(os.environ.get("SPHINXSIM_RUN_WORKBENCH_DESKTOP") != "1",
                    reason="set SPHINXSIM_RUN_WORKBENCH_DESKTOP=1 for the real desktop smoke test")
def test_real_workbench_desktop_chat_edit_preview_and_solver(tmp_path):
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run([sys.executable, str(root / "tests/workbench_desktop_probe.py"), str(tmp_path)],
                            cwd=root, env=dict(os.environ, SPHINXSIM_LLM_PROVIDER="mock"),
                            capture_output=True, timeout=210)
    assert result.returncode == 0, (result.stdout + result.stderr).decode("utf-8", errors="replace")
    evidence = json.loads((tmp_path / "probe.json").read_text(encoding="utf-8"))
    assert not evidence["errors"]
    assert len(evidence["checks"]) == 12
