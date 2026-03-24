"""sphinxsim.llm – LLM helpers (mock and production)."""

import os


def get_llm():
    provider = os.getenv("SPHINXSIM_LLM_PROVIDER", "mock").strip().lower()

    if provider in {"copilot", "github-copilot", "github_models", "github-models"}:
        from sphinxsim.llm.copilot_llm import CopilotLLM

        return CopilotLLM(
            model=os.getenv("COPILOT_MODEL", "gpt-4o-mini"),
            api_key=os.getenv("COPILOT_API_KEY") or os.getenv("GITHUB_TOKEN"),
            base_url=os.getenv("COPILOT_BASE_URL") or os.getenv("GITHUB_MODELS_BASE_URL"),
        )

    if provider == "openai":
        from sphinxsim.llm.openai_llm import OpenAILLM

        return OpenAILLM(
            model=os.getenv("OPENAI_MODEL", "gpt-4.1-mini"),
            api_key=os.getenv("OPENAI_API_KEY"),
        )

    from sphinxsim.llm.mock_llm import MockLLM

    return MockLLM()


__all__ = ["MockLLM", "OpenAILLM", "CopilotLLM", "get_llm"]


def __getattr__(name):
    if name == "MockLLM":
        from sphinxsim.llm.mock_llm import MockLLM

        return MockLLM

    if name == "OpenAILLM":
        from sphinxsim.llm.openai_llm import OpenAILLM

        return OpenAILLM

    if name == "CopilotLLM":
        from sphinxsim.llm.copilot_llm import CopilotLLM

        return CopilotLLM

    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
