"""Tests for sphinxsim.config.schemas (Pydantic validation)."""

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from sphinxsim.config.schemas import (
    DomainConfig,
    ObserverConfig,
    SolverConfig,
    SimulationConfig,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_minimal_config(**overrides) -> SimulationConfig:
    """Return a minimal valid SimulationConfig, applying optional *overrides*."""
    data = {
        "domain": {"lower_bound": [0.0, 0.0], "upper_bound": [1.0, 1.0]},
        "particle_spacing": 0.05,
        "particle_boundary_buffer": 4,
        "particle_sort_frequency": 100,
        "fluid_bodies": [
            {
                "name": "WaterBody",
                "geometry": {
                    "type": "bounding_box",
                    "lower_bound": [0.0, 0.0],
                    "upper_bound": [0.4, 0.2],
                },
                "material": {
                    "type": "weakly_compressible_fluid",
                    "density": 1000.0,
                    "sound_speed": 20.0,
                },
                "particle_reserve_factor": 100.0,
            }
        ],
        "solid_bodies": [
            {
                "name": "WallBoundary",
                "geometry": {
                    "type": "container_box",
                    "inner_lower_bound": [0.0, 0.0],
                    "inner_upper_bound": [1.0, 1.0],
                    "thickness": 0.2,
                },
                "material": {"type": "rigid_body"},
            }
        ],
        "gravity": [0.0, -1.0],
        "observers": [{"name": "Obs", "positions": [[0.5, 0.2]]}],
        "solver": {"dual_time_stepping": True, "free_surface_correction": True},
        "end_time": 1.0,
    }
    data.update(overrides)
    return SimulationConfig(**data)


# ---------------------------------------------------------------------------
# DomainConfig
# ---------------------------------------------------------------------------


class TestDomainConfig:
    def test_valid_2d(self):
        d = DomainConfig(lower_bound=[0.0, 0.0], upper_bound=[1.0, 1.0])
        assert d.dimensions == [1.0, 1.0]

    def test_valid_3d(self):
        d = DomainConfig(lower_bound=[0.0, 0.0, 0.0], upper_bound=[2.0, 1.0, 0.5])
        assert len(d.dimensions) == 3

    def test_negative_spacing_rejected(self):
        with pytest.raises(ValidationError):
            _make_minimal_config(particle_spacing=-0.01)

    def test_non_increasing_bounds_rejected(self):
        with pytest.raises(ValidationError):
            DomainConfig(lower_bound=[0.0, 0.0], upper_bound=[1.0, 0.0])


# ---------------------------------------------------------------------------
# ObserverConfig
# ---------------------------------------------------------------------------


class TestObserverConfig:
    def test_valid_observer(self):
        obs = ObserverConfig(name="Probe", positions=[[0.5, 0.2], [0.6, 0.3]])
        assert len(obs.positions) == 2

    def test_valid_single_position_observer(self):
        obs = ObserverConfig(name="Probe", position=[0.5, 0.2])
        assert obs.position == [0.5, 0.2]

    def test_invalid_position_dimension_rejected(self):
        with pytest.raises(ValidationError):
            ObserverConfig(name="Probe", positions=[[1.0]])

    def test_missing_position_and_positions_rejected(self):
        with pytest.raises(ValidationError, match="either position or positions"):
            ObserverConfig(name="Probe")

    def test_both_position_and_positions_rejected(self):
        with pytest.raises(ValidationError, match="either position or positions, not both"):
            ObserverConfig(name="Probe", position=[0.5, 0.2], positions=[[0.6, 0.3]])


class TestSolverConfig:
    def test_defaults(self):
        solver = SolverConfig()
        assert solver.dual_time_stepping is False
        assert solver.free_surface_correction is False


# ---------------------------------------------------------------------------
# SimulationConfig (top-level)
# ---------------------------------------------------------------------------


class TestSimulationConfig:
    def test_minimal_config(self):
        cfg = _make_minimal_config()
        assert len(cfg.fluid_bodies) == 1
        assert cfg.solver.dual_time_stepping is True

    def test_dimensionality_mismatch_rejected_fluid_body(self):
        with pytest.raises(ValidationError, match="dimensionality"):
            _make_minimal_config(
                fluid_bodies=[
                    {
                        "name": "x",
                        "geometry": {
                            "type": "bounding_box",
                            "lower_bound": [0.0, 0.0, 0.0],
                            "upper_bound": [1.0, 1.0, 1.0],
                        },
                        "material": {
                            "type": "weakly_compressible_fluid",
                            "density": 1.0,
                            "sound_speed": 1.0,
                        },
                    }
                ]
            )

    def test_dimensionality_mismatch_rejected_gravity(self):
        with pytest.raises(ValidationError, match="Gravity dimensionality"):
            _make_minimal_config(gravity=[0.0, -1.0, 0.0])

    def test_dimensionality_mismatch_rejected_solid_body(self):
        with pytest.raises(ValidationError, match="dimensionality"):
            _make_minimal_config(
                solid_bodies=[
                    {
                        "name": "WallBoundary",
                        "geometry": {
                            "type": "container_box",
                            "inner_lower_bound": [0.0, 0.0, 0.0],
                            "inner_upper_bound": [1.0, 1.0, 1.0],
                            "thickness": 0.2,
                        },
                        "material": {"type": "rigid_body"},
                    }
                ]
            )

    def test_dimensionality_mismatch_rejected_observer(self):
        with pytest.raises(ValidationError, match="Observer dimensionality"):
            _make_minimal_config(observers=[{"name": "Obs", "positions": [[0.1, 0.2, 0.3]]}])

    def test_dimensionality_mismatch_rejected_single_observer_position(self):
        with pytest.raises(ValidationError, match="Observer dimensionality"):
            _make_minimal_config(observers=[{"name": "Obs", "position": [0.1, 0.2, 0.3]}])

    def test_end_time_must_be_positive(self):
        with pytest.raises(ValidationError):
            _make_minimal_config(end_time=0.0)

    def test_particle_boundary_buffer_must_be_positive(self):
        with pytest.raises(ValidationError):
            _make_minimal_config(particle_boundary_buffer=0)

    def test_particle_sort_frequency_must_be_positive(self):
        with pytest.raises(ValidationError):
            _make_minimal_config(particle_sort_frequency=0)

    def test_accepts_multipolygon_solid_and_emitter_boundary_condition(self):
        cfg = _make_minimal_config(
            solid_bodies=[
                {
                    "name": "WallBoundary",
                    "material": {"type": "rigid_body"},
                    "geometry": {
                        "type": "multipolygon",
                        "polygons": [
                            {
                                "operation": "union",
                                "type": "container_box",
                                "inner_lower_bound": [0.0, 0.0],
                                "inner_upper_bound": [1.0, 1.0],
                                "thickness": 0.1,
                            },
                            {
                                "operation": "subtraction",
                                "type": "bounding_box",
                                "lower_bound": [0.0, 0.2],
                                "upper_bound": [0.2, 0.4],
                            },
                        ],
                    },
                }
            ],
            fluid_boundary_conditions=[
                {
                    "body_name": "WaterBody",
                    "type": "emitter",
                    "alignment_axis": 0,
                    "half_size": [0.1, 0.05],
                    "translation": [0.05, 0.25],
                    "rotation_angle": 0.0,
                    "inflow_speed": 1.5,
                }
            ],
        )
        assert cfg.solid_bodies[0].geometry.type.value == "multipolygon"
        assert cfg.fluid_boundary_conditions[0].type.value == "emitter"

    def test_fluid_boundary_condition_requires_existing_fluid_body(self):
        with pytest.raises(ValidationError, match="body_name must reference an existing fluid body"):
            _make_minimal_config(
                fluid_boundary_conditions=[
                    {
                        "body_name": "MissingBody",
                        "type": "emitter",
                        "alignment_axis": 0,
                        "half_size": [0.1, 0.05],
                        "translation": [0.05, 0.25],
                        "rotation_angle": 0.0,
                        "inflow_speed": 1.5,
                    }
                ]
            )

    def test_roundtrip_json(self):
        cfg = _make_minimal_config(
            fluid_bodies=[
                {
                    "name": "WaterBody",
                    "geometry": {
                        "type": "bounding_box",
                        "lower_bound": [0.0, 0.0],
                        "upper_bound": [0.4, 0.2],
                    },
                    "material": {
                        "type": "weakly_compressible_fluid",
                        "density": 1000.0,
                        "sound_speed": 20.0,
                    },
                }
            ]
        )
        restored = SimulationConfig.model_validate_json(cfg.model_dump_json())
        assert restored == cfg

    def test_full_updated_fixture_validates(self):
        fixture_path = Path(__file__).parent / "examples" / "full_updated_simulation_config.json"
        payload = json.loads(fixture_path.read_text())
        cfg = SimulationConfig.model_validate(payload)

        assert cfg.particle_sort_frequency == 100
        assert cfg.fluid_bodies[0].particle_reserve_factor == pytest.approx(350.0)
        assert cfg.solid_bodies[0].geometry.type.value == "multipolygon"
        assert cfg.fluid_boundary_conditions[0].type.value == "emitter"
