"""Tests for SPHinXsysBridge (thin C++ bindings with Python stub fallback)."""

import pytest

from sphinxsim.bindings.cpp_bridge import SPHinXsysBridge
from sphinxsim.config.schemas import (
    BoundaryCondition,
    BoundaryType,
    DomainConfig,
    MaterialConfig,
    PhysicsType,
    SimulationConfig,
    TimeSteppingConfig,
)


@pytest.fixture
def fluid_config() -> SimulationConfig:
    return SimulationConfig(
        name="pipe flow",
        physics=PhysicsType.FLUID,
        domain=DomainConfig(bounds_min=[0.0, 0.0], bounds_max=[1.0, 1.0], resolution=0.05),
        materials=[MaterialConfig(name="water", density=1000.0, dynamic_viscosity=1e-3)],
        boundary_conditions=[
            BoundaryCondition(name="inlet", type=BoundaryType.INLET, velocity=[1.0, 0.0]),
            BoundaryCondition(name="outlet", type=BoundaryType.OUTLET, pressure=0.0),
        ],
        time_stepping=TimeSteppingConfig(end_time=0.5, output_interval=0.1),
    )


class TestSPHinXsysBridge:
    def test_create_bridge(self, fluid_config):
        bridge = SPHinXsysBridge(fluid_config)
        assert bridge is not None

    def test_cpp_available_is_bool(self, fluid_config):
        bridge = SPHinXsysBridge(fluid_config)
        assert isinstance(bridge.cpp_available, bool)

    def test_initial_time_is_zero(self, fluid_config):
        bridge = SPHinXsysBridge(fluid_config)
        assert bridge.current_time == pytest.approx(0.0)

    def test_initial_step_count_is_zero(self, fluid_config):
        bridge = SPHinXsysBridge(fluid_config)
        assert bridge.step_count == 0

    def test_run_advances_time(self, fluid_config):
        bridge = SPHinXsysBridge(fluid_config)
        bridge.initialize()
        bridge.run()
        assert bridge.current_time == pytest.approx(fluid_config.time_stepping.end_time)

    def test_run_increments_step_count(self, fluid_config):
        bridge = SPHinXsysBridge(fluid_config)
        bridge.initialize()
        bridge.run()
        assert bridge.step_count >= 1

    def test_repr_contains_backend(self, fluid_config):
        bridge = SPHinXsysBridge(fluid_config)
        assert "backend=" in repr(bridge)

    def test_stub_used_when_no_cpp(self, fluid_config, monkeypatch):
        """Verify that the Python stub is used when _sphinxsys_core is absent."""
        import sphinxsim.bindings.cpp_bridge as mod

        monkeypatch.setattr(mod, "_CPP_AVAILABLE", False)
        monkeypatch.setattr(mod, "_core", None)
        bridge = SPHinXsysBridge(fluid_config)
        assert not bridge.cpp_available
        bridge.initialize()
        bridge.run()
        assert bridge.current_time == pytest.approx(fluid_config.time_stepping.end_time)
