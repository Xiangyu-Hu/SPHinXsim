"""Tests for the provider-neutral chatbot LLM boundary."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from sphinxsim.llm import get_llm, normalize_llm_provider
from sphinxsim.llm.nvidia_nim_llm import NvidiaNIMLLM
from sphinxsim.llm.ollama_llm import OllamaLLM
from sphinxsim.llm.openai_llm import OpenAILLM
from sphinxsim.ui.chatbot.controller import ChatController


MESSAGES = [
    {"role": "system", "content": "Be helpful."},
    {"role": "user", "content": "Hello."},
]


def test_mock_controller_uses_shared_provider_setting(monkeypatch) -> None:
    monkeypatch.setenv("SPHINXSIM_LLM_PROVIDER", "mock")

    controller = ChatController()
    response = controller.respond("Hello")

    assert controller.provider == "mock"
    assert response == "Mock response: Hello"
    assert controller.context()[-2:] == [
        {"role": "user", "content": "Hello"},
        {"role": "assistant", "content": "Mock response: Hello"},
    ]
    assert "not connected to the local SPHinXsim repository" in (
        controller.context()[0]["content"]
    )


def test_provider_aliases_match_existing_shell_configuration() -> None:
    assert normalize_llm_provider("nvidia") == "nvidia_nim"
    assert normalize_llm_provider("local") == "ollama"
    assert isinstance(get_llm("mock"), type(get_llm("mock")))


def test_nvidia_chat_uses_existing_chat_completion_boundary(monkeypatch) -> None:
    client = object.__new__(NvidiaNIMLLM)
    monkeypatch.setattr(client, "_post_chat", lambda *, messages, temperature: "NVIDIA reply")

    assert client.chat(MESSAGES) == "NVIDIA reply"


def test_ollama_chat_uses_existing_chat_endpoint(monkeypatch) -> None:
    client = object.__new__(OllamaLLM)
    monkeypatch.setattr(
        client,
        "_post_chat",
        lambda *, messages, format_json: "Ollama reply",
    )

    assert client.chat(MESSAGES) == "Ollama reply"


def test_openai_chat_uses_existing_client() -> None:
    completions = SimpleNamespace(
        create=lambda **kwargs: SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(content="OpenAI reply")
                )
            ]
        )
    )
    client = object.__new__(OpenAILLM)
    client.model = "test-model"
    client.client = SimpleNamespace(chat=SimpleNamespace(completions=completions))

    assert client.chat(MESSAGES) == "OpenAI reply"


def test_controller_rejects_empty_messages(monkeypatch) -> None:
    monkeypatch.setenv("SPHINXSIM_LLM_PROVIDER", "mock")
    controller = ChatController()

    with pytest.raises(ValueError, match="message must contain text or an attachment"):
        controller.respond("   ")
