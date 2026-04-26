"""Tests for sphinxsim.config.schemas (Pydantic validation)."""

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from sphinxsim.config.schemas import DomainConfig, SimulationConfig


def _make_minimal_fluid_config(**overrides) -> SimulationConfig:
    data = {
        "simulation_type": "fluid_dynamics",
        "geometries": {
            "system_domain": {"lower_bound": [0.0, 0.0], "upper_bound": [1.0, 1.0]},
            "global_resolution": {"particle_spacing": 0.05},
            "shapes": [
                {
                    "name": "WaterBody",
                    "type": "bounding_box",
                    "lower_bound": [0.0, 0.0],
                    "upper_bound": [0.4, 0.2],
                },
                {
                    "name": "WallBoundary",
                    "type": "bounding_box",
                    "lower_bound": [0.0, 0.0],
                    "upper_bound": [1.0, 1.0],
                },
            ],
            "aligned_boxes": [
                {
                    "name": "Inlet",
                    "type": "region",
                    "half_size": [0.1, 0.05],
                    "transform": {"translation": [0.05, 0.2], "rotation_angle": 0.0},
                }
            ],
        },
        "particle_generation": {
            "build_and_run": False,
            "settings": {
                "bodies": [
                    {"name": "WaterBody"},
                    {"name": "WallBoundary", "solid_body": {}},
                ],
                "relaxation_parameters": {"total_iterations": 1000},
            },
        },
        "fluid_bodies": [
            {
                "name": "WaterBody",
                "material": {
                    "type": "weakly_compressible_fluid",
                    "density": 1000.0,
                    "sound_speed": 20.0,
                },
                "particle_reserve_factor": 10.0,
            }
        ],
        "solid_bodies": [{"name": "WallBoundary", "material": {"type": "rigid_body"}}],
        "gravity": [0.0, -1.0],
        "observers": [
            {
                "name": "Obs",
                "observed_body": "WaterBody",
                "variable": {"real_type": "Pressure"},
                "positions": [[0.5, 0.2]],
            }
        ],
        "fluid_boundary_conditions": [
            {
                "body_name": "WaterBody",
                "aligned_box": "Inlet",
                "type": "emitter",
                "inflow_speed": 1.5,
            }
        ],
        "solver_parameters": {
            "end_time": 1.0,
            "output_interval": 0.01,
            "screen_interval": 100,
            "fluid_dynamics": {
                "acoustic_cfl": 0.6,
                "advection_cfl": 0.25,
                "flow_type": "free_surface",
                "particle_sort_frequency": 100,
            },
        },
    }
    data.update(overrides)
    return SimulationConfig(**data)


class TestDomainConfig:
    def test_valid(self):
        d = DomainConfig(lower_bound=[0.0, 0.0], upper_bound=[1.0, 2.0])
        assert d.upper_bound[1] == 2.0

    def test_non_increasing_bounds_rejected(self):
        with pytest.raises(ValidationError):
            DomainConfig(lower_bound=[0.0, 0.0], upper_bound=[1.0, 0.0])


class TestSimulationConfig:
    def test_minimal_fluid_config(self):
        cfg = _make_minimal_fluid_config()
        assert cfg.simulation_type.value == "fluid_dynamics"
        assert len(cfg.fluid_bodies) == 1

    def test_missing_fluid_solver_section_rejected(self):
        with pytest.raises(ValidationError, match="solver_parameters.fluid_dynamics"):
            _make_minimal_fluid_config(solver_parameters={"end_time": 1.0})

    def test_missing_fluid_bodies_rejected(self):
        with pytest.raises(ValidationError, match="requires fluid_bodies"):
            _make_minimal_fluid_config(fluid_bodies=[])

    def test_body_must_reference_shape_name(self):
        bad = {
            "fluid_bodies": [
                {
                    "name": "UnknownBody",
                    "material": {
                        "type": "weakly_compressible_fluid",
                        "density": 1000.0,
                        "sound_speed": 20.0,
                    },
                }
            ]
        }
        with pytest.raises(ValidationError, match="must match a shape name"):
            _make_minimal_fluid_config(**bad)

    def test_observer_observed_body_must_exist(self):
        with pytest.raises(ValidationError, match="observer observed_body"):
            _make_minimal_fluid_config(
                observers=[
                    {
                        "name": "Obs",
                        "observed_body": "MissingBody",
                        "variable": {"real_type": "Pressure"},
                        "positions": [[0.1, 0.2]],
                    }
                ]
            )

    def test_boundary_condition_requires_existing_aligned_box(self):
        with pytest.raises(ValidationError, match="aligned_box"):
            _make_minimal_fluid_config(
                fluid_boundary_conditions=[
                    {
                        "body_name": "WaterBody",
                        "aligned_box": "MissingBox",
                        "type": "emitter",
                        "inflow_speed": 1.0,
                    }
                ]
            )

    def test_dimensionality_mismatch_rejected(self):
        with pytest.raises(ValidationError, match="dimensionality"):
            _make_minimal_fluid_config(
                observers=[
                    {
                        "name": "Obs",
                        "observed_body": "WaterBody",
                        "variable": {"real_type": "Pressure"},
                        "positions": [[0.1, 0.2, 0.3]],
                    }
                ]
            )

    def test_roundtrip_json(self):
        cfg = _make_minimal_fluid_config()
        restored = SimulationConfig.model_validate_json(cfg.model_dump_json())
        assert restored == cfg

    def test_full_updated_fixture_validates(self):
        fixture_path = Path(__file__).parent / "examples" / "full_updated_simulation_config.json"
        payload = json.loads(fixture_path.read_text())
        cfg = SimulationConfig.model_validate(payload)

        assert cfg.simulation_type.value == "fluid_dynamics"
        assert cfg.solver_parameters.fluid_dynamics is not None
        assert cfg.fluid_bodies[0].particle_reserve_factor == pytest.approx(350.0)
        assert cfg.fluid_boundary_conditions[0].type.value == "emitter"
