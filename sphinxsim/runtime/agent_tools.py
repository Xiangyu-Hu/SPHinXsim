"""Configuration authoring and analysis operations shared by CLI and MCP."""
from contextlib import redirect_stdout
from copy import deepcopy
import base64
import json
from pathlib import Path
import shutil
import sys
import tempfile

from .export_vtp import atomic_json, hash_file
from .physics import check_config


def call(service, name, arguments):
    jobs = service.jobs
    if name == "config_propose":
        from sphinxsim.config.schemas import SimulationConfig
        from sphinxsim.config.update_patch import apply_update_patch
        from sphinxsim.llm import get_llm
        payload, _ = check_config(arguments["configuration"])
        with redirect_stdout(sys.stderr):
            patch = get_llm(arguments.get("provider")).update_patch(
                SimulationConfig.model_validate(payload), arguments["description"], strict=True)
        candidate = apply_update_patch(arguments["configuration"], patch, strict=True)
        if candidate.errors:
            raise ValueError("\n".join(candidate.errors))
        _, report = check_config(candidate.updated)
        return {"configuration": candidate.updated, "report": report, "applied": False}
    if name in ("config_generate", "config_update"):
        from sphinxsim.config.schemas import SimulationConfig
        from sphinxsim.llm import get_llm
        output = jobs.path(arguments["output"], exists=False)
        with redirect_stdout(sys.stderr):
            llm = get_llm(arguments.get("provider"))
            if name == "config_generate":
                config = llm.generate(arguments["description"])
            else:
                source = jobs.path(arguments["config_file"])
                payload, _ = check_config(json.loads(source.read_text(encoding="utf-8")))
                config = llm.update(SimulationConfig.model_validate(payload), arguments["description"])
        payload, report = check_config(config.model_dump(mode="json", exclude_none=True))
        atomic_json(output, payload)
        return {"config_file": arguments["output"], "report": report, "sha256": hash_file(output)}
    if name == "case_prepare":
        source = jobs.path(arguments["config_file"])
        output = jobs.path(arguments["output"], exists=False)
        prepare_case(service, source, output)
        return {"config_file": arguments["output"], "sha256": hash_file(output)}
    if name == "set_friction_angle":
        from sphinxsim.agent.case_tools import CaseWorkspace
        path = jobs.path(arguments["config_file"])
        payload, _ = check_config(json.loads(path.read_text(encoding="utf-8")))
        candidate = deepcopy(payload)
        matches = [body for body in candidate.get("continuum_bodies", []) if body["name"] == arguments["body_name"]]
        if len(matches) != 1 or matches[0]["material"]["type"] != "plastic_continuum":
            raise ValueError("Select exactly one plastic_continuum body")
        import math
        angle = math.radians(arguments["friction_angle_degrees"])
        matches[0]["material"]["friction_angle"] = angle
        check_config(candidate)
        CaseWorkspace(service.root).update_friction_angle(path, arguments["friction_angle_degrees"], body_name=arguments["body_name"])
        return {"config_file": arguments["config_file"], "friction_angle_radians": angle, "sha256": hash_file(path)}
    if name == "analyze_result":
        from sphinxsim.agent.analysis_plan import AnalysisPlan
        plan = AnalysisPlan.model_validate(arguments["analysis_plan"])
        config = jobs.path(arguments["config_file"], exists=plan.kind == "builtin")
        output = jobs.path(arguments["output_dir"])
        pointer = output / "job-reference.json"
        if pointer.exists():
            reference = json.loads(pointer.read_text(encoding="utf-8"))
            if Path(reference["workspace"]).resolve() != service.root:
                raise ValueError("Analysis job belongs to a different workspace")
            work = jobs.job_path(jobs.resolve(reference["reference"]))
            frozen = json.loads((work / "request.json").read_text(encoding="utf-8"))
            jobs.results(reference["reference"]["job_id"])
            from .project_input import load_project
            load_project(frozen["project"], work / "project", frozen.get("solver_defaults"))
            config = work / "project" / frozen["project"]["entry_file"]
        if plan.kind == "builtin":
            check_config(json.loads(config.read_text(encoding="utf-8")))
        registry, spec = plan.registry_and_spec()
        result = registry.analyze(config, output, spec)
        return {"metrics": result.model_dump(mode="json")}
    if name == "compare_results":
        from sphinxsim.agent.analysis import FrictionAngleSweepAnalyzer
        samples = [(item["friction_angle_degrees"], item["run_id"], item["analysis_id"], item["metrics"])
                   for item in arguments["samples"]]
        return FrictionAngleSweepAnalyzer().summarize(
            sweep_id=arguments["sweep_id"], target_value=arguments["target_value"], samples=samples).model_dump(mode="json")
    if name == "results_render":
        return render(service, arguments)
    raise ValueError("Unknown authoring or analysis operation")


