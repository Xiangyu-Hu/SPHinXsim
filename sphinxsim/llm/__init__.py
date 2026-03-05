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

__all__ = ["MockLLM", "OpenAILLM", "config_to_builder", "run_from_config"]


def __getattr__(name):
    if name == "MockLLM":
        from sphinxsim.llm.mock_llm import MockLLM

        return MockLLM
    if name == "OpenAILLM":
        from sphinxsim.llm.openai_llm import OpenAILLM

        return OpenAILLM
    if name in {"config_to_builder", "run_from_config"}:
        from sphinxsim.llm.config_to_simulation import config_to_builder, run_from_config

        return {
            "config_to_builder": config_to_builder,
            "run_from_config": run_from_config,
        }[name]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
