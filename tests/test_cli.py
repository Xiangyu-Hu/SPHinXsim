"""Tests for the sphinxsim CLI."""

import json
import sys
from pathlib import Path

import pytest

from sphinxsim.cli import main


class TestCLIGenerate:
    def test_generate_stdout(self, capsys):
        rc = main(["generate", "water flowing through a pipe"])
        assert rc == 0
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert "physics" in data
        assert data["physics"] == "fluid"

    def test_generate_to_file(self, tmp_path, capsys):
        out_file = tmp_path / "cfg.json"
        rc = main(["generate", "elastic beam bending", "-o", str(out_file)])
        assert rc == 0
        assert out_file.exists()
        data = json.loads(out_file.read_text())
        assert data["physics"] == "solid"

    def test_generate_empty_description_returns_nonzero(self, capsys):
        rc = main(["generate", ""])
        assert rc != 0


class TestCLIValidate:
    def _write_config(self, tmp_path: Path, data: dict) -> Path:
        p = tmp_path / "config.json"
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
            "materials": [{"name": "water", "density": 1000.0}],
            "boundary_conditions": [],
            "time_stepping": {"end_time": 1.0, "output_interval": 0.1},
        }

    def test_valid_config(self, tmp_path, capsys):
        p = self._write_config(tmp_path, self._valid_data())
        rc = main(["validate", str(p)])
        assert rc == 0
        assert "valid" in capsys.readouterr().out

    def test_invalid_config(self, tmp_path):
        bad = self._valid_data()
        bad["domain"]["resolution"] = -1  # invalid
        p = self._write_config(tmp_path, bad)
        rc = main(["validate", str(p)])
        assert rc != 0

    def test_missing_file(self, tmp_path):
        rc = main(["validate", str(tmp_path / "nonexistent.json")])
        assert rc != 0

    def test_bad_json(self, tmp_path):
        p = tmp_path / "bad.json"
        p.write_text("not json {{{")
        rc = main(["validate", str(p)])
        assert rc != 0


class TestCLIRun:
    def _write_valid(self, tmp_path: Path) -> Path:
        data = {
            "name": "quick run",
            "physics": "fluid",
            "domain": {
                "bounds_min": [0.0, 0.0],
                "bounds_max": [1.0, 1.0],
                "resolution": 0.1,
            },
            "materials": [{"name": "water", "density": 1000.0}],
            "boundary_conditions": [],
            "time_stepping": {"end_time": 0.5, "output_interval": 0.1},
        }
        p = tmp_path / "config.json"
        p.write_text(json.dumps(data))
        return p

    def test_run_completes(self, tmp_path, capsys):
        p = self._write_valid(tmp_path)
        rc = main(["run", str(p)])
        assert rc == 0
        out = capsys.readouterr().out
        assert "complete" in out.lower()

    def test_run_missing_file(self, tmp_path):
        rc = main(["run", str(tmp_path / "nope.json")])
        assert rc != 0
