"""SPHinXsim – Python UI for the SPHinXsys multi-physics C++ library."""

from sphinxsim.config.schemas import SimulationConfig
from sphinxsim.llm.mock_llm import MockLLM
from sphinxsim.bindings.cpp_bridge import SPHinXsysBridge

__all__ = ["SimulationConfig", "MockLLM", "SPHinXsysBridge"]
__version__ = "0.1.0"
