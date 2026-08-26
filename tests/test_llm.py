"""Tests for the MockLLM natural-language → config conversion."""

import json
import math

import pytest
from pydantic import ValidationError

from sphinxsim.config.schemas import SimulationConfig
from sphinxsim.llm.common import (
    apply_explicit_instruction_overrides,
    apply_stl_geometry_overrides,
    dump_simulation_config_json,
    example_config,
    infer_requested_material_type,
    infer_requested_simulation_type,
    sanitize_config_dict,
)
from sphinxsim.llm.mock_llm import MockLLM, PhysicsType, _detect_physics


# ---------------------------------------------------------------------------
# _detect_physics helper
# ---------------------------------------------------------------------------


class TestDetectPhysics:
    def test_fluid_keywords(self):
        assert _detect_physics("water flowing through a pipe") == PhysicsType.FLUID
        assert _detect_physics("channel flow simulation") == PhysicsType.FLUID
        assert _detect_physics("Navier-Stokes solver") == PhysicsType.FLUID

    def test_solid_keywords(self):
        assert _detect_physics("elastic beam under load") == PhysicsType.SOLID
        assert _detect_physics("deformation of a steel plate") == PhysicsType.SOLID

    def test_plastic_continuum_keywords(self):
        assert _detect_physics("granular soil column collapse") == PhysicsType.PLASTIC_CONTINUUM
        assert _detect_physics("landslide with plastic continuum material") == PhysicsType.PLASTIC_CONTINUUM

    def test_fsi_keywords(self):
        assert _detect_physics("fsi simulation of a flexible flap") == PhysicsType.FSI
        assert _detect_physics("fluid-structure interaction") == PhysicsType.FSI

    def test_both_fluid_and_solid_yields_fsi(self):
        assert _detect_physics("water flow over an elastic structure") == PhysicsType.FSI

    def test_unknown_defaults_to_fluid(self):
        assert _detect_physics("some random text") == PhysicsType.FLUID


# ---------------------------------------------------------------------------
# MockLLM.generate
# ---------------------------------------------------------------------------


