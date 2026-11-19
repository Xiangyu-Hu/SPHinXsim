"""Contract tests for provider-neutral structured Agent calls."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from sphinxsim.llm import normalize_llm_provider
from sphinxsim.llm.structured_provider import (
    ExistingLLMStructuredBackend,
    StructuredProviderError,
)


class FakeNvidia:
    def _post_chat(self, **kwargs):
        return "```json\n{\"answer\": 1}\n```"


class FakeOllama:
    def _post_chat(self, **kwargs):
        return {"answer": 2}


class FakeOpenAICompletions:
    def create(self, **kwargs):
        message = SimpleNamespace(content='{\"answer\": 3}')
        return SimpleNamespace(choices=[SimpleNamespace(message=message)])


class FakeOpenAI:
    model = "fake-model"
    client = SimpleNamespace(
        chat=SimpleNamespace(completions=FakeOpenAICompletions())
    )


@pytest.mark.parametrize(
    ("raw", "canonical"),
    [
        ("mock", "mock"),
        ("ollama", "ollama"),
        ("local", "ollama"),
        ("openai", "openai"),
        ("nvidia", "nvidia_nim"),
        ("nvidia_nim", "nvidia_nim"),
    ],
)
def test_provider_names_are_normalized(raw: str, canonical: str) -> None:
    assert normalize_llm_provider(raw) == canonical


@pytest.mark.parametrize(
    ("provider", "llm", "expected"),
    [
        ("nvidia_nim", FakeNvidia(), {"answer": 1}),
        ("ollama", FakeOllama(), {"answer": 2}),
        ("openai", FakeOpenAI(), {"answer": 3}),
    ],
)
def test_all_providers_share_one_structured_contract(provider, llm, expected) -> None:
    backend = ExistingLLMStructuredBackend(provider, llm=llm)

    result = backend.complete_json(
        system_prompt="Return JSON.",
        payload={"question": "test", "output_schema": {}},
    )

    assert result == expected
    assert backend.calls[0].payload["question"] == "test"


def test_structured_backend_rejects_non_object() -> None:
    backend = ExistingLLMStructuredBackend("nvidia_nim", llm=FakeNvidia())
    backend.llm._post_chat = lambda **kwargs: "[]"

    with pytest.raises(StructuredProviderError, match="one JSON object"):
        backend.complete_json(system_prompt="Return JSON.", payload={})


def test_mock_requires_explicit_role_response() -> None:
    with pytest.raises(StructuredProviderError, match="explicit deterministic"):
        ExistingLLMStructuredBackend("mock", llm=object())


def test_existing_environment_setting_selects_provider(monkeypatch) -> None:
    monkeypatch.setenv("SPHINXSIM_LLM_PROVIDER", "nvidia")

    assert normalize_llm_provider() == "nvidia_nim"


def test_unknown_environment_provider_is_rejected(monkeypatch) -> None:
    monkeypatch.setenv("SPHINXSIM_LLM_PROVIDER", "unknown-brain")

    with pytest.raises(ValueError, match="SPHINXSIM_LLM_PROVIDER"):
        normalize_llm_provider()
