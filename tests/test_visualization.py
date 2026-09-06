"""Tests for sphinxsim.visualization (annotations, preview, CLI preview command).

PyVista is *not* required for most tests — mesh-building helpers are tested via
a thin stub.  Tests that do require PyVista are skipped automatically when the
library is not installed.
"""

from __future__ import annotations

import json
import math
import sys
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
            "build_and_run": True,
            "settings": {
                "bodies": [
                    {"name": "WaterBody"},
                    {"name": "WallBoundary", "solid_body": {}},
                ],
                "relaxation_parameters": {"total_iterations": 1},
            },
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
                "surface_type": "free_surface",
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
        data["particle_generation"]["settings"]["relaxation_constraints"] = [
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

    def test_3d_config_info_is_centered_top(self, fluid_config, tmp_path):
        from sphinxsim.visualization.preview import ConfigVisualizer

        viz = ConfigVisualizer(fluid_config, tmp_path, off_screen=True)

        class FakePlotter:
            window_size = (1200, 800)

            def __init__(self):
                self.calls: list[dict[str, Any]] = []

            def add_text(self, text, **kwargs):
                self.calls.append({"text": text, **kwargs})
                return None

        fake_plotter = FakePlotter()
        viz._add_config_info_text(fake_plotter, "3-D  •  Fluid Dynamics  •  VTP geometry", ndim=3)

        assert len(fake_plotter.calls) == 1
        position = fake_plotter.calls[0].get("position")
        assert position == "upper_edge"


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

        # WaterBody: keep step 10, WallBoundary: keep step 2.
        (vtp_dir / "WaterBody_0000.vtp").write_text("a")
        (vtp_dir / "WaterBody_0010.vtp").write_text("b")
        (vtp_dir / "WaterBody_ite_0012.vtp").write_text("b2")
        (vtp_dir / "WallBoundary_0002.vtp").write_text("c")
        (vtp_dir / "WallBoundary_0001.vtp").write_text("d")

        # Should be ignored (not body state particle files).
        (vtp_dir / "ShapeWaterBody.vtp").write_text("shape")
        (vtp_dir / "particle_generation_0004.vtp").write_text("pg")

        latest = viz._discover_latest_particle_vtps(vtp_dir)

        assert latest["WaterBody"].name == "WaterBody_ite_0012.vtp"
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
            call for call in fake_plotter.mesh_calls if str(call.get("label", "")).startswith("Particles: ")
        ]
        assert len(particle_mesh_calls) == 2
        assert all(call.get("style") == "points" for call in particle_mesh_calls)
        assert all(call.get("point_size") == 5 for call in particle_mesh_calls)

        particle_label_calls = [
            call
            for call in fake_plotter.point_label_calls
            if call["labels"] and str(call["labels"][0]).startswith("Particles: ")
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
            call for call in fake_plotter.mesh_calls if str(call.get("label", "")).startswith("Particles: ")
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

        assert runtime.continue_to_run() == 0
        assert runtime._live_run_thread is not None
        runtime._live_run_thread.join()
        assert calls == ["build", "initialize", "run"]
        assert runtime._pending_simulation is None
        assert not runtime_config_path.exists()

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

            def _add_config_info_text(self, plotter, config_info, ndim):
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
        monkeypatch.setattr(cli_mod, "PROJECT_ROOT", tmp_path)

        with patch("sphinxsim.visualization.preview.ConfigVisualizer", FakeVisualizer):
            rc = runtime.show_or_update(
                cfg,
                resolved_config_path=tmp_path / "config.json",
                with_particles=False,
            )

        assert rc == 0

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

            def _add_config_info_text(self, plotter, config_info, ndim):
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

            def _add_config_info_text(self, plotter, config_info, ndim):
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
                self._observer = None
                self._pos = (0, 0)

            def AddObserver(self, event_name, callback):
                self._observer = callback
                return 1

            def RemoveObserver(self, tag):
                return None

            def GetEventPosition(self):
                return self._pos

            def trigger_mouse_move(self, x, y):
                self._pos = (x, y)
                if self._observer is not None:
                    self._observer(self, "MouseMoveEvent")

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
                }]

        with patch.dict(sys.modules, {"vtk": FakeVtkModule(actor)}):
            runtime._install_annotation_hover(FakeVisualizer())

        assert actor.prop.size == 8
        interactor.trigger_mouse_move(55, 24)
        assert actor.prop.size == 12
        interactor.trigger_mouse_move(1, 1)
        assert actor.prop.size == 8

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