class TestMockLLM:
    def setup_method(self):
        self.llm = MockLLM()

    def test_returns_simulation_config(self):
        cfg = self.llm.generate("simulate water flowing through a pipe")
        assert isinstance(cfg, SimulationConfig)

    def test_physics_fluid(self):
        cfg = self.llm.generate("water dam break simulation")
        assert cfg.fluid_bodies[0].name

    def test_physics_solid(self):
        cfg = self.llm.generate("elastic beam bending under load")
        assert cfg.simulation_type.value == "continuum_dynamics"

    def test_physics_plastic_continuum(self):
        cfg = self.llm.generate("2D column collapse of granular soil using plastic continuum")
        assert cfg.simulation_type.value == "continuum_dynamics"
        assert cfg.continuum_bodies[0].material.type.value == "plastic_continuum"
        assert cfg.continuum_bodies[0].material.friction_angle is not None

    def test_plastic_generation_does_not_add_observer_by_default(self):
        cfg = self.llm.generate("granular soil column collapse using plastic continuum")
        assert cfg.observers == []

    def test_plastic_generation_preserves_explicit_observer_request(self):
        cfg = self.llm.generate(
            "granular soil column collapse using plastic continuum with observer at (0.5, 0.2)"
        )
        assert len(cfg.observers) == 1

    def test_plastic_generation_respects_explicit_no_observer_request(self):
        cfg = self.llm.generate(
            "granular soil column collapse using plastic continuum without an observer"
        )
        assert cfg.observers == []

    def test_physics_fsi(self):
        cfg = self.llm.generate("hydroelastic fluid-structure interaction")
        assert cfg.solver_parameters.end_time is not None

    def test_name_extracted(self):
        cfg = self.llm.generate("water flowing through a pipe at 2 m/s")
        assert len(cfg.fluid_bodies[0].name) > 0

    def test_velocity_override(self):
        cfg = self.llm.generate("water flowing at 3 m/s through a channel")
        assert cfg.solver_parameters.fluid_dynamics is not None
        assert cfg.solver_parameters.fluid_dynamics.max_velocity_factor == pytest.approx(3.0)

    def test_end_time_override(self):
        cfg = self.llm.generate("simulate for 5 s")
        assert cfg.solver_parameters.end_time == pytest.approx(5.0)

    def test_domain_override(self):
        cfg = self.llm.generate("simulate water in a 2 m domain")
        assert cfg.geometries.system_domain is not None
        assert cfg.geometries.system_domain.upper_bound == [2.0, 2.0]

    def test_resolution_override(self):
        cfg = self.llm.generate("water flow with 5 mm resolution")
        assert cfg.geometries.global_resolution is not None
        assert cfg.geometries.global_resolution.particle_spacing == pytest.approx(0.005)

    def test_empty_description_raises(self):
        with pytest.raises(ValueError, match="description must not be empty"):
            self.llm.generate("")

    def test_whitespace_description_raises(self):
        with pytest.raises(ValueError):
            self.llm.generate("   ")

    def test_result_is_valid_schema(self):
        """Generated config must always pass Pydantic validation."""
        descriptions = [
            "water through a pipe",
            "elastic plate vibration",
            "fsi simulation of a flag in the wind",
            "dam break",
            "tensile test of rubber in a 2 m domain",
            "water at 10 m/s for 2 s",
        ]
        for desc in descriptions:
            cfg = self.llm.generate(desc)
            # round-trip through JSON to confirm schema is fully satisfied
            restored = SimulationConfig.model_validate_json(cfg.model_dump_json())
            assert restored == cfg

    def test_3d_stl_landslide_uses_repose_template_and_explicit_values(self):
        cfg = self.llm.generate(
            "Create a 3D landslide simulation using two STL files. "
            "Use landslides.stl to define the moving landslide body and boundary.stl "
            "to define the fixed terrain boundary. Assign the landslide material a "
            "density of 1800 kg/m\u00b3, a Young\u2019s modulus of 200 MPa, a "
            "Poisson\u2019s ratio of 0.3, a friction angle of 10.5 degrees, a cohesion "
            "of 15 kPa, and a dilatancy angle of 0 degrees. Set the particle spacing "
            "to 10 m, the end time to 80 s, and the output interval to 5 s."
        )

        shapes = {shape.name: shape for shape in cfg.geometries.shapes}
        material = cfg.continuum_bodies[0].material
        assert shapes["GranularBody"].type.value == "triangle_mesh"
        assert shapes["GranularBody"].file_name == "landslides.stl"
        assert shapes["WallBoundary"].type.value == "triangle_mesh"
        assert shapes["WallBoundary"].file_name == "boundary.stl"
        assert material.density == pytest.approx(1800.0)
        assert material.youngs_modulus == pytest.approx(200.0e6)
        assert material.poisson_ratio == pytest.approx(0.3)
        assert material.friction_angle == pytest.approx(math.radians(10.5))
        assert material.cohesion == pytest.approx(15.0e3)
        assert material.dilatancy_angle == pytest.approx(0.0)
        assert material.sound_speed is None
        assert cfg.geometries.global_resolution.particle_spacing == pytest.approx(10.0)
        assert cfg.solver_parameters.end_time == pytest.approx(80.0)
        assert cfg.solver_parameters.output_interval == pytest.approx(5.0)

    def test_update_changes_existing_end_time(self):
        base = self.llm.generate("water flow")
        updated = self.llm.update(base, "simulate for 3 s")
        assert updated.solver_parameters.end_time == pytest.approx(3.0)

    def test_update_changes_end_time_with_second_wording(self):
        base = self.llm.generate("water flow")
        updated = self.llm.update(base, "the end time is 3 second.")
        assert updated.solver_parameters.end_time == pytest.approx(3.0)

    def test_update_adds_observer(self):
        base = self.llm.generate("water flow")
        updated = self.llm.update(base, "add observer named outlet at (1.0, 0.5)")
        assert len(updated.observers) == len(base.observers) + 1
        assert updated.observers[-1].name == "outlet"


