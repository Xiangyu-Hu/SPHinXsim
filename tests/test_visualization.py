"""Tests for sphinxsim.visualization (annotations, preview, CLI preview command).

PyVista is *not* required for most tests — mesh-building helpers are tested via
a thin stub.  Tests that do require PyVista are skipped automatically when the
library is not installed.
"""

from __future__ import annotations

import json
import math
import os
import subprocess
import sys
import threading
from pathlib import Path

from typing import Any
import copy
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from sphinxsim.config.schemas import SimulationConfig
from sphinxsim.cli import main

# ---------------------------------------------------------------------------
# Helpers / shared fixtures
# ---------------------------------------------------------------------------

_DATA_DIR = Path(__file__).parent / "test_simulation" / "test_2d_simulation" / "data"
_HEAT_TRANSFER_JSON = _DATA_DIR / "heat_transfer.json"


def _minimal_fluid_config() -> dict:
    """Minimal valid fluid-dynamics config for 2-D tests."""
    return {
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
                    "lower_bound": [-0.05, -0.05],
                    "upper_bound": [1.05, 1.05],
                },
            ],
            "oriented_boxes": [
                {
                    "name": "Inlet",
                    "type": "boundary",
                    "center": [0.0, 0.1],
                    "normal": [1.0, 0.0],
                    "radius": 0.1,
                }
            ],
        },
        "particle_generation": {
            "bodies": [
                {"name": "WaterBody"},
                {"name": "WallBoundary", "solid_body": {}},
            ],
            "relaxation_parameters": {"total_iterations": 1},
        },
        "fluid_bodies": [
            {
                "name": "WaterBody",
                "material": {
                    "type": "weakly_compressible_fluid",
                    "density": 1000.0,
                },
            }
        ],
        "solid_bodies": [{"name": "WallBoundary", "material": {"type": "rigid_body"}}],
        "gravity": [0.0, -9.81],
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
            "fluid_dynamics": {
                "acoustic_cfl": 0.6,
                "advection_cfl": 0.25,
            },
        },
    }


@pytest.fixture
def fluid_config() -> SimulationConfig:
    return SimulationConfig(**_minimal_fluid_config())


@pytest.fixture
def heat_config() -> SimulationConfig:
    data = json.loads(_HEAT_TRANSFER_JSON.read_text())
    return SimulationConfig(**data)


# ---------------------------------------------------------------------------
# Annotations tests
# ---------------------------------------------------------------------------

def test_preview_element_actors_group_annotations_and_visualizations(
    fluid_config, tmp_path
):
    from sphinxsim.visualization.preview import ConfigVisualizer

    visualizer = ConfigVisualizer(fluid_config, tmp_path, off_screen=True)
    annotation = object()
    visualization = object()
    visualizer._register_visualization_actor(("gravity",), visualization)
    visualizer._annotation_label_actors = [
        {"actor": annotation, "config_path": "gravity"}
    ]

    assert visualizer.preview_element_actors == {
        "gravity": {
            "visualization": [visualization],
            "annotation": [annotation],
        }
    }


def test_particle_sphere_splats_render_and_survive_actor_replacement(fluid_config):
    pv = pytest.importorskip("pyvista")
    np = pytest.importorskip("numpy")
    from sphinxsim.visualization.particle_visibility import ParticleVisibility

    fluid_config.geometries.global_resolution.particle_spacing = 0.2
    mesh = pv.PolyData(np.array([[-0.2, 0, 0], [0.2, 0, 0]]))
    mesh.point_data["Pressure"] = [0.0, 1.0]
    plotter = pv.Plotter(off_screen=True, window_size=(400, 400))
    plotter.set_background("black")
    controller = ParticleVisibility(plotter)
    controller.set_scene("run", fluid_config)
    try:
        actor = plotter.add_mesh(mesh, name="particles", color="white", style="points", point_size=5)
        controller.add_actor("WaterBody", actor, mesh, initialized=True)
        original_mapper = actor.GetMapper()
        plotter.camera_position = [(0, 0, 3), (0, 0, 0), (0, 1, 0)]
        plotter.enable_parallel_projection()
        plotter.camera.parallel_scale = 0.5
        plotter.show(auto_close=False)
        points_image = plotter.screenshot(return_img=True)

        controller.set_sphere_splats("WaterBody", True)
        assert actor.GetMapper().GetClassName() == "vtkOpenGLPointGaussianMapper"
        assert actor.GetMapper().GetScaleFactor() == pytest.approx(0.1)
        splats_image = plotter.screenshot(return_img=True)
        assert np.count_nonzero(np.any(splats_image > 10, axis=2)) > (
            2 * np.count_nonzero(np.any(points_image > 10, axis=2))
        )

        controller.set_colour("WaterBody", "Pressure")
        coloured = plotter.screenshot(return_img=True)[100:300, 50:350, :3].astype(float)
        assert np.any((coloured[:, :, 0] > 1.4 * coloured[:, :, 2]) & (coloured[:, :, 0] > 30))
        assert np.any((coloured[:, :, 2] > 1.4 * coloured[:, :, 0]) & (coloured[:, :, 2] > 30))
        controller.set_visible("WaterBody", False)
        replacement = plotter.add_mesh(mesh.copy(), name="particles", color="white", style="points")
        controller.add_actor("WaterBody", replacement, replacement.GetMapper().GetInput(), initialized=True)
        assert replacement.GetMapper().GetClassName() == "vtkOpenGLPointGaussianMapper"
        assert replacement.GetMapper().GetScalarVisibility()
        assert not replacement.GetVisibility()
        controller.set_sphere_splats("WaterBody", False)
        assert replacement.GetMapper().GetClassName() != "vtkOpenGLPointGaussianMapper"
        assert replacement.GetMapper().GetScalarVisibility()
        assert not replacement.GetVisibility()

        controller.set_scene("new-run", fluid_config)
        assert not controller.sphere_splats
        assert original_mapper.GetClassName() != "vtkOpenGLPointGaussianMapper"
    finally:
        plotter.close()


@pytest.mark.parametrize("splats", [False, True])
@pytest.mark.parametrize("colour", [False, True])
def test_particle_transparency_changes_rendered_opacity(fluid_config, splats, colour):
    pv = pytest.importorskip("pyvista")
    np = pytest.importorskip("numpy")
    from sphinxsim.visualization.particle_visibility import ParticleVisibility

    fluid_config.geometries.global_resolution.particle_spacing = 0.2
    mesh = pv.PolyData(np.array([[-0.2, 0, 0], [0.2, 0, 0]]))
    mesh.point_data["Pressure"] = [0.0, 1.0]
    plotter = pv.Plotter(off_screen=True, window_size=(400, 400))
    plotter.set_background("black")
    controller = ParticleVisibility(plotter)
    controller.set_scene("run", fluid_config)
    try:
        actor = plotter.add_mesh(mesh, name="particles", color="white", style="points", point_size=10)
        controller.add_actor("WaterBody", actor, mesh, initialized=True)
        if splats:
            controller.set_sphere_splats("WaterBody", True)
        if colour:
            controller.set_colour("WaterBody", "Pressure")
        plotter.camera_position = [(0, 0, 3), (0, 0, 0), (0, 1, 0)]
        plotter.enable_parallel_projection()
        plotter.camera.parallel_scale = 0.5
        plotter.show(auto_close=False)
        assert actor.GetProperty().GetOpacity() == pytest.approx(0.95)
        transparent = plotter.screenshot(return_img=True)[100:300, 50:350]
        controller.set_opaque("WaterBody", True)
        assert actor.GetProperty().GetOpacity() == pytest.approx(1.0)
        opaque = plotter.screenshot(return_img=True)[100:300, 50:350]
        assert np.any(opaque > transparent)
        controller.set_opaque("WaterBody", False)
        assert actor.GetProperty().GetOpacity() == pytest.approx(0.95)
    finally:
        plotter.close()


def test_particle_sphere_splat_spacing_estimate_and_invalid_cloud(fluid_config):
    pv = pytest.importorskip("pyvista")
    np = pytest.importorskip("numpy")
    from sphinxsim.visualization.particle_visibility import _sphere_splat_radius

    fluid_config.geometries.global_resolution.particle_spacing = None
    mesh = pv.PolyData(np.array([[0, 0, 0], [0, 1, 0], [1, 0, 0], [1, 1, 0]], dtype=float))
    assert _sphere_splat_radius(mesh, fluid_config) == pytest.approx(0.25)
    point = pv.PolyData(np.array([[0, 0, 0]], dtype=float))
    with pytest.raises(ValueError, match="Cannot estimate particle spacing"):
        _sphere_splat_radius(point, fluid_config)


def test_particle_splat_invalid_frame_falls_back_and_recovers(fluid_config, caplog):
    pv = pytest.importorskip("pyvista")
    np = pytest.importorskip("numpy")
    from sphinxsim.visualization.particle_visibility import ParticleVisibility

    fluid_config.geometries.global_resolution.particle_spacing = None
    valid = pv.PolyData(np.array([[0, 0, 0], [1, 0, 0]], dtype=float))
    invalid = pv.PolyData(np.array([[0, 0, 0]], dtype=float))
    plotter = pv.Plotter(off_screen=True)
    controller = ParticleVisibility(plotter)
    controller.set_scene("run", fluid_config)
    try:
        actor = plotter.add_mesh(invalid, name="particles", color="blue", style="points")
        controller.add_actor("WaterBody", actor, invalid)
        with pytest.raises(ValueError, match="Cannot estimate particle spacing"):
            controller.set_sphere_splats("WaterBody", True)
        assert not controller.sphere_splats

        actor = plotter.add_mesh(valid, name="particles", color="blue", style="points")
        controller.add_actor("WaterBody", actor, valid)
        controller.set_sphere_splats("WaterBody", True)
        actor = plotter.add_mesh(invalid, name="particles", color="blue", style="points")
        controller.add_actor("WaterBody", actor, invalid)
        assert actor.GetMapper().GetClassName() != "vtkOpenGLPointGaussianMapper"
        assert "showing points" in caplog.text
        assert controller.sphere_splats == {"WaterBody"}
        actor = plotter.add_mesh(valid, name="particles", color="blue", style="points")
        controller.add_actor("WaterBody", actor, valid)
        assert actor.GetMapper().GetClassName() == "vtkOpenGLPointGaussianMapper"
    finally:
        plotter.close()


def test_particle_splat_selection_survives_live_and_replay(fluid_config, tmp_path):
    pv = pytest.importorskip("pyvista")
    np = pytest.importorskip("numpy")
    from sphinxsim.results import ResultCatalog
    from sphinxsim.visualization.live_particles import refresh_particle_actors
    from sphinxsim.visualization.particle_visibility import ParticleVisibility
    from sphinxsim.visualization.preview_runtime import _ShellPreviewRuntime

    mesh = pv.PolyData(np.array([[0, 0, 0], [1, 0, 0]], dtype=float))
    mesh.point_data["Pressure"] = [0.0, 1.0]
    mesh.field_data["TimeValue"] = [0.0]
    first = tmp_path / "WaterBody_0000.vtp"
    second = tmp_path / "WaterBody_0001.vtp"
    mesh.save(first, binary=False)
    plotter = pv.Plotter(off_screen=True)
    controller = ParticleVisibility(plotter)
    controller.set_scene("run", fluid_config)
    versions = {}
    colours = {"WaterBody": "blue"}
    try:
        refresh_particle_actors(plotter, tmp_path, colours, versions, ResultCatalog(), controller)
        controller.set_sphere_splats("WaterBody", True)
        controller.set_colour("WaterBody", "Pressure")
        controller.set_palette("WaterBody", "Viridis")
        controller.set_opaque("WaterBody", True)
        mesh.field_data["TimeValue"] = [1.0]
        mesh.save(second, binary=False)
        refresh_particle_actors(plotter, tmp_path, colours, versions, ResultCatalog(), controller)
        assert controller.actors["WaterBody"].GetMapper().GetClassName() == "vtkOpenGLPointGaussianMapper"
        assert controller.actors["WaterBody"].GetMapper().GetScalarVisibility()
        assert controller.actors["WaterBody"].GetProperty().GetOpacity() == pytest.approx(1.0)
        assert controller.actors["WaterBody"].GetMapper().GetLookupTable().GetColor(0) == pytest.approx(
            (0.267, 0.005, 0.329)
        )

        runtime = _ShellPreviewRuntime()
        runtime.plotter = plotter
        runtime._particle_visibility = controller
        runtime._live_output_dir = tmp_path
        runtime._live_particle_colours = colours
        runtime._live_replay_mode = True
        runtime._live_playback_queue = [(0, {"WaterBody": first})]
        runtime._refresh_live_particles()
        assert controller.actors["WaterBody"].GetMapper().GetClassName() == "vtkOpenGLPointGaussianMapper"
        assert controller.actors["WaterBody"].GetMapper().GetScalarVisibility()
        assert controller.actors["WaterBody"].GetProperty().GetOpacity() == pytest.approx(1.0)
        assert controller.palettes["WaterBody"] == "Viridis"
        controller.set_scene("new-run", fluid_config)
        replacement = plotter.add_mesh(mesh, name="particles", style="points", opacity=1.0)
        controller.add_actor("WaterBody", replacement, mesh, initialized=True)
        assert replacement.GetProperty().GetOpacity() == pytest.approx(0.95)
        assert not controller.opaque_bodies
        assert not controller.palettes
    finally:
        plotter.close()


def test_particle_palette_updates_per_body_and_representation(fluid_config):
    pv = pytest.importorskip("pyvista")
    np = pytest.importorskip("numpy")
    from sphinxsim.visualization.particle_visibility import ParticleVisibility

    mesh = pv.PolyData(np.array([[-0.2, 0, 0], [0.2, 0, 0]]))
    mesh.point_data["Pressure"] = [0.0, 1.0]
    mesh.point_data["Velocity"] = [[0.0, 0.0, 0.0], [1.0, 0.0, 0.0]]
    plotter = pv.Plotter(off_screen=True, window_size=(400, 400))
    plotter.set_background("black")
    controller = ParticleVisibility(plotter)
    controller.set_scene("run", fluid_config)
    try:
        for body in ("WaterBody", "WallBoundary"):
            actor = plotter.add_mesh(mesh.copy(), name=body, color="white", style="points", point_size=10)
            controller.add_actor(body, actor, actor.GetMapper().GetInput(), initialized=True)
            controller.set_colour(body, "Pressure")
        water = controller.actors["WaterBody"]
        wall = controller.actors["WallBoundary"]
        wall.SetPosition(0, 0.5, 0)
        plotter.camera_position = [(0, 0, 3), (0, 0, 0), (0, 1, 0)]
        plotter.enable_parallel_projection()
        plotter.camera.parallel_scale = 0.5
        plotter.show(auto_close=False)
        before = plotter.screenshot(return_img=True)[180:220, 100:300]
        assert water.GetMapper().GetLookupTable().GetColor(0) == pytest.approx((0.23, 0.30, 0.75))
        controller.set_palette("WaterBody", "Viridis")
        after = plotter.screenshot(return_img=True)[180:220, 100:300]
        assert np.any(before != after)
        assert water.GetMapper().GetLookupTable().GetColor(0) == pytest.approx((0.267, 0.005, 0.329))
        assert wall.GetMapper().GetLookupTable().GetColor(0) == pytest.approx((0.23, 0.30, 0.75))
        assert controller.scalar_bars["WaterBody"].GetLookupTable() == water.GetMapper().GetLookupTable()
        controller.set_sphere_splats("WaterBody", True)
        rgba = controller.meshes["WaterBody"].point_data["_sphinxsim_colour_Pressure_rgba"]
        assert rgba[0, :3] / 255.0 == pytest.approx((0.267, 0.005, 0.329), abs=0.005)
        controller.set_colour("WaterBody", "Velocity")
        assert water.GetMapper().GetLookupTable().GetColor(0) == pytest.approx((0.267, 0.005, 0.329))
        controller.set_palette("WaterBody", "Grayscale")
        assert water.GetMapper().GetLookupTable().GetColor(0) == pytest.approx((0, 0, 0))
        assert water.GetMapper().GetLookupTable().GetColor(1) == pytest.approx((1, 1, 1))
        controller.set_colour("WaterBody", None)
        controller.set_colour("WaterBody", "Pressure")
        assert controller.palettes["WaterBody"] == "Grayscale"
        water_mesh = controller.meshes["WaterBody"]
        controller.set_scene("run", fluid_config)
        controller.add_actor("WaterBody", water, water_mesh, initialized=True)
        assert water.GetMapper().GetLookupTable().GetColor(0) == pytest.approx((0, 0, 0))
        with pytest.raises(ValueError, match="Unknown particle color palette"):
            controller.set_palette("WaterBody", "invalid")
    finally:
        plotter.close()


