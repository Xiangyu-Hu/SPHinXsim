"""Frozen execution admission through the native CLI, before binding import."""
import json
from sphinxsim.cli import main


def test_inline_config_without_inventory_is_rejected_before_solver_start(tmp_path, capsys):
    request = tmp_path / "request.json"
    request.write_text(json.dumps({"config": {"simulation_type": "fluid_dynamics"}, "kind": "run"}))
    assert main(["run", "--request", str(request), "--output", str(tmp_path), "--json"]) == 1
    assert "frozen project inventory" in json.loads(capsys.readouterr().out)["error"]["message"]
    assert not (tmp_path / "solver").exists()


import pytest


@pytest.mark.parametrize("patch", [
    {"project": {"entry_file": "config.json", "files": []}},
    {"solver_defaults": {"end_time": 0}},
    {"solver_defaults": {"output_interval": -1}},
])
def test_frozen_request_bounds_are_rejected_before_project_or_binding_load(tmp_path, capsys, patch):
    request = {"project": {"entry_file": "config.json", "files": [
        {"path": "config.json", "bytes": 2, "sha256": "0" * 64}]}, **patch}
    source = tmp_path / "request.json"
    source.write_text(json.dumps(request))
    assert main(["run", "--request", str(source), "--output", str(tmp_path), "--json"]) == 1
    message = json.loads(capsys.readouterr().out)["error"]["message"]
    assert "minItems" in message or "exclusiveMinimum" in message
    assert not (tmp_path / "effective-config.json").exists()
    assert not (tmp_path / "solver").exists()


def test_frozen_request_does_not_silently_ignore_a_second_configuration(tmp_path, capsys):
    source = tmp_path / "request.json"
    source.write_text("{}")
    assert main(["run", "config.json", "--request", str(source), "--output", str(tmp_path), "--json"]) == 1
    assert "do not combine" in json.loads(capsys.readouterr().out)["error"]["message"]
    assert not (tmp_path / "solver").exists()
