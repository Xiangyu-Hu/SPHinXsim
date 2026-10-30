"""Headless, one-file-at-a-time VTK export with immutable content-addressed buffers."""
import hashlib
import json
import re
import shutil
from pathlib import Path

import numpy as np


def atomic_json(path, value):
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(value, allow_nan=False))
    temp.replace(path)

def hash_file(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()

def write_buffer(dest, label, values, dtype="<f4"):
    values = np.asarray(values, dtype=dtype)
    if not np.isfinite(values).all():
        raise ValueError("Invalid/overflowing numeric buffer")
    digest = hashlib.sha256(memoryview(values).cast("B")).hexdigest()
    name = f"{label}-{digest}.bin"
    path = dest / name
    if not path.exists():
        tmp = path.with_suffix(".tmp")
        values.tofile(tmp)
        tmp.replace(path)
    return {"file": name, "bytes": values.nbytes, "sha256": digest}

def complete_vtp(path):
    # ASCII and appended VTK XML both end in a closing root tag. Never parse an open writer.
    with path.open("rb") as stream:
        stream.seek(max(0, path.stat().st_size - 256))
        return b"</VTKFile>" in stream.read()

def read_polydata(path):
    from vtkmodules.vtkIOXML import vtkXMLPolyDataReader
    reader = vtkXMLPolyDataReader()
    reader.SetFileName(str(path))
    reader.Update()
    if reader.GetErrorCode() or reader.GetOutput().GetNumberOfPoints() == 0:
        raise ValueError(f"Invalid VTP: {path.name}")
    return reader.GetOutput()

def source_asset(path, dest):
    digest = hash_file(path)
    target = dest / f"source-{digest}.vtp"
    if not target.exists():
        tmp = target.with_suffix(".tmp")
        shutil.copyfile(path, tmp)
        if hash_file(tmp) != digest:
            tmp.unlink()
            raise ValueError("VTP changed during publication")
        tmp.replace(target)
    return target.name

def box_mesh(lo, hi):
    lo, hi = list(lo) + [0] * (3-len(lo)), list(hi) + [0] * (3-len(hi))
    if lo[2] == hi[2]:
        return np.array([[lo[0],lo[1],lo[2]],[hi[0],lo[1],lo[2]],[hi[0],hi[1],lo[2]],[lo[0],hi[1],lo[2]]]), np.array([0,1,2,0,2,3])
    return np.array([[x,y,z] for x in (lo[0],hi[0]) for y in (lo[1],hi[1]) for z in (lo[2],hi[2])]), np.array([0,2,6,0,6,4,1,5,7,1,7,3,0,4,5,0,5,1,2,3,7,2,7,6,0,1,3,0,3,2,4,6,7,4,7,5])

def composite_mesh(name, shapes, source, dimension=3):
    """Apply native ordered CSG to actual closed component meshes, never bounds."""
    from manifold3d import CrossSection, Error, Manifold, Mesh, OpType, triangulate
    from vtkmodules.util.numpy_support import vtk_to_numpy
    from vtkmodules.vtkFiltersCore import vtkCleanPolyData, vtkPolyDataNormals, vtkTriangleFilter

    cache = {}

    def build(key):
        if key in cache:
            return cache[key]
        paths = list(source.rglob(f"Shape{key}.vtp"))
        shape = shapes[key]
        if paths:
            triangles = vtkTriangleFilter()
            triangles.SetInputData(read_polydata(paths[0]))
            triangles.PassLinesOff(); triangles.PassVertsOff(); triangles.Update()
            clean = vtkCleanPolyData(); clean.SetInputData(triangles.GetOutput()); clean.Update()
            normals = vtkPolyDataNormals(); normals.SetInputData(clean.GetOutput())
            normals.SplittingOff(); normals.ConsistencyOn(); normals.AutoOrientNormalsOn(); normals.Update()
            data = normals.GetOutput()
            vertices = np.asarray(vtk_to_numpy(data.GetPoints().GetData()), dtype=np.float32)
            faces = np.asarray(vtk_to_numpy(data.GetPolys().GetData()).reshape(-1, 4)[:, 1:], dtype=np.uint32)
            if dimension == 2:
                polygons = []
                for face in faces:
                    xy = vertices[face, :2].astype(np.float64)
                    a, b = xy[1] - xy[0], xy[2] - xy[0]
                    if a[0] * b[1] - a[1] * b[0] < 0:
                        xy = xy[::-1]
                    polygons.append(CrossSection([xy]))
                result = CrossSection.batch_boolean(polygons, OpType.Add)
            else:
                result = Manifold(Mesh(vertices, faces))
        elif shape.get("type") == "complex_shape":
            result = CrossSection() if dimension == 2 else Manifold()
            for child, operation in zip(shape["sub_shapes"], shape["operations"], strict=True):
                component = build(child)
                if operation == "union":
                    result = result + component
                elif operation == "subtraction":
                    result = result - component
                else:
                    raise ValueError(f"Unsupported geometry operation: {operation}")
        else:
            raise ValueError(f"Native geometry mesh missing: {key}")
        if dimension == 3 and result.status() != Error.NoError:
            raise ValueError(f"Invalid closed geometry: {key} ({result.status()})")
        cache[key] = result
        return result

    result = build(name)
    if result.is_empty():
        raise ValueError(f"Empty composite geometry: {name}")
    if dimension == 2:
        polygons = result.to_polygons()
        vertices = np.concatenate(polygons)
        return np.column_stack((vertices, np.zeros(len(vertices)))), np.asarray(triangulate(polygons)).reshape(-1)
    mesh = result.to_mesh()
    return np.asarray(mesh.vert_properties)[:, :3], np.asarray(mesh.tri_verts).reshape(-1)

class Exporter:
    def __init__(self, source, dest, config, dimension, config_revision=0, identity=None):
        self.source, self.dest, self.config, self.dimension = source, dest, config, dimension
        self.revision, self.serial = config_revision, 0
        primary_bodies = config.get("fluid_bodies") or config.get("continuum_bodies")
        if not primary_bodies:
            raise ValueError("No primary fluid or continuum body")
        self.primary = primary_bodies[0]["name"]
        self.geometry, self.bodies, self.fields, self.seen = [], {}, {}, {}
        self.particle_bodies = {}
        self.dest.mkdir(parents=True, exist_ok=True)
        self.low, self.high = np.full(3, np.inf), np.full(3, -np.inf)
        from sphinxsim_agent.quality import Quality
        self.quality = Quality(config, self.geometry, dest, config_revision, dimension, identity)

    def extend_bounds(self, positions):
        self.low = np.minimum(self.low, positions.min(axis=0))
        self.high = np.maximum(self.high, positions.max(axis=0))

    def export_geometry(self, shape_bounds):
        from vtkmodules.util.numpy_support import vtk_to_numpy
        from vtkmodules.vtkFiltersCore import vtkTriangleFilter
        fluids = {b["name"] for b in self.config.get("fluid_bodies", [])}
        continua = {b["name"] for b in self.config.get("continuum_bodies", [])}
        solids = {b["name"] for b in self.config.get("solid_bodies",[])}
        shapes = {s["name"]:s for s in self.config["geometries"]["shapes"]}
        regions = {r["name"]: r for r in self.config["geometries"].get("oriented_boxes", [])}
        length_scale = next((d["value"] for d in self.config.get("characteristic_dimensions",[]) if d["name"]=="Length"),1)
        names = [name for name in shapes if name in fluids | continua | solids] + list(regions)
        for name in names:
            paths = list(self.source.rglob(f"Shape{name}.vtp"))
            item = {"id": name, "role": "boundary" if name in regions else "fluid" if name in fluids else "continuum" if name in continua else "solid"}
            if paths:
                data = read_polydata(paths[0])
                triangles = vtkTriangleFilter(); triangles.SetInputData(data); triangles.Update()
                data = triangles.GetOutput()
                positions = vtk_to_numpy(data.GetPoints().GetData())
                polygons = vtk_to_numpy(data.GetPolys().GetData())
                indices = []
                offset = 0
                while offset < len(polygons):
                    count = int(polygons[offset])
                    if count != 3:
                        raise ValueError("Triangulation did not produce triangles")
                    indices.extend(polygons[offset + 1:offset + 4]); offset += 4
                item["source"] = source_asset(paths[0], self.dest)
                if indices:
                    item["indices"] = write_buffer(self.dest, "indices", indices, "<u4")
            elif name in shape_bounds:
                lo, hi = shape_bounds[name]
                positions, indices = box_mesh([v*length_scale for v in lo],[v*length_scale for v in hi])
                shape = shapes.get(name,{})
                if self.dimension == 3 and shape.get("type") == "complex_shape" and shape.get("operations") == ["union","subtraction"] and all(shapes[n]["type"] in ("bounding_box","expanded_box") for n in shape["sub_shapes"]):
                    outer, inner = shape["sub_shapes"]
                    op, oi = box_mesh(*[[v*length_scale for v in bound] for bound in shape_bounds[outer]])
                    ip, ii = box_mesh(*[[v*length_scale for v in bound] for bound in shape_bounds[inner]])
                    positions = np.concatenate((op,ip))
                    indices = np.concatenate((oi,(ii.reshape(-1,3)[:,::-1]+len(op)).reshape(-1)))
                elif shape.get("type") == "complex_shape":
                    positions, indices = composite_mesh(name, shapes, self.source, self.dimension)
                elif shape.get("type") != "bounding_box":
                    raise ValueError(f"Native geometry mesh missing: {name}")
                item["indices"] = write_buffer(self.dest,"indices",indices,"<u4")
            else:
                continue
            item["positions"] = write_buffer(self.dest, "geometry", positions)
            self.extend_bounds(positions)
            if name in regions:
                region = regions[name]
                conditions = [b for b in self.config.get("fluid_boundary_conditions", []) if b["oriented_box"] == name]
                # Region boxes can reference a primitive or carry their own transform.
                primitive = next((p for p in self.config["geometries"].get("primitives",[]) if p["name"] == region.get("primitive")), {})
                transform = region.get("transform", primitive.get("transform", {}))
                center = region.get("center", transform.get("translation", [0]*self.dimension))
                angle = transform.get("rotation_angle", 0)
                normal = region.get("normal", [float(np.cos(angle)), float(np.sin(angle))] + ([0.] if self.dimension == 3 else []))
                for ic in self.config.get("initial_conditions", []):
                    for assignment in ic["assignments"]:
                        if assignment.get("region") == name and assignment["variable"].get("real_type") == "Temperature":
                            conditions.append({"body_name":ic["body_name"], "type":"temperature", "temperature":assignment["value"]})
                item["annotation"] = {"name": name, "type": region["type"], "center": center, "normal": normal, "radius": region.get("radius"), "conditions": conditions}
            self.geometry.append(item)
        # GeometryBuilder exposes body bounds; its system domain is their union
        # enlarged by an explicitly configured domain, as in createGeometries.
        if shape_bounds:
            low = np.min([np.array(lo)*length_scale for lo,_ in shape_bounds.values()],axis=0)
            high = np.max([np.array(hi)*length_scale for _,hi in shape_bounds.values()],axis=0)
            domain = self.config["geometries"].get("system_domain")
            if domain:
                low = np.minimum(low,domain["lower_bound"])
                high = np.maximum(high,domain["upper_bound"])
            positions,indices=box_mesh(low,high)
            self.geometry.append({"id":"SystemDomain","role":"domain","positions":write_buffer(self.dest,"geometry",positions),"indices":write_buffer(self.dest,"indices",indices,"<u4")})
            self.extend_bounds(positions)

    def scan(self, preview=False):
        from vtkmodules.util.numpy_support import vtk_to_numpy
        for path in sorted(self.source.rglob("*.vtp"), key=lambda p: (int(re.search(r"(\d+)$",p.stem).group(1)) if re.search(r"(\d+)$",p.stem) else -1, p.name)):
            if path.name.startswith("Shape") or not complete_vtp(path):
                continue
            match = re.fullmatch(r"([A-Za-z0-9_-]+?)(?:_ite)?_\d+", path.stem)
            if not match:
                continue
            body = match[1]
            if body not in {b["name"] for b in self.config.get("fluid_bodies", []) + self.config.get("continuum_bodies", []) + self.config.get("solid_bodies", [])}:
                continue
            stat = path.stat(); stamp = (stat.st_size, stat.st_mtime_ns)
            key = (str(path), preview)
            if self.seen.get(key) == stamp:
                continue
            immutable_source = source_asset(path,self.dest)
            data = read_polydata(self.dest / immutable_source)
            if path.stat().st_size != stamp[0] or path.stat().st_mtime_ns != stamp[1]:
                raise ValueError("VTP changed while decoding")
            time_data = data.GetFieldData().GetArray("TimeValue")
            if time_data is None and not preview:
                continue
            time = 0. if preview else float(vtk_to_numpy(time_data).flat[0])
            if not np.isfinite(time) or time < 0:
                raise ValueError("Invalid simulation time")
            positions = vtk_to_numpy(data.GetPoints().GetData())
            count = len(positions)
            if not count:
                continue
            frame = {"id": f"{body}-{'preview' if preview else format(time, '.17g')}", "time": time, "count": count, "positions": write_buffer(self.dest,"positions",positions), "fields": {}, "source": immutable_source}
            point_data = data.GetPointData()
            candidates = {}
            for i in range(point_data.GetNumberOfArrays()):
                array = point_data.GetArray(i)
                if array is None:
                    continue
                name = array.GetName()
                if not np.isfinite(vtk_to_numpy(array)).all():
                    raise ValueError(f"Non-finite output field: {body}/{name}")
                if not name or not re.fullmatch(r"[A-Za-z0-9_-]+",name) or name in ("ParticleID","SortedID","SortedParticle_ID","OriginalID"):
                    continue
                values = vtk_to_numpy(array)
                if values.shape == (count,):
                    candidates[name] = values
                elif name == "Velocity" and values.shape == (count,3):
                    candidates["Speed"] = np.linalg.norm(values,axis=1)
            for name, values in candidates.items():
                stats = {"min":float(values.min()),"max":float(values.max()),"mean":float(values.mean())}
                frame["fields"][name] = {**write_buffer(self.dest,"field",values),**stats}
                bounds = self.fields.setdefault(name,[stats["min"],stats["max"]]);bounds[0]=min(bounds[0],stats["min"]);bounds[1]=max(bounds[1],stats["max"])
            frames = self.bodies.setdefault(body,[])
            if preview:
                self.bodies[body] = [frame] # Final relaxed cloud for each body.
            elif not any(old["time"] == time for old in frames):
                frames.append(frame);frames.sort(key=lambda f:f["time"])
            if path.stat().st_size != stamp[0] or path.stat().st_mtime_ns != stamp[1]:
                raise ValueError("VTP changed while decoding")
            if not preview:
                self.quality.check(body, frame, positions, data)
            self.extend_bounds(positions);self.seen[key]=stamp

    def publish(self, phase, completed=False):
        if not np.isfinite(self.low).all():
            raise ValueError("No preview or result geometry")
        self.serial += 1
        manifest = {"version":2,"revision":self.serial,"configRevision":self.revision,"phase":phase,"primaryBody":self.primary,"dimension":self.dimension,"geometry":self.geometry,"bodies":self.bodies,"fields":self.fields,"bounds":[self.low.tolist(),self.high.tolist()],"units":"m, s, kg, Pa","completed":completed,"requestedEndTime":self.config.get("solver_parameters", {}).get("end_time"),"lastFrameTime":self.bodies.get(self.primary,[{"time":0}])[-1]["time"],"decimated":False,"interpolated":False,"gravity":self.config.get("gravity")}
        manifest["bodyMetadata"] = {}
        for key,role,zh,en in (("fluid_bodies","fluid","流体","Fluid"),("continuum_bodies","continuum","颗粒材料","Granular material"),("solid_bodies","solid","壁面","Walls")):
            for body in self.config.get(key,[]):
                manifest["bodyMetadata"][body["name"]] = {"role":role,"label":{"zh":zh,"en":en}}
        manifest["fieldMetadata"] = {name: field_metadata(name) for name in self.fields}
        report = self.quality.report(completed)
        raw = json.dumps(report, allow_nan=False, sort_keys=True).encode()
        digest = hashlib.sha256(raw).hexdigest()
        report_name = f"quality-{digest}.json"
        (self.dest / report_name).write_bytes(raw)
        manifest["quality"] = {"state": report["state"], "checkedFrames": report["checkedFrames"],
            "findingCount": len(report["findings"]), "notEvaluated": report["notEvaluated"],
            "findings": report["findings"], "bodies": report["bodies"], "scope": report["scope"],
            "completionEvidence": report["completionEvidence"],
            "report": {"file": report_name, "bytes": len(raw), "sha256": digest}}
        material = (self.config.get("fluid_bodies") or self.config["continuum_bodies"])[0]["material"]
        preferred = (["VolumeFractionOil"] if material["type"] == "weakly_compressible_multi_phase" else []) + ["Temperature", "MassFractionSpeciesA", "AccDeviatoricPlasticStrain", "Speed"]
        available = self.bodies.get(self.primary, [{}])[0].get("fields", {})
        default = next((name for name in preferred if name in available), next(iter(available), None))
        if default:
            manifest["bodyMetadata"][self.primary]["defaultField"] = default
        if self.particle_bodies:
            manifest["particleBodies"] = self.particle_bodies
        if not completed and phase=="results":
            manifest["partial"] = True
        name = f"manifest-r{self.serial:08d}.json"
        atomic_json(self.dest/name,manifest)
        return name,manifest

def field_metadata(name):
    known = {
        "Speed": ("速度", "Speed", "m/s"), "Pressure": ("压力", "Pressure", "Pa"),
        "Density": ("密度", "Density", "kg/m³"), "ReferenceDensity": ("参考密度", "Reference density", "kg/m³"),
        "Temperature": ("温度", "Temperature", "K"), "Compression": ("压缩比", "Compression", ""),
        "VerticalStress": ("竖向应力", "Vertical stress", "Pa"),
        "AccDeviatoricPlasticStrain": ("累计偏塑性应变", "Accumulated deviatoric plastic strain", ""),
    }
    if name in known:
        zh,en,unit = known[name]
    elif "MassFraction" in name or name.startswith("VolumeFraction"):
        mass = "MassFraction" in name
        species = name.split("MassFraction" if mass else "VolumeFraction",1)[1]
        zh_species = {"Water":"水","Oil":"油","SpeciesA":"组分 A","SpeciesB":"组分 B","MultiSpeciesPhase":"混合组分相"}.get(species,species)
        zh,en,unit = zh_species + ("质量分数" if mass else "体积分数"), species + (" mass fraction" if mass else " volume fraction"), ""
    else:
        zh,en,unit = name,name,""
    return {"label":{"zh":zh,"en":en},"unit":unit}