class TestBodyLabel:
    def test_fluid_body_label_includes_density(self, fluid_config):
        from sphinxsim.visualization.annotations import body_label

        label = body_label("WaterBody", fluid_config)
        assert "Fluid: WaterBody" in label
        assert "1000.0" in label


    def test_fluid_body_label_omits_sound_speed(self, fluid_config):
        from sphinxsim.visualization.annotations import body_label

        label = body_label("WaterBody", fluid_config)
        assert "c=" not in label

    def test_solid_body_label(self, fluid_config):
        from sphinxsim.visualization.annotations import body_label

        label = body_label("WallBoundary", fluid_config)
        assert "Solid: WallBoundary" in label
        assert "rigid" in label

    def test_unknown_body_returns_name(self, fluid_config):
        from sphinxsim.visualization.annotations import body_label

        label = body_label("NonExistent", fluid_config)
        assert label == "NonExistent"

    def test_thermal_boundary_shown_in_fluid_label(self, heat_config):
        from sphinxsim.visualization.annotations import body_label

        # WaterBody in heat_transfer has thermal_properties
        label = body_label("WaterBody", heat_config)
        assert "Fluid: WaterBody" in label


class TestPreviewMaterialInformation:
    def _config(self, *materials):
        bodies = [
            SimpleNamespace(name=f"GranularBody{index}", material=material)
            for index, material in enumerate(materials, start=1)
        ]
        return SimpleNamespace(continuum_bodies=bodies)

    def test_formats_continuum_material_units_and_angle(self):
        from sphinxsim.config.schemas import MaterialConfig, MaterialType
        from sphinxsim.visualization.annotations import collect_preview_body_information

        material = MaterialConfig(
            type=MaterialType.GENERAL_CONTINUUM,
            density=2040.0,
            sound_speed=100.0,
            youngs_modulus=1.0e6,
            poisson_ratio=0.3,
            friction_angle=math.radians(30.0),
            cohesion=1500.0,
        )
        info = collect_preview_body_information(self._config(material))[0]

        assert info["density"] == "2040 kg/m³"
        assert info["friction_angle"] == "30.0°"
        assert info["cohesion"] == "1.5 kPa"

    def test_missing_friction_angle_and_cohesion_are_safe(self):
        from sphinxsim.config.schemas import MaterialConfig, MaterialType
        from sphinxsim.visualization.annotations import collect_preview_body_information

        material = MaterialConfig(
            type=MaterialType.GENERAL_CONTINUUM,
            density=2040.0,
            sound_speed=100.0,
            youngs_modulus=1.0e6,
            poisson_ratio=0.3,
        )
        info = collect_preview_body_information(self._config(material))[0]

        assert info["friction_angle"] == "—"
        assert info["cohesion"] == "—"

    def test_multiple_continuum_bodies_remain_separate(self):
        from sphinxsim.config.schemas import MaterialConfig, MaterialType
        from sphinxsim.visualization.annotations import collect_preview_body_information

        materials = [
            MaterialConfig(
                type=MaterialType.GENERAL_CONTINUUM,
                density=1800.0,
                sound_speed=100.0,
                youngs_modulus=1.0e6,
                poisson_ratio=0.3,
            ),
            MaterialConfig(
                type=MaterialType.GENERAL_CONTINUUM,
                density=2200.0,
                sound_speed=100.0,
                youngs_modulus=1.0e6,
                poisson_ratio=0.3,
            ),
        ]
        info = collect_preview_body_information(self._config(*materials))

        assert [item["name"] for item in info] == ["GranularBody1", "GranularBody2"]
        assert [item["density"] for item in info] == ["1800 kg/m³", "2200 kg/m³"]

    def test_plastic_continuum_shows_dilatancy_but_j2_does_not(self):
        from sphinxsim.config.schemas import MaterialConfig, MaterialType
        from sphinxsim.visualization.annotations import collect_preview_body_information

        plastic = MaterialConfig(
            type=MaterialType.PLASTIC_CONTINUUM,
            density=2040.0,
            youngs_modulus=5.8e6,
            poisson_ratio=0.3,
            friction_angle=math.radians(30.0),
            dilatancy_angle=math.radians(8.0),
            cohesion=0.0,
        )
        j2 = MaterialConfig(
            type=MaterialType.J2_PLASTICITY,
            density=2040.0,
            sound_speed=48.0,
            youngs_modulus=5.8e6,
            poisson_ratio=0.3,
            yield_stress=1000.0,
            hardening_modulus=200.0,
        )
        plastic_rows = dict(collect_preview_body_information(self._config(plastic))[0]["rows"])
        j2_rows = dict(collect_preview_body_information(self._config(j2))[0]["rows"])

        assert plastic_rows["Dilatancy angle"] == "8.0°"
        expected_sound_speed = math.sqrt(5.8e6 / (2040.0 * 3.0 * (1.0 - 2.0 * 0.3)))
        assert plastic_rows["Sound speed"] == f"{expected_sound_speed:g} m/s"
        assert "Yield stress" not in plastic_rows
        assert "Dilatancy angle" not in j2_rows
        assert "Friction angle" not in j2_rows
        assert j2_rows["Yield stress"] == "1 kPa" or j2_rows["Yield stress"] == "1000 Pa"

    def test_plastic_continuum_displays_explicit_sound_speed_override(self):
        from sphinxsim.config.schemas import MaterialConfig, MaterialType
        from sphinxsim.visualization.annotations import collect_preview_body_information

        material = MaterialConfig(
            type=MaterialType.PLASTIC_CONTINUUM,
            density=2040.0,
            sound_speed=42.0,
            youngs_modulus=5.8e6,
            poisson_ratio=0.3,
            friction_angle=math.radians(30.0),
        )

        rows = dict(collect_preview_body_information(self._config(material))[0]["rows"])
        assert rows["Sound speed"] == "42 m/s"

    def test_particle_spacing_is_displayed_from_global_resolution(self):
        from sphinxsim.visualization.annotations import particle_resolution_label

        config = SimpleNamespace(
            geometries=SimpleNamespace(
                global_resolution=SimpleNamespace(particle_spacing=0.002)
            )
        )
        assert particle_resolution_label(config) == ("Particle spacing", "0.002 m")


class TestPreviewLegend:
    def test_fluid_legend_uses_material_model_not_granular_label(self, fluid_config):
        from sphinxsim.visualization.preview import _legend_entries_for_config

        entries = _legend_entries_for_config(fluid_config)
        labels = [label for label, _ in entries]

        assert "Weakly compressible fluid" in labels
        assert "Granular material" not in labels
        assert "Solid" in labels

    def test_multiple_fluid_bodies_have_distinct_render_and_legend_colours(
        self, fluid_config, tmp_path
    ):
        from sphinxsim.visualization.preview import (
            ConfigVisualizer,
            _body_colour,
            _legend_entries_for_config,
        )

        data = copy.deepcopy(fluid_config.model_dump(exclude_none=True))
        second_body = copy.deepcopy(data["fluid_bodies"][0])
        second_body["name"] = "AirBody"
        second_body["material"]["density"] = 1.0
        data["fluid_bodies"].append(second_body)
        data["geometries"]["shapes"].append(
            {
                "name": "AirBody",
                "type": "bounding_box",
                "lower_bound": [0.5, 0.1],
                "upper_bound": [0.9, 0.4],
            }
        )
        data["particle_generation"]["bodies"].append({"name": "AirBody"})
        config = SimulationConfig.model_validate(data)

        water_colour = _body_colour("WaterBody", config)
        air_colour = _body_colour("AirBody", config)
        assert water_colour != air_colour

        entries = _legend_entries_for_config(config)
        assert ("WaterBody", water_colour) in entries
        assert ("AirBody", air_colour) in entries
        particle_entries = _legend_entries_for_config(
            config, {"WaterBody", "AirBody"}
        )
        assert particle_entries == [
            ("AirBody Particles", air_colour),
            ("WaterBody Particles", water_colour),
        ]

        visualizer = ConfigVisualizer(config, tmp_path, off_screen=True)
        visualizer._shape_bounds_cache = {
            "WaterBody": ([0.0, 0.0], [0.4, 0.2]),
            "AirBody": ([0.5, 0.1], [0.9, 0.4]),
            "WallBoundary": ([-0.05, -0.05], [1.05, 1.05]),
        }
        fake_plotter = _FakePlotter()
        with patch.dict(sys.modules, {"pyvista": _FakePyVista}):
            visualizer._populate_plotter(fake_plotter, vtp_dir=None)

        body_meshes = {
            call["label"]: call["color"]
            for call in fake_plotter.mesh_calls
            if call.get("label") in {"WaterBody", "AirBody"}
        }
        assert body_meshes == {
            "WaterBody": water_colour,
            "AirBody": air_colour,
        }

    def test_fluid_body_palette_does_not_repeat_after_six_bodies(self, fluid_config):
        from sphinxsim.visualization.preview import _body_colour

        data = copy.deepcopy(fluid_config.model_dump(exclude_none=True))
        template_body = copy.deepcopy(data["fluid_bodies"][0])
        for index in range(1, 8):
            name = f"FluidBody{index}"
            body = copy.deepcopy(template_body)
            body["name"] = name
            data["fluid_bodies"].append(body)
            data["geometries"]["shapes"].append(
                {
                    "name": name,
                    "type": "bounding_box",
                    "lower_bound": [float(index), 0.0],
                    "upper_bound": [float(index) + 0.5, 0.5],
                }
            )
            data["particle_generation"]["bodies"].append({"name": name})

        config = SimulationConfig.model_validate(data)
        colours = [_body_colour(body.name, config) for body in config.fluid_bodies]

        assert len(set(colours)) == len(colours)


class TestPreviewGeometryFailure:
    def test_default_shape_has_no_preview_geometry(self, fluid_config, tmp_path):
        from sphinxsim.visualization.preview import ConfigVisualizer

        data = copy.deepcopy(fluid_config.model_dump(exclude_none=True))
        data["geometries"]["shapes"][0] = {
            "name": "WaterBody",
            "type": "default",
        }
        config = SimulationConfig.model_validate(data)
        visualizer = ConfigVisualizer(config, tmp_path, off_screen=True)
        visualizer._shape_bounds_cache = {
            "WaterBody": ([-1e300, -1e300], [1e300, 1e300]),
            "WallBoundary": ([-0.05, -0.05], [1.05, 1.05]),
        }

        assert visualizer._load_shape_mesh(
            config.geometries.shapes[0], vtp_dir=None, config=config
        ) is None
        assert visualizer._filter_non_geometry_bounds(
            visualizer._shape_bounds_cache
        ) == {
            "WallBoundary": ([-0.05, -0.05], [1.05, 1.05]),
        }
        lower, upper = visualizer._scene_extent(2)
        assert lower == pytest.approx([-0.105, -0.105])
        assert upper == pytest.approx([1.105, 1.105])

        vtp_dir = tmp_path / "vtp"
        vtp_dir.mkdir()
        (vtp_dir / "ShapeInlet.vtp").touch()
        visualizer._ensure_shape_geometry_available(vtp_dir=vtp_dir)
        assert visualizer._missing_shape_geometry == []

        plotter = _FakePlotter()
        with patch.dict(sys.modules, {"pyvista": _FakePyVista}):
            visualizer._populate_plotter(plotter, vtp_dir=vtp_dir)
        assert all(call.get("label") != "WaterBody" for call in plotter.mesh_calls)
        assert any(call.get("label") == "WallBoundary" for call in plotter.mesh_calls)

    def test_preview_fails_when_configured_shapes_have_no_native_geometry(
        self, fluid_config, tmp_path
    ):
        from sphinxsim.visualization.preview import ConfigVisualizer

        visualizer = ConfigVisualizer(fluid_config, tmp_path, off_screen=True)
        with pytest.raises(RuntimeError, match="WaterBody, WallBoundary"):
            visualizer._ensure_shape_geometry_available(vtp_dir=None)

        with patch.dict(sys.modules, {"pyvista": _FakePyVista}):
            visualizer._populate_plotter(_FakePlotter(), vtp_dir=None)

        with pytest.raises(RuntimeError, match="WaterBody, WallBoundary") as exc_info:
            visualizer._ensure_shapes_rendered()

        assert "Python binding library" in str(exc_info.value)

    def test_geometry_build_failure_identifies_outdated_binding(
        self, fluid_config, tmp_path, monkeypatch
    ):
        import sphinxsim.visualization.preview as preview_mod

        visualizer = preview_mod.ConfigVisualizer(
            fluid_config,
            tmp_path,
            config_path=tmp_path / "config.json",
        )
        monkeypatch.setattr(
            preview_mod,
            "load_sphinxsys_core_nd",
            lambda _ndim: SimpleNamespace(),
        )

        with pytest.raises(RuntimeError, match="binding may be out of date") as exc_info:
            visualizer._try_build_geometries(2)

        assert isinstance(exc_info.value.__cause__, AttributeError)

    def test_persistent_preview_reports_geometry_build_failure(
        self, fluid_config, tmp_path, monkeypatch, capsys
    ):
        from sphinxsim.visualization.preview_runtime import _ShellPreviewRuntime

        class FailingVisualizer:
            def __init__(self, *args, **kwargs):
                pass

            def _spatial_dim(self):
                return 2

            def _try_build_geometries(self, ndim, with_particles=False):
                raise RuntimeError(
                    "C++ geometry generation failed; update the Python binding."
                )

        runtime = _ShellPreviewRuntime()
        monkeypatch.setattr(
            "sphinxsim.visualization.preview.ConfigVisualizer",
            FailingVisualizer,
        )
        with patch.dict(sys.modules, {"pyvista": MagicMock()}):
            rc = runtime.show_or_update(
                fluid_config,
                resolved_config_path=tmp_path / "config.json",
                with_particles=False,
                force=True,
            )

        assert rc == 1
        assert (
            "Preview failed: C++ geometry generation failed"
            in capsys.readouterr().err
        )
        assert runtime.last_signature is None


class TestOrientedBoxLabel:
    def test_label_includes_name_and_type(self, fluid_config):
        from sphinxsim.visualization.annotations import oriented_box_label

        ob = fluid_config.geometries.oriented_boxes[0]  # "Inlet"
        label = oriented_box_label(ob, fluid_config)
        assert "Inlet" in label
        assert "boundary" in label

    def test_label_includes_bc_type(self, fluid_config):
        from sphinxsim.visualization.annotations import oriented_box_label

        ob = fluid_config.geometries.oriented_boxes[0]  # linked to emitter BC
        label = oriented_box_label(ob, fluid_config)
        assert "emitter" in label
        assert "WaterBody" in label

    def test_label_includes_inflow_speed(self, fluid_config):
        from sphinxsim.visualization.annotations import oriented_box_label

        ob = fluid_config.geometries.oriented_boxes[0]
        label = oriented_box_label(ob, fluid_config)
        assert "1.5" in label

    def test_oriented_box_no_bc(self, heat_config):
        from sphinxsim.visualization.annotations import oriented_box_label

        # UpperWall region has no BC in heat_transfer
        ob = next(o for o in heat_config.geometries.oriented_boxes if o.name == "UpperWall")
        label = oriented_box_label(ob, heat_config)
        assert "UpperWall" in label

    def test_bi_directional_bc_shows_pressure(self, heat_config):
        from sphinxsim.visualization.annotations import oriented_box_label

        ob = next(o for o in heat_config.geometries.oriented_boxes if o.name == "Inlet")
        label = oriented_box_label(ob, heat_config)
        assert "bi_directional" in label

    def test_label_includes_relaxation_constraint_for_oriented_box(self, fluid_config):
        from sphinxsim.visualization.annotations import oriented_box_label

        data = copy.deepcopy(fluid_config.model_dump(exclude_none=True))
        data["particle_generation"]["relaxation_constraints"] = [
            {
                "body_name": "WaterBody",
                "oriented_box": "Inlet",
                "type": "fixed"
            }
        ]
        cfg = SimulationConfig(**data)

        ob = cfg.geometries.oriented_boxes[0]
        label = oriented_box_label(ob, cfg)
        assert "Relaxation constraint" in label
        assert "WaterBody" in label
        assert "fixed" in label

    def test_label_does_not_use_body_constraints_for_oriented_box(self, fluid_config):
        from sphinxsim.visualization.annotations import oriented_box_label

        data = copy.deepcopy(fluid_config.model_dump(exclude_none=True))
        data["body_constraints"] = [
            {
                "body_name": "WallBoundary",
                "type": "fixed",
                "region": "Inlet",
            }
        ]
        cfg = SimulationConfig(**data)

        ob = cfg.geometries.oriented_boxes[0]
        label = oriented_box_label(ob, cfg)
        assert "Constraint →" not in label


