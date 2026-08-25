"""Tests for the Chainlit chatbot boundary and launcher."""

from __future__ import annotations

import argparse
import asyncio
import importlib
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace

import pytest

from sphinxsim import cli
from sphinxsim.ui.chatbot import application


class _FakeUserSession:
    def __init__(self) -> None:
        self.values: dict[str, object] = {}

    def get(self, key: str):
        return self.values.get(key)

    def set(self, key: str, value: object) -> None:
        self.values[key] = value


def _fake_chainlit() -> ModuleType:
    module = ModuleType("chainlit")
    module.user_session = _FakeUserSession()
    module.sent_messages = []

    def decorator(function):
        return function

    class Message:
        def __init__(self, content: str, author: str | None = None) -> None:
            self.content = content
            self.author = author

        async def send(self):
            module.sent_messages.append(self)
            return self

    module.on_chat_start = decorator
    module.on_message = decorator
    module.Message = Message
    return module


def test_chainlit_callbacks_keep_a_multi_turn_mock_conversation(
    monkeypatch,
) -> None:
    monkeypatch.setenv("SPHINXSIM_LLM_PROVIDER", "mock")
    fake = _fake_chainlit()
    monkeypatch.setitem(sys.modules, "chainlit", fake)
    sys.modules.pop("sphinxsim.ui.chatbot.chainlit_app", None)
    app = importlib.import_module("sphinxsim.ui.chatbot.chainlit_app")

    async def exercise() -> None:
        await app.on_chat_start()
        await app.on_message(SimpleNamespace(content="First message"))
        await app.on_message(SimpleNamespace(content="Second message"))

    asyncio.run(exercise())

    controller = fake.user_session.get(app.CONTROLLER_SESSION_KEY)
    assert controller is not None
    assert [message.content for message in fake.sent_messages] == [
        (
            "# 👋 SPHinXsim Chatbot\n\n"
            "I can have a normal conversation and run the current AgentCAE "
            "column-collapse workflow.\n\n"
            "### 🤖 Agent commands\n\n"
            "- `agent start <request>` — draft a simulation Plan\n"
            "- `agent approve` — approve and run the Plan\n"
            "- `agent status` — inspect current progress\n"
            "- `agent report` — show the final report\n"
            "- `agent cancel` — cancel the workflow\n\n"
            + app._agent_context_text()
        ),
        "Mock response: First message",
        "Mock response: Second message",
    ]
    assert [message["role"] for message in controller.context()] == [
        "system",
        "user",
        "assistant",
        "user",
        "assistant",
    ]


def test_chainlit_callback_reports_provider_errors(monkeypatch) -> None:
    fake = _fake_chainlit()
    monkeypatch.setitem(sys.modules, "chainlit", fake)
    sys.modules.pop("sphinxsim.ui.chatbot.chainlit_app", None)
    app = importlib.import_module("sphinxsim.ui.chatbot.chainlit_app")

    async def fail_controller():
        raise RuntimeError("provider unavailable")

    monkeypatch.setattr(app, "_new_controller", fail_controller)
    asyncio.run(app.on_chat_start())

    assert fake.sent_messages[-1].content == (
        "Unable to initialize the configured LLM provider: provider unavailable"
    )


def test_chainlit_passes_uploaded_files_to_the_shared_controller(
    monkeypatch,
    tmp_path,
) -> None:
    monkeypatch.setenv("SPHINXSIM_LLM_PROVIDER", "mock")
    upload = tmp_path / "figure.png"
    upload.write_bytes(b"image-data")
    fake = _fake_chainlit()
    monkeypatch.setitem(sys.modules, "chainlit", fake)
    sys.modules.pop("sphinxsim.ui.chatbot.chainlit_app", None)
    app = importlib.import_module("sphinxsim.ui.chatbot.chainlit_app")

    async def exercise() -> None:
        await app.on_chat_start()
        await app.on_message(
            SimpleNamespace(
                content="",
                elements=[
                    SimpleNamespace(
                        path=str(upload),
                        name="figure.png",
                        mime="image/png",
                    )
                ],
            )
        )

    asyncio.run(exercise())

    assert "may support text only" in fake.sent_messages[-1].content


def test_launcher_runs_chainlit_with_an_isolated_app_root(monkeypatch) -> None:
    captured: dict[str, object] = {}
    monkeypatch.setattr(application.importlib.util, "find_spec", lambda name: object())
    monkeypatch.setenv("DEBUG", "release")

    def fake_call(command, *, env, cwd):
        captured["command"] = command
        captured["app_root"] = env["CHAINLIT_APP_ROOT"]
        captured["cwd"] = cwd
        assert "DEBUG" not in env
        assert Path(env["CHAINLIT_APP_ROOT"]).is_dir()
        config = Path(env["CHAINLIT_APP_ROOT"], ".chainlit", "config.toml")
        config_text = config.read_text(encoding="utf-8")
        assert 'name = "SPHinXsim"' in config_text
        assert "[features.spontaneous_file_upload]" in config_text
        assert 'accept = ["application/pdf"' in config_text
        assert Path(env["CHAINLIT_APP_ROOT"], "chainlit.md").read_text() == ""
        return 7

    monkeypatch.setattr(application.subprocess, "call", fake_call)

    result = application.launch_chatbot(
        host="localhost",
        port=8123,
        headless=True,
        watch=True,
    )

    assert result == 7
    assert captured["command"] == [
        sys.executable,
        "-m",
        "chainlit",
        "run",
        str(Path(application.__file__).with_name("chainlit_app.py").resolve()),
        "--host",
        "localhost",
        "--port",
        "8123",
        "--headless",
        "--watch",
    ]
    assert captured["cwd"] == str(Path.cwd().resolve())
    assert not Path(captured["app_root"]).exists()


def test_launcher_explains_missing_optional_dependency(monkeypatch) -> None:
    monkeypatch.setattr(application.importlib.util, "find_spec", lambda name: None)

    with pytest.raises(application.ChatbotDependencyError, match="Chainlit is not installed"):
        application.launch_chatbot()


def test_cli_delegates_chatbot_server_options(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def fake_launch(**kwargs):
        captured.update(kwargs)
        return 0

    monkeypatch.setattr(application, "launch_chatbot", fake_launch)
    args = argparse.Namespace(
        host="127.0.0.1",
        port=9000,
        headless=True,
        watch=False,
    )

    assert cli.cmd_chatbot(args) == 0
    assert captured == {
        "host": "127.0.0.1",
        "port": 9000,
        "headless": True,
        "watch": False,
    }
