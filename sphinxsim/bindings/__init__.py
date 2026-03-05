"""sphinxsim.bindings – thin C++ bindings for SPHinXsys."""

__all__ = [
    "SimulationBuilder",
    "SimulationResult",
    "FluidBlock",
    "WallBoundary",
    "SolverConfiguration",
]


def __getattr__(name):
    if name in __all__:
        from sphinxsim.bindings import simulation_builder

        return getattr(simulation_builder, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