class TestGravityLabel:
    def test_2d_gravity_label(self, fluid_config):
        from sphinxsim.visualization.annotations import gravity_label

        label = gravity_label(fluid_config)
        assert label is not None
        assert "9.81" in label
        assert "g =" in label

    def test_no_gravity_returns_none(self, heat_config):
        from sphinxsim.visualization.annotations import gravity_label

        # heat_transfer.json has no gravity
        label = gravity_label(heat_config)
        assert label is None


class TestObserverLabel:
    def test_observer_label_includes_name_body_and_variable(self, fluid_config):
        from sphinxsim.visualization.annotations import observer_label

        data = copy.deepcopy(fluid_config.model_dump(exclude_none=True))
        data["observers"] = [
            {
                "name": "ProbeA",
                "observed_body": "WaterBody",
                "variable": {"real_type": "Pressure"},
                "positions": [[0.1, 0.1]],
            }
        ]
        cfg = SimulationConfig(**data)

        label = observer_label(cfg.observers[0])
        assert "Observer: ProbeA" in label
        assert "body=WaterBody" in label
        assert "var=Pressure" in label


# ---------------------------------------------------------------------------
# ConfigVisualizer.preview — no-pyvista guard test
# ---------------------------------------------------------------------------

class TestConfigVisualizerNoPyvista:
    def test_raises_import_error_without_pyvista(self, fluid_config, tmp_path):
        from sphinxsim.visualization.preview import ConfigVisualizer

        with patch.dict(sys.modules, {"pyvista": None}):
            viz = ConfigVisualizer(fluid_config, tmp_path, off_screen=True)
            with pytest.raises(ImportError, match="PyVista"):
                viz.preview()


class TestSpatialDimensionInference:
    def test_circle_primitive_supplies_2d_dimension(self, tmp_path):
        from sphinxsim.json_editor import _config_spatial_dim
        from sphinxsim.visualization.preview import ConfigVisualizer

        data = _minimal_fluid_config()
        data["geometries"].pop("system_domain")
        data["geometries"]["oriented_boxes"] = []
        data["geometries"]["primitives"] = [
            {"name": "Circle", "type": "circle", "center": [0, 0], "radius": 1, "resolution": 32}
        ]
        data["geometries"]["shapes"] = [
            {"name": name, "type": "multipolygon", "polygons": [
                {"operation": "union", "type": "circle", "primitive": "Circle"}
            ]}
            for name in ("WaterBody", "WallBoundary")
        ]
        data.pop("gravity")
        data["observers"] = []
        data["fluid_boundary_conditions"] = []
        config = SimulationConfig(**data)

        assert config._infer_spatial_dim() == 2
        assert ConfigVisualizer(config, tmp_path, off_screen=True)._spatial_dim() == 2
        assert _config_spatial_dim(config) == 2

    def test_named_primitives_supply_2d_dimension_without_domain_or_gravity(self, tmp_path):
        from sphinxsim.json_editor import _config_spatial_dim
        from sphinxsim.visualization.preview import ConfigVisualizer

        data = _minimal_fluid_config()
        geometries = data["geometries"]
        geometries.pop("system_domain")
        geometries["oriented_boxes"] = []
        data.pop("gravity")
        data["fluid_boundary_conditions"] = []
        primitives = []
        for shape in geometries["shapes"]:
            primitive_name = shape["name"] + "Bounds"
            primitives.append({
                "name": primitive_name, "type": "bounding_box",
                "lower_bound": shape.pop("lower_bound"),
                "upper_bound": shape.pop("upper_bound"),
            })
            shape["primitive"] = primitive_name
        geometries["primitives"] = primitives

        config = SimulationConfig(**data)

        assert ConfigVisualizer(config, tmp_path, off_screen=True)._spatial_dim() == 2
        assert _config_spatial_dim(config) == 2

    def test_planar_simbody_constraint_is_inferred_as_2d(self, tmp_path):
        from sphinxsim.visualization.preview import ConfigVisualizer

        data = _minimal_fluid_config()
        data["geometries"].pop("system_domain", None)
        data.pop("gravity", None)
        data["body_constraints"] = [
            {
                "body_name": "WallBoundary",
                "type": "simbody",
                "mobilized_body": "planar",
                "velocity": [0.0, -0.03],
                "angular_velocity": 2.0,
            }
        ]
        data["restart"] = {
            "restore_step": 0,
            "save_interval": 1000,
            "summary_enabled": True,
        }

        config = SimulationConfig(**data)
        viz = ConfigVisualizer(config, tmp_path, off_screen=True)

        assert viz._spatial_dim() == 2


class TestPreviewViewMode:
    def test_2d_default_view_uses_orthographic_xy(self, fluid_config, tmp_path):
        from sphinxsim.visualization.preview import ConfigVisualizer

        viz = ConfigVisualizer(fluid_config, tmp_path, off_screen=True)
        calls: list[str] = []

        class FakePlotter:
            def enable_2d_style(self):
                calls.append("enable_2d_style")

            def enable_parallel_projection(self):
                calls.append("enable_parallel_projection")

            def view_xy(self, negative=False):
                calls.append(f"view_xy:{negative}")

        viz._configure_default_view(FakePlotter(), ndim=2)

        assert "enable_2d_style" in calls
        assert "enable_parallel_projection" in calls
        assert "view_xy:False" in calls

    @pytest.mark.parametrize("ndim", [2, 3])
    @pytest.mark.parametrize("screenshot", [False, True])
    def test_preview_has_no_config_info_overlay(
        self, fluid_config, tmp_path, ndim, screenshot, monkeypatch
    ):
        from sphinxsim.visualization.preview import ConfigVisualizer

        viz = ConfigVisualizer(fluid_config, tmp_path, off_screen=True)
        plotter = MagicMock()
        monkeypatch.setitem(sys.modules, "pyvista", SimpleNamespace(Plotter=lambda **kwargs: plotter))
        monkeypatch.setattr(viz, "_spatial_dim", lambda: ndim)
        monkeypatch.setattr(viz, "_try_build_geometries", lambda *args, **kwargs: None)
        monkeypatch.setattr(viz, "_draw_preview_sidebar", lambda _plotter: None)
        monkeypatch.setattr(viz, "_populate_plotter", lambda *args: None)

        screenshot_path = tmp_path / "preview.png" if screenshot else None
        viz.preview(screenshot_path=screenshot_path)

        plotter.add_text.assert_not_called()
        if screenshot:
            plotter.screenshot.assert_called_once_with(str(screenshot_path))
        else:
            plotter.show.assert_called_once()


class TestPreviewObservers:
    def test_populate_plotter_renders_observer_points(self, fluid_config, tmp_path):
        from sphinxsim.visualization.preview import ConfigVisualizer

        data = copy.deepcopy(fluid_config.model_dump(exclude_none=True))
        data["geometries"].pop("system_domain", None)
        data["observers"] = [
            {
                "name": "ProbeA",
                "observed_body": "WaterBody",
                "variable": {"real_type": "Pressure"},
                "positions": [[0.1, 0.1], [0.2, 0.1]],
            }
        ]
        cfg = SimulationConfig(**data)
        viz = ConfigVisualizer(cfg, tmp_path, off_screen=True)

        class FakePlotter:
            def __init__(self):
                self.mesh_calls: list[dict[str, Any]] = []
                self.point_label_calls: list[dict[str, Any]] = []

            def add_mesh(self, mesh, **kwargs):
                self.mesh_calls.append({"mesh": mesh, **kwargs})

            def add_point_labels(self, points, labels, **kwargs):
                self.point_label_calls.append(
                    {"points": points, "labels": labels, **kwargs}
                )

            def add_text(self, *args, **kwargs):
                return None

            def add_legend(self, *args, **kwargs):
                return None

        class FakePyVista:
            @staticmethod
            def PolyData(points):
                return {"points": points}

            @staticmethod
            def Arrow(start, direction, scale):
                return {
                    "type": "arrow",
                    "start": start,
                    "direction": direction,
                    "scale": scale,
                }

        fake_plotter = FakePlotter()
        with patch.dict(sys.modules, {"pyvista": FakePyVista}):
            viz._populate_plotter(fake_plotter, vtp_dir=None)

        observer_mesh_calls = [
            call for call in fake_plotter.mesh_calls if call.get("label") == "Observer: ProbeA"
        ]
        assert len(observer_mesh_calls) == 1
        assert observer_mesh_calls[0]["color"] == (0.93, 0.13, 0.93)
        assert observer_mesh_calls[0]["point_size"] == 10

        observer_label_calls = [
            call
            for call in fake_plotter.point_label_calls
            if call["labels"] and "Observer 1" in call["labels"][0]
        ]
        assert len(observer_label_calls) == 1


class TestPreviewGeneratedParticles:
    def test_discovers_latest_particle_vtp_per_body(self, fluid_config, tmp_path):
        from sphinxsim.visualization.preview import ConfigVisualizer

        viz = ConfigVisualizer(fluid_config, tmp_path, off_screen=True)
        vtp_dir = tmp_path / "output"
        vtp_dir.mkdir(parents=True)

        # Runtime states use actual TimeValue; particle-generation iterations do not win.
        def state(name, time):
            (vtp_dir / name).write_text(
                f'<VTKFile><PolyData><FieldData><DataArray Name="TimeValue">{time}</DataArray>'
                '</FieldData></PolyData></VTKFile>'
            )
        state("WaterBody_0000.vtp", 0)
        state("WaterBody_0010.vtp", 10)
        state("WaterBody_ite_0012.vtp", 12)
        state("WallBoundary_0002.vtp", 2)
        state("WallBoundary_0001.vtp", 1)

        # Should be ignored (not body state particle files).
        (vtp_dir / "ShapeWaterBody.vtp").write_text("shape")
        (vtp_dir / "particle_generation_0004.vtp").write_text("pg")

        latest = viz._discover_latest_particle_vtps(vtp_dir)

        assert latest["WaterBody"].name == "WaterBody_0010.vtp"
        assert latest["WallBoundary"].name == "WallBoundary_0002.vtp"
        assert "particle_generation" not in latest

    def test_populate_plotter_overlays_latest_particles(self, fluid_config, tmp_path):
        from sphinxsim.visualization.preview import ConfigVisualizer

        viz = ConfigVisualizer(fluid_config, tmp_path, off_screen=True)
        fake_latest = {
            "WaterBody": tmp_path / "WaterBody_0010.vtp",
            "WallBoundary": tmp_path / "WallBoundary_0002.vtp",
        }

        class FakeMesh:
            def __init__(self, center):
                self.center = center
                self.bounds = [0.0, 1.0, 0.0, 1.0, -0.01, 0.01]

        class FakePlotter:
            def __init__(self):
                self.mesh_calls: list[dict[str, Any]] = []
                self.point_label_calls: list[dict[str, Any]] = []

            def add_mesh(self, mesh, **kwargs):
                self.mesh_calls.append({"mesh": mesh, **kwargs})

            def add_point_labels(self, points, labels, **kwargs):
                self.point_label_calls.append({"points": points, "labels": labels, **kwargs})

            def add_text(self, *args, **kwargs):
                return None

            def add_legend(self, *args, **kwargs):
                return None

        class FakePyVista:
            @staticmethod
            def read(path):
                if "WaterBody" in path:
                    return FakeMesh([0.2, 0.2, 0.0])
                return FakeMesh([0.8, 0.8, 0.0])

            @staticmethod
            def PolyData(points):
                return FakeMesh([0.5, 0.5, 0.0])

            @staticmethod
            def Arrow(start, direction, scale):
                return {
                    "type": "arrow",
                    "start": start,
                    "direction": direction,
                    "scale": scale,
                }

            @staticmethod
            def Box(bounds):
                return FakeMesh([0.5, 0.5, 0.0])

        fake_plotter = FakePlotter()
        with patch.dict(sys.modules, {"pyvista": FakePyVista}):
            viz._populate_plotter(fake_plotter, vtp_dir=None, latest_particle_vtps=fake_latest)

        particle_mesh_calls = [
            call for call in fake_plotter.mesh_calls
            if call.get("label") in {"WaterBody Particles", "WallBoundary Particles"}
        ]
        assert len(particle_mesh_calls) == 2
        assert {call["label"] for call in particle_mesh_calls} == {
            "WaterBody Particles",
            "WallBoundary Particles",
        }
        assert all(call.get("style") == "points" for call in particle_mesh_calls)
        assert all(call.get("point_size") == 5 for call in particle_mesh_calls)

        particle_label_calls = [
            call
            for call in fake_plotter.point_label_calls
            if call["labels"]
            and str(call["labels"][0])
            in {"WaterBody Particles", "WallBoundary Particles"}
        ]
        assert particle_label_calls == []

    def test_populate_plotter_hides_shapes_when_particles_present(self, fluid_config, tmp_path):
        from sphinxsim.visualization.preview import ConfigVisualizer

        viz = ConfigVisualizer(fluid_config, tmp_path, off_screen=True)
        fake_latest = {
            "WaterBody": tmp_path / "WaterBody_0010.vtp",
        }

        class FakeMesh:
            def __init__(self, center):
                self.center = center
                self.bounds = [0.0, 1.0, 0.0, 1.0, -0.01, 0.01]

        class FakePlotter:
            def __init__(self):
                self.mesh_calls: list[dict[str, Any]] = []

            def add_mesh(self, mesh, **kwargs):
                self.mesh_calls.append({"mesh": mesh, **kwargs})

            def add_point_labels(self, points, labels, **kwargs):
                return None

            def add_text(self, *args, **kwargs):
                return None

            def add_legend(self, *args, **kwargs):
                return None

        class FakePyVista:
            @staticmethod
            def read(path):
                return FakeMesh([0.2, 0.2, 0.0])

            @staticmethod
            def PolyData(points):
                return FakeMesh([0.5, 0.5, 0.0])

            @staticmethod
            def Arrow(start, direction, scale):
                return {
                    "type": "arrow",
                    "start": start,
                    "direction": direction,
                    "scale": scale,
                }

            @staticmethod
            def Box(bounds):
                return FakeMesh([0.5, 0.5, 0.0])

        fake_plotter = FakePlotter()
        with patch.dict(sys.modules, {"pyvista": FakePyVista}):
            with patch.object(viz, "_load_shape_mesh", side_effect=AssertionError("shape render should be skipped")):
                viz._populate_plotter(fake_plotter, vtp_dir=None, latest_particle_vtps=fake_latest)

        particle_mesh_calls = [
            call for call in fake_plotter.mesh_calls
            if call.get("label") == "WaterBody Particles"
        ]
        assert len(particle_mesh_calls) == 1


# ---------------------------------------------------------------------------
# Body constraint label tests
# ---------------------------------------------------------------------------

