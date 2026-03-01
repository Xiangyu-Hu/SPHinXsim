"""Tests for sphinxsim.config.schemas (Pydantic validation)."""

import pytest
from pydantic import ValidationError

from sphinxsim.config.schemas import (
    BoundaryCondition,
    BoundaryType,
    DomainConfig,
    MaterialConfig,
    OutputConfig,
    OutputFormat,
    PhysicsType,
    SimulationConfig,
    TimeSteppingConfig,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_minimal_config(**overrides) -> SimulationConfig:
    """Return a minimal valid SimulationConfig, applying optional *overrides*."""
    data = {
        "name": "test sim",
        "physics": PhysicsType.FLUID,
        "domain": {
            "bounds_min": [0.0, 0.0],
            "bounds_max": [1.0, 1.0],
            "resolution": 0.05,
        },
        "materials": [{"name": "water", "density": 1000.0, "dynamic_viscosity": 1e-3}],
        "boundary_conditions": [],
        "time_stepping": {"end_time": 1.0, "output_interval": 0.1},
    }
    data.update(overrides)
    return SimulationConfig(**data)


# ---------------------------------------------------------------------------
# DomainConfig
# ---------------------------------------------------------------------------


class TestDomainConfig:
    def test_valid_2d(self):
        d = DomainConfig(bounds_min=[0.0, 0.0], bounds_max=[1.0, 1.0], resolution=0.01)
        assert d.resolution == 0.01

    def test_valid_3d(self):
        d = DomainConfig(
            bounds_min=[0.0, 0.0, 0.0], bounds_max=[2.0, 1.0, 0.5], resolution=0.02
        )
        assert len(d.bounds_max) == 3

    def test_negative_resolution_rejected(self):
        with pytest.raises(ValidationError):
            DomainConfig(bounds_min=[0.0, 0.0], bounds_max=[1.0, 1.0], resolution=-0.01)

    def test_bounds_inverted_rejected(self):
        with pytest.raises(ValidationError, match="bounds_min must be less than bounds_max"):
            DomainConfig(bounds_min=[1.0, 0.0], bounds_max=[0.0, 1.0], resolution=0.01)

    def test_mismatched_dimensionality_rejected(self):
        with pytest.raises(ValidationError):
            DomainConfig(bounds_min=[0.0, 0.0], bounds_max=[1.0, 1.0, 1.0], resolution=0.01)


# ---------------------------------------------------------------------------
# MaterialConfig
# ---------------------------------------------------------------------------


class TestMaterialConfig:
    def test_fluid_material(self):
        m = MaterialConfig(name="air", density=1.2, dynamic_viscosity=1.8e-5)
        assert m.name == "air"

    def test_solid_material(self):
        m = MaterialConfig(name="rubber", density=900.0, youngs_modulus=1e6, poisson_ratio=0.49)
        assert m.poisson_ratio == pytest.approx(0.49)

    def test_zero_density_rejected(self):
        with pytest.raises(ValidationError):
            MaterialConfig(name="void", density=0.0)

    def test_poisson_ratio_at_limit_rejected(self):
        with pytest.raises(ValidationError):
            MaterialConfig(name="x", density=1.0, poisson_ratio=0.5)


# ---------------------------------------------------------------------------
# BoundaryCondition
# ---------------------------------------------------------------------------


class TestBoundaryCondition:
    def test_inlet_with_velocity(self):
        bc = BoundaryCondition(name="in", type=BoundaryType.INLET, velocity=[1.0, 0.0])
        assert bc.velocity == [1.0, 0.0]

    def test_wall_without_velocity(self):
        bc = BoundaryCondition(name="wall", type=BoundaryType.WALL)
        assert bc.velocity is None

    def test_empty_velocity_rejected(self):
        with pytest.raises(ValidationError):
            BoundaryCondition(name="in", type=BoundaryType.INLET, velocity=[])


# ---------------------------------------------------------------------------
# TimeSteppingConfig
# ---------------------------------------------------------------------------


class TestTimeSteppingConfig:
    def test_valid(self):
        ts = TimeSteppingConfig(end_time=1.0, output_interval=0.1)
        assert ts.dt is None

    def test_interval_exceeds_end_time_rejected(self):
        with pytest.raises(ValidationError, match="output_interval must not exceed end_time"):
            TimeSteppingConfig(end_time=0.1, output_interval=1.0)

    def test_explicit_dt(self):
        ts = TimeSteppingConfig(end_time=2.0, dt=1e-4, output_interval=0.5)
        assert ts.dt == pytest.approx(1e-4)


# ---------------------------------------------------------------------------
# SimulationConfig (top-level)
# ---------------------------------------------------------------------------


class TestSimulationConfig:
    def test_minimal_fluid_config(self):
        cfg = _make_minimal_config()
        assert cfg.physics == PhysicsType.FLUID
        assert cfg.output.format == OutputFormat.VTP

    def test_solid_config(self):
        cfg = _make_minimal_config(
            physics=PhysicsType.SOLID,
            materials=[{"name": "steel", "density": 7850.0, "youngs_modulus": 2e11, "poisson_ratio": 0.3}],
        )
        assert cfg.physics == PhysicsType.SOLID

    def test_fsi_config(self):
        cfg = _make_minimal_config(
            physics=PhysicsType.FSI,
            materials=[
                {"name": "water", "density": 1000.0, "dynamic_viscosity": 1e-3},
                {"name": "plate", "density": 900.0, "youngs_modulus": 1e6, "poisson_ratio": 0.4},
            ],
        )
        assert len(cfg.materials) == 2

    def test_empty_name_rejected(self):
        with pytest.raises(ValidationError):
            _make_minimal_config(name="")

    def test_empty_materials_rejected(self):
        with pytest.raises(ValidationError):
            _make_minimal_config(materials=[])

    def test_roundtrip_json(self):
        cfg = _make_minimal_config()
        restored = SimulationConfig.model_validate_json(cfg.model_dump_json())
        assert restored == cfg
