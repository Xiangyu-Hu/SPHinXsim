"""Native Agent operations through the public MCP SDK and job lifecycle."""
import asyncio
from contextlib import asynccontextmanager
import json
from pathlib import Path
import sys
from types import SimpleNamespace
from uuid import uuid4

import pytest

from sphinxsim.agent.analysis import AnalysisSpec
from sphinxsim.agent.analysis_plan import AnalysisPlan
from sphinxsim.agent.mcp_execution import MCPExecutionClient
from sphinxsim.runtime.client import MCPClient
from sphinxsim.runtime.export_vtp import hash_file
from sphinxsim.runtime.jobs import JobStore
from tests.test_runout_analyzer import _write_final_vtp


def test_native_agent_operations_are_discovered_and_executed_over_stdio(tmp_path):
    client = MCPClient(tmp_path, environment={"SPHINXSIM_LLM_PROVIDER": "mock"}, timeout=60)
    async def exercise():
        async with client.session() as session:
            names = {tool.name for tool in (await session.list_tools()).tools}
            assert {"validate", "submit", "config_generate", "config_propose", "case_prepare",
                    "set_friction_angle", "analyze_result", "compare_results", "results_render"} <= names
            assert "workbench" not in names
            await client.request(session, "config_generate", {
                "description": "Create a 2D granular column collapse with plastic continuum.",
                "output": "cases/generated.json"})
            await client.request(session, "case_prepare", {
                "config_file": "cases/generated.json", "output": "working/case.json"})
            await client.request(session, "set_friction_angle", {
                "config_file": "working/case.json", "body_name": "GranularBody", "friction_angle_degrees": 20})
            validated = await client.request(session, "validate", {"config_file": "working/case.json"})
            assert validated["report"]["dimension"] == 2
            source = (tmp_path / "working/case.json").read_bytes()
            proposal = await client.request(session, "config_propose", {
                "configuration": json.loads(source), "description": "Change end time to 0.08 seconds"})
            assert proposal["configuration"]["solver_parameters"]["end_time"] == .08
            assert proposal["applied"] is False
            assert (tmp_path / "working/case.json").read_bytes() == source

            output = tmp_path / "frames"
            output.mkdir()
            _write_final_vtp(output / "GranularBody_0001.vtp")
            plan = AnalysisPlan(builtin=AnalysisSpec(body_name="GranularBody", direction_vector=(1, 0)))
            analyzed = await client.request(session, "analyze_result", {
                "config_file": "working/case.json", "output_dir": "frames",
                "analysis_plan": plan.model_dump(mode="json")})
            assert analyzed["metrics"]["final_front"] == .4
            compared = await client.request(session, "compare_results", {
                "sweep_id": "sweep", "target_value": analyzed["metrics"]["runout_extension"],
                "samples": [{"friction_angle_degrees": 20, "run_id": "run", "analysis_id": "analysis",
                             "metrics": analyzed["metrics"]}]})
            assert compared["best_absolute_error"] == 0
            rejected = await session.call_tool("validate", {"config_file": "../outside.json"})
            assert rejected.isError
    asyncio.run(exercise())


class JobSession:
    def __init__(self, config, *, cancel_marker=None):
        self.calls = []
        self.cancel_marker = cancel_marker
        self.reference = {"job_id": str(uuid4()), "request_sha256": "a" * 64}
        self.project = {"entry_file": config.name, "files": [
            {"path": config.name, "sha256": hash_file(config), "bytes": config.stat().st_size}]}
        self.state = "queued"

    async def call_tool(self, name, arguments):
        self.calls.append((name, arguments))
        if name == "project_inspect":
            result = self.project
        elif name == "submit":
            result = {"state": "queued", "reference": self.reference}
        elif name == "job_status":
            if self.cancel_marker and self.state != "canceled":
                self.cancel_marker.touch()
                self.state = "running"
            elif self.state != "canceled":
                self.state = "succeeded"
            result = {"state": self.state}
        elif name == "job_cancel":
            self.state = "canceled"
            result = {"state": "canceled"}
        elif name == "results_inspect":
            result = {"available": True, "quality": {"state": "checked"}}
        else:
            raise AssertionError(name)
        return SimpleNamespace(isError=False, structuredContent=result)