class TestExampleConfig:
    def test_nonplastic_example_keeps_historical_default_fields(self):
        example = example_config("elastic solid milling")

        solver = example["solver_parameters"]["continuum_dynamics"]
        assert solver["linear_correction_matrix_coeff"] == pytest.approx(0.5)
        assert solver["linear_correction_matrix_coeff"] == pytest.approx(0.5)
        assert solver["contact_numerical_damping"] == pytest.approx(1.0)
        assert solver["shear_stress_damping"] == pytest.approx(1.0)
        assert solver["hourglass_factor"] == pytest.approx(2.0)
        assert "observers" in example

    def test_nonplastic_serialization_does_not_exclude_defaults(self):
        config = MockLLM().generate("water flowing through a pipe")

        dumped = json.loads(dump_simulation_config_json(config))
        assert "fluid_bodies" in dumped
        assert "continuum_bodies" in dumped
        assert "solid_bodies" in dumped
        assert dumped["continuum_bodies"] == []

    def test_3d_dam_break_uses_3d_fixture(self):
        example = example_config("3d dam break")

        assert len(example["geometries"]["system_domain"]["lower_bound"]) == 3
        assert all(shape["type"] != "multipolygon" for shape in example["geometries"]["shapes"])

    def test_3d_plastic_column_uses_repose_angle_fixture(self):
        example = example_config("3d column collapse using plastic material")

        assert example["simulation_type"] == "continuum_dynamics"
        assert len(example["geometries"]["system_domain"]["lower_bound"]) == 3
        assert example["continuum_bodies"][0]["material"]["type"] == "plastic_continuum"
        assert all(shape["type"] != "multipolygon" for shape in example["geometries"]["shapes"])

    def test_3d_landslide_with_two_stl_files_uses_repose_angle_fixture(self):
        example = example_config(
            "Create a 3D landslide simulation using landslides.stl as the moving "
            "landslide body and boundary.stl as the fixed terrain boundary."
        )

        shapes = {shape["name"]: shape for shape in example["geometries"]["shapes"]}
        assert example["geometries"]["system_domain"]["upper_bound"] == pytest.approx(
            [0.588, 0.16, 0.588]
        )
        assert shapes["GranularBody"]["type"] == "cylinder"
        assert shapes["WallBoundary"]["type"] == "complex_shape"

    def test_3d_landslide_without_two_stl_files_keeps_repose_angle_fixture(self):
        example = example_config("Create a 3D landslide simulation")

        assert example["geometries"]["system_domain"]["upper_bound"] == pytest.approx(
            [0.588, 0.16, 0.588]
        )
        assert example["geometries"]["shapes"][0]["type"] == "cylinder"
        assert "relaxation_parameters" not in example["particle_generation"]["settings"]


