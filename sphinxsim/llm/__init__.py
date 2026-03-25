"""sphinxsim.llm – LLM helpers (mock and production)."""

import os

def get_llm():
    provider = os.getenv("SPHINXSIM_LLM_PROVIDER", "mock")
    if provider == "openai":
        from sphinxsim.llm.openai_llm import OpenAILLM

        return OpenAILLM(
            model=os.getenv("OPENAI_MODEL", "gpt-4.1-mini"),
            api_key=os.getenv("OPENAI_API_KEY"),
        )
    from sphinxsim.llm.mock_llm import MockLLM

    return MockLLM()

__all__ = ["MockLLM", "OpenAILLM", "get_llm"]


def __getattr__(name):
    if name == "MockLLM":
        from sphinxsim.llm.mock_llm import MockLLM

        return MockLLM
    if name == "OpenAILLM":
        from sphinxsim.llm.openai_llm import OpenAILLM

        return OpenAILLM
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