class TestConstraintLabel:
    def test_fixed_constraint_label(self, fluid_config):
        from sphinxsim.visualization.annotations import body_constraint_label

        data = copy.deepcopy(fluid_config.model_dump(exclude_none=True))
        data["body_constraints"] = [
            {"body_name": "WallBoundary", "type": "fixed"}
        ]
        cfg = SimulationConfig(**data)

        label = body_constraint_label(cfg.body_constraints[0])
        assert "Constraint → WallBoundary" in label
        assert "type=fixed" in label

    def test_fixed_constraint_with_region_label(self, fluid_config):
        from sphinxsim.visualization.annotations import body_constraint_label

        data = copy.deepcopy(fluid_config.model_dump(exclude_none=True))
        data["geometries"]["oriented_boxes"].append(
            {
                "name": "ClampRegion",
                "type": "region",
                "half_size": [0.1, 0.1],
                "transform": {
                    "translation": [0.5, 0.5],
                    "rotation_angle": 0.0,
                    "rotation_axis": [0.0, 0.0, 1.0],
                },
            }
        )
        data["body_constraints"] = [
            {"body_name": "WallBoundary", "type": "fixed", "region": "ClampRegion"}
        ]
        cfg = SimulationConfig(**data)

        label = body_constraint_label(cfg.body_constraints[0])
        assert "Constraint → WallBoundary" in label
        assert "type=fixed" in label
        assert "region=ClampRegion" in label

    def test_simbody_constraint_label(self, fluid_config):
        from sphinxsim.visualization.annotations import body_constraint_label

        data = copy.deepcopy(fluid_config.model_dump(exclude_none=True))
        # Simbody constraints require config.restart
        data["restart"] = {
            "restore_step": 0,
            "save_interval": 1000,
            "summary_enabled": False,
        }
        data["body_constraints"] = [
            {
                "body_name": "WallBoundary",
                "type": "simbody",
                "mobilized_body": "planar",
                "velocity": [0.0, -0.03],
                "angular_velocity": 2.0,
            }
        ]
        cfg = SimulationConfig(**data)

        label = body_constraint_label(cfg.body_constraints[0])
        assert "Constraint → WallBoundary" in label
        assert "type=simbody" in label
        assert "mob=planar" in label
        assert "v=(0.0, -0.03)" in label
        assert "ω=2.0" in label


# ---------------------------------------------------------------------------
# Body constraint preview tests
# ---------------------------------------------------------------------------

class TestPreviewConstraints:
    def test_preview_renders_constraint_with_region(self, fluid_config, tmp_path, monkeypatch):
        """A constraint with a region should not raise an error during visualization."""
        from sphinxsim.visualization.preview import ConfigVisualizer

        data = copy.deepcopy(fluid_config.model_dump(exclude_none=True))
        data["geometries"].pop("system_domain", None)
        data["geometries"]["oriented_boxes"].append(
            {
                "name": "ClampRegion",
                "type": "region",
                "half_size": [0.1, 0.1],
                "transform": {
                    "translation": [0.5, 0.5],
                    "rotation_angle": 0.0,
                    "rotation_axis": [0.0, 0.0, 1.0],
                },
            }
        )
        data["body_constraints"] = [
            {"body_name": "WallBoundary", "type": "fixed", "region": "ClampRegion"}
        ]
        cfg = SimulationConfig(**data)
        viz = ConfigVisualizer(cfg, tmp_path, off_screen=True)

        # Mock PyVista to avoid requiring a display or actual rendering
        class MockPolyData:
            def __init__(self, points):
                self.points = points
                self.center = [0.5, 0.5, 0.0] if len(points) > 0 else [0.0, 0.0, 0.0]
                self.bounds = [[0.0, 1.0], [0.0, 1.0], [0.0, 1.0]]

        class MockPlotter:
            def add_mesh(self, mesh, **kwargs):
                pass

            def add_point_labels(self, points, labels, **kwargs):
                pass

            def add_text(self, *args, **kwargs):
                pass

            def add_legend(self, *args, **kwargs):
                pass

        class MockPyVista:
            def Plotter(self, **kwargs):
                return MockPlotter()

            @staticmethod
            def PolyData(points):
                return MockPolyData(points)

            @staticmethod
            def Arrow(start, direction, scale):
                return {
                    "type": "arrow",
                    "start": start,
                    "direction": direction,
                    "scale": scale,
                }

        # Mock pyvista import
        import sys

        monkeypatch.setitem(sys.modules, "pyvista", MockPyVista())

        # Should not raise an error
        try:
            plotter = MockPlotter()
            viz._populate_plotter(plotter, vtp_dir=None)
        except Exception as e:
            pytest.fail(f"_populate_plotter raised {type(e).__name__}: {e}")

    def test_preview_renders_constraint_without_region(self, fluid_config, tmp_path, monkeypatch):
        """A constraint without a region should not raise an error during visualization."""
        from sphinxsim.visualization.preview import ConfigVisualizer

        data = copy.deepcopy(fluid_config.model_dump(exclude_none=True))
        data["geometries"].pop("system_domain", None)
        data["body_constraints"] = [
            {"body_name": "WallBoundary", "type": "fixed"}
        ]
        cfg = SimulationConfig(**data)
        viz = ConfigVisualizer(cfg, tmp_path, off_screen=True)

        # Mock PyVista to avoid requiring a display or actual rendering
        class MockPolyData:
            def __init__(self, points):
                self.points = points
                self.center = [0.5, 0.5, 0.0] if len(points) > 0 else [0.0, 0.0, 0.0]
                self.bounds = [[0.0, 1.0], [0.0, 1.0], [0.0, 1.0]]

        class MockPlotter:
            def add_mesh(self, mesh, **kwargs):
                pass

            def add_point_labels(self, points, labels, **kwargs):
                pass

            def add_text(self, *args, **kwargs):
                pass

            def add_legend(self, *args, **kwargs):
                pass

        class MockPyVista:
            def Plotter(self, **kwargs):
                return MockPlotter()

            @staticmethod
            def PolyData(points):
                return MockPolyData(points)

            @staticmethod
            def Arrow(start, direction, scale):
                return {
                    "type": "arrow",
                    "start": start,
                    "direction": direction,
                    "scale": scale,
                }

        # Mock pyvista import
        import sys

        monkeypatch.setitem(sys.modules, "pyvista", MockPyVista())

        # Should not raise an error
        try:
            plotter = MockPlotter()
            viz._populate_plotter(plotter, vtp_dir=None)
        except Exception as e:
            pytest.fail(f"_populate_plotter raised {type(e).__name__}: {e}")


# ---------------------------------------------------------------------------
# CLI preview command tests (no PyVista / no C++ required)
# ---------------------------------------------------------------------------

class TestCLIPreviewCommand:
    def _write_config(self, path: Path) -> Path:
        p = path / "config.json"
        p.write_text(json.dumps(_minimal_fluid_config()))
        return p

    def test_preview_missing_pyvista_returns_nonzero(self, build_temp_path, capsys):
        cfg = self._write_config(build_temp_path)
        with patch.dict(sys.modules, {"pyvista": None}):
            rc = main(["preview", str(cfg)])
        assert rc != 0
        err = capsys.readouterr().err
        assert "PyVista" in err or "pyvista" in err.lower()

    def test_preview_missing_config_returns_nonzero(self, build_temp_path, capsys):
        with patch.dict(sys.modules, {"pyvista": MagicMock()}):
            rc = main(["preview", str(build_temp_path / "nonexistent.json")])
        assert rc != 0

    def test_preview_calls_visualizer_preview(self, build_temp_path):
        cfg = self._write_config(build_temp_path)
        mock_pv = MagicMock()
        fake_visualizer = MagicMock()

        with patch.dict(sys.modules, {"pyvista": mock_pv}):
            with patch(
                "sphinxsim.visualization.preview.ConfigVisualizer",
                return_value=fake_visualizer,
            ) as MockViz:
                rc = main(["preview", str(cfg)])

        assert rc == 0
        MockViz.assert_called_once()
        fake_visualizer.preview.assert_called_once_with(
            screenshot_path=None,
            with_particles=False,
        )

    def test_preview_with_particles_flag(self, build_temp_path):
        cfg = self._write_config(build_temp_path)
        mock_pv = MagicMock()
        fake_visualizer = MagicMock()

        with patch.dict(sys.modules, {"pyvista": mock_pv}):
            with patch(
                "sphinxsim.visualization.preview.ConfigVisualizer",
                return_value=fake_visualizer,
            ):
                rc = main(["preview", str(cfg), "--with-particles"])

        assert rc == 0
        fake_visualizer.preview.assert_called_once_with(
            screenshot_path=None,
            with_particles=True,
        )

    def test_preview_invalid_config_returns_nonzero(self, build_temp_path, capsys):
        bad = _minimal_fluid_config()
        bad["fluid_bodies"] = []  # invalid — no fluid bodies
        p = build_temp_path / "bad.json"
        p.write_text(json.dumps(bad))
        mock_pv = MagicMock()
        with patch.dict(sys.modules, {"pyvista": mock_pv}):
            rc = main(["preview", str(p)])
        assert rc != 0

    def test_preview_visualizer_exception_returns_nonzero(self, build_temp_path, capsys):
        cfg = self._write_config(build_temp_path)
        mock_pv = MagicMock()
        fake_visualizer = MagicMock()
        fake_visualizer.preview.side_effect = RuntimeError("render failed")

        with patch.dict(sys.modules, {"pyvista": mock_pv}):
            with patch(
                "sphinxsim.visualization.preview.ConfigVisualizer",
                return_value=fake_visualizer,
            ):
                rc = main(["preview", str(cfg)])

        assert rc != 0
        assert "render failed" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# Shell mode preview tests
# ---------------------------------------------------------------------------