class TestSTLGeometryOverrides:
    def test_stl_override_removes_template_or_llm_system_domain(self):
        cfg = example_config(
            "Create a 3D landslide using moving_case.stl as the moving soil "
            "and terrain_case.stl as the fixed terrain boundary."
        )
        cfg["geometries"]["system_domain"] = {
            "lower_bound": [-100.0, -700.0, 0.0],
            "upper_bound": [2900.0, 400.0, 2600.0],
        }

        updated = apply_stl_geometry_overrides(
            cfg,
            "Create a 3D landslide using moving_case.stl as the moving soil "
            "and terrain_case.stl as the fixed terrain boundary.",
        )

        assert "system_domain" not in updated["geometries"]

    def test_nonplastic_stl_override_keeps_default_transform(self):
        base = example_config("elastic solid milling")
        expected_domain = json.loads(json.dumps(base["geometries"]["system_domain"]))

        updated = apply_stl_geometry_overrides(
            base,
            "Use milling.stl for the moving continuum body.",
        )

        body_name = base["continuum_bodies"][0]["name"]
        shape = next(
            shape
            for shape in updated["geometries"]["shapes"]
            if shape["name"] == body_name
        )
        assert shape["file_name"] == "milling.stl"
        assert shape["translation"] == [0.0, 0.0, 0.0]
        assert shape["scale"] == pytest.approx(1.0)
        assert updated["geometries"]["system_domain"] == expected_domain

    def test_landslide_stl_files_replace_body_and_boundary_shapes(self):
        cfg = example_config("3d landslide case")
        updated = apply_stl_geometry_overrides(
            cfg,
            "Create a runnable 3D landslide simulation from two STL files: "
            "./input/SlideBody.stl is the moving landslide soil body, and "
            "./input/Channel.stl is the fixed terrain boundary.",
        )

        shapes = {shape["name"]: shape for shape in updated["geometries"]["shapes"]}
        assert shapes["GranularBody"]["type"] == "triangle_mesh"
        assert shapes["GranularBody"]["file_name"] == "./input/SlideBody.stl"
        assert "translation" not in shapes["GranularBody"]
        assert "scale" not in shapes["GranularBody"]
        assert shapes["WallBoundary"]["type"] == "triangle_mesh"
        assert shapes["WallBoundary"]["file_name"] == "./input/Channel.stl"
        assert "WallInnerBox" not in shapes
        assert "WallOuterBox" not in shapes
        particle_bodies = {
            body["name"]: body
            for body in updated["particle_generation"]["settings"]["bodies"]
        }
        assert particle_bodies["GranularBody"]["relaxation"]["level_set"] == {}
        assert particle_bodies["WallBoundary"]["relaxation"]["level_set"] == {}

        restored = SimulationConfig.model_validate(updated)
        assert restored.continuum_bodies[0].name == "GranularBody"
        assert restored.solid_bodies[0].name == "WallBoundary"

    def test_column_collapse_wall_thickness_defaults_to_four_particle_spacings(self):
        description = (
            "Create a 2D granular column collapse simulation with a particle "
            "spacing of 0.003 m."
        )

        updated = apply_explicit_instruction_overrides(
            example_config(description), description
        )

        wall = next(
            shape
            for shape in updated["geometries"]["shapes"]
            if shape.get("type") == "multipolygon"
        )
        assert wall["polygons"][0]["thickness"] == pytest.approx(0.012)

    def test_four_particle_spacing_wall_default_is_not_case_name_dependent(self):
        config = {
            "geometries": {
                "global_resolution": {"particle_spacing": 0.01},
                "shapes": [
                    {
                        "name": "TankWall",
                        "type": "multipolygon",
                        "polygons": [
                            {
                                "operation": "union",
                                "type": "container_box",
                                "inner_lower_bound": [0.0, 0.0],
                                "inner_upper_bound": [1.0, 0.5],
                                "thickness": 0.01,
                            }
                        ],
                    }
                ],
            }
        }

        updated = apply_explicit_instruction_overrides(
            config, "Create a 2D particle tank with a particle spacing of 0.003 m."
        )

        polygon = updated["geometries"]["shapes"][0]["polygons"][0]
        assert polygon["thickness"] == pytest.approx(0.012)

    def test_explicit_column_collapse_wall_thickness_takes_precedence(self):
        description = (
            "Create a 2D granular column collapse simulation with a particle "
            "spacing of 0.003 m and a wall thickness of 0.02 m."
        )

        updated = apply_explicit_instruction_overrides(
            example_config(description), description
        )

        wall = next(
            shape
            for shape in updated["geometries"]["shapes"]
            if shape.get("type") == "multipolygon"
        )
        assert wall["polygons"][0]["thickness"] == pytest.approx(0.02)

    def test_column_collapse_explicit_inner_size_sets_container_and_domain(self):
        description = (
            "Create a 2D granular column collapse simulation in a wall container "
            "with an inner length of 0.4 m and an inner height of 0.15 m. "
            "Use a particle spacing of 0.002 m."
        )

        updated = apply_explicit_instruction_overrides(
            example_config(description), description
        )

        wall = next(
            shape
            for shape in updated["geometries"]["shapes"]
            if shape.get("type") == "multipolygon"
        )
        polygon = wall["polygons"][0]
        assert polygon["inner_upper_bound"] == pytest.approx([0.4, 0.15])
        assert polygon["thickness"] == pytest.approx(0.008)
        assert updated["geometries"]["system_domain"]["lower_bound"] == pytest.approx(
            [-0.008, -0.008]
        )
        assert updated["geometries"]["system_domain"]["upper_bound"] == pytest.approx(
            [0.408, 0.158]
        )

    def test_sanitize_removes_shape_fields_that_do_not_match_type(self):
        cfg = example_config("3d landslide case")
        cfg["geometries"]["shapes"][0]["file_name"] = "./input/SlideBody.stl"
        cfg["geometries"]["shapes"][0]["scale"] = 1.0

        sanitized = sanitize_config_dict(cfg)

        granular = sanitized["geometries"]["shapes"][0]
        assert granular["type"] == "cylinder"
        assert "file_name" not in granular
        assert "scale" not in granular

    def test_stl_file_name_does_not_drive_material_intent(self):
        description = "Create a 3D case from ./input/Landslide.stl and ./input/Channel.stl."

        assert infer_requested_simulation_type(description) is None
        assert infer_requested_material_type(description) is None

    def test_textual_landslide_typo_drives_material_intent(self):
        description = "Create a 3D landsldie case using ./input/SlideBody.stl as the moving body."

        assert infer_requested_simulation_type(description) == "continuum_dynamics"
        assert infer_requested_material_type(description) == "plastic_continuum"