def prepare_case(service, source, output):
    from sphinxsim.agent.store import AgentTaskStore
    from sphinxsim.json_format import format_json_like_source

    if source == output:
        raise ValueError("Select a working case file different from its source")
    if source.suffix.lower() != ".json" or output.suffix.lower() != ".json":
        raise ValueError("Case preparation requires JSON source and output files")
    original = source.read_bytes().decode("utf-8")
    payload = json.loads(original)
    check_config(payload)

    def resources(value):
        if isinstance(value, dict):
            for key, child in value.items():
                if key != "file_name":
                    resources(child)
                    continue
                if child is None:
                    continue
                resource = Path(child)
                if not resource.is_absolute():
                    resource = source.parent / resource
                resource = service.jobs.path(resource.resolve().relative_to(service.root).as_posix())
                if resource.suffix.lower() not in (".stl", ".txt", ".dat"):
                    raise ValueError("Native projects admit STL and data resources")
                digest = hash_file(resource)
                relative = Path("assets") / (digest + resource.suffix.lower())
                destination = output.parent / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                temporary = None
                try:
                    with tempfile.NamedTemporaryFile(dir=destination.parent, delete=False) as handle:
                        temporary = Path(handle.name)
                    shutil.copyfile(resource, temporary)
                    if hash_file(temporary) != digest:
                        raise ValueError("Resource changed while preparing the case")
                    temporary.replace(destination)
                finally:
                    if temporary is not None:
                        temporary.unlink(missing_ok=True)
                value[key] = relative.as_posix()
        elif isinstance(value, list):
            for child in value:
                resources(child)

    resources(payload)
    check_config(payload)
    output.parent.mkdir(parents=True, exist_ok=True)
    AgentTaskStore._atomic_write(output, format_json_like_source(payload, original, source))


def render(service, arguments):
    import numpy as np
    import pyvista as pv

    jobs = service.jobs
    job_id = jobs.resolve(arguments["reference"])
    publication = jobs.results(job_id)
    if not publication["available"]:
        raise ValueError("No published frames available for rendering")
    if "manifest" not in publication:
        raise ValueError("Select a native geometry or particle job for rendering")
    directory = jobs.job_path(job_id) / "result"
    manifest = json.loads((directory / publication["manifest"]).read_text(encoding="utf-8"))
    from sphinxsim.config.schemas import SimulationConfig
    from sphinxsim.visualization.preview import _body_colour
    config = SimulationConfig.model_validate_json((jobs.job_path(job_id) / "effective-config.json").read_text(encoding="utf-8"))
    output = jobs.path(arguments["output"], exists=False)
    output.parent.mkdir(parents=True, exist_ok=True)
    plotter = pv.Plotter(off_screen=True)
    try:
        for geometry in manifest["geometry"]:
            positions = np.fromfile(directory / geometry["positions"]["file"], dtype="<f4").reshape(-1, 3)
            triangles = np.fromfile(directory / geometry["indices"]["file"], dtype="<u4").reshape(-1, 3)
            faces = np.column_stack((np.full(len(triangles), 3), triangles)).ravel()
            plotter.add_mesh(pv.PolyData(positions, faces), color="gray", opacity=.2)
        for body, frames in manifest["bodies"].items():
            if frames:
                plotter.add_mesh(pv.read(directory / frames[-1]["source"]), style="points",
                                 color=_body_colour(body, config), point_size=4, label=body)
        if any(manifest["bodies"].values()):
            plotter.add_legend()
        if manifest["dimension"] == 2:
            plotter.view_xy()
            plotter.enable_parallel_projection()
        plotter.reset_camera()
        plotter.screenshot(str(output))
    finally:
        plotter.close()
    return {
        "file": arguments["output"],
        "bytes": output.stat().st_size,
        "sha256": hash_file(output),
        "content": [{"type": "image", "mimeType": "image/png",
                     "data": base64.b64encode(output.read_bytes()).decode("ascii")}],
    }
