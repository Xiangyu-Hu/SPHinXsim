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


@pytest.mark.parametrize("shape_type", ["box", "bounding_box"])
def test_primitive_references_survive_cli_mcp_and_compilation(tmp_path, capsys, shape_type):
    config = j2_configuration()
    shape = config["geometries"]["shapes"][0]
    primitive = {"name": "InitialBounds", "type": shape_type}
    if shape_type == "bounding_box":
        primitive.update(lower_bound=[0, 0], upper_bound=[.2, .001])
    else:
        primitive.update(half_size=[.1, .1], transform={"translation": [.1, .1], "rotation_angle": 0})
    config["geometries"]["primitives"] = [primitive]
    config["geometries"]["shapes"][0] = {
        "name": shape["name"], "type": shape_type, "primitive": "InitialBounds"
    }
    path = tmp_path / "case.json"
    path.write_text(json.dumps(config), encoding="utf-8")
    original = path.read_bytes()

    assert main(["validate", str(path), "--root", str(tmp_path), "--json"]) == 0
    result = json.loads(capsys.readouterr().out)["data"]
    assert Operations(tmp_path).call("validate", {"config_file": "case.json"}) == result
    compiled, _ = compile_config({"version": 1, "configuration": config})
    assert compiled["geometries"]["shapes"][0]["primitive"] == "InitialBounds"
    if shape_type == "bounding_box":
        assert any(note["code"] == "thin_geometry" for note in result["report"]["diagnostics"])
    assert main(["validate", str(path)]) == 0
    assert path.read_bytes() == original


def test_particle_generation_dictionaries_accept_native_refinement_and_level_set():
    config = j2_configuration()
    bodies = config["particle_generation"]["settings"]["bodies"]
    bodies[0]["relaxation"] = {"level_set": {"shape": bodies[0]["name"]}}
    bodies[1]["solid_body"] = {"refinement_to_global": 2.0}

    normalized, _ = check_config(config)

    normalized_bodies = normalized["particle_generation"]["settings"]["bodies"]
    assert normalized_bodies[0]["relaxation"]["level_set"] == bodies[0]["relaxation"]["level_set"]
    assert normalized_bodies[1]["solid_body"]["refinement_to_global"] == 2.0


@pytest.mark.parametrize("location", ["particle_generation", "solid_bodies"])
def test_unused_adaptation_ratios_report_the_native_configuration_path(location):
    config = j2_configuration()
    if location == "particle_generation":
        body = config["particle_generation"]["settings"]["bodies"][1]
    else:
        body = config["solid_bodies"][0]
    body["adaptation_ratios"] = [1.15, 2.0]

    with pytest.raises(ValueError, match="solid_body.refinement_to_global"):
        check_config(config)


def test_unknown_model_fields_are_rejected_with_dictionary_support():
    config = j2_configuration()
    config["geometries"]["shapes"][0]["invented_geometry"] = True

    with pytest.raises(ValueError, match="Unknown fields"):
        check_config(config)


def test_unknown_particle_generation_model_fields_remain_rejected():
    config = j2_configuration()
    config["particle_generation"]["settings"]["bodies"][0]["invented_option"] = True

    with pytest.raises(ValueError, match="Unknown or invalid fields"):
        check_config(config)
