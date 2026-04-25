"""Tests for the sphinxsim CLI."""

import json
import importlib.util
import importlib
from pathlib import Path
from unittest.mock import patch

import pytest

import sphinxsim
from sphinxsim.cli import _load_config, main


def _has_native_extension() -> bool:
    if importlib.util.find_spec("_sphinxsys_core_2d") is None:
        return False
    try:
        importlib.import_module("_sphinxsys_core_2d")
    except ImportError:
        return False
    return True


def _valid_data() -> dict:
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
                    "sound_speed": 20.0,
                },
                "particle_reserve_factor": 100.0,
            }
        ],
        "solid_bodies": [{"name": "WallBoundary", "material": {"type": "rigid_body"}}],
        "gravity": [0.0, -1.0],
        "observers": [
            {
                "name": "Observer",
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
                "inflow_speed": 1.0,
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


class TestLoadConfigHelper:
    def _write(self, build_temp_path: Path, data: dict) -> Path:
        p = build_temp_path / "cfg.json"
        p.write_text(json.dumps(data))
        return p

    def test_valid_returns_config_and_zero(self, build_temp_path):
        p = self._write(build_temp_path, _valid_data())
        config, rc = _load_config(p)
        assert rc == 0
        assert config is not None
        assert config.fluid_bodies[0].name == "WaterBody"

    def test_missing_file_returns_none_and_nonzero(self, build_temp_path):
        config, rc = _load_config(build_temp_path / "missing.json")
        assert config is None
        assert rc != 0

    def test_bad_json_returns_none_and_nonzero(self, build_temp_path):
        p = build_temp_path / "bad.json"
        p.write_text("{{{{")
        config, rc = _load_config(p)
        assert config is None
        assert rc != 0

    def test_invalid_schema_returns_none_and_nonzero(self, build_temp_path):
        bad = _valid_data()
        bad["fluid_bodies"] = []
        p = self._write(build_temp_path, bad)
        config, rc = _load_config(p)
        assert config is None
        assert rc != 0


class TestCLIGenerate:
    def test_generate_stdout(self, capsys):
        rc = main(["generate", "water flowing through a pipe"])
        assert rc == 0
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert "simulation_type" in data
        assert "geometries" in data
        assert "solver_parameters" in data

    def test_generate_to_file(self, build_temp_path):
        out_file = build_temp_path / "cfg.json"
        rc = main(["generate", "elastic beam bending", "-o", str(out_file)])
        assert rc == 0
        assert out_file.exists()
        data = json.loads(out_file.read_text())
        assert data["simulation_type"] == "continuum_dynamics"

    def test_generate_creates_parent_dirs(self, build_temp_path):
        out_file = build_temp_path / "nested" / "dir" / "cfg.json"
        rc = main(["generate", "water flow", "-o", str(out_file)])
        assert rc == 0
        assert out_file.exists()

    def test_generate_oserror_returns_nonzero(self, build_temp_path, capsys):
        out_file = build_temp_path / "cfg.json"
        with patch("sphinxsim.cli.Path.write_text", side_effect=OSError("disk full")):
            rc = main(["generate", "water flow", "-o", str(out_file)])
        assert rc != 0
        assert "disk full" in capsys.readouterr().err

    def test_generate_empty_description_returns_nonzero(self):
        rc = main(["generate", ""])
        assert rc != 0


class TestCLIValidate:
    def _write_config(self, build_temp_path: Path, data: dict) -> Path:
        p = build_temp_path / "config.json"
        p.write_text(json.dumps(data))
        return p

    def test_valid_config(self, build_temp_path, capsys):
        p = self._write_config(build_temp_path, _valid_data())
        rc = main(["validate", str(p)])
        assert rc == 0
        output = capsys.readouterr().out
        assert "Generated configuration" in output
        assert "Simulation type" in output
        assert "Fluid bodies" in output

    def test_invalid_config(self, build_temp_path):
        bad = _valid_data()
        bad["solver_parameters"] = {"end_time": 1.0}
        p = self._write_config(build_temp_path, bad)
        rc = main(["validate", str(p)])
        assert rc != 0

    def test_missing_file(self, build_temp_path):
        rc = main(["validate", str(build_temp_path / "nonexistent.json")])
        assert rc != 0

    def test_bad_json(self, build_temp_path):
        p = build_temp_path / "bad.json"
        p.write_text("not json {{{")
        rc = main(["validate", str(p)])
        assert rc != 0


class TestCLIRun:
    def _write_valid(self, build_temp_path: Path) -> Path:
        p = build_temp_path / "config.json"
        p.write_text(json.dumps(_valid_data()))
        return p

    def test_run_completes(self, build_temp_path, capsys):
        if not _has_native_extension():
            pytest.skip("_sphinxsys_core_2d is not available in this environment")
        p = self._write_valid(build_temp_path)
        rc = main(["run", str(p)])
        assert rc == 0
        out = capsys.readouterr().out
        assert "complete" in out.lower()

    def test_run_missing_file(self, build_temp_path):
        if not _has_native_extension():
            pytest.skip("_sphinxsys_core_2d is not available in this environment")
        rc = main(["run", str(build_temp_path / "nope.json")])
        assert rc != 0


class TestCLIUpdate:
    def _write_valid(self, build_temp_path: Path) -> Path:
        p = build_temp_path / "config.json"
        p.write_text(json.dumps(_valid_data()))
        return p

    def test_update_in_place(self, build_temp_path):
        p = self._write_valid(build_temp_path)
        rc = main(["update", str(p), "simulate for 2 s"])
        assert rc == 0
        data = json.loads(p.read_text())
        assert data["solver_parameters"]["end_time"] == pytest.approx(2.0)

    def test_update_to_output_file(self, build_temp_path):
        p = self._write_valid(build_temp_path)
        out = build_temp_path / "updated.json"
        rc = main(["update", str(p), "water flow with 5 mm resolution", "-o", str(out)])
        assert rc == 0
        assert out.exists()
        data = json.loads(out.read_text())
        assert data["geometries"]["global_resolution"]["particle_spacing"] == pytest.approx(0.005)

    def test_update_missing_file(self, build_temp_path):
        rc = main(["update", str(build_temp_path / "missing.json"), "simulate for 1 s"])
        assert rc != 0


class TestCLIVersion:
    def test_version_matches_package(self, capsys):
        with pytest.raises(SystemExit) as exc_info:
            main(["--version"])
        assert exc_info.value.code == 0
        out = capsys.readouterr().out
        assert sphinxsim.__version__ in out
