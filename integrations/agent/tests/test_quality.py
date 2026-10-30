"""Real VTP publication checks including hollow geometry and open boundaries."""
import json
from pathlib import Path
import tempfile
import unittest

import numpy as np
from vtkmodules.vtkCommonCore import vtkPoints, vtkDoubleArray
from vtkmodules.vtkCommonDataModel import vtkPolyData
from vtkmodules.vtkIOXML import vtkXMLPolyDataWriter

from sphinxsim_agent.export_vtp import Exporter, hash_file


class QualityTests(unittest.TestCase):
    def test_saved_outputs_distinguish_hollow_space_solid_penetration_and_open_boundary_counts(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); source = root / "solver"; source.mkdir(); dest = root / "result"
            config = {"fluid_bodies": [{"name": "Liquid", "material": {"type": "weakly_compressible_fluid"}}],
                "solid_bodies": [{"name": "Walls"}], "gravity": [0, -9.81, 0],
                "geometries": {"global_resolution": {"particle_spacing": .1}, "system_domain": {"lower_bound": [-2,-2,-2], "upper_bound": [2,2,2]},
                "shapes": [{"name": "Outer", "type": "bounding_box"}, {"name": "Inner", "type": "bounding_box"},
                    {"name": "Walls", "type": "complex_shape", "sub_shapes": ["Outer", "Inner"], "operations": ["union", "subtraction"]}]},
                "solver_parameters": {"end_time": 1, "output_interval": .1}}
            exporter = Exporter(source,dest,config,3,7,{"snapshotId": "frozen"})
            exporter.export_geometry({"Outer":([-1,-1,-1],[1,1,1]), "Inner":([-.5,-.5,-.5],[.5,.5,.5]), "Walls":([-1,-1,-1],[1,1,1])})
            for time, values in ((0,[[0,0,0],[.8,0,0],[3,0,0]]),(1,[[0,0,0],[0,.2,0]])):
                points = vtkPoints()
                for value in values: points.InsertNextPoint(*value)
                data = vtkPolyData(); data.SetPoints(points)
                stamp = vtkDoubleArray(); stamp.SetName("TimeValue"); stamp.InsertNextValue(time); data.GetFieldData().AddArray(stamp)
                writer = vtkXMLPolyDataWriter(); writer.SetFileName(str(source / f"Liquid_{time}.vtp")); writer.SetInputData(data); writer.Write()
            (root / "solver-completion.json").write_text(json.dumps({"completed": True}))
            exporter.scan(); name, manifest = exporter.publish("results",completed=True)
            report = json.loads((dest / manifest["quality"]["report"]["file"]).read_text())
            summary = report["bodies"]["Liquid"]
            self.assertEqual(summary["insideSolid"],1,report)
            self.assertEqual(summary["outsideDomain"],1)
            self.assertEqual((summary["minCount"],summary["maxCount"]),(2,3))
            self.assertEqual(report["identity"]["snapshotId"],"frozen")
            self.assertEqual(report["completionEvidence"],{"completed":True})
            self.assertEqual(hash_file(dest / manifest["quality"]["report"]["file"]),manifest["quality"]["report"]["sha256"])
            # Open-boundary count changes cannot be reported as conservation failures.
            config["fluid_boundary_conditions"]=[{"type":"emitter"}]
            open_run = Exporter(source,root/"open",config,3)
            open_run.scan(); _, result = open_run.publish("results",completed=True)
            self.assertEqual(result["quality"]["bodies"]["Liquid"]["outsideDomain"],0)
            self.assertIn("particle_conservation_and_domain_escape_with_open_boundaries",result["quality"]["notEvaluated"])
