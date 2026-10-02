"""Tests for the sphinxsim CLI."""

import json
import importlib.util
import importlib
from pathlib import Path
from unittest.mock import patch

import pytest

import sphinxsim
from sphinxsim.cli import _config_spatial_dim, _load_config, _new_json_editor_list_item, main
from sphinxsim.cli import _build_parser
from sphinxsim.llm.common import LLMRepairWarning


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
            "global_resolution": {"particle_spacing": 0.025},
            "shapes": [
                {
                    "name": "WaterBody",
                    "type": "bounding_box",
                    "lower_bound": [0.0, 0.0],
                    "upper_bound": [0.4, 0.2],
                },
                {
                    "name": "WallBoundary",
                    "type": "multipolygon",
                    "polygons": [
                        {
                            "operation": "union",
                            "type": "container_box",
                            "inner_lower_bound": [0.0, 0.0],
                            "inner_upper_bound": [1.0, 1.0],
                            "thickness": 0.1
                        }
                    ]
                }
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
                "oriented_box": "Inlet",
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
                "surface_type": "free_surface",
                "particle_sort_frequency": 100,
            },
        },
    }


def _valid_3d_data() -> dict:
    data = json.loads(json.dumps(_valid_data()))
    data["geometries"]["system_domain"] = {
        "lower_bound": [0.0, 0.0, 0.0],
        "upper_bound": [1.0, 1.0, 1.0],
    }
    data["geometries"]["shapes"] = [
        {
            "name": "WaterBody",
            "type": "bounding_box",
            "lower_bound": [0.0, 0.0, 0.0],
            "upper_bound": [0.4, 0.2, 0.2],
        },
        {
            "name": "WallBoundary",
            "type": "bounding_box",
            "lower_bound": [0.0, 0.0, 0.0],
            "upper_bound": [1.0, 1.0, 1.0],
        },
    ]
    data["geometries"]["oriented_boxes"] = []
    data["gravity"] = [0.0, 0.0, -1.0]
    data["observers"][0]["positions"] = [[0.5, 0.2, 0.1]]
    data["fluid_boundary_conditions"] = []
    return data


class _FakeSimulation:
    def __init__(self, config_path):
        self.config_path = config_path

    def resetOutputRoot(self, output_dir):
        self.output_dir = output_dir

    def buildGeometries(self):
        pass

    def generateParticles(self):
        pass

    def buildSimulation(self):
        pass

    def initializeSimulation(self):
        pass

    def run(self):
        pass


class _FakeNativeModule:
    SPHSimulation = _FakeSimulation


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


class TestSpatialDimension:
    def test_infers_2d_from_domain(self):
        config = sphinxsim.SimulationConfig.model_validate(_valid_data())
        assert _config_spatial_dim(config) == 2

    def test_infers_3d_from_domain(self):
        config = sphinxsim.SimulationConfig.model_validate(_valid_3d_data())
        assert _config_spatial_dim(config) == 3


