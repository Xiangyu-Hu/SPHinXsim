"""SPHinXsim – Python UI for the SPHinXsys multi-physics C++ library."""

__all__ = ["SimulationConfig", "MockLLM"]
__version__ = "0.1.0"


def __getattr__(name):
    """Lazy imports to avoid loading C++ extension until needed."""
    if name == "SimulationConfig":
        from sphinxsim.config.schemas import SimulationConfig
        return SimulationConfig
    elif name == "MockLLM":
        from sphinxsim.llm.mock_llm import MockLLM
        return MockLLM
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")