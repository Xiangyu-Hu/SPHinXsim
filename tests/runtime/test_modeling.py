"""Real OCCT geometry and native CLI boundaries; no GPU accuracy claim."""
import hashlib
import json
import math
from pathlib import Path
import subprocess
import sys

import pytest

pytest.importorskip("cadquery")
pytest.importorskip("vtkmodules")

from sphinxsim.runtime.modeling import build
from sphinxsim.runtime.project_input import validate_stl


def step(name, op, **args):
    return {"id": name, "op": op, "args": args}


BOX = step("a", "box", size=[2, 2, 2])
RECTANGLE = {"segments": [{"move": [1, 0]}, {"line": [2, 0]}, {"line": [2, 1]}, {"line": [1, 1]}]}


@pytest.mark.parametrize("steps,volume", [
    ([BOX], 8),
    ([step("a", "cylinder", radius=1, height=2)], 2 * math.pi),
    ([step("a", "sphere", radius=1)], 4 * math.pi / 3),
    ([step("a", "cone", radius1=1, radius2=.5, height=2)], 7 * math.pi / 6),
    ([step("a", "extrude", profile=RECTANGLE, distance=2)], 2),
    ([step("a", "revolve", profile=RECTANGLE)], 3 * math.pi),
    ([step("a", "loft", profiles=[{"circle": 1}, {"origin": [0, 0, 2], "circle": .5}])], 7 * math.pi / 6),
    ([step("a", "sweep", profile={"circle": 1}, path=[[0, 0, 0], [0, 0, 1], [0, 0, 2]])], 2 * math.pi),
    ([BOX, step("b", "translate", input="a", offset=[1, 0, 0]), step("c", "union", inputs=["a", "b"])], 12),
    ([BOX, step("b", "translate", input="a", offset=[1, 0, 0]), step("c", "subtract", inputs=["a", "b"])], 4),
    ([BOX, step("b", "translate", input="a", offset=[1, 0, 0]), step("c", "intersect", inputs=["a", "b"])], 4),
    ([BOX, step("b", "translate", input="a", offset=[1, 0, 0])], 8),
    ([BOX, step("b", "rotate", input="a", axis_start=[0, 0, 0], axis_end=[0, 0, 1], angle=45)], 8),
    ([BOX, step("b", "scale", input="a", factor=2)], 64),
    ([BOX, step("b", "fillet", input="a", radius=.1, edges="all")], None),
    ([BOX, step("b", "chamfer", input="a", distance=.1, edges="all")], None),
])
def test_each_public_cad_operation_produces_a_real_closed_solid(tmp_path, steps, volume):
    result = build({"version": 1, "operations": steps, "output": steps[-1]["id"]}, tmp_path)
    assert result["volume"] == pytest.approx(volume) if volume is not None else 0 < result["volume"] < 8
    if steps[-1]["op"] == "translate":
        assert [(a + b) / 2 for a, b in zip(*result["bounds"])] == pytest.approx([1, 0, 0])
    if steps[-1]["op"] == "rotate":
        assert [b - a for a, b in zip(*result["bounds"])] == pytest.approx([2 * math.sqrt(2), 2 * math.sqrt(2), 2])
    validate_stl(tmp_path / "model.stl")
    import cadquery as cq
    assert cq.importers.importStep(str(tmp_path / "model.step")).val().isValid()
    for item in result["files"]:
        assert hashlib.sha256((tmp_path / item["path"]).read_bytes()).hexdigest() == item["sha256"]


@pytest.mark.parametrize("segment", [
    {"arc": [1, 1], "through": [1.2, .5]},
    {"spline": [[1.1, .3], [1.1, .7], [1, 1]]},
])
def test_curved_sketch_segments_are_not_flattened_into_a_box(tmp_path, segment):
    profile = {"segments": [{"move": [0, 0]}, {"line": [1, 0]}, segment, {"line": [0, 1]}]}
    result = build({"version": 1, "operations": [step("body", "extrude", profile=profile, distance=1)], "output": "body"}, tmp_path)
    assert 1 < result["volume"] < 1.3


def test_native_cli_converts_mm_and_preserves_existing_output_on_rejection(tmp_path):
    recipe = {"version": 1, "unit": "mm", "operations": [step("tank", "box", size=[2000, 1000, 800])], "output": "tank"}
    source = tmp_path / "recipe.json"
    source.write_text(json.dumps(recipe))
    output = tmp_path / "candidate"
    command = [sys.executable, "-m", "sphinxsim", "geometry", str(source), "-o", str(output), "--json"]
    child = subprocess.run(command, capture_output=True, timeout=45)
    assert child.returncode == 0, child.stderr.decode()
    result = json.loads(child.stdout)["data"]
    assert result["volume"] == pytest.approx(1.6)
    assert result["bounds"][1][0] - result["bounds"][0][0] == pytest.approx(2)
    before = (output / "model.stl").read_bytes()
    child = subprocess.run(command, capture_output=True, timeout=45)
    assert child.returncode == 1
    assert "preserved" in json.loads(child.stdout)["error"]["message"]
    assert (output / "model.stl").read_bytes() == before
    recipe["operations"][0]["args"]["python"] = "import os"
    source.write_text(json.dumps(recipe))
    command[command.index(str(output))] = str(tmp_path / "rejected")
    child = subprocess.run(command, capture_output=True, timeout=45)
    assert child.returncode == 1
    assert "unknown parameters" in json.loads(child.stdout)["error"]["message"]
    assert not (tmp_path / "rejected").exists()


@pytest.mark.parametrize("steps,unit,output", [
    ([step("a", "translate", input="missing", offset=[0, 0, 0])], "m", "a"),
    ([step("a", "box", size=[float("nan"), 1, 1])], "m", "a"),
    ([BOX], "feet", "a"),
    ([BOX], "m", "missing"),
    ([BOX, BOX], "m", "a"),
])
def test_invalid_recipe_does_not_publish_geometry(tmp_path, steps, unit, output):
    with pytest.raises(ValueError):
        build({"version": 1, "unit": unit, "operations": steps, "output": output}, tmp_path)
    assert not (tmp_path / "model.stl").exists()