class TestJsonEditorListItems:
    def test_empty_shapes_list_gets_an_editable_shape(self):
        item = _new_json_editor_list_item(("geometries", "shapes"), spatial_dim=2)

        assert item == {
            "name": "New shape",
            "type": "bounding_box",
            "lower_bound": [0.0, 0.0],
            "upper_bound": [1.0, 1.0],
        }

    def test_empty_nested_list_uses_a_schema_aware_object(self):
        item = _new_json_editor_list_item(
            ("initial_conditions", 0, "assignments"), spatial_dim=3
        )

        assert item == {"variable": {"real_type": "Pressure"}, "value": 0.0}


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

    def test_generate_reports_post_llm_angle_correction(self, capsys):
        payload = json.loads(
            Path("tests/test_simulation/test_3d_simulation/data/repose_angle.json").read_text()
        )
        payload["continuum_bodies"][0]["material"]["friction_angle"] = 30.0

        class DegreeAngleLLM:
            def generate(self, description):
                return sphinxsim.SimulationConfig.model_validate(payload)

        with patch("sphinxsim.cli.get_llm", return_value=DegreeAngleLLM()):
            rc = main(["generate", "landslide with friction angle 30 degrees"])

        captured = capsys.readouterr()
        assert rc == 0
        assert "Physical correction applied" in captured.err
        assert "friction_angle from 30 degrees" in captured.err
        generated = json.loads(captured.out)
        assert generated["continuum_bodies"][0]["material"]["friction_angle"] == pytest.approx(
            0.5235987755982988
        )

    def test_generate_reports_llm_repair_changes(self, capsys):
        config = sphinxsim.SimulationConfig.model_validate(_valid_data())

        class RepairingLLM:
            def generate(self, description):
                import warnings

                warnings.warn(
                    "LLM repaired the generated config after validation failed: "
                    "continuum_bodies[0].material.poisson_ratio: 0.6 -> 0.3",
                    LLMRepairWarning,
                )
                return config

        with patch("sphinxsim.cli.get_llm", return_value=RepairingLLM()):
            rc = main(["generate", "landslide"])

        captured = capsys.readouterr()
        assert rc == 0
        assert "LLM repair applied" in captured.err
        assert "poisson_ratio: 0.6 -> 0.3" in captured.err


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

    def _write_valid_3d(self, build_temp_path: Path) -> Path:
        p = build_temp_path / "config_3d.json"
        p.write_text(json.dumps(_valid_3d_data()))
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

    def test_run_selects_3d_native_module(self, build_temp_path):
        p = self._write_valid_3d(build_temp_path)
        with patch("sphinxsim.cli.load_sphinxsys_core_nd", return_value=_FakeNativeModule) as load_native:
            rc = main(["run", str(p)])

        assert rc == 0
        load_native.assert_called_once_with(3)


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

    def test_top_level_cli_rejects_slash_style_commands(self):
        with pytest.raises(SystemExit):
            main(["/generate", "water flow"])

    def test_update_patch_mode_in_place(self, build_temp_path):
        p = self._write_valid(build_temp_path)
        rc = main(["update", str(p), "simulate for 2 s", "--patch-mode"])
        assert rc == 0
        data = json.loads(p.read_text())
        assert data["solver_parameters"]["end_time"] == pytest.approx(2.0)

    def test_update_patch_mode_dry_run_does_not_write(self, build_temp_path, capsys):
        p = self._write_valid(build_temp_path)
        before = p.read_text()
        rc = main(["update", str(p), "simulate for 3 s", "--patch-mode", "--dry-run"])
        assert rc == 0
        after = p.read_text()
        assert after == before
        out = capsys.readouterr().out
        assert "Dry run" in out

    def test_update_patch_mode_strict_failure(self, build_temp_path):
        class _BadPatchLLM:
            def update(self, existing, description):
                return existing

            def update_patch(self, existing, description, strict=True):
                return {
                    "schema_version": "1.0",
                    "strict": True,
                    "operations": [
                        {
                            "op": "append_item",
                            "path": "solver_parameters.end_time",
                            "value": 1,
                        }
                    ],
                }

        p = self._write_valid(build_temp_path)
        with patch("sphinxsim.cli.get_llm", return_value=_BadPatchLLM()):
            rc = main(["update", str(p), "simulate for 1 s", "--patch-mode", "--strict", "true"])
        assert rc != 0

    def test_update_unexpected_provider_exception_returns_nonzero(self, build_temp_path):
        class _BoomLLM:
            def update(self, existing, description):
                raise TypeError("boom")

        p = self._write_valid(build_temp_path)
        with patch("sphinxsim.cli.get_llm", return_value=_BoomLLM()):
            rc = main(["update", str(p), "simulate for 1 s"])
        assert rc != 0


