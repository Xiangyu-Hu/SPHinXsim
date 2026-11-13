"""sphinxsim.llm – LLM helpers (mock and production)."""

import os
from typing import TYPE_CHECKING, Any, Literal

if TYPE_CHECKING:
    from sphinxsim.llm.mock_llm import MockLLM
    from sphinxsim.llm.nvidia_nim_llm import NvidiaNIMLLM
    from sphinxsim.llm.ollama_llm import OllamaLLM
    from sphinxsim.llm.openai_llm import OpenAILLM

LLMProviderName = Literal["mock", "ollama", "openai", "nvidia_nim"]


def _first_env(*names: str, default: str | None = None) -> str | None:
    """Read the first non-empty environment variable in compatibility order."""
    for name in names:
        value = os.getenv(name)
        if value is not None and value.strip():
            return value.strip()
    return default


def normalize_llm_provider(value: str | None = None) -> LLMProviderName:
    """Return the canonical provider selected by the existing shell setting."""
    raw = value if value is not None else os.getenv("SPHINXSIM_LLM_PROVIDER", "mock")
    normalized = raw.strip().lower().replace("-", "_")
    aliases = {
        "nvidia": "nvidia_nim",
        "nim": "nvidia_nim",
        "local": "ollama",
    }
    normalized = aliases.get(normalized, normalized)
    if normalized not in {"mock", "ollama", "openai", "nvidia_nim"}:
        raise ValueError(
            "SPHINXSIM_LLM_PROVIDER must be one of: mock, ollama, openai, "
            "nvidia_nim"
        )
    return normalized  # type: ignore[return-value]


def get_llm_settings(provider: str | None = None) -> dict[str, Any]:
    """Resolve shared LLM settings with generic and legacy aliases.

    ``SPHINXSIM_LLM_*`` is the canonical interface shared by Shell, Agent, and
    Chatbot. Provider-specific OpenAI/Ollama aliases remain only where they are
    already part of those providers' existing Shell setup.
    """
    canonical = normalize_llm_provider(provider)
    if canonical == "nvidia_nim":
        model = _first_env(
            "SPHINXSIM_LLM_MODEL",
            default="z-ai/glm-5.2",
        )
        base_url = _first_env(
            "SPHINXSIM_LLM_BASE_URL",
            "OPENAI_BASE_URL",
            default="https://integrate.api.nvidia.com/v1",
        )
        api_key = _first_env(
            "SPHINXSIM_LLM_API_KEY",
            "OPENAI_API_KEY",
        )
        fallback_text = _first_env(
            "SPHINXSIM_LLM_FALLBACK_MODELS",
            default="",
        )
    elif canonical == "openai":
        model = _first_env(
            "SPHINXSIM_LLM_MODEL", "OPENAI_MODEL", default="gpt-4.1-mini"
        )
        base_url = _first_env("SPHINXSIM_LLM_BASE_URL", "OPENAI_BASE_URL")
        api_key = _first_env("SPHINXSIM_LLM_API_KEY", "OPENAI_API_KEY")
        fallback_text = ""
    elif canonical == "ollama":
        model = _first_env(
            "SPHINXSIM_LLM_MODEL", "OLLAMA_MODEL", default="qwen2.5:3b"
        )
        base_url = _first_env(
            "SPHINXSIM_LLM_BASE_URL",
            "OLLAMA_BASE_URL",
            default="http://localhost:11434",
        )
        api_key = _first_env("SPHINXSIM_LLM_API_KEY")
        fallback_text = ""
    else:
        model = None
        base_url = None
        api_key = None
        fallback_text = ""

    timeout_text = _first_env(
        "SPHINXSIM_LLM_TIMEOUT",
        "OLLAMA_TIMEOUT" if canonical == "ollama" else "",
        default="180",
    )
    fallback_models = tuple(
        item.strip() for item in (fallback_text or "").split(",") if item.strip()
    )
    return {
        "provider": canonical,
        "model": model,
        "base_url": base_url,
        "api_key": api_key,
        "fallback_models": fallback_models,
        "timeout": float(timeout_text or "180"),
    }


def get_llm(provider: str | None = None):
    provider = normalize_llm_provider(provider)
    settings = get_llm_settings(provider)

    if provider == "openai":
        from sphinxsim.llm.openai_llm import OpenAILLM

        return OpenAILLM(
            model=settings["model"],
            api_key=settings["api_key"],
            base_url=settings["base_url"],
        )

    if provider == "nvidia_nim":
        from sphinxsim.llm.nvidia_nim_llm import NvidiaNIMLLM

        return NvidiaNIMLLM(
            base_url=settings["base_url"],
            model=settings["model"],
            fallback_models=settings["fallback_models"],
            api_key=settings["api_key"],
            timeout=settings["timeout"],
        )

    if provider == "ollama":
        from sphinxsim.llm.ollama_llm import OllamaLLM

        return OllamaLLM(
            base_url=settings["base_url"],
            model=settings["model"],
            timeout=settings["timeout"],
        )

    from sphinxsim.llm.mock_llm import MockLLM

    return MockLLM()

__all__ = [
    "LLMProviderName",
    "MockLLM",
    "OpenAILLM",
    "OllamaLLM",
    "NvidiaNIMLLM",
    "get_llm",
    "get_llm_settings",
    "normalize_llm_provider",
]


def __getattr__(name):
    if name == "MockLLM":
        from sphinxsim.llm.mock_llm import MockLLM

        return MockLLM

    if name == "OpenAILLM":
        from sphinxsim.llm.openai_llm import OpenAILLM

        return OpenAILLM

    if name == "OllamaLLM":
        from sphinxsim.llm.ollama_llm import OllamaLLM

        return OllamaLLM

    if name == "NvidiaNIMLLM":
        from sphinxsim.llm.nvidia_nim_llm import NvidiaNIMLLM

        return NvidiaNIMLLM
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
