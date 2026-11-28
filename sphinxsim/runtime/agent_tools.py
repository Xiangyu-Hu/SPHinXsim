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
    if name == "templates":
        from sphinxsim.templates import list_templates
        return {"templates": list_templates()}
    if name == "template_init":
        from sphinxsim.templates import init_template
        output = jobs.path(arguments["output"], exists=False)
        result = init_template(arguments["template"], output, overwrite=bool(arguments.get("overwrite", False)))
        return {**result, "config_file": arguments["output"]}
    if name == "config_read":
        return read_config_or_template(service, arguments)
    if name == "config_patch":
        return patch_config_file(service, arguments)
    if name == "config_restore":
        return restore_config_file(service, arguments)
    if name == "search_docs":
        return search_local_docs(arguments)
    if name == "results_summary":
        from sphinxsim.results import render_run_results, summarize_run_results
        config_path = jobs.path(arguments["config_file"])
        output_dir = jobs.path(arguments["output_dir"])
        summary = summarize_run_results(config_path, output_dir)
        if arguments.get("screenshot"):
            shot_path = jobs.path(arguments["screenshot"], exists=False)
            summary["rendered"] = render_run_results(
                config_path,
                output_dir,
                shot_path,
                color_by=arguments.get("color_by"),
                frame=arguments.get("frame", "latest"),
            )
        return summary
    raise ValueError("Unknown authoring or analysis operation")


def read_config_or_template(service, arguments):
    from sphinxsim.config.migrations import migrate_legacy_configuration
    from sphinxsim.config.schemas import SimulationConfig
    from sphinxsim.templates import resolve_template

    has_file = bool(arguments.get("config_file"))
    has_tmpl = bool(arguments.get("template"))
    if has_file == has_tmpl:
        raise ValueError("Provide exactly one of 'config_file' or 'template'")

    if has_tmpl:
        entry = resolve_template(arguments["template"])
        path = Path(entry["source_path"]).resolve()
        label = entry["id"]
    else:
        path = service.jobs.path(arguments["config_file"])
        label = arguments["config_file"]

    if path.stat().st_size > 1 << 20:
        raise ValueError("Configuration file exceeds 1 MiB")
    raw_text = path.read_text(encoding="utf-8", errors="replace")
    valid = False
    report = None
    error = None
    try:
        parsed = json.loads(raw_text)
        migrated = migrate_legacy_configuration(parsed)
        normalized, report = check_config(migrated)
        SimulationConfig.model_validate(normalized)
        valid = True
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"

    return {
        "source": label,
        "text": raw_text,
        "valid": valid,
        "report": report,
        "error": error,
        "sha256": hash_file(path),
    }


def restore_config_file(service, arguments):
    import difflib
    from sphinxsim.agent.store import AgentTaskStore
    from sphinxsim.config.migrations import migrate_legacy_configuration
    from sphinxsim.config.schemas import SimulationConfig

    path = service.jobs.path(arguments["config_file"])
    backup_path = path.with_name(path.name + ".bak")
    if backup_path.is_symlink() or not backup_path.is_file():
        raise ValueError(f"No backup file found for {arguments['config_file']} ({backup_path.name})")

    current_text = path.read_bytes().decode("utf-8")
    backup_text = backup_path.read_bytes().decode("utf-8")
    migrated = migrate_legacy_configuration(json.loads(backup_text))
    normalized, report = check_config(migrated)
    SimulationConfig.model_validate(normalized)

    diff = "".join(
        difflib.unified_diff(
            current_text.splitlines(True),
            backup_text.splitlines(True),
            fromfile=f"a/{arguments['config_file']}",
            tofile=f"b/{arguments['config_file']} (restored)",
        )
    )
    dry_run = bool(arguments.get("dry_run", False))
    if not dry_run:
        AgentTaskStore._atomic_write(backup_path, current_text)
        AgentTaskStore._atomic_write(path, backup_text)

    return {
        "config_file": arguments["config_file"],
        "backup_file": backup_path.relative_to(service.root).as_posix(),
        "restored": not dry_run,
        "dry_run": dry_run,
        "diff": diff,
        "report": report,
        "sha256": hash_file(path),
    }