@pytest.mark.parametrize("cancel", [False, True])
def test_agent_uses_native_submission_references_and_explicit_cancellation(tmp_path, monkeypatch, cancel):
    config = tmp_path / "case.json"
    config.write_text("{}")
    control = tmp_path / "control"
    control.mkdir()
    session = JobSession(config, cancel_marker=control / "cancel.request" if cancel else None)
    client = MCPExecutionClient(cwd=tmp_path, control_dir=control, default_timeout=10)
    @asynccontextmanager
    async def connection():
        yield session
    monkeypatch.setattr(client, "session", connection)

    result = client.run(config, output_dir=tmp_path / "run")

    names = [name for name, _ in session.calls]
    assert "submit" in names
    assert result.cancelled is cancel
    assert result.succeeded is not cancel
    reference = json.loads((tmp_path / "run/job-reference.json").read_text())
    assert reference["reference"] == session.reference
    record = json.loads(Path(result.attempt_path).read_text())
    assert record["transport"] == "mcp"
    assert record["reference"] == session.reference
    if cancel:
        assert names.count("job_cancel") == 1
    else:
        assert "results_inspect" in names
        assert client.run(config, output_dir=tmp_path / "run").succeeded
        assert [name for name, _ in session.calls].count("submit") == 1


def test_job_supervisor_cancels_owned_process_on_windows_and_posix(tmp_path, monkeypatch):
    from sphinxsim.runtime import job_worker
    real_popen = job_worker.subprocess.Popen
    children = []
    def spawn(_command, **kwargs):
        process = real_popen([sys.executable, "-c", "import time; time.sleep(30)"], **kwargs)
        children.append(process)
        return process
    (tmp_path / "job.json").write_text(json.dumps({
        "kind": "run", "state": "queued", "wall_seconds": 10}))
    (tmp_path / "cancel.json").write_text('{"requested":true}')
    monkeypatch.setattr(job_worker.subprocess, "Popen", spawn)
    monkeypatch.setattr(sys, "argv", ["job_worker", str(tmp_path)])

    job_worker.main()

    assert json.loads((tmp_path / "job.json").read_text())["state"] == "canceled"
    assert children and children[0].poll() is not None


def test_lost_submission_reply_reuses_the_persisted_submission_id(tmp_path, monkeypatch):
    config = tmp_path / "case.json"
    config.write_text("{}")
    session = JobSession(config)
    original_call = session.call_tool
    submission_ids = []
    async def unreliable(name, arguments):
        if name == "submit":
            submission_ids.append(arguments["submission_id"])
            if len(submission_ids) == 1:
                raise ValueError("Submission reply was lost")
        return await original_call(name, arguments)
    session.call_tool = unreliable
    client = MCPExecutionClient(cwd=tmp_path)
    @asynccontextmanager
    async def connection():
        yield session
    monkeypatch.setattr(client, "session", connection)

    assert not client.run(config, output_dir=tmp_path / "run").succeeded
    assert client.run(config, output_dir=tmp_path / "run").succeeded
    assert len(submission_ids) == 2
    assert submission_ids[0] == submission_ids[1]


def test_native_job_store_accepts_submission_and_resolves_complete_reference(tmp_path, monkeypatch):
    from sphinxsim.runtime import jobs as module
    source = Path(__file__).parents[1] / "test_simulation/test_2d_simulation/data/column_collapse.json"
    (tmp_path / "case.json").write_bytes(source.read_bytes())
    monkeypatch.setattr(module.subprocess, "Popen", lambda *args, **kwargs: None)
    jobs = JobStore(tmp_path)
    project = jobs.inspect_project("case.json")
    result = jobs.submit("case.json", expected_project=project, submission_id=str(uuid4()))
    assert jobs.resolve(result["reference"]) == result["reference"]["job_id"]
    assert jobs.cancel(result["reference"]["job_id"])["cancellation_requested"]


