"""Validate immutable native project inputs before invoking any binding."""
import hashlib
import json
from pathlib import Path, PurePosixPath
import re


def load_project(snapshot, root, solver_defaults=None):
    root = Path(root).resolve(strict=True)
    files = snapshot["files"]
    if not 0 < len(files) <= 128:
        raise ValueError("Invalid project file count")
    known = {}
    total = 0
    for item in files:
        name = item["path"]
        relative = PurePosixPath(name)
        if (not re.fullmatch(r"[A-Za-z0-9_-][A-Za-z0-9_./-]{0,179}", name)
                or ".." in name or str(relative) != name
                or relative.suffix.lower() not in (".json", ".stl", ".step", ".md", ".txt")
                or name in known):
            raise ValueError("Invalid project path")
        target = root / name
        for candidate in (target, *target.parents):
            if candidate == root:
                break
            if candidate.is_symlink():
                raise ValueError("Project symlinks are not allowed")
        if not target.resolve(strict=True).is_relative_to(root):
            raise ValueError("Project path escapes input root")
        size = target.stat().st_size
        total += size
        if size != item["bytes"] or size > 512 << 20 or total > 2 << 30:
            raise ValueError("Project size mismatch")
        from sphinxsim_agent.export_vtp import hash_file
        if hash_file(target) != item["sha256"]:
            raise ValueError("Project hash mismatch")
        known[name] = target
    entry = snapshot["entryFile"]
    if entry not in known or not entry.endswith(".json"):
        raise ValueError("Missing project entry configuration")
    if known[entry].stat().st_size > 1 << 20:
        raise ValueError("Entry configuration exceeds 1 MiB")
    config = json.loads(known[entry].read_text())

    def resources(value):
        if isinstance(value, list):
            for child in value:
                resources(child)
        elif isinstance(value, dict):
            for key, child in value.items():
                if key == "file_name":
                    if not isinstance(child, str):
                        raise ValueError("Invalid resource reference")
                    name = str(PurePosixPath(entry).parent / child)
                    if name not in known or known[name].suffix.lower() not in (".stl", ".txt"):
                        raise ValueError("Resource must reference a project STL or data file")
                    # The native IO environment prefixes file_name with the
                    # effective config directory; absolute names are not accepted.
                    value[key] = str(known[name].relative_to(root.parent))
                elif key == "name" and isinstance(child, str):
                    if not child or len(child) > 128 or any(c in child for c in "/\\\x00\r\n") or ".." in child:
                        raise ValueError("Unsafe native object name")
                else:
                    resources(child)
    resources(config)
    if solver_defaults:
        solver = config.setdefault("solver_parameters", {})
        for key in ("end_time", "output_interval"):
            if solver_defaults.get(key) is not None:
                solver.setdefault(key, solver_defaults[key])
    # Schema validation uses the same installed native source as the solver.
    from sphinxsim_agent.physics import check_config
    normalized, report = check_config(config)
    for target in known.values():
        if target.suffix.lower() == ".stl":
            validate_stl(target)
    return normalized, report["dimension"]


def validate_stl(path):
    import numpy as np
    from vtkmodules.vtkIOGeometry import vtkSTLReader
    from vtkmodules.vtkFiltersCore import vtkFeatureEdges, vtkMassProperties
    from vtkmodules.util.numpy_support import vtk_to_numpy
    reader = vtkSTLReader()
    reader.SetFileName(str(path))
    reader.MergingOn()
    reader.Update()
    mesh = reader.GetOutput()
    if mesh.GetNumberOfCells() == 0 or mesh.GetPoints() is None:
        raise ValueError("Empty STL")
    points = vtk_to_numpy(mesh.GetPoints().GetData())
    if not np.isfinite(points).all():
        raise ValueError("STL has non-finite coordinates")
    cells = vtk_to_numpy(mesh.GetPolys().GetData()).reshape(-1, 4)
    if not (cells[:, 0] == 3).all():
        raise ValueError("STL must contain triangles")
    triangles = points[cells[:, 1:]]
    if (np.linalg.norm(np.cross(triangles[:, 1] - triangles[:, 0], triangles[:, 2] - triangles[:, 0]), axis=1) == 0).any():
        raise ValueError("STL has degenerate triangles")
    edges = vtkFeatureEdges()
    edges.SetInputData(mesh)
    edges.BoundaryEdgesOn()
    edges.NonManifoldEdgesOn()
    edges.FeatureEdgesOff()
    edges.ManifoldEdgesOff()
    edges.Update()
    if edges.GetOutput().GetNumberOfCells():
        raise ValueError("STL must be a closed manifold")
    mass = vtkMassProperties()
    mass.SetInputData(mesh)
    mass.Update()
    if not np.isfinite(mass.GetVolume()) or mass.GetVolume() <= 0:
        raise ValueError("STL must have positive volume")
