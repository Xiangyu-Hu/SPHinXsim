"""Tests for the sphinxsim CLI."""

import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

import sphinxsim
from sphinxsim.cli import _load_config, main


class TestLoadConfigHelper:
    def _write(self, build_temp_path: Path, data: dict) -> Path:
        p = build_temp_path / "cfg.json"
        p.write_text(json.dumps(data))
        return p

    def _valid_data(self) -> dict:
        return {
            "name": "helper test",
            "physics": "fluid",
            "domain": {"bounds_min": [0.0, 0.0], "bounds_max": [1.0, 1.0], "resolution": 0.05},
            "materials": [{"name": "water", "density": 1000.0, "dynamic_viscosity": 0.001}],
            "boundary_conditions": [],
            "time_stepping": {"end_time": 1.0, "output_interval": 0.1},
        }

    def test_valid_returns_config_and_zero(self, build_temp_path):
        p = self._write(build_temp_path, self._valid_data())
        config, rc = _load_config(p)
        assert rc == 0
        assert config is not None
        assert config.name == "helper test"

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
        bad = self._valid_data()
        bad["domain"]["resolution"] = -1
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
        assert "physics" in data
        assert data["physics"] == "fluid"

    def test_generate_to_file(self, build_temp_path, capsys):
        out_file = build_temp_path / "cfg.json"
        rc = main(["generate", "elastic beam bending", "-o", str(out_file)])
        assert rc == 0
        assert out_file.exists()
        data = json.loads(out_file.read_text())
        assert data["physics"] == "solid"

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

    def test_generate_empty_description_returns_nonzero(self, capsys):
        rc = main(["generate", ""])
        assert rc != 0


class TestCLIValidate:
    def _write_config(self, build_temp_path: Path, data: dict) -> Path:
        p = build_temp_path / "config.json"
        p.write_text(json.dumps(data))
        return p

    def _valid_data(self) -> dict:
        return {
            "name": "test",
            "physics": "fluid",
            "domain": {
                "bounds_min": [0.0, 0.0],
                "bounds_max": [1.0, 1.0],
                "resolution": 0.05,
            },
            "materials": [{"name": "water", "density": 1000.0, "dynamic_viscosity": 0.001}],
            "boundary_conditions": [],
            "time_stepping": {"end_time": 1.0, "output_interval": 0.1},
        }

    def test_valid_config(self, build_temp_path, capsys):
        p = self._write_config(build_temp_path, self._valid_data())
        rc = main(["validate", str(p)])
        assert rc == 0
        output = capsys.readouterr().out
        assert "Generated configuration" in output
        assert "test" in output

    def test_invalid_config(self, build_temp_path):
        bad = self._valid_data()
        bad["domain"]["resolution"] = -1  # invalid
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
        data = {
            "name": "quick run",
            "physics": "fluid",
            "domain": {
                "bounds_min": [0.0, 0.0],
                "bounds_max": [1.0, 1.0],
                "resolution": 0.1,
            },
            "materials": [{"name": "water", "density": 1000.0, "dynamic_viscosity": 0.001}],
            "boundary_conditions": [],
            "time_stepping": {"end_time": 0.5, "output_interval": 0.1},
        }
        p = build_temp_path / "config.json"
        p.write_text(json.dumps(data))
        return p

    def test_run_completes(self, build_temp_path, capsys):
        p = self._write_valid(build_temp_path)
        rc = main(["run", str(p)])
        assert rc == 0
        out = capsys.readouterr().out
        assert "complete" in out.lower()

    def test_run_missing_file(self, build_temp_path):
        rc = main(["run", str(build_temp_path / "nope.json")])
        assert rc != 0


class TestCLIVersion:
    def test_version_matches_package(self, capsys):
        with pytest.raises(SystemExit) as exc_info:
            main(["--version"])
        assert exc_info.value.code == 0
        out = capsys.readouterr().out
        assert sphinxsim.__version__ in out