class TestCLIShell:
    def test_shell_generate_then_update_auto_validates(self, build_temp_path, capsys):
        cfg = build_temp_path / "shell_config.json"
        shell_rel_cfg = f"pytest-temp/{build_temp_path.name}/shell_config.json"
        inputs = [
            f'generate "water dam break simulation" {shell_rel_cfg}',
            'update "simulate for 2 s"',
            "exit",
        ]
        with patch("builtins.input", side_effect=inputs):
            rc = main(["shell"])

        assert rc == 0
        assert cfg.exists()
        data = json.loads(cfg.read_text())
        assert data["fluid_bodies"][0]["name"] == "WaterBody"
        assert data["solver_parameters"]["end_time"] == pytest.approx(2.0)

        out = capsys.readouterr().out
        assert "Auto-validation passed" in out

    def test_shell_accepts_slash_style_commands(self, build_temp_path):
        cfg = build_temp_path / "shell_slash_config.json"
        shell_rel_cfg = f"pytest-temp/{build_temp_path.name}/shell_slash_config.json"
        inputs = [
            f"/generate water dam break simulation {shell_rel_cfg}",
            "/update simulate for 2 s",
            "exit",
        ]
        with patch("builtins.input", side_effect=inputs):
            rc = main(["shell"])

        assert rc == 0
        assert cfg.exists()
        data = json.loads(cfg.read_text())
        assert data["solver_parameters"]["end_time"] == pytest.approx(2.0)

    def test_shell_run_selects_3d_native_module(self, build_temp_path):
        cfg = build_temp_path / "shell_3d_config.json"
        cfg.write_text(json.dumps(_valid_3d_data()))
        shell_rel_cfg = f"pytest-temp/{build_temp_path.name}/shell_3d_config.json"
        inputs = [f"load {shell_rel_cfg}", "run", "exit"]

        with (
            patch("builtins.input", side_effect=inputs),
            patch("sphinxsim.cli.load_sphinxsys_core_nd", return_value=_FakeNativeModule) as load_native,
        ):
            rc = main(["shell"])

        assert rc == 0
        load_native.assert_called_once_with(3)

    def test_shell_update_before_load_errors(self, build_temp_path, capsys):
        inputs = ['update "simulate for 2 s"', "exit"]
        with patch("builtins.input", side_effect=inputs):
            rc = main(["shell"])

        assert rc == 0
        err = capsys.readouterr().err
        assert "No config loaded" in err

    def test_shell_explore_returns_answer(self, build_temp_path, capsys):
        inputs = ['explore "what are top-level schema fields?"', "exit"]
        with patch("builtins.input", side_effect=inputs):
            rc = main(["shell"])

        assert rc == 0
        out = capsys.readouterr().out
        assert "Top-level SimulationConfig fields" in out

    def test_shell_update_patch_mode_applies(self, build_temp_path):
        cfg = build_temp_path / "shell_patch_apply.json"
        shell_rel_cfg = f"pytest-temp/{build_temp_path.name}/shell_patch_apply.json"
        inputs = [
            f'generate "water dam break simulation" {shell_rel_cfg}',
            'update --patch-mode "simulate for 2 s"',
            "exit",
        ]
        with patch("builtins.input", side_effect=inputs):
            rc = main(["shell"])

        assert rc == 0
        data = json.loads(cfg.read_text())
        assert data["solver_parameters"]["end_time"] == pytest.approx(2.0)

    def test_shell_update_patch_mode_dry_run_does_not_write(self, build_temp_path):
        cfg = build_temp_path / "shell_patch_dry_run.json"
        shell_rel_cfg = f"pytest-temp/{build_temp_path.name}/shell_patch_dry_run.json"
        inputs = [
            f'generate "water flow for 1 s" {shell_rel_cfg}',
            'update --patch-mode --dry-run "simulate for 2 s"',
            "exit",
        ]
        with patch("builtins.input", side_effect=inputs):
            rc = main(["shell"])

        assert rc == 0
        data = json.loads(cfg.read_text())
        assert data["solver_parameters"]["end_time"] == pytest.approx(1.0)

    def test_shell_geometry_update_after_generate_is_allowed(self, build_temp_path):
        cfg = build_temp_path / "shell_geometry_update.json"
        shell_rel_cfg = f"pytest-temp/{build_temp_path.name}/shell_geometry_update.json"
        inputs = [
            f'generate "water dam break simulation" {shell_rel_cfg}',
            'update "water flow with 5 mm resolution"',
            "exit",
        ]
        with patch("builtins.input", side_effect=inputs):
            rc = main(["shell"])

        assert rc == 0
        data = json.loads(cfg.read_text())
        assert data["geometries"]["global_resolution"]["particle_spacing"] == pytest.approx(0.005)


class TestCLIExplore:
    def test_explore_outputs_schema_guidance(self, capsys):
        rc = main(["explore", "what can I configure in SimulationConfig?"])
        assert rc == 0
        out = capsys.readouterr().out
        assert "Top-level SimulationConfig fields" in out

    def test_explore_empty_question_returns_nonzero(self, capsys):
        rc = main(["explore", ""])
        assert rc != 0
        err = capsys.readouterr().err
        assert "question must not be empty" in err


class TestCLIVersion:
    def test_version_matches_package(self, capsys):
        with pytest.raises(SystemExit) as exc_info:
            main(["--version"])
        assert exc_info.value.code == 0
        out = capsys.readouterr().out
        assert sphinxsim.__version__ in out

class TestCLIGenerateCompletion:
    def test_generate_completion_bash(self, capsys):
        """Test bash shell completion generation."""
        parser = _build_parser()
        with pytest.raises(SystemExit) as exc_info:
            parser.parse_args(["--generate-completion", "bash"])
        assert exc_info.value.code == 0

        captured = capsys.readouterr()
        assert "_sphinxsim_completion" in captured.out
        assert "generate" in captured.out

    def test_generate_completion_zsh(self, capsys):
        """Test zsh shell completion generation."""
        parser = _build_parser()
        with pytest.raises(SystemExit) as exc_info:
            parser.parse_args(["--generate-completion", "zsh"])
        assert exc_info.value.code == 0

        captured = capsys.readouterr()
        assert "#compdef sphinxsim" in captured.out
        assert "generate" in captured.out

    def test_generate_completion_fish(self, capsys):
        """Test fish shell completion generation."""
        parser = _build_parser()
        with pytest.raises(SystemExit) as exc_info:
            parser.parse_args(["--generate-completion", "fish"])
        assert exc_info.value.code == 0

        captured = capsys.readouterr()
        assert "complete -c sphinxsim" in captured.out
        assert "generate" in captured.out
