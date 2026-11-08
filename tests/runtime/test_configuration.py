"""Native configuration admission through the public CLI and shared operation."""
from copy import deepcopy
import json
from pathlib import Path

import pytest

from sphinxsim.cli import main
from sphinxsim.operations import Operations
from sphinxsim.runtime.physics import check_config, compile_config


def j2_configuration():
    source = Path(__file__).parents[1] / "test_simulation/test_2d_simulation/data/column_collapse.json"
    config = json.loads(source.read_text())
    config["continuum_bodies"][0]["material"] = {
        "type": "j2_plasticity", "density": 1000, "sound_speed": 100,
        "youngs_modulus": 1e6, "poisson_ratio": .3, "yield_stress": 100,
        "hardening_modulus": 10, "ductile_damage": {"enabled": True,
            "threshold_equivalent_plastic_strain": .1,
            "critical_equivalent_plastic_strain": .2, "critical_damage": .9}}
    return config


def test_j2_damage_is_accepted_by_the_same_cli_and_mcp_admission_without_writes(tmp_path, capsys):
    path = tmp_path / "config.json"
    config = j2_configuration()
    path.write_text(json.dumps(config))
    raw = path.read_bytes()
    assert main(["validate", str(path), "--root", str(tmp_path), "--json"]) == 0
    result = json.loads(capsys.readouterr().out)["data"]
    damage = result["configuration"]["continuum_bodies"][0]["material"]["ductile_damage"]
    assert damage == config["continuum_bodies"][0]["material"]["ductile_damage"]
    assert Operations(tmp_path).call("validate", {"config_file": "config.json"}) == result
    assert path.read_bytes() == raw
    assert main(["validate", str(path)]) == 0


@pytest.mark.parametrize("damage", [
    {"enabled": True},
    {"enabled": True, "threshold_equivalent_plastic_strain": .2, "critical_equivalent_plastic_strain": .1, "critical_damage": .9},
])
def test_invalid_j2_damage_is_rejected_before_native_submission(tmp_path, capsys, damage):
    config = j2_configuration()
    config["continuum_bodies"][0]["material"]["ductile_damage"] = damage
    path = tmp_path / "config.json"
    path.write_text(json.dumps(config))
    assert main(["validate", str(path), "--root", str(tmp_path), "--json"]) == 1
    assert "ductile_damage" in json.loads(capsys.readouterr().out)["error"]["message"]
    assert not (tmp_path / ".sphinxsim/jobs").exists() or not list((tmp_path / ".sphinxsim/jobs").iterdir())


def test_compilation_is_explicit_preserves_source_and_rejects_incompatible_units(tmp_path, capsys):
    config = j2_configuration()
    config["geometries"]["global_resolution"]["particle_spacing"] = {"value": 25, "unit": "mm"}
    spec = {"version": 1, "configuration": config}
    source = tmp_path / "spec.json"
    source.write_text(json.dumps(spec))
    original = source.read_bytes()
    output = tmp_path / "compiled.json"
    assert main(["compile", str(source), "-o", str(output), "--json"]) == 0
    report = json.loads(capsys.readouterr().out)["data"]
    assert not report["applied"]
    assert json.loads(output.read_text())["geometries"]["global_resolution"]["particle_spacing"] == .025
    assert source.read_bytes() == original
    wrong = deepcopy(spec)
    wrong["configuration"]["geometries"]["global_resolution"]["particle_spacing"]["unit"] = "s"
    with pytest.raises(ValueError, match="incompatible unit"):
        compile_config(wrong)
    config["geometries"]["global_resolution"]["particle_spacing"] = .025
    config["continuum_bodies"][0]["material"]["invented_model"] = True
    with pytest.raises(ValueError, match="Unknown fields"):
        check_config(config)


def test_declared_but_inactive_material_fields_do_not_silently_pass():
    config = j2_configuration()
    config["solid_bodies"][0]["material"]["youngs_modulus"] = 1000
    with pytest.raises(ValueError, match="Fields not consumed"):
        check_config(config)
