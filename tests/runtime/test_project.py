import hashlib
import json
from pathlib import Path
import tempfile
import unittest

import pytest

pytest.importorskip("vtkmodules")
pytest.importorskip("manifold3d")

from sphinxsim.runtime.project_input import load_project


class ProjectInputTests(unittest.TestCase):
    def project(self, root):
        fixture = Path(__file__).parent / "fixtures"
        for name in ("fixed-ship.json", "fixed-hull.stl"):
            (root / name).write_bytes((fixture / name).read_bytes())
        return self.project_from_files(root)

    def project_from_files(self, root):
        files = [{"path": p.name, "bytes": p.stat().st_size,
                  "sha256": hashlib.sha256(p.read_bytes()).hexdigest()}
                 for p in sorted(root.iterdir())]
        return {"entry_file": "fixed-ship.json", "files": files}

    def test_fixed_hull_config_and_mesh_are_validated_before_binding(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config, dimension = load_project(self.project(root), root)
            self.assertEqual(dimension, 3)
            self.assertEqual(config["solver_parameters"]["end_time"], 1)
            self.assertEqual(config["fluid_bodies"][0]["material"]["density"], 1000)
            mesh = next(s for s in config["geometries"]["shapes"] if s["type"] == "triangle_mesh")
            self.assertEqual(mesh["file_name"], str(Path(root.name) / "fixed-hull.stl"))
            snapshot = self.project(root)
            preserved, _ = load_project(snapshot, root, {"end_time": 2, "output_interval": .25})
            self.assertEqual(preserved["solver_parameters"]["end_time"], 1)
            data = json.loads((root / "fixed-ship.json").read_text())
            data["solver_parameters"].pop("end_time", None)
            data["solver_parameters"].pop("output_interval", None)
            (root / "fixed-ship.json").write_text(json.dumps(data))
            snapshot = self.project_from_files(root)
            native, _ = load_project(snapshot, root)
            self.assertNotIn("end_time", native["solver_parameters"])
            hosted, _ = load_project(snapshot, root, {"end_time": 2, "output_interval": .25})
            self.assertEqual(hosted["solver_parameters"]["end_time"], 2)
            self.assertEqual(hosted["solver_parameters"]["output_interval"], .25)

    def test_native_polygon_data_file_is_inventoried_and_modified_bytes_are_rejected(self):
        from sphinxsim.operations import Operations
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = Path(__file__).parents[1] / "test_simulation/test_2d_simulation/data"
            for name in ("milling.json", "milling_tool.dat"):
                (root / name).write_bytes((source / name).read_bytes())
            snapshot = Operations(root).call("project_inspect", {"config_file": "milling.json"})
            resource = next(item for item in snapshot["files"] if item["path"] == "milling_tool.dat")
            self.assertEqual(resource["sha256"], hashlib.sha256((root / "milling_tool.dat").read_bytes()).hexdigest())
            (root / "milling_tool.dat").write_bytes(b"changed")
            with self.assertRaisesRegex(ValueError, "size mismatch|hash mismatch"):
                load_project(snapshot, root)

    def test_changed_bytes_and_external_resource_references_are_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            snapshot = self.project(root)
            (root / "fixed-hull.stl").write_text("corrupt")
            with self.assertRaisesRegex(ValueError, "size mismatch|hash mismatch"):
                load_project(snapshot, root)
            snapshot = self.project(root)
            config = json.loads((root / "fixed-ship.json").read_text())
            mesh = next(s for s in config["geometries"]["shapes"] if s["type"] == "triangle_mesh")
            mesh["file_name"] = "/etc/passwd"
            data = json.dumps(config).encode()
            (root / "fixed-ship.json").write_bytes(data)
            for f in snapshot["files"]:
                if f["path"] == "fixed-ship.json":
                    f.update(bytes=len(data), sha256=hashlib.sha256(data).hexdigest())
            with self.assertRaisesRegex(ValueError, "Resource must reference"):
                load_project(snapshot, root)


if __name__ == "__main__":
    unittest.main()
