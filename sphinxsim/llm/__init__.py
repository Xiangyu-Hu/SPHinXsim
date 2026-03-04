"""sphinxsim.llm – LLM helpers (mock and production)."""

import os
from sphinxsim.llm.mock_llm import MockLLM
from sphinxsim.llm.openai_llm import OpenAILLM

from sphinxsim.llm.config_to_simulation import config_to_builder, run_from_config

def get_llm():
    provider = os.getenv("SPHINXSIM_LLM_PROVIDER", "mock")
    if provider == "openai":
        return OpenAILLM(
            model=os.getenv("OPENAI_MODEL", "gpt-4.1-mini"),
            api_key=os.getenv("OPENAI_API_KEY"),
        )
    return MockLLM()

__all__ = ["MockLLM", "OpenAILLM", "config_to_builder", "run_from_config"]
