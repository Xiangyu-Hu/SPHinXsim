"""Checks of saved outputs, kept separate from execution success and accuracy."""
import hashlib
import json
from pathlib import Path
import time

import numpy as np

VERSION = "1"


class Quality:
    def __init__(self, config, geometry, directory, revision, dimension=3, identity=None):
        self.config, self.directory, self.revision = config, Path(directory), revision
        self.dimension, self.identity = dimension, identity or {}
        self.geometry = geometry
        self.checked, self.diagnostics, self.summaries = set(), [], {}
        self.started = time.monotonic()
        self.tolerance = config.get("geometries", {}).get("global_resolution", {}).get("particle_spacing", 0) * .5
        self.fluid = {b["name"] for b in config.get("fluid_bodies", [])}
        self.solid = {b["name"] for b in config.get("solid_bodies", []) if not b.get("is_moving")}
        self.locators = None
        self.unavailable = set()

    def check(self, body, frame, positions, data):
        key = (body, frame["time"], frame["source"])
        if key in self.checked:
            return
        summary = self.summaries.setdefault(body, {"frames": 0, "min_count": frame["count"], "max_count": frame["count"],
            "first_time": frame["time"], "last_time": frame["time"], "outside_domain": 0, "inside_solid": 0})
        summary["frames"] += 1
        summary["min_count"] = min(summary["min_count"], frame["count"])
        summary["max_count"] = max(summary["max_count"], frame["count"])
        summary["first_time"] = min(summary["first_time"], frame["time"])
        summary["last_time"] = max(summary["last_time"], frame["time"])
        domain = self.config.get("geometries", {}).get("system_domain")
        if body in self.fluid and domain and not self.config.get("fluid_boundary_conditions") and not self.config.get("characteristic_dimensions"):
            dim = len(domain["lower_bound"])
            outside = np.any((positions[:, :dim] < np.array(domain["lower_bound"]) - self.tolerance) |
                             (positions[:, :dim] > np.array(domain["upper_bound"]) + self.tolerance), axis=1)
            summary["outside_domain"] += int(outside.sum())
        if body in self.fluid and self.tolerance > 0 and self.dimension == 3 and self.solid and not self.config.get("characteristic_dimensions"):
            try:
                self._solids()
                from vtkmodules.vtkCommonCore import vtkDoubleArray
                from vtkmodules.util.numpy_support import vtk_to_numpy, numpy_to_vtk
                # Evaluate in bounded batches; no Python loop per particle and
                # no duplicate full-frame distance buffers for multiple solids.
                for distance in self.locators:
                    for start in range(0, len(positions), 65536):
                        values = vtkDoubleArray()
                        distance.FunctionValue(numpy_to_vtk(np.ascontiguousarray(positions[start:start+65536])), values)
                        summary["inside_solid"] += int((vtk_to_numpy(values) < -self.tolerance).sum())
            except Exception as exc:
                self.unavailable.add("solid_penetration: " + str(exc)[:200])
        self.checked.add(key)

    def _solids(self):
        if self.locators is not None:
            return
        from vtkmodules.vtkCommonCore import vtkPoints
        from vtkmodules.vtkCommonDataModel import vtkCellArray, vtkPolyData
        from vtkmodules.vtkFiltersCore import vtkImplicitPolyDataDistance
        from vtkmodules.util.numpy_support import numpy_to_vtk, numpy_to_vtkIdTypeArray
        locators = []
        for item in self.geometry:
            if item["id"] not in self.solid or "indices" not in item:
                continue
            points = np.fromfile(self.directory / item["positions"]["file"], dtype="<f4").reshape(-1, 3)
            faces = np.fromfile(self.directory / item["indices"]["file"], dtype="<u4").reshape(-1, 3)
            vtkpoints = vtkPoints(); vtkpoints.SetData(numpy_to_vtk(points, deep=True))
            cells = vtkCellArray()
            cells.SetCells(len(faces), numpy_to_vtkIdTypeArray(np.column_stack((np.full(len(faces), 3), faces)).astype(np.int64).ravel(), deep=True))
            mesh = vtkPolyData(); mesh.SetPoints(vtkpoints); mesh.SetPolys(cells)
            # Preserve CSG cavity orientation. Auto-orienting disconnected inner
            # surfaces can invert hollow solids and misclassify their contents.
            distance = vtkImplicitPolyDataDistance(); distance.SetInput(mesh)
            locators.append(distance)
        self.locators = locators
        if len(locators) != len(self.solid):
            self.unavailable.add("solid_penetration: missing triangulated solid surface")

    def report(self, completed=False):
        findings = list(self.diagnostics)
        for body, summary in self.summaries.items():
            solver = self.config.get("solver_parameters", {})
            end, interval = solver.get("end_time"), solver.get("output_interval")
            if completed and body in self.fluid and end and interval and not self.config.get("characteristic_dimensions") and summary["last_time"] + interval * 1.1 < end:
                findings.append({"code": "saved_output_coverage_gap", "severity": "warning", "body": body,
                    "last_saved_time": summary["last_time"], "requested_end_time": end, "threshold_seconds": interval * 1.1,
                    "message": "Saved output ends earlier than the requested duration; solver return alone does not establish output coverage."})
            if body in self.fluid and not self.config.get("fluid_boundary_conditions") and summary["min_count"] != summary["max_count"]:
                findings.append({"code": "closed_body_count_changed", "severity": "warning", "body": body,
                    "min_count": summary["min_count"], "max_count": summary["max_count"],
                    "message": "Saved particle count changed without configured open boundaries; inspect IDs and solver settings. This is not proof of leakage."})
            for key in ("outside_domain", "inside_solid"):
                if summary[key]:
                    findings.append({"code": key, "severity": "warning", "body": body, "particle_frame_occurrences": summary[key],
                        "tolerance": self.tolerance, "units": "native_output_coordinates", "time_range": [summary["first_time"], summary["last_time"]],
                        "message": "Occurrences across saved frames, not unique particles or proof of leakage."})
        native = self.directory.parent / "solver-completion.json"
        completion = json.loads(native.read_text()) if native.exists() else None
        not_evaluated = ["physical_accuracy", "mass_balance_without_boundary_flux", "inter_frame_trajectories",
                         "container_escape_without_allowed_fluid_volume", "unrecorded_time_intervals", "particle_identity_tracking", "final_solver_time_without_native_time_accessor", *sorted(self.unavailable)]
        if self.dimension != 3 or not self.tolerance:
            not_evaluated.append("solid_penetration_requires_3d_and_particle_spacing")
        if self.config.get("characteristic_dimensions"):
            not_evaluated.append("SI_unit_interpretation_of_native_scaled_outputs")
        if self.config.get("fluid_boundary_conditions"):
            not_evaluated.append("particle_conservation_and_domain_escape_with_open_boundaries")
        from .identity import effective_identity
        return {"version": VERSION, "config_revision": self.revision, "identity": self.identity,
            "effective_configuration_identity": effective_identity(self.config),
            "configuration_hash": hashlib.sha256(json.dumps(self.config, sort_keys=True).encode()).hexdigest(),
            "state": "warning" if findings else "checked" if completed and self.summaries else "checking" if self.summaries else "not_evaluated",
            "completed": completed, "scope": "Successfully decoded saved frames; no physical accuracy certification.",
            "checked_frames": len(self.checked), "bodies": self.summaries, "findings": findings,
            "completion_evidence": completion, "not_evaluated": not_evaluated,
            "duration_seconds": time.monotonic() - self.started}