class TestShellPreview:
    def _write_config(self, path: Path) -> tuple[Path, str]:
        """Write config and return (abs_path, shell-relative path)."""
        p = path / "config.json"
        p.write_text(json.dumps(_minimal_fluid_config()))
        rel = f"pytest-temp/{path.name}/config.json"
        return p, rel

    def test_shell_input_pumps_preview_events_while_waiting(self):
        from sphinxsim import cli as cli_mod

        input_can_return = threading.Event()
        pump_calls = []

        def fake_input(_prompt):
            assert input_can_return.wait(timeout=2)
            return "exit"

        runtime = SimpleNamespace(
            plotter=object(),
            _using_background_plotter=True,
            pump_ui_events=lambda: (pump_calls.append(True), input_can_return.set()),
        )
        with patch("builtins.input", side_effect=fake_input):
            assert cli_mod._read_shell_input("sphinxsim> ", runtime) == "exit"

        assert pump_calls

    def test_shell_preview_no_pyvista_prints_error(self, build_temp_path, capsys):
        _, rel = self._write_config(build_temp_path)
        inputs = [f"load {rel}", "preview", "exit"]
        with patch.dict(sys.modules, {"pyvista": None}):
            with patch("builtins.input", side_effect=inputs):
                rc = main(["shell"])
        assert rc == 0  # shell itself exits cleanly
        err = capsys.readouterr().err
        assert "PyVista" in err or "pyvista" in err.lower()

    def test_shell_preview_before_load_errors(self, build_temp_path, capsys):
        inputs = ["preview", "exit"]
        with patch("builtins.input", side_effect=inputs):
            rc = main(["shell"])
        assert rc == 0
        assert "No config loaded" in capsys.readouterr().err

    def test_shell_preview_calls_visualizer(self, build_temp_path, capsys):
        _, rel = self._write_config(build_temp_path)

        inputs = [f"load {rel}", "preview", "exit"]
        with patch.dict(sys.modules, {"pyvista": MagicMock()}):
            with patch(
                "sphinxsim.cli._ShellPreviewRuntime.show_or_update",
                return_value=0,
            ) as mock_show_or_update:
                with patch("builtins.input", side_effect=inputs):
                    rc = main(["shell"])

        assert rc == 0
        mock_show_or_update.assert_called_once()
        _, kwargs = mock_show_or_update.call_args
        assert kwargs.get("with_particles") is False

    def test_shell_preview_with_particles_flag(self, build_temp_path):
        _, rel = self._write_config(build_temp_path)

        inputs = [f"load {rel}", "preview --with-particles", "exit"]
        with patch.dict(sys.modules, {"pyvista": MagicMock()}):
            with patch(
                "sphinxsim.cli._ShellPreviewRuntime.show_or_update",
                return_value=0,
            ) as mock_show_or_update:
                with patch("builtins.input", side_effect=inputs):
                    rc = main(["shell"])

        assert rc == 0
        mock_show_or_update.assert_called_once()
        _, kwargs = mock_show_or_update.call_args
        assert kwargs.get("with_particles") is True

    def test_shell_run_with_animation_previews_then_continues(self, build_temp_path):
        from sphinxsim import cli as cli_mod

        _, rel = self._write_config(build_temp_path)
        inputs = [f"load {rel}", "run --with-animation", "exit"]

        with (
            patch.object(
                cli_mod._ShellPreviewRuntime,
                "show_or_update",
                return_value=0,
            ) as show_or_update,
            patch.object(
                cli_mod._ShellPreviewRuntime,
                "initialize_simulation",
                return_value=0,
            ) as initialize,
            patch.object(
                cli_mod._ShellPreviewRuntime,
                "continue_to_run",
                return_value=0,
            ) as continue_to_run,
            patch("builtins.input", side_effect=inputs),
        ):
            assert main(["shell"]) == 0

        show_or_update.assert_called_once()
        _, kwargs = show_or_update.call_args
        assert kwargs["with_particles"] is True
        assert kwargs["force"] is True
        initialize.assert_called_once_with()
        continue_to_run.assert_called_once_with()

    def test_shell_runtime_continues_retained_particle_simulation(self, tmp_path):
        from sphinxsim import cli as cli_mod

        calls: list[str] = []

        class FakeSimulation:
            def buildSimulation(self):
                calls.append("build")

            def initializeSimulation(self):
                calls.append("initialize")

            def run(self):
                calls.append("run")

        runtime = cli_mod._ShellPreviewRuntime()
        runtime._pending_simulation = FakeSimulation()
        runtime_config_path = tmp_path / "sphinxsim_preview.json"
        runtime_config_path.write_text("{}")
        runtime._pending_runtime_config_path = runtime_config_path

        assert runtime.continue_to_run() == 1
        assert calls == []
        assert runtime.initialize_simulation() == 0
        assert calls == ["build", "initialize"]
        assert runtime._pending_simulation is not None
        assert runtime_config_path.exists()
        assert runtime.initialize_simulation() == 1
        assert runtime.continue_to_run() == 0
        assert runtime._live_run_thread is not None
        runtime._live_run_thread.join()
        assert calls == ["build", "initialize", "run"]
        assert runtime._pending_simulation is None
        assert not runtime_config_path.exists()

    def test_shell_initialization_displays_real_body_state_without_running(self, tmp_path):
        from sphinxsim import cli as cli_mod

        calls = []
        runtime = cli_mod._ShellPreviewRuntime()
        simulation = MagicMock()
        simulation.buildSimulation.side_effect = lambda: calls.append("build")
        simulation.initializeSimulation.side_effect = lambda: calls.append("initialize")
        runtime._pending_simulation = simulation
        runtime.plotter = MagicMock()
        runtime._particle_visibility = MagicMock()
        runtime._live_output_dir = tmp_path
        runtime._live_particle_body_names = {"WaterBody"}
        runtime._live_particle_colours = {"WaterBody": (0.2, 0.5, 0.8)}
        frame = SimpleNamespace(path=tmp_path / "WaterBody_0.vtp")
        mesh = object()
        with patch("sphinxsim.results.ResultCatalog.latest", return_value={"WaterBody": frame}) as latest, \
             patch("sphinxsim.results.read_particle_mesh", return_value=mesh):
            assert runtime.initialize_simulation() == 0

        latest.assert_called_once_with(tmp_path, ["WaterBody"])
        simulation.run.assert_not_called()
        assert calls == ["build", "initialize"]
        runtime.plotter.add_mesh.assert_called_once_with(
            mesh, name="particle-preview-WaterBody", color=(0.2, 0.5, 0.8),
            opacity=0.95, style="points", point_size=5, label="WaterBody Particles",
            reset_camera=False,
        )
        runtime._particle_visibility.add_actor.assert_called_once_with(
            "WaterBody", runtime.plotter.add_mesh.return_value, mesh, initialized=True
        )
        runtime.plotter.render.assert_called_once()

    def test_shell_initialization_failure_releases_simulation_and_config(self, tmp_path, capsys):
        from sphinxsim import cli as cli_mod

        runtime = cli_mod._ShellPreviewRuntime()
        runtime._pending_simulation = MagicMock()
        runtime._pending_simulation.initializeSimulation.side_effect = RuntimeError("native failure")
        runtime._pending_runtime_config_path = tmp_path / "runtime.json"
        runtime._pending_runtime_config_path.write_text("{}")
        assert runtime.initialize_simulation() == 1
        assert runtime._pending_simulation is None
        assert not runtime._pending_initialized
        assert not (tmp_path / "runtime.json").exists()
        assert "native failure" in capsys.readouterr().err

    def test_shell_initial_snapshot_error_keeps_initialized_simulation(self, tmp_path, capsys):
        from sphinxsim import cli as cli_mod

        runtime = cli_mod._ShellPreviewRuntime()
        runtime._pending_simulation = MagicMock()
        runtime.plotter = MagicMock()
        runtime._live_output_dir = tmp_path
        runtime._live_particle_body_names = {"WaterBody"}
        with patch("sphinxsim.results.ResultCatalog.latest", return_value={}):
            assert runtime.initialize_simulation() == 1
        assert runtime._pending_initialized
        assert runtime._pending_simulation is not None
        assert "No initial body-state snapshots" in capsys.readouterr().err
        runtime.discard_pending_simulation()
        assert not runtime._pending_initialized

    def test_native_initialization_writes_and_displays_body_states(self, tmp_path):
        pytest.importorskip("pyvista")
        from sphinxsim.bindings.loader import load_sphinxsys_core_nd

        try:
            load_sphinxsys_core_nd(2)
        except ImportError as exc:
            pytest.skip(f"Native 2D binding unavailable: {exc}")

        project = Path(__file__).resolve().parents[1]
        source = _DATA_DIR / "dambreak.json"
        script = """
import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch
from types import SimpleNamespace
from sphinxsim.config.schemas import SimulationConfig
from sphinxsim.results import ResultCatalog
from sphinxsim.visualization.preview import ConfigVisualizer
from sphinxsim.visualization.preview_runtime import _ShellPreviewRuntime

source, root = map(Path, sys.argv[1:])
data = json.loads(source.read_text())
data["geometries"]["global_resolution"]["particle_spacing"] = 0.1
data["particle_generation"]["relaxation_parameters"]["total_iterations"] = 1
data["solver_parameters"].update(end_time=0.05, output_interval=0.01)
case = root / "case.json"
case.write_text(json.dumps(data))
config = SimulationConfig.model_validate(data)
visualizer = ConfigVisualizer(config, root, config_path=case, output_root=root / "run")
vtp_dir = visualizer._try_build_geometries(2, with_particles=True)
simulation, runtime_config = visualizer.take_particle_simulation()
runtime = _ShellPreviewRuntime()
runtime._pending_simulation = simulation
runtime._pending_runtime_config_path = runtime_config
runtime._live_output_dir = vtp_dir
runtime._live_particle_body_names = visualizer._particle_generation_body_names()
runtime.plotter = MagicMock()
runtime._using_background_plotter = True
runtime._animation_output_dir = vtp_dir
runtime._animation_particle_body_names = set(runtime._live_particle_body_names)
timer = MagicMock()
with patch.dict(sys.modules, {"qtpy": SimpleNamespace(QtCore=SimpleNamespace(QTimer=lambda: timer))}):
    assert runtime.replay_animation(relaxation=True) == 0
    assert all("_ite_" in path.name for _, batch in runtime._live_playback_queue for path in batch.values())
    assert runtime.initialize_simulation() == 0
    timer.stop.assert_called_once()
assert runtime._pending_initialized
assert runtime_config.exists()
frames = ResultCatalog().latest(vtp_dir, sorted(runtime._live_particle_body_names))
assert frames and all(frame.time == 0 for frame in frames.values())
assert runtime.plotter.add_mesh.call_count == len(frames)
assert any(call.args[0].point_data for call in runtime.plotter.add_mesh.call_args_list)
assert runtime._live_run_thread is None
runtime._using_background_plotter = False
assert runtime.continue_to_run() == 0
runtime._live_run_thread.join(timeout=60)
assert not runtime._live_run_thread.is_alive()
assert runtime._animation_run_completed
assert not runtime_config.exists()
final_frames = ResultCatalog().latest(vtp_dir, sorted(runtime._live_particle_body_names))
assert max(frame.time for frame in final_frames.values()) > 0
runtime._using_background_plotter = True
with patch.dict(sys.modules, {"qtpy": SimpleNamespace(QtCore=SimpleNamespace(QTimer=lambda: timer))}):
    assert runtime.replay_animation() == 0
    assert all("_ite_" not in path.name for _, batch in runtime._live_playback_queue for path in batch.values())
"""
        env = os.environ.copy()
        env["PYTHONPATH"] = str(project) + os.pathsep + env.get("PYTHONPATH", "")
        env["PYTHONIOENCODING"] = "utf-8"
        result = subprocess.run(
            [sys.executable, "-c", script, str(source), str(tmp_path)],
            cwd=tmp_path, env=env, text=True, encoding="utf-8", capture_output=True, timeout=120,
        )
        assert result.returncode == 0, result.stdout + result.stderr

    def test_shell_initialize_command_and_slash_form(self):
        from sphinxsim import cli as cli_mod

        with patch.object(cli_mod._ShellPreviewRuntime, "initialize_simulation", return_value=0) as initialize, \
             patch("builtins.input", side_effect=["initialize-simulation", "/initialize-simulation", "exit"]):
            assert main(["shell"]) == 0
        assert initialize.call_count == 2

    def test_shell_lifecycle_commands_do_not_discard_retained_simulation(self):
        from sphinxsim import cli as cli_mod

        calls = []
        with patch.object(cli_mod._ShellPreviewRuntime, "initialize_simulation",
                          side_effect=lambda: calls.append("initialize")), \
             patch.object(cli_mod._ShellPreviewRuntime, "continue_to_run",
                          side_effect=lambda *_: calls.append("run")), \
             patch.object(cli_mod._ShellPreviewRuntime, "discard_pending_simulation",
                          side_effect=lambda: calls.append("discard")), \
             patch("builtins.input", side_effect=["initialize-simulation", "continue-to-run", "validate", "exit"]):
            assert main(["shell"]) == 0
        assert calls == ["initialize", "run", "discard", "discard"]

    def test_shell_animation_does_not_run_if_initialization_fails(self, build_temp_path):
        from sphinxsim import cli as cli_mod

        _, rel = self._write_config(build_temp_path)
        with patch.object(cli_mod._ShellPreviewRuntime, "show_or_update", return_value=0), \
             patch.object(cli_mod._ShellPreviewRuntime, "initialize_simulation", return_value=1), \
             patch.object(cli_mod._ShellPreviewRuntime, "continue_to_run") as run, \
             patch("builtins.input", side_effect=[f"load {rel}", "run --with-animation", "exit"]):
            assert main(["shell"]) == 0
        run.assert_not_called()

    def test_shell_runtime_plays_final_snapshots_when_run_finishes_before_refresh(self, tmp_path):
        from sphinxsim import cli as cli_mod

        class FakeTimer:
            def __init__(self):
                self.timeout = MagicMock()
                self.interval = None
                self.interval_updates = 0

            def setInterval(self, interval):
                self.interval = interval
                self.interval_updates += 1

            def start(self):
                pass

            def stop(self):
                pass

        class FakePlotter:
            def __init__(self):
                self.meshes = []
                self.app = SimpleNamespace(processEvents=lambda: None)

            def add_mesh(self, mesh, **_kwargs):
                self.meshes.append(mesh)

        class FakeSimulation:
            def buildSimulation(self):
                pass

            def initializeSimulation(self):
                pass

            def run(self):
                for step in (1, 2):
                    (tmp_path / f"WaterBody_{step}.vtp").touch()

        timer = FakeTimer()
        runtime = cli_mod._ShellPreviewRuntime()
        runtime._pending_simulation = FakeSimulation()
        runtime._pending_initialized = True
        runtime._using_background_plotter = True
        runtime._live_output_dir = tmp_path
        runtime._live_particle_body_names = {"WaterBody"}
        runtime.plotter = FakePlotter()

        with patch.dict(
            sys.modules,
            {"qtpy": SimpleNamespace(QtCore=SimpleNamespace(QTimer=lambda: timer))},
        ):
            assert runtime.continue_to_run(refresh_interval=30.0) == 0
            runtime._live_run_thread.join()
            assert timer.interval == 30_000
            runtime.pump_ui_events()
            runtime.pump_ui_events()
            assert timer.interval == 40
            assert timer.interval_updates == 2

            with patch.dict(
                sys.modules,
                {"pyvista": SimpleNamespace(read=lambda path: path)},
            ):
                runtime._refresh_live_particles()
                runtime._refresh_live_particles()
                runtime._refresh_live_particles()

        assert runtime.plotter.meshes == [
            str(tmp_path / "WaterBody_1.vtp"),
            str(tmp_path / "WaterBody_2.vtp"),
        ]
        assert runtime._live_refresh_timer is None

    def test_shell_runtime_plays_particle_snapshots_in_sequence(self, tmp_path):
        from sphinxsim import cli as cli_mod

        class FakePlotter:
            def __init__(self):
                self.meshes = []

            def add_mesh(self, mesh, **kwargs):
                self.meshes.append(mesh)

        runtime = cli_mod._ShellPreviewRuntime()
        runtime.plotter = FakePlotter()
        runtime._live_output_dir = tmp_path
        runtime._live_particle_body_names = {"WaterBody"}
        runtime._live_run_thread = SimpleNamespace(is_alive=lambda: True)
        snapshots = [tmp_path / f"WaterBody_{step}.vtp" for step in range(3)]
        for snapshot in snapshots:
            snapshot.touch()

        with patch.dict(sys.modules, {"pyvista": SimpleNamespace(read=str)}):
            for _ in range(6):
                runtime._refresh_live_particles()

        assert runtime.plotter.meshes == [str(path) for path in snapshots]

    def test_live_playback_ignores_relaxation_snapshots(self, tmp_path):
        from sphinxsim import cli as cli_mod

        runtime = cli_mod._ShellPreviewRuntime()
        runtime.plotter = MagicMock()
        runtime._live_output_dir = tmp_path
        runtime._live_particle_body_names = {"Water", "Water_Extra"}
        runtime._live_run_thread = SimpleNamespace(is_alive=lambda: True)
        for name in ("Water_ite_1.vtp", "Water_1.vtp", "Water_Extra_ite_1.vtp",
                     "Water_Extra_1.vtp", "Water_2.vtp"):
            (tmp_path / name).touch()

        with patch.dict(sys.modules, {"pyvista": SimpleNamespace(read=str)}):
            for _ in range(4):
                runtime._refresh_live_particles()

        assert {Path(call.args[0]).name for call in runtime.plotter.add_mesh.call_args_list} == {
            "Water_1.vtp", "Water_Extra_1.vtp", "Water_2.vtp"
        }

    def test_replay_separates_relaxation_from_simulation_and_preserves_pending(self, tmp_path):
        from sphinxsim import cli as cli_mod

        timer = MagicMock()
        runtime = cli_mod._ShellPreviewRuntime()
        runtime.plotter = MagicMock()
        runtime._using_background_plotter = True
        runtime._animation_output_dir = tmp_path
        runtime._animation_particle_body_names = {"Water", "Water_Extra"}
        runtime._pending_simulation = MagicMock()
        for name in ("Water_ite_10.vtp", "Water_ite_2.vtp", "Water_2.vtp",
                     "Water_Extra_ite_2.vtp", "Water_Extra_2.vtp", "Water_10.vtp"):
            (tmp_path / name).touch()

        with patch.dict(sys.modules, {"qtpy": SimpleNamespace(QtCore=SimpleNamespace(QTimer=lambda: timer))}):
            assert runtime.replay_animation(relaxation=True) == 0
            assert [step for step, _ in runtime._live_playback_queue] == [2, 10]
            assert set(runtime._live_playback_queue[0][1]) == {"Water", "Water_Extra"}
            assert all("_ite_" in path.name for _, batch in runtime._live_playback_queue
                       for path in batch.values())
            runtime._animation_run_completed = True
            assert runtime.replay_animation() == 0
            assert [step for step, _ in runtime._live_playback_queue] == [2, 10]
            assert all("_ite_" not in path.name for _, batch in runtime._live_playback_queue
                       for path in batch.values())
        assert runtime._pending_simulation is not None

    def test_initialization_stops_relaxation_timer_and_preserves_output_context(self, tmp_path):
        from sphinxsim import cli as cli_mod

        runtime = cli_mod._ShellPreviewRuntime()
        runtime._pending_simulation = MagicMock()
        runtime.plotter = MagicMock()
        runtime._live_output_dir = tmp_path
        runtime._live_particle_body_names = {"WaterBody"}
        runtime._live_refresh_timer = MagicMock()
        timer = runtime._live_refresh_timer
        runtime._live_replay_mode = True
        runtime._live_playback_queue = [(1, {"WaterBody": tmp_path / "WaterBody_ite_1.vtp"})]
        with patch.object(runtime, "_show_initialized_particles") as show:
            assert runtime.initialize_simulation() == 0
        timer.stop.assert_called_once()
        assert runtime._live_output_dir == tmp_path
        assert runtime._live_particle_body_names == {"WaterBody"}
        assert not runtime._live_playback_queue
        show.assert_called_once()

    def test_completed_relaxation_playback_keeps_initialization_context(self, tmp_path):
        from sphinxsim import cli as cli_mod

        runtime = cli_mod._ShellPreviewRuntime()
        runtime.plotter = MagicMock()
        runtime._pending_simulation = MagicMock()
        runtime._live_output_dir = tmp_path
        runtime._live_particle_body_names = {"WaterBody"}
        runtime._live_refresh_timer = MagicMock()
        runtime._live_replay_mode = True
        runtime._live_playback_queue = [(1, {"WaterBody": tmp_path / "WaterBody_ite_1.vtp"})]
        with patch.dict(sys.modules, {"pyvista": SimpleNamespace(read=str)}), \
             patch.object(runtime, "_show_initialized_particles") as show:
            runtime._refresh_live_particles()
            assert runtime.initialize_simulation() == 0
        assert runtime._live_output_dir == tmp_path
        assert runtime._live_particle_body_names == {"WaterBody"}
        show.assert_called_once()

    def test_particle_preview_starts_relaxation_playback(self, tmp_path, fluid_config):
        from sphinxsim import cli as cli_mod

        runtime = cli_mod._ShellPreviewRuntime()
        runtime.plotter = MagicMock()
        runtime._using_background_plotter = True
        visualizer = MagicMock()
        visualizer._spatial_dim.return_value = 2
        visualizer._try_build_geometries.return_value = tmp_path
        visualizer._discover_latest_particle_vtps.return_value = {}
        visualizer._particle_generation_body_names.return_value = {"WaterBody"}
        simulation = MagicMock()
        visualizer.take_particle_simulation.return_value = (simulation, None)
        (tmp_path / "WaterBody_ite_1.vtp").touch()
        timer = MagicMock()
        with patch("sphinxsim.visualization.preview.ConfigVisualizer", return_value=visualizer), \
             patch.dict(sys.modules, {
                 "pyvista": MagicMock(),
                 "qtpy": SimpleNamespace(QtCore=SimpleNamespace(QTimer=lambda: timer)),
             }), \
             patch.object(runtime, "_install_json_editor", return_value=True), \
             patch.object(runtime, "_install_annotation_hover"):
            assert runtime.show_or_update(
                fluid_config, resolved_config_path=tmp_path / "case.json", with_particles=True
            ) == 0

        visualizer._discover_latest_particle_vtps.assert_called_once_with(tmp_path, generation_only=True)
        assert runtime._pending_simulation is simulation
        assert runtime._live_replay_mode
        assert runtime._live_playback_queue[0][1]["WaterBody"].name == "WaterBody_ite_1.vtp"
        timer.start.assert_called_once()

    def test_relaxation_replay_reports_missing_snapshots(self, tmp_path, capsys):
        from sphinxsim import cli as cli_mod

        runtime = cli_mod._ShellPreviewRuntime()
        runtime._animation_output_dir = tmp_path
        runtime._animation_particle_body_names = {"WaterBody"}
        (tmp_path / "WaterBody_1.vtp").touch()
        assert runtime.replay_animation(relaxation=True) == 1
        assert "No relaxation particle snapshots" in capsys.readouterr().err

    def test_automatic_relaxation_reports_unavailable_qt_without_failing_preview(
        self, tmp_path, fluid_config, capsys
    ):
        from sphinxsim import cli as cli_mod

        runtime = cli_mod._ShellPreviewRuntime()
        runtime.plotter = MagicMock()
        visualizer = MagicMock()
        visualizer._spatial_dim.return_value = 2
        visualizer._try_build_geometries.return_value = tmp_path
        visualizer._discover_latest_particle_vtps.return_value = {}
        visualizer._particle_generation_body_names.return_value = {"WaterBody"}
        simulation = MagicMock()
        visualizer.take_particle_simulation.return_value = (simulation, None)
        (tmp_path / "WaterBody_ite_1.vtp").touch()
        with patch("sphinxsim.visualization.preview.ConfigVisualizer", return_value=visualizer), \
             patch.dict(sys.modules, {"pyvista": MagicMock()}), \
             patch.object(runtime, "_install_annotation_hover"):
            assert runtime.show_or_update(
                fluid_config, resolved_config_path=tmp_path / "case.json", with_particles=True
            ) == 0
        assert runtime._pending_simulation is simulation
        assert "Relaxation playback unavailable" in capsys.readouterr().err

    def test_shell_runtime_caps_live_refresh_at_25_fps(self):
        from sphinxsim import cli as cli_mod

        class FakeTimer:
            def __init__(self):
                self.timeout = MagicMock()
                self.interval = None

            def setInterval(self, interval):
                self.interval = interval

            def start(self):
                pass

        timer = FakeTimer()
        runtime = cli_mod._ShellPreviewRuntime()
        runtime.plotter = MagicMock()
        runtime._live_output_dir = Path(".")
        runtime._live_particle_body_names = {"WaterBody"}
        runtime._live_run_thread = SimpleNamespace(is_alive=lambda: True)
        runtime._using_background_plotter = True
        snapshot_paths = [Path(f"WaterBody_{step}.vtp") for step in (1, 2)]
        with patch.dict(
            sys.modules,
            {"qtpy": SimpleNamespace(QtCore=SimpleNamespace(QTimer=lambda: timer))},
        ), patch.dict(sys.modules, {"pyvista": SimpleNamespace(read=str)}), patch.object(
            Path, "glob", return_value=snapshot_paths
        ), patch.object(Path, "stat", return_value=SimpleNamespace(st_size=1, st_mtime_ns=1)):
            runtime._start_live_refresh(10.0)
            assert timer.interval == 10_000
            runtime._refresh_live_particles()
            assert timer.interval == 40
            runtime._refresh_live_particles()
            assert timer.interval == 10_000

        assert runtime.plotter.add_mesh.call_count == 2

    def test_shell_runtime_skips_empty_particle_mesh(self, tmp_path):
        from sphinxsim import cli as cli_mod

        plotter = MagicMock()
        runtime = cli_mod._ShellPreviewRuntime()
        runtime.plotter = plotter
        runtime._live_output_dir = tmp_path
        runtime._live_particle_body_names = {"WaterBody"}
        (tmp_path / "WaterBody_1.vtp").touch()

        with patch.dict(
            sys.modules,
            {"pyvista": SimpleNamespace(read=lambda _path: SimpleNamespace(n_points=0))},
        ):
            runtime._refresh_live_particles()
            runtime._refresh_live_particles()

        plotter.add_mesh.assert_not_called()

    def test_shell_live_and_replay_frames_keep_particle_visibility(self, tmp_path, fluid_config):
        from sphinxsim import cli as cli_mod
        from sphinxsim.visualization.particle_visibility import ParticleVisibility

        plotter = MagicMock()
        actors = [MagicMock(), MagicMock()]
        plotter.add_mesh.side_effect = actors
        visibility = ParticleVisibility(plotter)
        visibility.set_scene("run", fluid_config)
        visibility.hidden.add("WaterBody")
        runtime = cli_mod._ShellPreviewRuntime()
        runtime.plotter = plotter
        runtime._particle_visibility = visibility
        runtime._live_output_dir = tmp_path
        runtime._live_replay_mode = True
        runtime._live_playback_queue = [
            (1, {"WaterBody": tmp_path / "WaterBody_1.vtp"}),
            (2, {"WaterBody": tmp_path / "WaterBody_2.vtp"}),
        ]
        with patch.object(visibility, "_update_legend"), patch.dict(
            sys.modules,
            {"pyvista": SimpleNamespace(read=lambda _path: SimpleNamespace(n_points=1))},
        ):
            runtime._refresh_live_particles()
            runtime._refresh_live_particles()

        for actor in actors:
            actor.SetVisibility.assert_called_once_with(False)
        assert visibility.actors["WaterBody"] is actors[-1]

    def test_shell_runtime_replays_recorded_frames_at_25_fps(self, tmp_path):
        from sphinxsim import cli as cli_mod

        class FakeTimer:
            def __init__(self):
                self.timeout = MagicMock()
                self.interval = None

            def setInterval(self, interval):
                self.interval = interval

            def start(self):
                pass

            def stop(self):
                pass

        timer = FakeTimer()
        runtime = cli_mod._ShellPreviewRuntime()
        runtime.plotter = MagicMock()
        runtime._using_background_plotter = True
        runtime._animation_output_dir = tmp_path
        runtime._animation_particle_body_names = {"WaterBody"}
        runtime._animation_run_completed = True
        (tmp_path / "WaterBody_1.vtp").touch()
        (tmp_path / "WaterBody_2.vtp").touch()

        with patch.dict(
            sys.modules,
            {"qtpy": SimpleNamespace(QtCore=SimpleNamespace(QTimer=lambda: timer))},
        ):
            assert runtime.replay_animation() == 0

        assert timer.interval == 40
        assert len(runtime._live_playback_queue) == 2
        assert runtime._live_replay_mode is True
        with patch.dict(
            sys.modules,
            {
                "pyvista": SimpleNamespace(
                    read=lambda path: SimpleNamespace(n_points=10, path=path)
                )
            },
        ):
            runtime._refresh_live_particles()
            runtime._refresh_live_particles()

        assert runtime.plotter.add_mesh.call_count == 2
        assert all(
            call.kwargs["reset_camera"] is False
            for call in runtime.plotter.add_mesh.call_args_list
        )
        assert runtime._live_replay_mode is False
        assert runtime._live_refresh_timer is None

    def test_shell_runtime_does_not_replay_before_run_finishes(self, tmp_path, capsys):
        from sphinxsim import cli as cli_mod

        runtime = cli_mod._ShellPreviewRuntime()
        runtime._animation_output_dir = tmp_path
        runtime._animation_particle_body_names = {"WaterBody"}

        assert runtime.replay_animation() == 1
        assert "Complete a successful animated run" in capsys.readouterr().err

    def test_shell_runtime_opens_window_for_replay_after_regular_run(self, tmp_path):
        from sphinxsim import cli as cli_mod

        class FakeTimer:
            def __init__(self):
                self.timeout = MagicMock()

            def setInterval(self, _interval):
                pass

            def start(self):
                pass

        timer = FakeTimer()
        plotter = MagicMock()
        runtime = cli_mod._ShellPreviewRuntime()
        runtime._animation_output_dir = tmp_path
        runtime._animation_particle_body_names = {"WaterBody"}
        runtime._animation_run_completed = True
        runtime._animation_config = SimulationConfig(**_minimal_fluid_config())
        runtime._animation_config_path = tmp_path / "config.json"
        (tmp_path / "WaterBody_1.vtp").touch()

        def create_preview(*_args, **_kwargs):
            runtime.plotter = plotter
            runtime._using_background_plotter = True
            return 0

        with patch.dict(
            sys.modules,
            {"qtpy": SimpleNamespace(QtCore=SimpleNamespace(QTimer=lambda: timer))},
        ), patch.object(
            runtime, "show_or_update", side_effect=create_preview
        ) as show_or_update:
            assert runtime.replay_animation() == 0

        show_or_update.assert_called_once_with(
            runtime._animation_config,
            resolved_config_path=runtime._animation_config_path,
            with_particles=False,
            force=True,
        )
        assert runtime.plotter is plotter
        assert runtime._using_background_plotter is True

    def test_shell_replay_command_calls_runtime(self):
        from sphinxsim import cli as cli_mod

        with patch.object(
            cli_mod._ShellPreviewRuntime, "replay_animation", return_value=0
        ) as replay:
            with patch("builtins.input", side_effect=["replay", "exit"]):
                assert main(["shell"]) == 0

        replay.assert_called_once_with()

    def test_shell_relaxation_replay_and_slash_form_preserve_simulation(self):
        from sphinxsim import cli as cli_mod

        calls = []
        with patch.object(cli_mod._ShellPreviewRuntime, "replay_animation",
                          side_effect=lambda **kwargs: calls.append(kwargs)), \
             patch.object(cli_mod._ShellPreviewRuntime, "initialize_simulation",
                          side_effect=lambda: calls.append("initialize")), \
             patch.object(cli_mod._ShellPreviewRuntime, "discard_pending_simulation",
                          side_effect=lambda: calls.append("discard")), \
             patch("builtins.input", side_effect=[
                 "replay --relaxation", "/replay --relaxation", "initialize-simulation", "exit"
             ]):
            assert main(["shell"]) == 0
        assert calls == [{"relaxation": True}, {"relaxation": True}, "initialize", "discard"]

    def test_shell_continue_to_run_accepts_refresh_interval(self):
        from sphinxsim import cli as cli_mod

        with patch.object(cli_mod._ShellPreviewRuntime, "continue_to_run", return_value=0) as continue_to_run:
            with patch("builtins.input", side_effect=["continue-to-run --refresh-interval 2.5", "exit"]):
                assert main(["shell"]) == 0

        continue_to_run.assert_called_once_with(2.5)

    def test_shell_runtime_releases_pending_simulation_for_other_command(self, build_temp_path):
        _, rel = self._write_config(build_temp_path)
        pending_simulation = object()

        with patch.dict(sys.modules, {"pyvista": MagicMock()}):
            with patch(
                "sphinxsim.cli._ShellPreviewRuntime.show_or_update",
                return_value=0,
            ):
                with patch.object(
                    __import__("sphinxsim.cli", fromlist=["_ShellPreviewRuntime"])._ShellPreviewRuntime,
                    "discard_pending_simulation",
                ) as discard:
                    with patch("builtins.input", side_effect=[f"load {rel}", "preview", "exit"]):
                        rc = main(["shell"])

        assert rc == 0
        assert discard.call_count >= 2

    def test_shell_runtime_does_not_require_legacy_view_widgets(self, tmp_path, monkeypatch):
        from sphinxsim import cli as cli_mod

        cfg = SimulationConfig(**_minimal_fluid_config())
        runtime = cli_mod._ShellPreviewRuntime()

        class FakeVisualizer:
            used_cpp_bounds = False

            def __init__(self, *args, **kwargs):
                pass

            def _spatial_dim(self):
                return 2

            def _populate_plotter(self, plotter, vtp_dir, latest_particle_vtps):
                return None

            def _configure_default_view(self, plotter, ndim):
                return None

            def _try_build_geometries(self, ndim, with_particles=False):
                return None

            def _discover_latest_particle_vtps(self, vtp_dir):
                return {}

        class FakePlotter:
            def clear(self):
                pass

            def add_axes(self):
                pass

            def show_grid(self, **kwargs):
                pass

            def show(self, **kwargs):
                pass

            def render(self):
                pass

        class FakePyVista:
            def Plotter(self, **kwargs):
                return FakePlotter()

        monkeypatch.setitem(sys.modules, "pyvista", FakePyVista())
        monkeypatch.setitem(sys.modules, "pyvistaqt", None)
        monkeypatch.setattr(cli_mod, "PROJECT_ROOT", tmp_path)

        with patch("sphinxsim.visualization.preview.ConfigVisualizer", FakeVisualizer):
            rc = runtime.show_or_update(
                cfg,
                resolved_config_path=tmp_path / "config.json",
                with_particles=False,
            )

        assert rc == 0
        assert not runtime._using_background_plotter

    def test_shell_runtime_rebinds_editor_when_preview_path_changes(self, tmp_path, monkeypatch):
        """An existing editor must follow the newly loaded shell config file."""
        from sphinxsim import cli as cli_mod

        cfg = SimulationConfig(**_minimal_fluid_config())
        config_a = tmp_path / "preview-a.json"
        config_b = tmp_path / "preview-b.json"
        visualizer_paths: list[Path] = []

        class FakeVisualizer:
            used_cpp_bounds = False

            def __init__(self, _config, _project_root, *, config_path, off_screen):
                visualizer_paths.append(config_path)

            def _spatial_dim(self):
                return 2

            def _populate_plotter(self, plotter, vtp_dir, latest_particle_vtps):
                return None

            def _configure_default_view(self, plotter, ndim):
                return None

            def _try_build_geometries(self, ndim, with_particles=False):
                return None

            def _discover_latest_particle_vtps(self, vtp_dir):
                return {}

        class FakePlotter:
            def clear(self):
                return None

            def add_axes(self):
                return None

            def show_grid(self, **kwargs):
                return None

            def render(self):
                return None

        runtime = cli_mod._ShellPreviewRuntime()
        runtime.plotter = FakePlotter()
        runtime._using_background_plotter = True
        refresh_editor = MagicMock()
        runtime._json_editor = {"refresh": refresh_editor}

        monkeypatch.setitem(sys.modules, "pyvista", SimpleNamespace())
        monkeypatch.setitem(sys.modules, "pyvistaqt", SimpleNamespace(BackgroundPlotter=object))
        monkeypatch.setattr(cli_mod, "PROJECT_ROOT", tmp_path)

        with patch("sphinxsim.visualization.preview.ConfigVisualizer", FakeVisualizer):
            assert runtime.show_or_update(cfg, resolved_config_path=config_a, with_particles=False) == 0
            assert runtime.show_or_update(cfg, resolved_config_path=config_b, with_particles=False) == 0

        assert visualizer_paths == [config_a, config_b]
        assert [call.args[1] for call in refresh_editor.call_args_list] == [config_a, config_b]

    def test_shell_runtime_preserves_editor_when_preview_reset_is_canceled(
        self, tmp_path, monkeypatch
    ):
        """Canceling the warning must leave both editor and preview state untouched."""
        from sphinxsim import cli as cli_mod

        cfg = SimulationConfig(**_minimal_fluid_config())
        config_path = tmp_path / "config.json"
        runtime = cli_mod._ShellPreviewRuntime()
        runtime.plotter = MagicMock()
        runtime._using_background_plotter = True
        refresh_editor = MagicMock(return_value=False)
        runtime._json_editor = {"refresh": refresh_editor}

        monkeypatch.setitem(sys.modules, "pyvista", SimpleNamespace())

        assert (
            runtime.show_or_update(
                cfg,
                resolved_config_path=config_path,
                with_particles=False,
            )
            == 0
        )

        refresh_editor.assert_called_once_with(cfg, config_path, False)
        assert runtime.last_signature is None
        runtime.plotter.clear.assert_not_called()

    def test_shell_runtime_recreates_preview_after_user_closes_window(self, tmp_path, monkeypatch):
        """Closing the native Qt window must allow the same config to reopen."""
        from sphinxsim import cli as cli_mod

        cfg = SimulationConfig(**_minimal_fluid_config())
        config_path = tmp_path / "config.json"

        class FakeVisualizer:
            used_cpp_bounds = False

            def __init__(self, *args, **kwargs):
                pass

            def _spatial_dim(self):
                return 2

            def _populate_plotter(self, plotter, vtp_dir, latest_particle_vtps):
                return None

            def _configure_default_view(self, plotter, ndim):
                return None

            def _try_build_geometries(self, ndim, with_particles=False):
                return None

            def _discover_latest_particle_vtps(self, vtp_dir):
                return {}

        class FakeSignal:
            def __init__(self):
                self.callback = None

            def connect(self, callback):
                self.callback = callback

            def emit(self):
                assert self.callback is not None
                self.callback()

        class FakeBackgroundPlotter:
            instances: list["FakeBackgroundPlotter"] = []

            def __init__(self, **kwargs):
                self.app_window = SimpleNamespace(signal_close=FakeSignal())
                self.instances.append(self)

            def clear(self):
                return None

            def add_axes(self):
                return None

            def show_grid(self, **kwargs):
                return None

            def render(self):
                return None

        runtime = cli_mod._ShellPreviewRuntime()
        monkeypatch.setattr(runtime, "_install_json_editor", MagicMock())
        monkeypatch.setitem(sys.modules, "pyvista", SimpleNamespace())
        monkeypatch.setitem(sys.modules, "pyvistaqt", SimpleNamespace(BackgroundPlotter=FakeBackgroundPlotter))
        monkeypatch.setattr(cli_mod, "PROJECT_ROOT", tmp_path)

        with patch("sphinxsim.visualization.preview.ConfigVisualizer", FakeVisualizer):
            assert runtime.show_or_update(cfg, resolved_config_path=config_path, with_particles=False) == 0
            first_plotter = runtime.plotter
            assert first_plotter is FakeBackgroundPlotter.instances[0]

            first_plotter.app_window.signal_close.emit()
            assert runtime.plotter is None
            assert runtime.last_signature is None

            assert runtime.show_or_update(cfg, resolved_config_path=config_path, with_particles=False) == 0

        assert len(FakeBackgroundPlotter.instances) == 2
        assert runtime.plotter is FakeBackgroundPlotter.instances[1]

    def test_shell_runtime_hover_enlarges_annotation_font(self):
        from sphinxsim import cli as cli_mod

        class FakeTextProperty:
            def __init__(self, size=8):
                self.size = size

            def SetFontSize(self, size):
                self.size = int(size)

        class FakeMapper:
            def __init__(self, prop):
                self.prop = prop

            def GetLabelTextProperty(self):
                return self.prop

            def Modified(self):
                return None

        class FakeActor:
            def __init__(self, size=8):
                self.prop = FakeTextProperty(size=size)
                self.mapper = FakeMapper(self.prop)

            def GetMapper(self):
                return self.mapper

            def Modified(self):
                return None

        class FakeInteractor:
            def __init__(self):
                self._observers = {}
                self._next_tag = 1
                self._pos = (0, 0)

            def AddObserver(self, event_name, callback):
                tag = self._next_tag
                self._next_tag += 1
                self._observers[event_name] = callback
                return tag

            def RemoveObserver(self, tag):
                return None

            def GetEventPosition(self):
                return self._pos

            def trigger_mouse_move(self, x, y):
                self._pos = (x, y)
                callback = self._observers.get("MouseMoveEvent")
                if callback is not None:
                    callback(self, "MouseMoveEvent")

            def trigger_left_click(self, x, y):
                self._pos = (x, y)
                callback = self._observers.get("LeftButtonPressEvent")
                if callback is not None:
                    callback(self, "LeftButtonPressEvent")

        class FakeCoordinate:
            def __init__(self):
                self._value = (0.0, 0.0, 0.0)

            def SetCoordinateSystemToWorld(self):
                return None

            def SetValue(self, x, y, z):
                self._value = (float(x), float(y), float(z))

            def GetComputedDisplayValue(self, renderer):
                return (int(self._value[0]), int(self._value[1]))

        class FakeVtkModule:
            def __init__(self, actor):
                self.actor = actor

            def vtkCoordinate(self):
                return FakeCoordinate()

        class FakeIrenWrapper:
            def __init__(self, interactor):
                self.interactor = interactor

        class FakePlotter:
            def __init__(self, interactor):
                self.iren = FakeIrenWrapper(interactor)
                self.renderer = object()

            def render(self):
                return None

        actor = FakeActor(size=8)
        interactor = FakeInteractor()

        runtime = cli_mod._ShellPreviewRuntime()
        runtime.plotter = FakePlotter(interactor)
        runtime._using_background_plotter = True

        class FakeVisualizer:
            @property
            def annotation_label_actors(self):
                return [{
                    "actor": actor,
                    "font_size": 8,
                    "points": [(20.0, 20.0, 0.0)],
                    "labels": ["demo annotation"],
                    "text_color": "white",
                    "config_path": "geometries.oriented_boxes.0",
                }]

        revealed_paths = []
        runtime._json_editor = {"reveal_path": revealed_paths.append}
        with patch.dict(sys.modules, {"vtk": FakeVtkModule(actor)}):
            runtime._install_annotation_hover(FakeVisualizer())

        assert actor.prop.size == 8
        interactor.trigger_mouse_move(55, 24)
        assert actor.prop.size == 12
        interactor.trigger_left_click(55, 24)
        assert revealed_paths == ["geometries.oriented_boxes.0"]
        interactor.trigger_mouse_move(1, 1)
        assert actor.prop.size == 8

    def test_hover_rebuild_rebinds_annotation_and_preserves_visibility(self):
        from sphinxsim.visualization.preview_runtime import _ShellPreviewRuntime

        class Actor:
            def __init__(self):
                self.visible = True

            def GetMapper(self):
                return object()

            def GetVisibility(self):
                return self.visible

            def SetVisibility(self, visible):
                self.visible = bool(visible)

        class Interactor:
            def __init__(self):
                self.observers = {}

            def AddObserver(self, event, callback):
                self.observers[event] = callback
                return event

            def GetEventPosition(self):
                return (20, 20)

            def trigger(self, event):
                self.observers[event](self, event)

        class Coordinate:
            def SetCoordinateSystemToWorld(self):
                pass

            def SetValue(self, *args):
                pass

            def GetComputedDisplayValue(self, renderer):
                return (20, 20)

        entry = {
            "actor": Actor(),
            "font_size": 8,
            "points": [(0.0, 0.0, 0.0)],
            "labels": ["label"],
            "config_path": "gravity",
            "label_options": {
                "shape_color": (0.2, 0.2, 0.2),
                "shape_opacity": 0.96,
                "background_opacity": 0.96,
            },
        }

        class Visualizer:
            annotation_label_actors = [entry]

            @property
            def preview_element_actors(self):
                return {"gravity": {"annotation": [entry["actor"]]}}

        interactor = Interactor()
        runtime = _ShellPreviewRuntime()
        runtime._using_background_plotter = True
        runtime.plotter = SimpleNamespace(
            iren=SimpleNamespace(interactor=interactor),
            renderer=object(),
            remove_actor=lambda *args, **kwargs: None,
            add_point_labels=MagicMock(side_effect=lambda *args, **kwargs: Actor()),
        )
        bindings = []
        revealed = []
        runtime._json_editor = {
            "set_preview_elements": bindings.append,
            "reveal_path": revealed.append,
        }
        visualizer = Visualizer()
        with patch.dict(sys.modules, {"vtk": SimpleNamespace(vtkCoordinate=Coordinate)}):
            runtime._install_annotation_hover(visualizer)

        for key, value in entry["label_options"].items():
            assert runtime.plotter.add_point_labels.call_args.kwargs[key] == value
        runtime._json_editor["set_preview_elements"](visualizer.preview_element_actors)
        assert bindings[-1]["gravity"]["annotation"] == [entry["actor"]]
        interactor.trigger("MouseMoveEvent")
        entry["actor"].SetVisibility(False)
        interactor.trigger("MouseMoveEvent")
        assert not entry["actor"].GetVisibility()
        assert bindings[-1]["gravity"]["annotation"] == [entry["actor"]]
        interactor.trigger("LeftButtonPressEvent")
        assert revealed == []

    def test_shell_help_mentions_preview(self, build_temp_path, capsys):
        inputs = ["help", "exit"]
        with patch("builtins.input", side_effect=inputs):
            rc = main(["shell"])
        assert rc == 0
        assert "preview" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# Screenshot tests
