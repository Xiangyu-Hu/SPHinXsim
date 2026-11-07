"""Headless export contract tests; no GPU/solver acceptance is claimed."""
import json
from pathlib import Path
import tempfile
import unittest

import numpy as np
from vtkmodules.vtkCommonCore import vtkPoints, vtkDoubleArray
from vtkmodules.vtkCommonDataModel import vtkPolyData, vtkCellArray
from vtkmodules.vtkIOXML import vtkXMLPolyDataWriter

from sphinxsim_agent.export_vtp import Exporter, complete_vtp, hash_file

class PreviewExportTests(unittest.TestCase):
    def test_planar_subtraction_preserves_hole_instead_of_bounds(self):
        from sphinxsim_agent.export_vtp import box_mesh, composite_mesh
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary)
            shapes = {"Outer": {"type": "bounding_box"}, "Inner": {"type": "bounding_box"},
                "Wall": {"type": "complex_shape", "sub_shapes": ["Outer", "Inner"], "operations": ["union", "subtraction"]}}
            for name, lo, hi in (("Outer", [0, 0], [2, 2]), ("Inner", [.5, .5], [1.5, 1.5])):
                vertices, faces = box_mesh(lo, hi)
                points = vtkPoints()
                for p in vertices: points.InsertNextPoint(*p)
                cells = vtkCellArray()
                for face in faces.reshape(-1, 3):
                    cells.InsertNextCell(3)
                    for i in face: cells.InsertCellPoint(int(i))
                data = vtkPolyData(); data.SetPoints(points); data.SetPolys(cells)
                writer = vtkXMLPolyDataWriter(); writer.SetInputData(data); writer.SetFileName(str(source / f"Shape{name}.vtp")); writer.Write()
            points, indices = composite_mesh("Wall", shapes, source, 2)
            triangles = points[indices.reshape(-1, 3)]
            area = np.linalg.norm(np.cross(triangles[:, 1] - triangles[:, 0], triangles[:, 2] - triangles[:, 0]), axis=1).sum() / 2
            self.assertAlmostEqual(area, 3)
            centers = triangles.mean(axis=1)
            self.assertFalse(((centers[:, :2] > .5) & (centers[:, :2] < 1.5)).all(axis=1).any())

    def test_custom_water_subtracts_intersecting_hull_and_rejects_open_mesh(self):
        from vtkmodules.vtkFiltersSources import vtkCubeSource
        from vtkmodules.vtkFiltersCore import vtkTriangleFilter, vtkMassProperties, vtkFeatureEdges, vtkCleanPolyData
        with tempfile.TemporaryDirectory() as root:
            source, dest = Path(root)/"solver", Path(root)/"result"; source.mkdir()
            config, dimension = {"fluid_bodies":[{"name":"WaterBody","material":{"type":"weakly_compressible"}}],"geometries":{},"solver_parameters":{"end_time":1}}, 3
            config["solid_bodies"] = []
            config["geometries"]["oriented_boxes"] = []
            config["geometries"]["shapes"] = [
                {"name":"Tank", "type":"bounding_box"},
                {"name":"Hull", "type":"triangle_mesh"},
                {"name":"WaterBody", "type":"complex_shape", "sub_shapes":["Tank","Hull"], "operations":["union","subtraction"]},
            ]
            for name, bounds in (("Tank",(0,2,0,2,0,2)), ("Hull",(.5,1.5,1.5,2.5,.5,1.5))):
                cube = vtkCubeSource(); cube.SetBounds(*bounds); cube.Update()
                triangles = vtkTriangleFilter(); triangles.SetInputData(cube.GetOutput()); triangles.Update()
                writer = vtkXMLPolyDataWriter(); writer.SetInputData(triangles.GetOutput())
                writer.SetFileName(str(source/f"Shape{name}.vtp")); writer.Write()
            exporter = Exporter(source,dest,config,dimension,4)
            exporter.export_geometry({"WaterBody":([0,0,0],[2,2,2])})
            _, manifest = exporter.publish("geometry")
            geometry = manifest["geometry"][0]
            vertices = np.fromfile(dest/geometry["positions"]["file"],dtype="<f4").reshape(-1,3)
            indices = np.fromfile(dest/geometry["indices"]["file"],dtype="<u4").reshape(-1,3)
            points = vtkPoints()
            for point in vertices: points.InsertNextPoint(*point)
            cells = vtkCellArray()
            for face in indices:
                cells.InsertNextCell(3)
                for index in face: cells.InsertCellPoint(int(index))
            mesh = vtkPolyData(); mesh.SetPoints(points); mesh.SetPolys(cells)
            volume = vtkMassProperties(); volume.SetInputData(mesh); volume.Update()
            self.assertAlmostEqual(volume.GetVolume(),7.5,places=5)
            clean = vtkCleanPolyData(); clean.SetInputData(mesh); clean.Update()
            edges = vtkFeatureEdges(); edges.SetInputData(clean.GetOutput())
            edges.BoundaryEdgesOn(); edges.NonManifoldEdgesOn(); edges.FeatureEdgesOff(); edges.ManifoldEdgesOff(); edges.Update()
            self.assertEqual(edges.GetOutput().GetNumberOfCells(),0)
            # A component with a missing face cannot be published as a solid.
            triangles.GetOutput().BuildCells(); triangles.GetOutput().DeleteCell(0); triangles.GetOutput().RemoveDeletedCells()
            writer.Write()
            with self.assertRaisesRegex(ValueError,"Invalid closed geometry"):
                Exporter(source,dest,config,dimension).export_geometry({"WaterBody":([0,0,0],[2,2,2])})

    def test_continuum_frames_preserve_counts_stress_and_material_metadata(self):
        with tempfile.TemporaryDirectory() as root:
            source,dest=Path(root)/"solver",Path(root)/"result";source.mkdir()
            config,dimension={"continuum_bodies":[{"name":"GranularBody","material":{"type":"plastic_continuum"}}],"geometries":{"shapes":[{"name":"GranularBody","type":"bounding_box","lower_bound":[0,0],"upper_bound":[.2,.1]}]},"solver_parameters":{"end_time":.1}},2
            exporter=Exporter(source,dest,config,dimension,3)
            exporter.export_geometry({"GranularBody":([0,0],[.2,.1])})
            for iteration,count in enumerate((2,3)):
                points=vtkPoints();stress=vtkDoubleArray();stress.SetName("VerticalStress")
                for i in range(count):points.InsertNextPoint(i*.01,0,0);stress.InsertNextValue(-100*(i+1))
                data=vtkPolyData();data.SetPoints(points);data.GetPointData().AddArray(stress)
                time=vtkDoubleArray();time.SetName("TimeValue");time.InsertNextValue(iteration*.05);data.GetFieldData().AddArray(time)
                writer=vtkXMLPolyDataWriter();writer.SetInputData(data);writer.SetFileName(str(source/f"GranularBody_{iteration:04}.vtp"));writer.Write()
            exporter.scan();_,manifest=exporter.publish("results",completed=True)
            self.assertEqual(manifest["primaryBody"],"GranularBody")
            self.assertEqual([f["count"] for f in manifest["bodies"]["GranularBody"]],[2,3])
            self.assertEqual(manifest["geometry"][0]["role"],"continuum")
            self.assertEqual(manifest["fieldMetadata"]["VerticalStress"]["unit"],"Pa")
            self.assertEqual(manifest["bodies"]["GranularBody"][1]["fields"]["VerticalStress"]["mean"],-200)
            self.assertEqual(manifest["bodyMetadata"]["GranularBody"]["role"],"continuum")
            # A missing native cylinder mesh must never be replaced by its bounds.
            cylinder, dimension = {"continuum_bodies":[{"name":"GranularBody","material":{"type":"plastic_continuum"}}],"geometries":{"shapes":[{"name":"GranularBody","type":"cylinder","radius":.1,"half_height":.05}]},"solver_parameters":{"end_time":.1}},3
            with self.assertRaisesRegex(ValueError, "Native geometry mesh missing"):
                Exporter(source, dest, cylinder, dimension).export_geometry({"GranularBody":([0,0,0],[.2,.1,.2])})
    def test_binary_geometry_and_partial_output_publish_immutable_buffers(self):
        with tempfile.TemporaryDirectory() as root:
            source, dest = Path(root)/"solver",Path(root)/"result"
            source.mkdir()
            points=vtkPoints()
            for point in [(-1.,-1.,0.),(1.,-1.,0.),(0.,1.,0.)]:points.InsertNextPoint(*point)
            data=vtkPolyData();data.SetPoints(points)
            cells=vtkCellArray();cells.InsertNextCell(3)
            for i in range(3):cells.InsertCellPoint(i)
            data.SetPolys(cells)
            writer=vtkXMLPolyDataWriter();writer.SetInputData(data);writer.SetFileName(str(source/"ShapeBloodBody.vtp"));writer.SetDataModeToBinary();writer.Write()
            config,dimension={"fluid_bodies":[{"name":"BloodBody","material":{"type":"weakly_compressible"}}],"geometries":{"shapes":[{"name":"BloodBody","type":"triangle_mesh","file_name":"blood.stl"}]},"solver_parameters":{"end_time":.1}},3
            exporter=Exporter(source,dest,config,dimension,2)
            exporter.export_geometry({})
            name,geometry=exporter.publish("geometry")
            self.assertEqual(geometry["geometry"][0]["id"],"BloodBody")
            self.assertEqual(geometry["geometry"][0]["indices"]["bytes"],12)
            self.assertTrue(complete_vtp(source/"ShapeBloodBody.vtp"))
            time=vtkDoubleArray();time.SetName("TimeValue");time.InsertNextValue(.05);data.GetFieldData().AddArray(time)
            pressure=vtkDoubleArray();pressure.SetName("Pressure")
            for value in (2.,3.,4.):pressure.InsertNextValue(value)
            data.GetPointData().AddArray(pressure)
            writer.SetFileName(str(source/"BloodBody_0001.vtp"));writer.Write()
            (source/"BloodBody_0002.vtp").write_text('<VTKFile type="PolyData">')
            exporter.scan();_,manifest=exporter.publish("results")
            self.assertEqual(manifest["primaryBody"],"BloodBody")
            self.assertEqual(manifest["lastFrameTime"],.05)
            self.assertEqual(len(manifest["bodies"]["BloodBody"]),1)
            self.assertEqual(manifest["bodies"]["BloodBody"][0]["fields"]["Pressure"]["mean"],3.)
            self.assertFalse(manifest["completed"])
            positions=manifest["bodies"]["BloodBody"][0]["positions"]
            self.assertEqual(hash_file(dest/positions["file"]),positions["sha256"])
            self.assertEqual(np.fromfile(dest/positions["file"],dtype="<f4").reshape(-1,3).tolist(),[[-1.,-1.,0.],[1.,-1.,0.],[0.,1.,0.]])
            self.assertEqual(json.loads((dest/name).read_text())["phase"],"geometry")
            pressure.SetValue(0,float("nan"));pressure.Modified()
            writer.SetFileName(str(source/"BloodBody_0003.vtp"));writer.Write()
            with self.assertRaises(ValueError):exporter.scan()
            self.assertEqual(json.loads((dest/name).read_text())["phase"],"geometry")

if __name__=="__main__":unittest.main()