def search_local_docs(arguments):
    from sphinxsim.agent.capabilities import build_capability_registry
    from sphinxsim.agent.knowledge import KnowledgeDocument, build_default_knowledge_base
    from sphinxsim.agent.tools import build_builtin_tool_registry
    from sphinxsim.templates import _default_project_root, list_templates

    root = _default_project_root()
    registry = build_builtin_tool_registry()
    capabilities = build_capability_registry(registry)
    kb = build_default_knowledge_base(root, registry, capabilities)

    extra_docs: list[KnowledgeDocument] = []
    docs_dir = root / "docs"
    if docs_dir.is_dir():
        for md_path in sorted(docs_dir.rglob("*.md")):
            try:
                rel = md_path.relative_to(root).as_posix()
                doc_id = "doc." + rel.replace("/", ".").replace(" ", "_")
                if any(existing.document_id == doc_id for existing in kb.documents):
                    continue
                text = md_path.read_text(encoding="utf-8", errors="replace")[:16000]
                if not text.strip():
                    continue
                title = next(
                    (line.lstrip("# ").strip() for line in text.splitlines() if line.startswith("#")),
                    md_path.stem,
                )
                extra_docs.append(
                    KnowledgeDocument(
                        document_id=doc_id,
                        title=title or md_path.stem,
                        text=text,
                        source=rel,
                        tags=["docs", md_path.parent.name],
                    )
                )
            except Exception:
                continue

    for tmpl in list_templates(root):
        doc_id = "template." + tmpl["id"].replace("/", ".")
        extra_docs.append(
            KnowledgeDocument(
                document_id=doc_id,
                title=f"Template {tmpl['id']} ({tmpl['dimension']}D {tmpl['simulation_type']})",
                text=(
                    f"Reference template {tmpl['id']}: {tmpl['dimension']}D {tmpl['simulation_type']}. "
                    f"Bodies: {', '.join(tmpl['bodies'])}. "
                    f"Assets: {', '.join(tmpl['assets']) or 'none'}."
                ),
                source=tmpl["id"],
                tags=["template", tmpl["simulation_type"], f"{tmpl['dimension']}d"],
            )
        )

    from sphinxsim.agent.knowledge import LocalKnowledgeBase

    full_kb = LocalKnowledgeBase([*kb.documents, *extra_docs], version="1.1")
    limit = int(arguments.get("limit", 5))
    matches = full_kb.retrieve(arguments["query"], limit=limit)
    return {
        "query": arguments["query"],
        "matches": [
            {
                "id": item.document.document_id,
                "title": item.document.title,
                "source": item.document.source,
                "score": round(item.score, 3),
                "matched_terms": item.matched_terms,
                "excerpt": item.document.text[:1200],
            }
            for item in matches
        ],
    }


def patch_config_file(service, arguments):
    import difflib
    from sphinxsim.agent.store import AgentTaskStore
    from sphinxsim.config.migrations import migrate_legacy_configuration
    from sphinxsim.config.schemas import SimulationConfig
    from sphinxsim.config.update_patch import UpdatePatch, apply_update_patch
    from sphinxsim.json_editor.json_format import format_json_like_source
    from sphinxsim.llm import get_llm

    has_patch = arguments.get("patch") is not None
    has_config = arguments.get("configuration") is not None
    has_desc = bool(arguments.get("description"))
    if not (has_patch or has_config or has_desc):
        raise ValueError("config_patch requires 'patch', 'configuration', or 'description'")

    path = service.jobs.path(arguments["config_file"])
    original_text = path.read_bytes().decode("utf-8")

    if has_config:
        updated_raw = migrate_legacy_configuration(deepcopy(arguments["configuration"]))
        summary_ops = {"replace_configuration": 1}
        format_ref = original_text
        try:
            json.loads(original_text)
        except Exception:
            format_ref = json.dumps(updated_raw, indent=2) + "\n"
    else:
        raw_payload = migrate_legacy_configuration(json.loads(original_text))
        normalized, _ = check_config(raw_payload)
        current_model = SimulationConfig.model_validate(normalized)

        if has_patch:
            parsed_patch = UpdatePatch.model_validate(arguments["patch"])
        else:
            with redirect_stdout(sys.stderr):
                patch_payload = get_llm(arguments.get("provider")).update_patch(
                    current_model, arguments["description"], strict=True
                )
            parsed_patch = (
                patch_payload
                if isinstance(patch_payload, UpdatePatch)
                else UpdatePatch.model_validate(patch_payload)
            )

        patch_result = apply_update_patch(raw_payload, parsed_patch, strict=True)
        if patch_result.errors:
            raise ValueError("\n".join(patch_result.errors))
        updated_raw = patch_result.updated
        summary_ops = patch_result.summary
        format_ref = original_text

    checked, report = check_config(updated_raw)
    SimulationConfig.model_validate(checked)
    updated_text = format_json_like_source(updated_raw, format_ref, path)
    diff = "".join(
        difflib.unified_diff(
            original_text.splitlines(True),
            updated_text.splitlines(True),
            fromfile=f"a/{arguments['config_file']}",
            tofile=f"b/{arguments['config_file']}",
        )
    )
    dry_run = bool(arguments.get("dry_run", False))
    backup_rel = None
    if not dry_run and updated_text != original_text:
        backup_path = path.with_name(path.name + ".bak")
        AgentTaskStore._atomic_write(backup_path, original_text)
        AgentTaskStore._atomic_write(path, updated_text)
        backup_rel = backup_path.relative_to(service.root).as_posix()

    return {
        "config_file": arguments["config_file"],
        "changed": updated_text != original_text,
        "applied": not dry_run and updated_text != original_text,
        "dry_run": dry_run,
        "backup_file": backup_rel,
        "operations": summary_ops,
        "diff": diff,
        "report": report,
        "sha256": hash_file(path),
    }


def prepare_case(service, source, output):
    from sphinxsim.config.migrations import migrate_legacy_configuration
    from sphinxsim.agent.store import AgentTaskStore
    from sphinxsim.json_editor import format_json_like_source

    if source == output:
        raise ValueError("Select a working case file different from its source")
    if source.suffix.lower() != ".json" or output.suffix.lower() != ".json":
        raise ValueError("Case preparation requires JSON source and output files")
    original = source.read_bytes().decode("utf-8")
    payload = migrate_legacy_configuration(json.loads(original))
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