# ---------------------------------------------------------------------------

class TestScreenshot:
    """Tests for the screenshot output feature."""

    def test_preview_does_not_request_legacy_view_widgets(self, tmp_path, monkeypatch):
        """preview() should rely on the native plotter UI instead of in-canvas view widgets."""
        import sphinxsim.visualization.preview as pv_mod

        cfg = SimulationConfig(**_minimal_fluid_config())

        class WidgetlessPlotter:
            window_size = (800, 600)

            def add_mesh(self, mesh, **kwargs):
                pass

            def add_point_labels(self, points, labels, **kwargs):
                pass

            def add_text(self, *args, **kwargs):
                pass

            def add_legend(self, *args, **kwargs):
                pass

            def show(self):
                pass

            def enable_2d_style(self):
                pass

            def enable_parallel_projection(self):
                pass

            def view_xy(self, negative=False):
                pass

            def __getattr__(self, name):
                if name == "add_radio_button_widget":
                    raise AssertionError("legacy in-canvas view widgets should not be requested")

                def _noop(*args, **kwargs):
                    pass

                return _noop

        class WidgetlessMockPyVista:
            def Plotter(self, **kwargs):
                return WidgetlessPlotter()

            @staticmethod
            def PolyData(points):
                class MockPolyData:
                    def __init__(self, pts):
                        self.points = pts
                        self.center = [0.5, 0.5, 0.0] if len(pts) > 0 else [0.0, 0.0, 0.0]
                        self.bounds = [[0.0, 1.0], [0.0, 1.0], [0.0, 1.0]]

                return MockPolyData(points)

            @staticmethod
            def Arrow(start, direction, scale):
                return {
                    "type": "arrow",
                    "start": start,
                    "direction": direction,
                    "scale": scale,
                }

            @staticmethod
            def Box(bounds):
                class MockBox:
                    def __init__(self):
                        self.bounds = bounds

                return MockBox()

        monkeypatch.setitem(sys.modules, "pyvista", WidgetlessMockPyVista())

        viz = pv_mod.ConfigVisualizer(cfg, tmp_path, off_screen=False)
        viz.preview()

    def test_preview_screenshot_calls_plotter_screenshot(self, tmp_path, monkeypatch):
        """preview() with screenshot_path should call plotter.screenshot() instead of plotter.show()."""
        import sphinxsim.visualization.preview as pv_mod

        cfg = SimulationConfig(**_minimal_fluid_config())

        screenshot_calls: list[str] = []
        show_calls: list[int] = []

        class ScreenshotMockPlotter:
            window_size = (800, 600)

            def add_mesh(self, mesh, **kwargs):
                pass

            def add_point_labels(self, points, labels, **kwargs):
                pass

            def add_text(self, *args, **kwargs):
                pass

            def add_legend(self, *args, **kwargs):
                pass

            def screenshot(self, path):
                screenshot_calls.append(path)

            def show(self):
                show_calls.append(1)

            def __getattr__(self, name):
                def _noop(*args, **kwargs):
                    pass
                return _noop

        class ScreenshotMockPyVista:
            def Plotter(self, **kwargs):
                return ScreenshotMockPlotter()

            @staticmethod
            def PolyData(points):
                class MockPolyData:
                    def __init__(self, pts):
                        self.points = pts
                        self.center = [0.5, 0.5, 0.0] if len(pts) > 0 else [0.0, 0.0, 0.0]
                        self.bounds = [[0.0, 1.0], [0.0, 1.0], [0.0, 1.0]]

                return MockPolyData(points)

            @staticmethod
            def Arrow(start, direction, scale):
                return {
                    "type": "arrow",
                    "start": start,
                    "direction": direction,
                    "scale": scale,
                }

            @staticmethod
            def Box(bounds):
                class MockBox:
                    def __init__(self):
                        self.bounds = bounds
                return MockBox()

        monkeypatch.setitem(sys.modules, "pyvista", ScreenshotMockPyVista())

        viz = pv_mod.ConfigVisualizer(cfg, tmp_path, off_screen=False)
        out_file = str(tmp_path / "screenshot.png")
        viz.preview(screenshot_path=out_file)

        assert len(screenshot_calls) == 1
        assert screenshot_calls[0] == out_file
        assert len(show_calls) == 0  # show() should NOT be called when screenshot_path is set

    def test_preview_without_screenshot_calls_show(self, tmp_path, monkeypatch):
        """preview() without screenshot_path should call plotter.show() and NOT plotter.screenshot()."""
        import sphinxsim.visualization.preview as pv_mod

        cfg = SimulationConfig(**_minimal_fluid_config())

        screenshot_calls: list[str] = []
        show_calls: list[int] = []

        class ShowMockPlotter:
            window_size = (800, 600)

            def add_mesh(self, mesh, **kwargs):
                pass

            def add_point_labels(self, points, labels, **kwargs):
                pass

            def add_text(self, *args, **kwargs):
                pass

            def add_legend(self, *args, **kwargs):
                pass

            def screenshot(self, path):
                screenshot_calls.append(path)

            def show(self):
                show_calls.append(1)

            def __getattr__(self, name):
                def _noop(*args, **kwargs):
                    pass
                return _noop

        class ShowMockPyVista:
            def Plotter(self, **kwargs):
                return ShowMockPlotter()

            @staticmethod
            def PolyData(points):
                class MockPolyData:
                    def __init__(self, pts):
                        self.points = pts
                        self.center = [0.5, 0.5, 0.0] if len(pts) > 0 else [0.0, 0.0, 0.0]
                        self.bounds = [[0.0, 1.0], [0.0, 1.0], [0.0, 1.0]]

                return MockPolyData(points)

            @staticmethod
            def Arrow(start, direction, scale):
                return {
                    "type": "arrow",
                    "start": start,
                    "direction": direction,
                    "scale": scale,
                }

            @staticmethod
            def Box(bounds):
                class MockBox:
                    def __init__(self):
                        self.bounds = bounds
                return MockBox()

        monkeypatch.setitem(sys.modules, "pyvista", ShowMockPyVista())

        viz = pv_mod.ConfigVisualizer(cfg, tmp_path, off_screen=False)
        viz.preview()

        assert len(show_calls) == 1
        assert len(screenshot_calls) == 0

    def test_preview_refreshes_shape_bounds_cache_on_rerun(self, tmp_path, monkeypatch):
        """A rerun should discard stale bounds and rebuild the cache from the current geometry."""
        import sphinxsim.visualization.preview as pv_mod

        cfg = SimulationConfig(**_minimal_fluid_config())

        class CacheRefreshPlotter:
            window_size = (800, 600)

            def add_mesh(self, *args, **kwargs):
                pass

            def add_point_labels(self, *args, **kwargs):
                pass

            def add_text(self, *args, **kwargs):
                pass

            def add_legend(self, *args, **kwargs):
                pass

            def add_axes(self, *args, **kwargs):
                pass

            def show_grid(self, *args, **kwargs):
                pass

            def show(self, *args, **kwargs):
                pass

            def __getattr__(self, name):
                def _noop(*args, **kwargs):
                    pass

                return _noop

        class CacheRefreshPyVista:
            def Plotter(self, **kwargs):
                return CacheRefreshPlotter()

            @staticmethod
            def PolyData(points):
                class MockPolyData:
                    def __init__(self, pts):
                        self.points = pts
                        self.center = [0.5, 0.5, 0.0] if len(pts) > 0 else [0.0, 0.0, 0.0]
                        self.bounds = [[0.0, 1.0], [0.0, 1.0], [0.0, 1.0]]

                return MockPolyData(points)

            @staticmethod
            def Arrow(start, direction, scale):
                return {
                    "type": "arrow",
                    "start": start,
                    "direction": direction,
                    "scale": scale,
                }

            @staticmethod
            def Box(bounds):
                class MockBox:
                    def __init__(self):
                        self.bounds = bounds

                return MockBox()

        monkeypatch.setitem(sys.modules, "pyvista", CacheRefreshPyVista())

        viz = pv_mod.ConfigVisualizer(cfg, tmp_path, off_screen=False)
        viz._shape_bounds_cache = {
            "stale": ([9.0, 9.0], [10.0, 10.0]),
        }

        def fake_try_build_geometries(self, ndim, with_particles=False):
            assert self._shape_bounds_cache is None
            self._shape_bounds_cache = {
                "WaterBody": ([0.0, 0.0], [0.4, 0.2]),
                "WallBoundary": ([0.0, 0.0], [0.4, 0.2]),
            }
            return None

        monkeypatch.setattr(pv_mod.ConfigVisualizer, "_try_build_geometries", fake_try_build_geometries)
        monkeypatch.setattr(pv_mod.ConfigVisualizer, "_populate_plotter", lambda *args, **kwargs: None)

        viz.preview()

        assert viz._shape_bounds_cache == {
            "WaterBody": ([0.0, 0.0], [0.4, 0.2]),
            "WallBoundary": ([0.0, 0.0], [0.4, 0.2]),
        }


