"""Tests for provider-neutral PDF and image chat attachments."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from sphinxsim.llm.chat_attachments import (
    ChatAttachment,
    UnsupportedAttachmentError,
)
from sphinxsim.llm.mock_llm import MockLLM
from sphinxsim.llm.nvidia_nim_llm import NvidiaNIMLLM
from sphinxsim.llm.ollama_llm import OllamaLLM
from sphinxsim.llm.openai_llm import OpenAILLM
from sphinxsim.ui.chatbot.controller import ChatController


def _attachment(tmp_path: Path, name: str, media_type: str) -> ChatAttachment:
    path = tmp_path / name
    path.write_bytes(b"attachment-data")
    return ChatAttachment(path=path, name=name, media_type=media_type)


def _messages(attachment: ChatAttachment):
    return [
        {"role": "system", "content": "Be helpful."},
        {
            "role": "user",
            "content": "Compare the attached material with my question.",
            "attachments": [attachment],
        },
    ]


def test_openai_uses_responses_api_for_pdf(tmp_path) -> None:
    attachment = _attachment(tmp_path, "paper.pdf", "application/pdf")
    captured = {}
    client = object.__new__(OpenAILLM)
    client.model = "gpt-test-multimodal"
    client.client = SimpleNamespace(
        responses=SimpleNamespace(
            create=lambda **kwargs: captured.update(kwargs)
            or SimpleNamespace(output_text="PDF response")
        )
    )

    assert client.chat(_messages(attachment)) == "PDF response"
    user_content = captured["input"][1]["content"]
    assert user_content[0] == {
        "type": "input_text",
        "text": "Compare the attached material with my question.",
    }
    assert user_content[1]["type"] == "input_file"
    assert user_content[1]["filename"] == "paper.pdf"
    assert user_content[1]["file_data"].startswith(
        "data:application/pdf;base64,"
    )


def test_openai_uses_responses_api_for_image_without_injected_prompt(
    tmp_path,
) -> None:
    attachment = _attachment(tmp_path, "figure.png", "image/png")
    captured = {}
    client = object.__new__(OpenAILLM)
    client.model = "gpt-test-multimodal"
    client.client = SimpleNamespace(
        responses=SimpleNamespace(
            create=lambda **kwargs: captured.update(kwargs)
            or SimpleNamespace(output_text="Image response")
        )
    )
    messages = [
        {"role": "system", "content": "Be helpful."},
        {"role": "user", "content": "", "attachments": [attachment]},
    ]

    assert client.chat(messages) == "Image response"
    content = captured["input"][1]["content"]
    assert [item["type"] for item in content] == ["input_image"]


def test_nvidia_converts_image_to_openai_compatible_content(
    tmp_path,
    monkeypatch,
) -> None:
    attachment = _attachment(tmp_path, "figure.jpg", "image/jpeg")
    captured = {}
    client = object.__new__(NvidiaNIMLLM)
    client.model = "vision-model"

    def fake_post_chat(*, messages, temperature):
        captured["messages"] = messages
        return "NVIDIA image response"

    monkeypatch.setattr(client, "_post_chat", fake_post_chat)

    assert client.chat(_messages(attachment)) == "NVIDIA image response"
    image_part = captured["messages"][1]["content"][1]
    assert image_part["type"] == "image_url"
    assert image_part["image_url"]["url"].startswith(
        "data:image/jpeg;base64,"
    )


def test_nvidia_reports_pdf_as_unsupported(tmp_path) -> None:
    attachment = _attachment(tmp_path, "paper.pdf", "application/pdf")
    client = object.__new__(NvidiaNIMLLM)
    client.model = "text-model"

    with pytest.raises(UnsupportedAttachmentError, match="may support text only"):
        client.chat(_messages(attachment))


def test_ollama_passes_images_to_local_vision_model(tmp_path, monkeypatch) -> None:
    attachment = _attachment(tmp_path, "figure.webp", "image/webp")
    captured = {}
    client = object.__new__(OllamaLLM)
    client.model = "llava"

    def fake_post_chat(*, messages, format_json):
        captured["messages"] = messages
        return "Ollama image response"

    monkeypatch.setattr(client, "_post_chat", fake_post_chat)

    assert client.chat(_messages(attachment)) == "Ollama image response"
    assert captured["messages"][1]["images"]
    assert captured["messages"][1]["content"] == (
        "Compare the attached material with my question."
    )


def test_mock_provider_explains_that_attachments_are_unsupported(tmp_path) -> None:
    attachment = _attachment(tmp_path, "figure.png", "image/png")

    with pytest.raises(UnsupportedAttachmentError, match="text only"):
        MockLLM().chat(_messages(attachment))


def test_controller_retains_attachments_for_follow_up_turns(tmp_path) -> None:
    attachment = _attachment(tmp_path, "figure.png", "image/png")
    controller = ChatController(provider="mock")

    class CapturingLLM:
        def __init__(self) -> None:
            self.calls = []

        def chat(self, messages):
            self.calls.append([dict(message) for message in messages])
            return "Accepted"

    llm = CapturingLLM()
    controller.llm = llm

    assert controller.respond("Initial question", [attachment]) == "Accepted"
    assert controller.respond("What about the second part?") == "Accepted"
    assert llm.calls[1][1]["attachments"] == [attachment]
