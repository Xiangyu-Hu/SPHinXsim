"""sphinxsim.llm – LLM helpers (mock and production)."""

import os
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from sphinxsim.llm.mock_llm import MockLLM
    from sphinxsim.llm.nvidia_nim_llm import NvidiaNIMLLM
    from sphinxsim.llm.ollama_llm import OllamaLLM
    from sphinxsim.llm.openai_llm import OpenAILLM

LLMProviderName = Literal["mock", "ollama", "openai", "nvidia_nim"]


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


def get_llm(provider: str | None = None):
    provider = normalize_llm_provider(provider)

    if provider == "openai":
        from sphinxsim.llm.openai_llm import OpenAILLM

        return OpenAILLM(
            model=os.getenv("OPENAI_MODEL", "gpt-4.1-mini"),
            api_key=os.getenv("OPENAI_API_KEY"),
        )

    if provider == "nvidia_nim":
        from sphinxsim.llm.nvidia_nim_llm import NvidiaNIMLLM

        return NvidiaNIMLLM(
            base_url=os.getenv(
                "NVIDIA_NIM_BASE_URL",
                "https://integrate.api.nvidia.com/v1",
            ),
            model=os.getenv("NVIDIA_NIM_MODEL", os.getenv("NVIDIA_NIM", "z-ai/glm-5.2")),
            fallback_models=tuple(
                m.strip()
                for m in os.getenv("NVIDIA_NIM_FALLBACK_MODELS", "").split(",")
                if m.strip()
            ),
            api_key=os.getenv("NVIDIA_NIM_API_KEY", os.getenv("NVIDIA_API_KEY")),
            timeout=float(os.getenv("NVIDIA_NIM_TIMEOUT", "180")),
        )

    if provider == "ollama":
        from sphinxsim.llm.ollama_llm import OllamaLLM

        return OllamaLLM(
            base_url=os.getenv("OLLAMA_BASE_URL", "http://localhost:11434"),
            model=os.getenv("OLLAMA_MODEL", "qwen2.5:3b"),
            timeout=float(os.getenv("OLLAMA_TIMEOUT", "180")),
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