class TestCLIScreenshotCommand:
    """CLI tests for the --screenshot flag."""

    def _write_config(self, path: Path) -> Path:
        p = path / "config.json"
        p.write_text(json.dumps(_minimal_fluid_config()))
        return p

    def test_screenshot_flag_passes_screenshot_path(self, build_temp_path):
        """--screenshot FILE should pass screenshot_path to visualizer.preview()."""
        cfg = self._write_config(build_temp_path)
        mock_pv = MagicMock()
        fake_visualizer = MagicMock()

        with patch.dict(sys.modules, {"pyvista": mock_pv}):
            with patch(
                "sphinxsim.visualization.preview.ConfigVisualizer",
                return_value=fake_visualizer,
            ) as MockViz:
                rc = main(["preview", str(cfg), "--screenshot", "output.png"])

        assert rc == 0
        fake_visualizer.preview.assert_called_once_with(
            screenshot_path="output.png",
            with_particles=False,
        )

    def test_screenshot_short_flag_passes_screenshot_path(self, build_temp_path):
        """-s FILE should pass screenshot_path to visualizer.preview()."""
        cfg = self._write_config(build_temp_path)
        mock_pv = MagicMock()
        fake_visualizer = MagicMock()

        with patch.dict(sys.modules, {"pyvista": mock_pv}):
            with patch(
                "sphinxsim.visualization.preview.ConfigVisualizer",
                return_value=fake_visualizer,
            ):
                rc = main(["preview", str(cfg), "-s", "out.png"])

        assert rc == 0
        fake_visualizer.preview.assert_called_once_with(
            screenshot_path="out.png",
            with_particles=False,
        )

    def test_screenshot_implies_off_screen(self, build_temp_path):
        """--screenshot should cause ConfigVisualizer to be constructed with off_screen=True."""
        cfg = self._write_config(build_temp_path)
        mock_pv = MagicMock()
        fake_visualizer = MagicMock()

        with patch.dict(sys.modules, {"pyvista": mock_pv}):
            with patch(
                "sphinxsim.visualization.preview.ConfigVisualizer",
                return_value=fake_visualizer,
            ) as MockViz:
                rc = main(["preview", str(cfg), "--screenshot", "shot.png"])

        assert rc == 0
        _, kwargs = MockViz.call_args
        assert kwargs.get("off_screen") is True

    def test_no_screenshot_does_not_force_off_screen(self, build_temp_path):
        """Without --screenshot, off_screen should remain False (unless --off-screen is given)."""
        cfg = self._write_config(build_temp_path)
        mock_pv = MagicMock()
        fake_visualizer = MagicMock()

        with patch.dict(sys.modules, {"pyvista": mock_pv}):
            with patch(
                "sphinxsim.visualization.preview.ConfigVisualizer",
                return_value=fake_visualizer,
            ) as MockViz:
                rc = main(["preview", str(cfg)])

        assert rc == 0
        _, kwargs = MockViz.call_args
        assert kwargs.get("off_screen") is False


