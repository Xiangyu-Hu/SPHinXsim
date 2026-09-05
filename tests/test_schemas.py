"""Tests for sphinxsim.config.schemas (Pydantic validation)."""

import json
import math
from pathlib import Path

import pytest
from pydantic import ValidationError

from sphinxsim.config.schemas import DomainConfig, LogLevel, SimulationConfig


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
            "oriented_boxes": [
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
                "oriented_box": "Inlet",
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
                "surface_type": "free_surface",
                "particle_sort_frequency": 100,
            },
        },
    }
    data.update(overrides)
    return SimulationConfig(**data)


def _make_minimal_continuum_config(**overrides) -> SimulationConfig:
    data = {
        "simulation_type": "continuum_dynamics",
        "geometries": {
            "system_domain": {"lower_bound": [0.0, 0.0], "upper_bound": [1.0, 1.0]},
            "global_resolution": {"particle_spacing": 0.05},
            "shapes": [
                {
                    "name": "ContinuumBody",
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
        },
        "particle_generation": {
            "build_and_run": False,
            "settings": {
                "bodies": [
                    {"name": "ContinuumBody"},
                    {"name": "WallBoundary", "solid_body": {}},
                ],
                "relaxation_parameters": {"total_iterations": 1000},
            },
        },
        "continuum_bodies": [
            {
                "name": "ContinuumBody",
                "material": {
                    "type": "general_continuum",
                    "density": 1000.0,
                    "sound_speed": 20.0,
                    "youngs_modulus": 1.0e6,
                    "poisson_ratio": 0.3,
                },
            }
        ],
        "solid_bodies": [{"name": "WallBoundary", "material": {"type": "rigid_body"}}],
        "solver_parameters": {
            "end_time": 1.0,
            "output_interval": 0.01,
            "continuum_dynamics": {
                "acoustic_cfl": 0.4,
                "advection_cfl": 0.2,
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

    def test_nonplastic_solver_keeps_historical_continuum_defaults(self):
        cfg = _make_minimal_continuum_config()

        solver = cfg.model_dump(exclude_none=True)["solver_parameters"]["continuum_dynamics"]
        assert solver["linear_correction_matrix_coeff"] == pytest.approx(0.5)
        assert solver["contact_numerical_damping"] == pytest.approx(0.5)
        assert solver["shear_stress_damping"] == pytest.approx(0.0)
        assert solver["hourglass_factor"] == pytest.approx(2.0)

    def test_plastic_solver_removes_explicitly_supplied_irrelevant_controls(self):
        data = _make_minimal_continuum_config().model_dump(exclude_none=True)
        data["continuum_bodies"][0]["material"].update(
            {
                "type": "plastic_continuum",
                "friction_angle": math.radians(30.0),
            }
        )
        data["solver_parameters"]["continuum_dynamics"].update(
            {
                "linear_correction_matrix_coeff": 0.6,
                "contact_numerical_damping": 0.7,
                "shear_stress_damping": 0.8,
                "hourglass_factor": 1.5,
            }
        )

        cfg = SimulationConfig.model_validate(data)
        solver = cfg.model_dump(exclude_none=True)["solver_parameters"]["continuum_dynamics"]
        assert "linear_correction_matrix_coeff" not in solver
        assert "contact_numerical_damping" not in solver
        assert "shear_stress_damping" not in solver
        assert "hourglass_factor" not in solver

    def test_other_continuum_solver_controls_remain_explicitly_configurable(self):
        data = _make_minimal_continuum_config().model_dump(exclude_none=True)
        solver = data["solver_parameters"]["continuum_dynamics"]
        solver.update(
            {
                "linear_correction_matrix_coeff": 0.6,
                "contact_numerical_damping": 0.7,
                "shear_stress_damping": 0.8,
                "hourglass_factor": 1.5,
            }
        )

        cfg = SimulationConfig.model_validate(data)
        parsed = cfg.solver_parameters.continuum_dynamics
        assert parsed is not None
        assert parsed.linear_correction_matrix_coeff == pytest.approx(0.6)
        assert parsed.contact_numerical_damping == pytest.approx(0.7)
        assert parsed.shear_stress_damping == pytest.approx(0.8)
        assert parsed.hourglass_factor == pytest.approx(1.5)

    def test_minimal_fluid_config(self):
        cfg = _make_minimal_fluid_config()
        assert cfg.simulation_type.value == "fluid_dynamics"
        assert cfg.log_level is LogLevel.INFO
        assert len(cfg.fluid_bodies) == 1
        assert cfg.solver_parameters.fluid_dynamics is not None
        assert cfg.solver_parameters.fluid_dynamics.surface_type == "free_surface"

    def test_log_level_accepts_cpp_parser_values(self):
        for log_level in ("trace", "debug", "info", "warning", "error", "critical", "off"):
            cfg = _make_minimal_fluid_config(log_level=log_level)
            assert cfg.log_level.value == log_level

    def test_log_level_rejects_unknown_value(self):
        with pytest.raises(ValidationError, match="log_level"):
            _make_minimal_fluid_config(log_level="verbose")

    def test_fluid_solver_accepts_surface_type(self):
        cfg = _make_minimal_fluid_config(
            solver_parameters={
                "end_time": 1.0,
                "output_interval": 0.01,
                "screen_interval": 100,
                "fluid_dynamics": {
                    "acoustic_cfl": 0.6,
                    "advection_cfl": 0.25,
                    "surface_type": "open_boundary",
                    "particle_sort_frequency": 100,
                },
            }
        )
        assert cfg.solver_parameters.fluid_dynamics is not None
        assert cfg.solver_parameters.fluid_dynamics.surface_type == "open_boundary"

    def test_fluid_solver_rejects_unknown_key(self):
        with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
            _make_minimal_fluid_config(
                solver_parameters={
                    "end_time": 1.0,
                    "output_interval": 0.01,
                    "screen_interval": 100,
                    "fluid_dynamics": {
                        "acoustic_cfl": 0.6,
                        "advection_cfl": 0.25,
                        "surface_type": "free_surface",
                        "unsupported_key": "x",
                    },
                }
            )

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
                    },
                }
            ]
        }
        with pytest.raises(ValidationError, match="must match a shape name"):
            _make_minimal_fluid_config(**bad)

    def test_shape_reference_to_previous_shape_is_allowed(self):
        geometries = {
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
                    "name": "ExpandedWaterBody",
                    "type": "expanded_box",
                    "original": "WaterBody",
                    "expansion": 0.01,
                },
                {
                    "name": "WallBoundary",
                    "type": "bounding_box",
                    "lower_bound": [0.0, 0.0],
                    "upper_bound": [1.0, 1.0],
                },
            ],
            "oriented_boxes": [
                {
                    "name": "Inlet",
                    "type": "region",
                    "half_size": [0.1, 0.05],
                    "transform": {"translation": [0.05, 0.2], "rotation_angle": 0.0},
                }
            ],
        }
        cfg = _make_minimal_fluid_config(geometries=geometries)
        assert any(shape.name == "ExpandedWaterBody" for shape in cfg.geometries.shapes)

    def test_multipolygon_accepts_new_polygon_types(self):
        geometries = {
            "system_domain": {"lower_bound": [0.0, 0.0], "upper_bound": [1.0, 1.0]},
            "global_resolution": {"particle_spacing": 0.05},
            "shapes": [
                {
                    "name": "WaterBody",
                    "type": "multipolygon",
                    "polygons": [
                        {
                            "operation": "union",
                            "type": "circle",
                            "center": [0.2, 0.2],
                            "radius": 0.1,
                            "resolution": 24,
                        },
                        {
                            "operation": "union",
                            "type": "triangle",
                            "half_size": [0.1, 0.05],
                            "transform": {
                                "translation": [0.35, 0.25],
                                "rotation_angle": 0.0,
                            },
                        },
                        {
                            "operation": "union",
                            "type": "clockwise_points",
                            "points": [[0.6, 0.6], [0.8, 0.6], [0.8, 0.8], [0.6, 0.6]],
                        },
                    ],
                },
                {
                    "name": "WallBoundary",
                    "type": "bounding_box",
                    "lower_bound": [0.0, 0.0],
                    "upper_bound": [1.0, 1.0],
                },
            ],
            "oriented_boxes": [
                {
                    "name": "Inlet",
                    "type": "region",
                    "half_size": [0.1, 0.05],
                    "transform": {"translation": [0.05, 0.2], "rotation_angle": 0.0},
                }
            ],
        }

        cfg = _make_minimal_fluid_config(geometries=geometries)
        multipolygon = cfg.geometries.shapes[0]
        assert multipolygon.type.value == "multipolygon"
        assert [polygon.type.value for polygon in multipolygon.polygons] == [
            "circle",
            "triangle",
            "clockwise_points",
        ]

    def test_multipolygon_data_file_uses_file_name(self):
        geometries = {
            "system_domain": {"lower_bound": [0.0, 0.0], "upper_bound": [1.0, 1.0]},
            "global_resolution": {"particle_spacing": 0.05},
            "shapes": [
                {
                    "name": "WaterBody",
                    "type": "multipolygon",
                    "polygons": [
                        {
                            "operation": "union",
                            "type": "data_file",
                            "file_name": "water.dat",
                        }
                    ],
                },
                {
                    "name": "WallBoundary",
                    "type": "bounding_box",
                    "lower_bound": [0.0, 0.0],
                    "upper_bound": [1.0, 1.0],
                },
            ],
            "oriented_boxes": [
                {
                    "name": "Inlet",
                    "type": "region",
                    "half_size": [0.1, 0.05],
                    "transform": {"translation": [0.05, 0.2], "rotation_angle": 0.0},
                }
            ],
        }

        cfg = _make_minimal_fluid_config(geometries=geometries)
        data_file = cfg.geometries.shapes[0].polygons[0]
        assert data_file.type.value == "data_file"
        assert data_file.file_name == "water.dat"

    def test_triangle_mesh_uses_file_name(self):
        geometries = {
            "system_domain": {"lower_bound": [0.0, 0.0, 0.0], "upper_bound": [1.0, 1.0, 1.0]},
            "global_resolution": {"particle_spacing": 0.05},
            "shapes": [
                {
                    "name": "TetraBody",
                    "type": "triangle_mesh",
                    "file_name": "tetra.stl",
                },
                {
                    "name": "WallBoundary",
                    "type": "bounding_box",
                    "lower_bound": [0.0, 0.0, 0.0],
                    "upper_bound": [1.0, 1.0, 1.0],
                },
            ],
        }

        continuum_bodies = [
            {
                "name": "TetraBody",
                "material": {
                    "type": "general_continuum",
                    "density": 1000.0,
                    "sound_speed": 20.0,
                    "youngs_modulus": 1.0e6,
                    "poisson_ratio": 0.3,
                },
            }
        ]

        particle_generation = {
            "build_and_run": False,
            "settings": {
                "bodies": [
                    {"name": "TetraBody"},
                    {"name": "WallBoundary", "solid_body": {}},
                ],
                "relaxation_parameters": {"total_iterations": 1000},
            },
        }

        cfg = _make_minimal_continuum_config(
            geometries=geometries,
            continuum_bodies=continuum_bodies,
            particle_generation=particle_generation,
        )
        triangle_mesh = cfg.geometries.shapes[0]
        assert triangle_mesh.type.value == "triangle_mesh"
        assert triangle_mesh.file_name == "tetra.stl"

    def test_3d_dambreak_fixture_uses_supported_shapes(self):
        data_path = Path("tests/test_simulation/test_3d_simulation/data/dambreak.json")
        data = json.loads(data_path.read_text())

        cfg = SimulationConfig.model_validate(data)

        assert len(cfg.geometries.system_domain.lower_bound) == 3
        assert all(shape.type.value != "multipolygon" for shape in cfg.geometries.shapes)
        assert any(shape.type.value == "cylinder" for shape in cfg.geometries.shapes)

    def test_2d_config_rejects_cylinder_shape(self):
        data = _make_minimal_fluid_config().model_dump(mode="json", exclude_none=True)
        data["geometries"]["shapes"].append(
            {
                "name": "EmitterCylinder",
                "type": "cylinder",
                "radius": 0.05,
                "half_height": 0.025,
                "transform": {
                    "translation": [0.5, 1.5],
                    "rotation_angle": 0.0,
                },
            }
        )

        with pytest.raises(ValidationError, match="cylinder shapes are 3D-only"):
            SimulationConfig.model_validate(data)

    def test_3d_repose_angle_fixture_uses_plastic_continuum(self):
        data_path = Path("tests/test_simulation/test_3d_simulation/data/repose_angle.json")
        data = json.loads(data_path.read_text())

        cfg = SimulationConfig.model_validate(data)

        assert cfg.simulation_type.value == "continuum_dynamics"
        assert len(cfg.geometries.system_domain.lower_bound) == 3
        assert cfg.continuum_bodies[0].material.type.value == "plastic_continuum"
        assert all(shape.type.value != "multipolygon" for shape in cfg.geometries.shapes)

    def test_3d_config_rejects_multipolygon_shape(self):
        data_path = Path("tests/test_simulation/test_3d_simulation/data/dambreak.json")
        data = json.loads(data_path.read_text())
        data["geometries"]["shapes"][0] = {
            "name": "WaterBody",
            "type": "multipolygon",
            "polygons": [
                {
                    "operation": "union",
                    "type": "bounding_box",
                    "lower_bound": [0.0, 0.0],
                    "upper_bound": [1.0, 1.0],
                }
            ],
        }

        with pytest.raises(ValidationError, match="multipolygon shapes are 2D-only"):
            SimulationConfig.model_validate(data)

    def test_shape_duplicate_name_rejected(self):
        geometries = {
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
                    "name": "WaterBody",
                    "type": "expanded_box",
                    "original": "WaterBody",
                    "expansion": 0.01,
                },
                {
                    "name": "WallBoundary",
                    "type": "bounding_box",
                    "lower_bound": [0.0, 0.0],
                    "upper_bound": [1.0, 1.0],
                },
            ],
            "oriented_boxes": [
                {
                    "name": "Inlet",
                    "type": "region",
                    "half_size": [0.1, 0.05],
                    "transform": {"translation": [0.05, 0.2], "rotation_angle": 0.0},
                }
            ],
        }
        with pytest.raises(ValidationError, match="duplicate shape name"):
            _make_minimal_fluid_config(geometries=geometries)

    def test_duplicate_body_name_rejected(self):
        cfg = _make_minimal_fluid_config()
        data = cfg.model_dump(mode="json", exclude_none=True)
        data["fluid_bodies"].append(json.loads(json.dumps(data["fluid_bodies"][0])))

        with pytest.raises(ValidationError, match="fluid_bodies must use unique body names"):
            SimulationConfig.model_validate(data)

    def test_duplicate_particle_generation_body_name_rejected(self):
        cfg = _make_minimal_fluid_config()
        data = cfg.model_dump(mode="json", exclude_none=True)
        duplicate = json.loads(json.dumps(data["particle_generation"]["settings"]["bodies"][0]))
        data["particle_generation"]["settings"]["bodies"].append(duplicate)

        with pytest.raises(ValidationError, match="settings.bodies must use unique body names"):
            SimulationConfig.model_validate(data)

    @pytest.mark.parametrize("non_finite", [math.nan, math.inf, -math.inf])
    def test_non_finite_numeric_value_rejected(self, non_finite):
        cfg = _make_minimal_fluid_config()
        data = cfg.model_dump(mode="json", exclude_none=True)
        data["gravity"][1] = non_finite

        with pytest.raises(ValidationError, match="all numeric configuration values must be finite"):
            SimulationConfig.model_validate(data)

    def test_shape_reference_must_be_previously_defined(self):
        geometries = {
            "system_domain": {"lower_bound": [0.0, 0.0], "upper_bound": [1.0, 1.0]},
            "global_resolution": {"particle_spacing": 0.05},
            "shapes": [
                {
                    "name": "ExpandedWaterBody",
                    "type": "expanded_box",
                    "original": "WaterBody",
                    "expansion": 0.01,
                },
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
            "oriented_boxes": [
                {
                    "name": "Inlet",
                    "type": "region",
                    "half_size": [0.1, 0.05],
                    "transform": {"translation": [0.05, 0.2], "rotation_angle": 0.0},
                }
            ],
        }
        with pytest.raises(ValidationError, match="previously defined"):
            _make_minimal_fluid_config(geometries=geometries)

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

    def test_boundary_condition_requires_existing_oriented_box(self):
        with pytest.raises(ValidationError, match="oriented_box"):
            _make_minimal_fluid_config(
                fluid_boundary_conditions=[
                    {
                        "body_name": "WaterBody",
                        "oriented_box": "MissingBox",
                        "type": "emitter",
                        "inflow_speed": 1.0,
                    }
                ]
            )

    def test_particle_generation_blockers_accept_existing_oriented_box(self):
        cfg = _make_minimal_fluid_config(
            particle_generation={
                "build_and_run": False,
                "settings": {
                    "bodies": [
                        {"name": "WaterBody", "blockers": ["Inlet"]},
                        {"name": "WallBoundary", "solid_body": {}},
                    ],
                    "relaxation_parameters": {"total_iterations": 1000},
                },
            }
        )
        assert cfg.particle_generation.settings is not None
        assert cfg.particle_generation.settings.bodies[0].blockers == ["Inlet"]

    def test_particle_generation_blockers_reject_unknown_oriented_box(self):
        with pytest.raises(ValidationError, match="blockers entries must reference existing"):
            _make_minimal_fluid_config(
                particle_generation={
                    "build_and_run": False,
                    "settings": {
                        "bodies": [
                            {"name": "WaterBody", "blockers": ["MissingBox"]},
                            {"name": "WallBoundary", "solid_body": {}},
                        ],
                        "relaxation_parameters": {"total_iterations": 1000},
                    },
                }
            )

    def test_particle_generation_box_shape_inserts_accept_existing_shape(self):
        cfg = _make_minimal_fluid_config(
            particle_generation={
                "build_and_run": False,
                "settings": {
                    "bodies": [
                        {"name": "WaterBody", "box_shape_inserts": ["WallBoundary"]},
                        {"name": "WallBoundary", "solid_body": {}},
                    ],
                    "relaxation_parameters": {"total_iterations": 1000},
                },
            }
        )
        assert cfg.particle_generation.settings is not None
        assert cfg.particle_generation.settings.bodies[0].box_shape_inserts == ["WallBoundary"]

    def test_particle_generation_box_shape_inserts_reject_unknown_shape(self):
        with pytest.raises(ValidationError, match="box_shape_inserts entries must reference existing"):
            _make_minimal_fluid_config(
                particle_generation={
                    "build_and_run": False,
                    "settings": {
                        "bodies": [
                            {"name": "WaterBody", "box_shape_inserts": ["MissingShape"]},
                            {"name": "WallBoundary", "solid_body": {}},
                        ],
                        "relaxation_parameters": {"total_iterations": 1000},
                    },
                }
            )

    def test_particle_generation_box_shape_inserts_reject_non_box_shape(self):
        data_path = Path("tests/test_simulation/test_3d_simulation/data/dambreak.json")
        data = json.loads(data_path.read_text())
        data["particle_generation"]["settings"]["bodies"][0]["box_shape_inserts"] = ["WallBoundary"]

        with pytest.raises(ValidationError, match="box-compatible shapes"):
            SimulationConfig.model_validate(data)

    def test_particle_generation_cylinder_shape_inserts_accept_existing_cylinder_shape(self):
        data_path = Path("tests/test_simulation/test_3d_simulation/data/dambreak.json")
        data = json.loads(data_path.read_text())

        cfg = SimulationConfig.model_validate(data)

        assert cfg.particle_generation.settings is not None
        assert cfg.particle_generation.settings.bodies[0].cylinder_shape_inserts == ["EmitterCylinder"]

    def test_particle_generation_cylinder_shape_inserts_reject_unknown_shape(self):
        data_path = Path("tests/test_simulation/test_3d_simulation/data/dambreak.json")
        data = json.loads(data_path.read_text())
        data["particle_generation"]["settings"]["bodies"][0]["cylinder_shape_inserts"] = ["MissingShape"]

        with pytest.raises(ValidationError, match="cylinder_shape_inserts entries must reference existing"):
            SimulationConfig.model_validate(data)

    def test_particle_generation_cylinder_shape_inserts_reject_non_cylinder_shape(self):
        data_path = Path("tests/test_simulation/test_3d_simulation/data/dambreak.json")
        data = json.loads(data_path.read_text())
        data["particle_generation"]["settings"]["bodies"][0]["cylinder_shape_inserts"] = ["WallInnerBox"]

        with pytest.raises(ValidationError, match="must reference cylinder shapes"):
            SimulationConfig.model_validate(data)

    def test_particle_generation_body_unknown_field_warns_and_is_preserved(self):
        with pytest.warns(UserWarning, match="contains unknown keys that are preserved"):
            cfg = _make_minimal_fluid_config(
                particle_generation={
                    "build_and_run": False,
                    "settings": {
                        "bodies": [
                            {"name": "WaterBody", "unknown_key": ["Inlet"]},
                            {"name": "WallBoundary", "solid_body": {}},
                        ],
                        "relaxation_parameters": {"total_iterations": 1000},
                    },
                }
            )

        dumped = cfg.model_dump(mode="json")
        assert "unknown_key" in dumped["particle_generation"]["settings"]["bodies"][0]
        assert dumped["particle_generation"]["settings"]["bodies"][0]["unknown_key"] == ["Inlet"]

    def test_global_resolution_is_required(self):
        data = _make_minimal_fluid_config().model_dump(mode="json")
        del data["geometries"]["global_resolution"]

        with pytest.raises(ValidationError, match="global_resolution"):
            SimulationConfig.model_validate(data)

    def test_body_constraint_region_requires_existing_oriented_box(self):
        cfg = _make_minimal_fluid_config(
            body_constraints=[
                {
                    "body_name": "WallBoundary",
                    "type": "fixed",
                    "region": "Inlet",
                }
            ]
        )
        assert cfg.body_constraints[0].region == "Inlet"

        with pytest.raises(ValidationError, match="existing oriented box"):
            _make_minimal_fluid_config(
                body_constraints=[
                    {
                        "body_name": "WallBoundary",
                        "type": "fixed",
                        "region": "WallBoundary",
                    }
                ]
            )

    def test_extra_state_recording_accepts_int_type(self):
        cfg = _make_minimal_fluid_config(
            extra_state_recording=[
                {
                    "name": "WaterBody",
                    "variables": [{"int_type": ["BufferIndicator", "Indicator"]}],
                }
            ]
        )
        assert cfg.extra_state_recording[0].variables[0].int_type == ["BufferIndicator", "Indicator"]

    def test_extra_state_recording_rejects_unknown_variable_type_key(self):
        with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
            _make_minimal_fluid_config(
                extra_state_recording=[
                    {
                        "name": "WaterBody",
                        "variables": [{"bool_type": ["Flag"]}],
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

    def test_filling_tank_fixture_accepts_boundary_condition_schedule(self):
        fixture_path = (
            Path(__file__).parent
            / "test_simulation"
            / "test_2d_simulation"
            / "data"
            / "filling_tank.json"
        )
        payload = json.loads(fixture_path.read_text())
        cfg = SimulationConfig.model_validate(payload)

        schedule = cfg.fluid_boundary_conditions[0].on_schedule
        assert schedule is not None
        assert schedule.switch_on_time == pytest.approx(1.0)
        assert schedule.duration == pytest.approx(25.0)

    def test_boundary_condition_schedule_rejects_negative_duration(self):
        with pytest.raises(ValidationError):
            _make_minimal_fluid_config(
                fluid_boundary_conditions=[
                    {
                        "body_name": "WaterBody",
                        "oriented_box": "Inlet",
                        "type": "emitter",
                        "inflow_speed": 1.5,
                        "on_schedule": {
                            "switch_on_time": 0.0,
                            "duration": -1.0,
                        },
                    }
                ]
            )

    def test_boundary_condition_schedule_allows_missing_duration(self):
        cfg = _make_minimal_fluid_config(
            fluid_boundary_conditions=[
                {
                    "body_name": "WaterBody",
                    "oriented_box": "Inlet",
                    "type": "emitter",
                    "inflow_speed": 1.5,
                    "on_schedule": {
                        "switch_on_time": 0.0,
                    },
                }
            ]
        )

        schedule = cfg.fluid_boundary_conditions[0].on_schedule
        assert schedule is not None
        assert schedule.switch_on_time == pytest.approx(0.0)
        assert schedule.duration is None

    def test_fluid_dynamics_rejects_multiple_fluid_bodies(self):
        data = _make_minimal_fluid_config().model_dump(mode="json", exclude_none=True)
        extra_body = json.loads(json.dumps(data["fluid_bodies"][0]))
        extra_body["name"] = "WaterBody2"
        data["geometries"]["shapes"].append(
            {
                "name": "WaterBody2",
                "type": "bounding_box",
                "lower_bound": [0.45, 0.0],
                "upper_bound": [0.8, 0.2],
            }
        )
        data["particle_generation"]["settings"]["bodies"].append({"name": "WaterBody2"})
        data["fluid_bodies"].append(extra_body)

        with pytest.raises(ValidationError, match="exactly one fluid body"):
            SimulationConfig.model_validate(data)

    def test_continuum_dynamics_rejects_multiple_continuum_bodies(self):
        data = _make_minimal_continuum_config().model_dump(mode="json", exclude_none=True)
        extra_body = json.loads(json.dumps(data["continuum_bodies"][0]))
        extra_body["name"] = "ContinuumBody2"
        data["geometries"]["shapes"].append(
            {
                "name": "ContinuumBody2",
                "type": "bounding_box",
                "lower_bound": [0.45, 0.0],
                "upper_bound": [0.8, 0.2],
            }
        )
        data["particle_generation"]["settings"]["bodies"].append({"name": "ContinuumBody2"})
        data["continuum_bodies"].append(extra_body)

        with pytest.raises(ValidationError, match="exactly one continuum body"):
            SimulationConfig.model_validate(data)

    def test_3d_transform_requires_rotation_axis(self):
        with pytest.raises(ValidationError, match="3D transform requires rotation_axis"):
            _make_minimal_continuum_config(
                geometries={
                    "system_domain": {"lower_bound": [0.0, 0.0, 0.0], "upper_bound": [1.0, 1.0, 1.0]},
                    "global_resolution": {"particle_spacing": 0.05},
                    "primitives": [
                        {
                            "name": "BoxPrimitive",
                            "type": "box",
                            "half_size": [0.1, 0.1, 0.1],
                            "transform": {
                                "translation": [0.1, 0.1, 0.1],
                                "rotation_angle": 0.0,
                            },
                        }
                    ],
                    "shapes": [
                        {
                            "name": "ContinuumBody",
                            "type": "box",
                            "primitive": "BoxPrimitive",
                        },
                        {
                            "name": "WallBoundary",
                            "type": "bounding_box",
                            "lower_bound": [0.0, 0.0, 0.0],
                            "upper_bound": [1.0, 1.0, 1.0],
                        },
                    ],
                }
            )

    def test_initial_condition_real_type_rejects_vector_value(self):
        with pytest.raises(ValidationError, match="real_type assignment requires a scalar"):
            _make_minimal_fluid_config(
                initial_conditions=[
                    {
                        "body_name": "WaterBody",
                        "assignments": [
                            {
                                "variable": {"real_type": "Pressure"},
                                "value": [1.0, 2.0],
                            }
                        ],
                    }
                ]
            )

    def test_initial_condition_vector_type_rejects_scalar_value(self):
        with pytest.raises(ValidationError, match="vector_type assignment requires a vector"):
            _make_minimal_fluid_config(
                initial_conditions=[
                    {
                        "body_name": "WaterBody",
                        "assignments": [
                            {
                                "variable": {"vector_type": "Velocity"},
                                "value": 1.0,
                            }
                        ],
                    }
                ]
            )

    def test_emitter_accepts_multi_species_phases_and_volume_fractions(self):
        cfg = _make_minimal_fluid_config(
            fluid_bodies=[
                {
                    "name": "WaterBody",
                    "material": {
                        "type": "weakly_compressible_multi_phase",
                        "pure_phases": [
                            {"name": "Water", "density": 1.0},
                            {"name": "Oil", "density": 0.8},
                        ],
                        "multi_species_phases": [
                            {
                                "name": "SpeciesPhaseA",
                                "species": [
                                    {"name": "SpeciesA", "density": 2.0},
                                    {"name": "SpeciesB", "density": 0.5},
                                ],
                            }
                        ],
                    },
                    "particle_reserve_factor": 10.0,
                }
            ],
            fluid_boundary_conditions=[
                {
                    "body_name": "WaterBody",
                    "oriented_box": "Inlet",
                    "type": "emitter",
                    "inflow_speed": 1.5,
                    "multi_species_phases": [
                        {
                            "phase_name": "SpeciesPhaseA",
                            "mass_fractions": [0.4, 0.6],
                        }
                    ],
                    "volume_fractions": [0.2, 0.3, 0.5],
                }
            ]
        )

        bc = cfg.fluid_boundary_conditions[0]
        assert bc.multi_species_phases is not None
        assert bc.multi_species_phases[0].phase_name == "SpeciesPhaseA"
        assert bc.multi_species_phases[0].mass_fractions == pytest.approx([0.4, 0.6])
        assert bc.volume_fractions == pytest.approx([0.2, 0.3, 0.5])

    def test_emitter_rejects_multi_species_phase_mass_fractions_not_normalized(self):
        with pytest.raises(ValidationError, match="multi_species_phases mass_fractions must sum to 1.0"):
            _make_minimal_fluid_config(
                fluid_bodies=[
                    {
                        "name": "WaterBody",
                        "material": {
                            "type": "weakly_compressible_multi_phase",
                            "pure_phases": [
                                {"name": "Water", "density": 1.0},
                            ],
                            "multi_species_phases": [
                                {
                                    "name": "SpeciesPhaseA",
                                    "species": [
                                        {"name": "SpeciesA", "density": 2.0},
                                        {"name": "SpeciesB", "density": 0.5},
                                    ],
                                }
                            ],
                        },
                        "particle_reserve_factor": 10.0,
                    }
                ],
                fluid_boundary_conditions=[
                    {
                        "body_name": "WaterBody",
                        "oriented_box": "Inlet",
                        "type": "emitter",
                        "inflow_speed": 1.5,
                        "multi_species_phases": [
                            {
                                "phase_name": "SpeciesPhaseA",
                                "mass_fractions": [0.8, 0.3],
                            }
                        ],
                    }
                ]
            )

    def test_emitter_rejects_volume_fractions_not_normalized(self):
        with pytest.raises(ValidationError, match="volume_fractions must sum to 1.0"):
            _make_minimal_fluid_config(
                fluid_bodies=[
                    {
                        "name": "WaterBody",
                        "material": {
                            "type": "weakly_compressible_multi_phase",
                            "pure_phases": [
                                {"name": "Water", "density": 1.0},
                                {"name": "Oil", "density": 0.8},
                            ],
                        },
                        "particle_reserve_factor": 10.0,
                    }
                ],
                fluid_boundary_conditions=[
                    {
                        "body_name": "WaterBody",
                        "oriented_box": "Inlet",
                        "type": "emitter",
                        "inflow_speed": 1.5,
                        "volume_fractions": [0.2, 0.2],
                    }
                ]
            )

    def test_bi_directional_rejects_multi_species_phases(self):
        with pytest.raises(
            ValidationError,
            match="multi_species_phases are only supported for emitter boundary conditions",
        ):
            _make_minimal_fluid_config(
                fluid_boundary_conditions=[
                    {
                        "body_name": "WaterBody",
                        "oriented_box": "Inlet",
                        "type": "bi_directional",
                        "pressure": 1000.0,
                        "multi_species_phases": [
                            {
                                "phase_name": "SpeciesPhaseA",
                                "mass_fractions": [0.5, 0.5],
                            }
                        ],
                    }
                ]
            )

    def test_column_collapse_fixture_accepts_plastic_continuum(self):
        fixture_path = (
            Path(__file__).parent
            / "test_simulation"
            / "test_2d_simulation"
            / "data"
            / "column_collapse.json"
        )
        payload = json.loads(fixture_path.read_text())
        cfg = SimulationConfig.model_validate(payload)

        material = cfg.continuum_bodies[0].material
        assert material.type.value == "plastic_continuum"
        assert material.friction_angle is not None
        assert material.cohesion is not None
        assert material.dilatancy_angle is not None

    def test_plastic_continuum_converts_degree_angles_to_radians(self):
        with pytest.warns(UserWarning, match="Corrected friction_angle from 30 degrees"):
            cfg = _make_minimal_continuum_config(
                continuum_bodies=[
                    {
                        "name": "ContinuumBody",
                        "material": {
                            "type": "plastic_continuum",
                            "density": 1000.0,
                            "youngs_modulus": 1.0e6,
                            "poisson_ratio": 0.3,
                            "friction_angle": 30.0,
                        },
                    }
                ]
            )
        material = cfg.continuum_bodies[0].material
        assert material.type.value == "plastic_continuum"
        assert material.friction_angle == pytest.approx(math.pi / 6)
        assert material.cohesion is None
        assert material.dilatancy_angle is None

    def test_plastic_continuum_accepts_optional_sound_speed_override(self):
        material = {
            "type": "plastic_continuum",
            "density": 1000.0,
            "youngs_modulus": 1.0e6,
            "poisson_ratio": 0.3,
            "friction_angle": math.radians(30),
            "sound_speed": 42.0,
        }

        cfg = _make_minimal_continuum_config(
            continuum_bodies=[{"name": "ContinuumBody", "material": material}]
        )

        assert cfg.continuum_bodies[0].material.sound_speed == pytest.approx(42.0)

    @pytest.mark.parametrize(
        ("updates", "message"),
        [
            ({"poisson_ratio": 0.5}, "0 <= poisson_ratio < 0.5"),
            ({"friction_angle": math.pi / 2}, "friction_angle in radians"),
            (
                {"friction_angle": math.radians(30), "dilatancy_angle": math.radians(35)},
                "dilatancy_angle <= friction_angle",
            ),
        ],
    )
    def test_plastic_continuum_rejects_unphysical_parameters(self, updates, message):
        material = {
            "type": "plastic_continuum",
            "density": 1000.0,
            "youngs_modulus": 1.0e6,
            "poisson_ratio": 0.3,
            "friction_angle": math.radians(30),
        }
        material.update(updates)
        with pytest.raises(ValidationError, match=message):
            _make_minimal_continuum_config(
                continuum_bodies=[{"name": "ContinuumBody", "material": material}]
            )

    def test_plastic_continuum_converts_dilatancy_degrees(self):
        material = {
            "type": "plastic_continuum",
            "density": 1000.0,
            "youngs_modulus": 1.0e6,
            "poisson_ratio": 0.3,
            "friction_angle": 30.0,
            "dilatancy_angle": 10.0,
        }
        with pytest.warns(UserWarning) as caught:
            cfg = _make_minimal_continuum_config(
                continuum_bodies=[{"name": "ContinuumBody", "material": material}]
            )
        assert len(caught) == 2
        assert cfg.continuum_bodies[0].material.friction_angle == pytest.approx(math.pi / 6)
        assert cfg.continuum_bodies[0].material.dilatancy_angle == pytest.approx(math.radians(10))

    def test_plastic_continuum_requires_friction_angle(self):
        with pytest.raises(ValidationError, match="plastic_continuum requires"):
            _make_minimal_continuum_config(
                continuum_bodies=[
                    {
                        "name": "ContinuumBody",
                        "material": {
                            "type": "plastic_continuum",
                            "density": 1000.0,
                            "youngs_modulus": 1.0e6,
                            "poisson_ratio": 0.3,
                        },
                    }
                ]
            )

    def test_continuum_solver_accepts_surface_type(self):
        cfg = _make_minimal_continuum_config(
            solver_parameters={
                "end_time": 1.0,
                "output_interval": 0.01,
                "continuum_dynamics": {
                    "acoustic_cfl": 0.4,
                    "advection_cfl": 0.2,
                    "surface_type": "open_boundary",
                },
            }
        )
        assert cfg.solver_parameters.continuum_dynamics is not None
        assert cfg.solver_parameters.continuum_dynamics.surface_type == "open_boundary"

    def test_continuum_solver_rejects_invalid_surface_type(self):
        with pytest.raises(ValidationError):
            _make_minimal_continuum_config(
                solver_parameters={
                    "end_time": 1.0,
                    "output_interval": 0.01,
                    "continuum_dynamics": {
                        "acoustic_cfl": 0.4,
                        "advection_cfl": 0.2,
                        "surface_type": "invalid_surface",
                    },
                }
            )

    def test_continuum_solver_accepts_plastic_riemann_dissipation_factor(self):
        cfg = _make_minimal_continuum_config(
            solver_parameters={
                "end_time": 1.0,
                "output_interval": 0.01,
                "continuum_dynamics": {
                    "acoustic_cfl": 0.4,
                    "advection_cfl": 0.2,
                    "plastic_riemann_dissipation_factor": 35.0,
                },
            }
        )
        assert cfg.solver_parameters.continuum_dynamics is not None
        assert (
            cfg.solver_parameters.continuum_dynamics.plastic_riemann_dissipation_factor
            == pytest.approx(35.0)
        )

    def test_continuum_solver_rejects_non_positive_plastic_riemann_dissipation_factor(self):
        with pytest.raises(ValidationError):
            _make_minimal_continuum_config(
                solver_parameters={
                    "end_time": 1.0,
                    "output_interval": 0.01,
                    "continuum_dynamics": {
                        "acoustic_cfl": 0.4,
                        "advection_cfl": 0.2,
                        "plastic_riemann_dissipation_factor": 0.0,
                    },
                }
            )

    def test_heat_transfer_fixture_accepts_thermal_properties(self):
        fixture_path = (
            Path(__file__).parent
            / "test_simulation"
            / "test_2d_simulation"
            / "data"
            / "heat_transfer.json"
        )
        payload = json.loads(fixture_path.read_text())
        cfg = SimulationConfig.model_validate(payload)

        thermal = cfg.fluid_bodies[0].material.thermal_properties
        assert thermal is not None
        expected_conductivity = payload["fluid_bodies"][0]["material"]["thermal_properties"][
            "thermal_conductivity"
        ]
        expected_heat_capacity = payload["fluid_bodies"][0]["material"]["thermal_properties"][
            "volumetric_heat_capacity"
        ]
        assert thermal.thermal_conductivity == pytest.approx(expected_conductivity)
        assert thermal.thermal_conductivity > 0.0
        assert thermal.volumetric_heat_capacity == pytest.approx(expected_heat_capacity)
        assert thermal.volumetric_heat_capacity > 0.0
        assert cfg.solid_bodies[0].material.thermal_properties is not None
        assert cfg.solid_bodies[0].material.thermal_properties.thermal_boundary.value == "Dirichlet"
        assert len(cfg.initial_conditions) == 2
        assert cfg.initial_conditions[0].body_name == "WallBoundary"
        assert cfg.initial_conditions[0].assignments[0].variable.real_type == "Temperature"

    def test_t_junction_fixture_accepts_multi_species_and_mass_fractions(self):
        fixture_path = (
            Path(__file__).parent
            / "test_simulation"
            / "test_2d_simulation"
            / "data"
            / "t_junction.json"
        )
        payload = json.loads(fixture_path.read_text())
        cfg = SimulationConfig.model_validate(payload)

        material = cfg.fluid_bodies[0].material
        assert material.type.value == "weakly_compressible_multi_species"
        assert len(material.species) == 3

        first_bc = cfg.fluid_boundary_conditions[0]
        assert first_bc.mass_fractions == pytest.approx([0.5, 0.3, 0.2])

    def test_t_junction_fixture_rejects_mass_fractions_not_normalized(self):
        fixture_path = (
            Path(__file__).parent
            / "test_simulation"
            / "test_2d_simulation"
            / "data"
            / "t_junction.json"
        )
        payload = json.loads(fixture_path.read_text())
        payload["fluid_boundary_conditions"][0]["mass_fractions"] = [0.6, 0.3, 0.2]

        with pytest.raises(ValidationError, match="mass_fractions must sum to 1.0"):
            SimulationConfig.model_validate(payload)

    def test_t_junction_fixture_rejects_mass_fractions_out_of_range(self):
        fixture_path = (
            Path(__file__).parent
            / "test_simulation"
            / "test_2d_simulation"
            / "data"
            / "t_junction.json"
        )
        payload = json.loads(fixture_path.read_text())
        payload["fluid_boundary_conditions"][0]["mass_fractions"] = [1.2, -0.1, -0.1]

        with pytest.raises(ValidationError, match="mass_fractions values must be in \\[0, 1\\]"):
            SimulationConfig.model_validate(payload)

    def test_fluid_solver_accepts_max_velocity_factor(self):
        cfg = _make_minimal_fluid_config(
            solver_parameters={
                "end_time": 1.0,
                "output_interval": 0.01,
                "screen_interval": 100,
                "fluid_dynamics": {
                    "acoustic_cfl": 0.6,
                    "advection_cfl": 0.25,
                    "max_velocity_factor": 2.0,
                    "surface_type": "free_surface",
                    "particle_sort_frequency": 100,
                },
            }
        )
        assert cfg.solver_parameters.fluid_dynamics is not None
        assert cfg.solver_parameters.fluid_dynamics.max_velocity_factor == pytest.approx(2.0)

    def test_fluid_material_accepts_viscosity_reynolds_number_object(self):
        cfg = _make_minimal_fluid_config(
            fluid_bodies=[
                {
                    "name": "WaterBody",
                    "material": {
                        "type": "weakly_compressible_fluid",
                        "density": 1000.0,
                        "viscosity": {"Reynolds_number": 50.0},
                    },
                }
            ]
        )
        assert cfg.fluid_bodies[0].material.viscosity is not None

    def test_fluid_solver_max_velocity_factor_default(self):
        cfg = _make_minimal_fluid_config()
        assert cfg.solver_parameters.fluid_dynamics is not None
        assert cfg.solver_parameters.fluid_dynamics.max_velocity_factor == pytest.approx(1.0)

    def test_fluid_material_accepts_thermal_properties(self):
        cfg = _make_minimal_fluid_config(
            fluid_bodies=[
                {
                    "name": "WaterBody",
                    "material": {
                        "type": "weakly_compressible_fluid",
                        "density": 1000.0,
                        "thermal_properties": {
                            "thermal_conductivity": 0.6,
                            "volumetric_heat_capacity": 4181.3,
                        },
                    },
                }
            ]
        )
        thermal = cfg.fluid_bodies[0].material.thermal_properties
        assert thermal is not None
        assert thermal.thermal_conductivity == pytest.approx(0.6)
        assert thermal.volumetric_heat_capacity == pytest.approx(4181.3)

    def test_fluid_material_rejects_incomplete_thermal_properties(self):
        with pytest.raises(ValidationError, match="thermal_properties requires"):
            _make_minimal_fluid_config(
                fluid_bodies=[
                    {
                        "name": "WaterBody",
                        "material": {
                            "type": "weakly_compressible_fluid",
                            "density": 1000.0,
                            "thermal_properties": {
                                "thermal_conductivity": 0.6,
                            },
                        },
                    }
                ]
            )

    def test_solid_material_accepts_thermal_boundary_mode(self):
        cfg = _make_minimal_fluid_config(
            solid_bodies=[
                {
                    "name": "WallBoundary",
                    "material": {
                        "type": "rigid_body",
                        "thermal_properties": {
                            "thermal_boundary": "Dirichlet",
                        },
                    },
                }
            ]
        )
        thermal = cfg.solid_bodies[0].material.thermal_properties
        assert thermal is not None
        assert thermal.thermal_boundary is not None
        assert thermal.thermal_boundary.value == "Dirichlet"

    def test_characteristic_dimensions_support_new_base_units(self):
        cfg = _make_minimal_fluid_config(
            characteristic_dimensions=[
                {
                    "value": 1.0,
                    "name": "Length",
                    "hint": "geometries.system_domain.upper_bound",
                },
                {
                    "value": 1.0,
                    "name": "Temperature",
                    "hint": "geometries.system_domain.upper_bound",
                },
                {
                    "value": 1.0,
                    "name": "ElectricCurrent",
                    "hint": "geometries.system_domain.upper_bound",
                },
                {
                    "value": 1.0,
                    "name": "AmountOfSubstance",
                    "hint": "geometries.system_domain.upper_bound",
                },
                {
                    "value": 1.0,
                    "name": "LuminousIntensity",
                    "hint": "geometries.system_domain.upper_bound",
                },
                {
                    "value": 1.0,
                    "name": "AngularVelocity",
                    "hint": "geometries.system_domain.upper_bound",
                },
            ]
        )
        names = {d.name.value for d in cfg.characteristic_dimensions or []}
        assert "Temperature" in names
        assert "ElectricCurrent" in names
        assert "AmountOfSubstance" in names
        assert "LuminousIntensity" in names
        assert "AngularVelocity" in names

    def test_continuum_config_can_omit_restart(self):
        cfg = _make_minimal_continuum_config()
        assert cfg.restart is None

    def test_complex_shape_disallows_intersection(self):
        with pytest.raises(ValidationError, match="only support union and subtraction"):
            _make_minimal_fluid_config(
                geometries={
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
                        {
                            "name": "BadComplex",
                            "type": "complex_shape",
                            "sub_shapes": ["WaterBody", "WallBoundary"],
                            "operations": ["union", "intersection"],
                        },
                    ],
                }
            )

    def test_3d_fuel_tank_fixture_accepts_pin_constraint_without_velocity(self):
        fixture_path = (
            Path(__file__).parent
            / "test_simulation"
            / "test_3d_simulation"
            / "data"
            / "fuel_tank.json"
        )
        cfg = SimulationConfig.model_validate(json.loads(fixture_path.read_text()))

        assert cfg.solid_bodies[0].is_moving is True
        assert cfg.body_constraints[0].mobilized_body == "pin"
        assert cfg.body_constraints[0].velocity is None
