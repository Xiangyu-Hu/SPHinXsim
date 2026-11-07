"""Real OCCT/VTK tests; these do not claim GPU or desktop E2E."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from sphinxsim_agent.modeling import build
from sphinxsim_agent.project_input import validate_stl
from sphinxsim_agent.physics import check_config, compile_config, editor_config_type
from sphinxsim_agent.desktop.modeling_session import ModelingSession


class ModelingTests(unittest.TestCase):
    def test_cli_generates_hollow_baffled_solid_in_meters_and_rejects_code(self):
        recipe = {"version": 1, "unit": "mm", "operations": [
            {"id": "outer", "op": "box", "args": {"size": [2000, 1000, 800]}},
            {"id": "inner", "op": "box", "args": {"size": [1800, 800, 600]}},
            {"id": "shell", "op": "subtract", "args": {"inputs": ["outer", "inner"]}},
            {"id": "baffle", "op": "box", "args": {"size": [50, 800, 500]}},
            {"id": "final", "op": "union", "args": {"inputs": ["shell", "baffle"]}},
        ], "output": "final"}
        with tempfile.TemporaryDirectory() as root:
            root = Path(root); source = root / "request.json"; output = root / "candidate"
            source.write_text(json.dumps(recipe))
            child = subprocess.run([sys.executable, "-m", "sphinxsim_agent.modeling", "--input", str(source), "--output", str(output)], capture_output=True)
            self.assertEqual(child.returncode, 0, child.stderr.decode())
            result = json.loads((output / "cad-result.json").read_text())
            self.assertAlmostEqual(result["bounds"][1][0] - result["bounds"][0][0], 2, places=6)
            self.assertAlmostEqual(result["volume"], .756)
            validate_stl(output / "model.stl")
            self.assertGreater((output / "model.step").stat().st_size, 100)
            for item in result["files"]:
                self.assertEqual(hashlib.sha256((output / item["path"]).read_bytes()).hexdigest(), item["sha256"])
            recipe["operations"][0]["args"]["python"] = "import os"
            source.write_text(json.dumps(recipe))
            child = subprocess.run([sys.executable, "-m", "sphinxsim_agent.modeling", "--input", str(source), "--output", str(output)], capture_output=True)
            self.assertNotEqual(child.returncode, 0)
            self.assertIn("unknown parameters", json.loads((output / "cad-result.json").read_text())["error"])

    def test_loft_has_true_curved_geometry_and_compile_preserves_explicit_units(self):
        with tempfile.TemporaryDirectory() as root:
            result = build({"version": 1, "operations": [{"id": "body", "op": "loft", "args": {"profiles": [
                {"origin": [0, 0, 0], "circle": 1}, {"origin": [0, 0, 2], "circle": .5},
            ]}}], "output": "body"}, root)
            self.assertAlmostEqual(result["volume"], 3.665191429, places=6)
            validate_stl(Path(root) / "model.stl")
        config = json.loads((Path(__file__).parent / "fixtures/fixed-ship.json").read_text())
        config["geometries"]["global_resolution"]["particle_spacing"] = {"value": 25, "unit": "mm"}
        normalized, report = compile_config({"version": 1, "configuration": config})
        self.assertEqual(normalized["geometries"]["global_resolution"]["particle_spacing"], .025)
        self.assertEqual(report["unitConversions"][0]["to"], .025)
        config["geometries"]["global_resolution"]["particle_spacing"] = .025
        config["fluid_bodies"][0]["material"]["invented_model"] = 1
        with self.assertRaisesRegex(ValueError, "Unknown fields"):
            check_config(config)
        del config["fluid_bodies"][0]["material"]["invented_model"]
        config["fluid_bodies"][0]["material"]["youngs_modulus"] = 10000
        for validator in (check_config, editor_config_type().model_validate):
            with self.assertRaisesRegex(ValueError, "Fields not consumed"):
                validator(config)

    def test_shared_desktop_applies_real_candidate_but_preserves_edit_and_stop(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "project"; root.mkdir()
            entry = root / "config.json"
            fixture = Path(__file__).parent / "fixtures/fixed-ship.json"
            entry.write_bytes(fixture.read_bytes())
            session = ModelingSession(root, entry)
            def generate():
                return session.command("geometry_generate", {"shapeName": "BoatHull", "expectedHash": hashlib.sha256(entry.read_bytes()).hexdigest(),
                    "recipe": {"version": 1, "operations": [{"id": "body", "op": "box", "args": {"size": [1, .5, .2]}}], "output": "body"}},
                    dirty=False, frozen=False, automation="turn")
            def apply(config):
                entry.write_text(json.dumps(config))
            generate(); session.process.wait(timeout=30)
            session.tick(dirty=False, frozen=False, blocked=set(), apply=apply)
            self.assertEqual(session.job["state"], "applied")
            mesh = next(s for s in json.loads(entry.read_text())["geometries"]["shapes"] if s["name"] == "BoatHull")
            self.assertTrue((root / mesh["file_name"]).is_file())
            saved = entry.read_bytes()
            generate(); session.process.wait(timeout=30)
            session.tick(dirty=True, frozen=False, blocked=set(), apply=apply)
            self.assertEqual(session.job["state"], "conflict")
            self.assertEqual(entry.read_bytes(), saved)
            self.assertTrue((session.directory / session.job["id"] / "candidate/model.stl").exists())
            job = generate()
            session.tick(dirty=False, frozen=False, blocked={"turn"}, apply=apply)
            self.assertEqual(session.job["state"], "stopped")
            self.assertIsNone(session.process)
            self.assertEqual(entry.read_bytes(), saved)
            restarted = ModelingSession(root, entry)
            status = restarted.command("geometry_status", {"jobId": job["id"]}, dirty=False, frozen=False, automation=None)
            self.assertEqual(status["state"], "stopped")