class TestShellScreenshot:
    """Shell mode tests for the --screenshot flag."""

    def _write_config(self, path: Path) -> tuple[Path, str]:
        p = path / "config.json"
        p.write_text(json.dumps(_minimal_fluid_config()))
        rel = f"pytest-temp/{path.name}/config.json"
        return p, rel

    def test_shell_screenshot_passes_screenshot_path(self, build_temp_path):
        """Shell mode: 'preview --screenshot FILE' should pass screenshot_path to preview()."""
        _, rel = self._write_config(build_temp_path)
        fake_visualizer = MagicMock()

        inputs = [f"load {rel}", "preview --screenshot shell_out.png", "exit"]
        with patch.dict(sys.modules, {"pyvista": MagicMock()}):
            with patch(
                "sphinxsim.visualization.preview.ConfigVisualizer",
                return_value=fake_visualizer,
            ):
                with patch("builtins.input", side_effect=inputs):
                    rc = main(["shell"])

        assert rc == 0
        fake_visualizer.preview.assert_called_once_with(
            screenshot_path="shell_out.png",
            with_particles=False,
        )

    def test_shell_screenshot_short_flag(self, build_temp_path):
        """Shell mode: 'preview -s FILE' should pass screenshot_path to preview()."""
        _, rel = self._write_config(build_temp_path)
        fake_visualizer = MagicMock()

        inputs = [f"load {rel}", "preview -s short.png", "exit"]
        with patch.dict(sys.modules, {"pyvista": MagicMock()}):
            with patch(
                "sphinxsim.visualization.preview.ConfigVisualizer",
                return_value=fake_visualizer,
            ):
                with patch("builtins.input", side_effect=inputs):
                    rc = main(["shell"])

        assert rc == 0
        fake_visualizer.preview.assert_called_once_with(
            screenshot_path="short.png",
            with_particles=False,
        )


# ---------------------------------------------------------------------------
# Gravity arrow preview tests
# ---------------------------------------------------------------------------

class _FakeMesh:
    """Lightweight stand-in for a PyVista mesh with bounds/center."""

    def __init__(self, bounds: tuple[float, ...]):
        # bounds is a flat tuple (x0, x1, y0, y1, z0, z1)
        self.bounds = bounds
        x0, x1, y0, y1, z0, z1 = bounds
        self.center = ((x0 + x1) / 2, (y0 + y1) / 2, (z0 + z1) / 2)


class _FakePlotter:
    """Records all add_* calls for later assertions."""

    def __init__(self):
        self.mesh_calls: list[dict[str, Any]] = []
        self.label_calls: list[dict[str, Any]] = []
        self.text_calls: list[dict[str, Any]] = []

    def add_mesh(self, mesh, **kwargs):
        self.mesh_calls.append({"mesh": mesh, **kwargs})

    def add_point_labels(self, points, labels, **kwargs):
        self.label_calls.append({"points": points, "labels": labels, **kwargs})

    def add_text(self, *args, **kwargs):
        self.text_calls.append({"args": args, **kwargs})

    def add_legend(self, *args, **kwargs):
        return None


class _FakePyVista:
    """Mock pyvista module providing Box, Arrow, PolyData, read."""

    @staticmethod
    def Box(bounds):
        return _FakeMesh(bounds=bounds)

    @staticmethod
    def Arrow(start, direction, scale):
        # Match PyVista's requirement: both vectors must be 3-D.
        if len(start) != 3 or len(direction) != 3:
            raise ValueError("Arrow start and direction must be 3-D vectors")
        return {"type": "arrow", "start": start, "direction": direction, "scale": scale}

    @staticmethod
    def PolyData(points):
        return _FakeMesh(bounds=(0.0, 1.0, 0.0, 1.0, 0.0, 0.0))

    @staticmethod
    def read(path):
        return _FakeMesh(bounds=(0.0, 1.0, 0.0, 1.0, 0.0, 0.0))


class TestPreviewGravityArrow:
    """Tests that the gravity arrow is rendered in the preview plotter."""

    def test_dambreak_emitter_annotation_has_visible_background(self, tmp_path, monkeypatch):
        from sphinxsim.visualization.preview import ConfigVisualizer

        config_path = Path(__file__).parent / "test_simulation" / "test_3d_simulation" / "data" / "dambreak.json"
        config = SimulationConfig.model_validate_json(config_path.read_text())
        viz = ConfigVisualizer(config, tmp_path, off_screen=True)
        monkeypatch.setattr(
            viz, "_load_oriented_box_mesh",
            lambda ob, vtp_dir: _FakeMesh((0.4, 0.6, 1.4, 1.6, 0.1, 0.4)),
        )
        plotter = _FakePlotter()
        with patch.dict(sys.modules, {"pyvista": _FakePyVista}):
            viz._populate_plotter(plotter, vtp_dir=None)

        emitter = next(
            call for call in plotter.label_calls
            if any("Emitter" in label for label in call["labels"])
        )
        assert emitter["fill_shape"]
        assert emitter["shape_color"] == "grey"
        assert emitter["shape_opacity"] == 1.0
        observer = next(
            call for call in plotter.label_calls if call["labels"] == ["Observer 1"]
        )
        assert observer["shape_opacity"] == 0.96

    def test_dambreak_emitter_background_survives_hover_and_toggle(self, tmp_path, monkeypatch):
        pv = pytest.importorskip("pyvista")
        import numpy as np

        from sphinxsim.visualization.preview import ConfigVisualizer
        from sphinxsim.visualization.preview_runtime import _ShellPreviewRuntime

        config_path = Path(__file__).parent / "test_simulation" / "test_3d_simulation" / "data" / "dambreak.json"
        config = SimulationConfig.model_validate_json(config_path.read_text())
        viz = ConfigVisualizer(config, tmp_path, off_screen=True)
        monkeypatch.setattr(
            viz, "_load_oriented_box_mesh",
            lambda ob, vtp_dir: pv.Box(bounds=(0.4, 0.6, 1.4, 1.6, 0.1, 0.4)),
        )
        plotter = pv.Plotter(off_screen=True, window_size=(600, 400))
        try:
            viz._populate_plotter(plotter, vtp_dir=None)
            entry = next(
                item for item in viz.annotation_label_actors
                if item["config_path"] == "geometries.oriented_boxes.0"
            )
            actor = entry["actor"]
            mapper = actor.GetMapper()
            assert mapper.GetBackgroundOpacity() == pytest.approx(1.0)
            assert mapper.GetBackgroundColor() == pytest.approx((0.5, 0.5, 0.5), abs=0.01)
            assert _ShellPreviewRuntime._set_label_font_size(actor, 12)

            plotter.view_xy()
            plotter.show(auto_close=False)
            shown = plotter.screenshot(return_img=True)
            actor.SetVisibility(False)
            plotter.render()
            hidden = plotter.screenshot(return_img=True)
            changed = np.any(shown != hidden, axis=2)
            grey = np.all((shown >= 120) & (shown <= 136), axis=2)
            assert np.any(changed & grey)

            actor.SetVisibility(True)
            plotter.render()
            assert np.any(plotter.screenshot(return_img=True) != hidden)
        finally:
            plotter.close()

    def test_gravity_arrow_added_when_gravity_set(self, fluid_config, tmp_path):
        """When gravity is set, _populate_plotter should add an arrow mesh."""
        from sphinxsim.visualization.preview import ConfigVisualizer

        viz = ConfigVisualizer(fluid_config, tmp_path, off_screen=True)

        fake_plotter = _FakePlotter()
        with patch.dict(sys.modules, {"pyvista": _FakePyVista}):
            viz._populate_plotter(fake_plotter, vtp_dir=None)

        # An arrow mesh should have been added with the gravity colour and label.
        arrow_calls = [c for c in fake_plotter.mesh_calls if c.get("label") == "Gravity"]
        assert len(arrow_calls) == 1
        assert arrow_calls[0]["color"] == (0.00, 0.48, 0.50)

        # The arrow direction should match the gravity direction (normalised).
        arrow = arrow_calls[0]["mesh"]
        assert arrow["direction"] == (0.0, -1.0, 0.0)

        # The gravity text label should have been rendered.
        assert len(fake_plotter.label_calls) > 0 or len(fake_plotter.text_calls) > 0

    def test_no_gravity_arrow_when_gravity_unset(self, fluid_config, tmp_path):
        """When gravity is None, no arrow mesh should be added."""
        from sphinxsim.visualization.preview import ConfigVisualizer

        data = copy.deepcopy(fluid_config.model_dump(exclude_none=True))
        data["gravity"] = None
        cfg = SimulationConfig(**data)
        viz = ConfigVisualizer(cfg, tmp_path, off_screen=True)

        fake_plotter = _FakePlotter()
        with patch.dict(sys.modules, {"pyvista": _FakePyVista}):
            viz._populate_plotter(fake_plotter, vtp_dir=None)

    def test_overlapping_shape_annotations_are_deconflicted(self, fluid_config, tmp_path):
        """Labels for overlapping shapes should not end up at the same anchor."""
        from sphinxsim.visualization.preview import ConfigVisualizer

        data = copy.deepcopy(fluid_config.model_dump(exclude_none=True))
        # Force both shapes to the same center so naive labeling would collide.
        data["geometries"]["shapes"][1]["lower_bound"] = [0.0, 0.0]
        data["geometries"]["shapes"][1]["upper_bound"] = [0.4, 0.2]
        cfg = SimulationConfig(**data)

        viz = ConfigVisualizer(cfg, tmp_path, off_screen=True)
        viz._shape_bounds_cache = {
            "WaterBody": ([0.0, 0.0], [0.4, 0.2]),
            "WallBoundary": ([0.0, 0.0], [0.4, 0.2]),
        }

        fake_plotter = _FakePlotter()
        with patch.dict(sys.modules, {"pyvista": _FakePyVista}):
            viz._populate_plotter(fake_plotter, vtp_dir=None)

        body_labels = [
            call for call in fake_plotter.label_calls
            if call.get("labels") and any(
                str(lbl) in {"WaterBody", "WallBoundary"}
                for lbl in call["labels"]
            )
        ]
        assert body_labels == []

    def test_gravity_arrow_3d(self, tmp_path):
        """A 3-D gravity vector should produce an arrow with 3-D direction."""
        from sphinxsim.visualization.preview import ConfigVisualizer

        data = _minimal_fluid_config()
        data["geometries"]["system_domain"] = {
            "lower_bound": [0.0, 0.0, 0.0],
            "upper_bound": [1.0, 1.0, 1.0],
        }
        data["geometries"]["shapes"] = [
            {
                "name": "WaterBody",
                "type": "bounding_box",
                "lower_bound": [0.0, 0.0, 0.0],
                "upper_bound": [0.4, 0.2, 0.4],
            },
            {
                "name": "WallBoundary",
                "type": "bounding_box",
                "lower_bound": [-0.05, -0.05, -0.05],
                "upper_bound": [1.05, 1.05, 1.05],
            },
        ]
        data["geometries"]["oriented_boxes"][0]["center"] = [0.0, 0.1, 0.0]
        data["geometries"]["oriented_boxes"][0]["normal"] = [1.0, 0.0, 0.0]
        data["gravity"] = [0.0, 0.0, -9.81]
        cfg = SimulationConfig(**data)
        viz = ConfigVisualizer(cfg, tmp_path, off_screen=True)

        fake_plotter = _FakePlotter()
        with patch.dict(sys.modules, {"pyvista": _FakePyVista}):
            viz._populate_plotter(fake_plotter, vtp_dir=None)

        arrow_calls = [c for c in fake_plotter.mesh_calls if c.get("label") == "Gravity"]
        assert len(arrow_calls) == 1
        arrow = arrow_calls[0]["mesh"]
        assert arrow["direction"] == (0.0, 0.0, -1.0)

    def test_zero_gravity_skips_arrow(self, fluid_config, tmp_path):
        from sphinxsim.visualization.preview import ConfigVisualizer

        data = copy.deepcopy(fluid_config.model_dump(exclude_none=True))
        data["gravity"] = [0.0, 0.0]
        cfg = SimulationConfig(**data)
        viz = ConfigVisualizer(cfg, tmp_path, off_screen=True)
        fake_plotter = _FakePlotter()
        with patch.dict(sys.modules, {"pyvista": _FakePyVista}):
            viz._populate_plotter(fake_plotter, vtp_dir=None)

        assert not [call for call in fake_plotter.mesh_calls if call.get("label") == "Gravity"]

    def test_non_vertical_gravity_uses_normalized_direction_and_y_scale(self, fluid_config, tmp_path):
        from sphinxsim.visualization.preview import ConfigVisualizer

        data = copy.deepcopy(fluid_config.model_dump(exclude_none=True))
        data["geometries"]["system_domain"] = {
            "lower_bound": [0.0, 0.0],
            "upper_bound": [2.0, 4.0],
        }
        data["gravity"] = [3.0, -4.0]
        cfg = SimulationConfig(**data)
        viz = ConfigVisualizer(cfg, tmp_path, off_screen=True)
        fake_plotter = _FakePlotter()
        with patch.dict(sys.modules, {"pyvista": _FakePyVista}):
            viz._populate_plotter(fake_plotter, vtp_dir=None)

        arrow = next(call["mesh"] for call in fake_plotter.mesh_calls if call.get("label") == "Gravity")
        assert arrow["direction"] == (0.6, -0.8, 0.0)
        assert arrow["scale"] == pytest.approx(0.8)
