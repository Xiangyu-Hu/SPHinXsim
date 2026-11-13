"""Tests for the unified LLM environment interface and legacy aliases."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

from sphinxsim.llm import get_llm_settings
from sphinxsim.llm.openai_llm import OpenAILLM


def _clear_settings(monkeypatch) -> None:
    for name in (
        "SPHINXSIM_LLM_MODEL",
        "SPHINXSIM_LLM_BASE_URL",
        "SPHINXSIM_LLM_API_KEY",
        "SPHINXSIM_LLM_FALLBACK_MODELS",
        "SPHINXSIM_LLM_TIMEOUT",
        "OPENAI_MODEL",
        "OPENAI_BASE_URL",
        "OPENAI_API_KEY",
        "OLLAMA_MODEL",
        "OLLAMA_BASE_URL",
        "OLLAMA_TIMEOUT",
    ):
        monkeypatch.delenv(name, raising=False)


def test_generic_settings_are_shared_by_nvidia_chatbot_and_agent(monkeypatch) -> None:
    _clear_settings(monkeypatch)
    monkeypatch.setenv("SPHINXSIM_LLM_MODEL", "meta/muse-glimmer-30b")
    monkeypatch.setenv("SPHINXSIM_LLM_BASE_URL", "https://example.test/v1")
    monkeypatch.setenv("SPHINXSIM_LLM_API_KEY", "generic-key")
    monkeypatch.setenv("SPHINXSIM_LLM_FALLBACK_MODELS", "model-a, model-b")
    monkeypatch.setenv("SPHINXSIM_LLM_TIMEOUT", "42")

    settings = get_llm_settings("nvidia_nim")

    assert settings == {
        "provider": "nvidia_nim",
        "model": "meta/muse-glimmer-30b",
        "base_url": "https://example.test/v1",
        "api_key": "generic-key",
        "fallback_models": ("model-a", "model-b"),
        "timeout": 42.0,
    }


def test_openai_compatible_base_url_is_forwarded_to_openai_client() -> None:
    client = SimpleNamespace()
    with patch("sphinxsim.llm.openai_llm.OpenAI", return_value=client) as factory:
        llm = OpenAILLM(
            model="meta/muse-glimmer-30b",
            api_key="generic-key",
            base_url="https://integrate.api.nvidia.com/v1",
        )

    factory.assert_called_once_with(
        api_key="generic-key",
        base_url="https://integrate.api.nvidia.com/v1",
    )
    assert llm.client is client