def test_published_job_analysis_uses_frozen_configuration_and_verified_assets(tmp_path):
    from sphinxsim.runtime.export_vtp import atomic_json
    source = Path(__file__).parents[1] / "test_simulation/test_2d_simulation/data/column_collapse.json"
    jobs = JobStore(tmp_path)
    job_id = str(uuid4())
    work = jobs.directory / job_id
    project = work / "project"
    project.mkdir(parents=True)
    (project / "case.json").write_bytes(source.read_bytes())
    snapshot = {"entry_file": "case.json", "files": [{"path": "case.json",
        "bytes": source.stat().st_size, "sha256": hash_file(source)}]}
    atomic_json(work / "request.json", {"version": 1, "kind": "run", "project": snapshot})
    atomic_json(work / "job.json", {"kind": "run", "state": "succeeded"})
    directory = work / "result"
    directory.mkdir()
    frame = directory / "frame.vtp"
    _write_final_vtp(frame)
    asset = directory / f"source-{hash_file(frame)}.vtp"
    frame.rename(asset)
    atomic_json(directory / "manifest.json", {"version": 2, "dimension": 2, "geometry": [],
        "bodies": {"GranularBody": [{"time": .8, "source": asset.name}]}})
    atomic_json(work / "published.json", {"state": "succeeded", "snapshot": "manifest.json"})
    (work / "effective-config.json").write_bytes(source.read_bytes())
    output = tmp_path / "run"
    output.mkdir()
    reference = jobs.reference(job_id)
    atomic_json(output / "job-reference.json", {"workspace": str(tmp_path), "reference": reference})
    changed = json.loads(source.read_text())
    changed["geometries"]["shapes"][0]["upper_bound"][0] = .3
    atomic_json(tmp_path / "case.json", changed)
    plan = AnalysisPlan(builtin=AnalysisSpec(body_name="GranularBody", direction_vector=(1, 0)))
    client = MCPClient(tmp_path)

    async def exercise():
        async with client.session() as session:
            inspected = await client.request(session, "results_inspect", {"reference": reference})
            assert inspected["resource"] == f"sphinxsim://jobs/{job_id}/artifacts/manifest.json"
            arguments = {"config_file": "case.json", "output_dir": "run", "analysis_plan": plan.model_dump(mode="json")}
            result = await client.request(session, "analyze_result", arguments)
            assert result["metrics"]["initial_front"] == .2
            assert result["metrics"]["runout_extension"] == .2
            asset.write_bytes(asset.read_bytes() + b"corrupted")
            rejected = await session.call_tool("analyze_result", arguments)
            assert rejected.isError
            assert "hash mismatch" in rejected.content[0].text
    asyncio.run(exercise())


def test_case_preparation_stages_relative_resources_for_native_project_inventory(tmp_path):
    from sphinxsim.operations import Operations
    source = Path(__file__).parents[1] / "test_simulation/test_2d_simulation/data/column_collapse.json"
    config = json.loads(source.read_text())
    config["geometries"]["shapes"][0]["file_name"] = None
    config["geometries"]["shapes"][1]["polygons"] = [{
        "operation": "union", "type": "data_file", "file_name": "boundary.dat"}]
    original = json.dumps(config, indent=2).replace("\n", "\r\n").encode("utf-8")
    (tmp_path / "case.json").write_bytes(original)
    data = "0 0\n1 0\n1 1\n0 0\n"
    (tmp_path / "boundary.dat").write_text(data)
    operations = Operations(tmp_path)

    prepared = operations.call("case_prepare", {"config_file": "case.json", "output": "working/case.json"})

    candidate = json.loads((tmp_path / prepared["config_file"]).read_text())
    prepared_bytes = (tmp_path / prepared["config_file"]).read_bytes()
    assert b"\r\n" in prepared_bytes
    assert b"\r\r\n" not in prepared_bytes
    resource = candidate["geometries"]["shapes"][1]["polygons"][0]["file_name"]
    assert (tmp_path / "working" / resource).read_text() == data
    project = operations.call("project_inspect", {"config_file": prepared["config_file"]})
    assert resource in {item["path"] for item in project["files"]}
    assert json.loads((tmp_path / "case.json").read_text()) == config
    assert (tmp_path / "case.json").read_bytes() == original


def test_atomic_job_status_retries_a_windows_reader_sharing_violation(tmp_path, monkeypatch):
    from sphinxsim.runtime import export_vtp
    path = tmp_path / "job.json"
    path.write_text('{"state":"queued"}')
    original_replace = Path.replace
    replacements = []
    def replace(source, target):
        replacements.append(target)
        if len(replacements) == 1:
            raise PermissionError("Windows reader temporarily holds the destination")
        return original_replace(source, target)
    monkeypatch.setattr(export_vtp, "os", SimpleNamespace(name="nt"))
    monkeypatch.setattr(Path, "replace", replace)

    export_vtp.atomic_json(path, {"state": "running"})

    assert json.loads(path.read_text()) == {"state": "running"}
    assert len(replacements) == 2
    assert not list(tmp_path.glob("*.tmp"))


def test_concurrent_retries_submit_one_frozen_native_job(tmp_path, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from sphinxsim.runtime import jobs as module
    source = Path(__file__).parents[1] / "test_simulation/test_2d_simulation/data/column_collapse.json"
    (tmp_path / "case.json").write_bytes(source.read_bytes())
    monkeypatch.setattr(module.subprocess, "Popen", lambda *args, **kwargs: None)
    jobs = JobStore(tmp_path)
    project = jobs.inspect_project("case.json")
    submission_id = str(uuid4())
    def submit(_):
        return jobs.submit("case.json", expected_project=project, submission_id=submission_id)

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(submit, range(2)))

    assert results[0]["reference"] == results[1]["reference"]
    assert len(list(jobs.directory.glob("*/request.json"))) == 1
