"""Public native CLI and real MCP stdio client, without platform services."""
import asyncio
import base64
import json
import os
from pathlib import Path
import subprocess
import sys
import time

import pytest

pytest.importorskip("mcp")
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


@pytest.mark.skipif(os.name != "posix", reason="Detached local jobs explicitly require POSIX")
def test_native_mcp_discovers_validates_and_generates_verified_cad(tmp_path):
    pytest.importorskip("cadquery")
    pytest.importorskip("vtkmodules")
    case = tmp_path / "case"
    case.mkdir()
    fixture = Path(__file__).parent / "fixtures/fixed-ship.json"
    (case / "config.json").write_bytes(fixture.read_bytes())

    async def exercise():
        args = ["-m", "sphinxsim", "mcp", "--root", str(tmp_path)]
        async with stdio_client(StdioServerParameters(command=sys.executable, args=args, env=dict(os.environ))) as streams:
            async with ClientSession(*streams) as client:
                assert (await client.initialize()).serverInfo.name == "sphinxsim"
                tools = {tool.name for tool in (await client.list_tools()).tools}
                assert {"validate", "compile", "geometry", "submit", "job_continue", "results_inspect"} <= tools
                assert "desktop" not in tools
                valid = await client.call_tool("validate", {"config_file": "case/config.json"})
                assert valid.structuredContent["report"]["dimension"] == 3
                assert valid.structuredContent["configuration"]["gravity"] == [0, -9.81, 0]
                rejected = await client.call_tool("validate", {"config_file": "../outside.json"})
                assert rejected.isError
                started = await client.call_tool("geometry", {"recipe": {"version": 1,
                    "operations": [{"id": "body", "op": "box", "args": {"size": [1, .5, .2]}}], "output": "body"}})
                reference = started.structuredContent["reference"]
                for _ in range(150):
                    state = (await client.call_tool("job_status", {"reference": reference})).structuredContent
                    if state["state"] in ("succeeded", "failed", "interrupted"):
                        break
                    await asyncio.sleep(.2)
                assert state["state"] == "succeeded", state
                result = (await client.call_tool("results_inspect", {"reference": reference})).structuredContent
                assert result["geometry"]["volume"] == pytest.approx(.1)
                model = next(item for item in result["geometry"]["files"] if item["path"] == "model.stl")
                resource = await client.read_resource(model["resource"])
                from sphinxsim.runtime.jobs import JobStore
                path = JobStore(tmp_path).job_path(reference["job_id"]) / "cad/model.stl"
                assert base64.b64decode(resource.contents[0].blob) == path.read_bytes()
                with pytest.raises(Exception):
                    await client.read_resource(f"sphinxsim://jobs/{reference['job_id']}/artifacts/job.json")
                bad = {**reference, "request_sha256": "0" * 64}
                assert (await client.call_tool("job_cancel", {"reference": bad})).isError
                path.write_bytes(path.read_bytes() + b"corrupt")
                assert (await client.call_tool("results_inspect", {"reference": reference})).isError

    asyncio.run(exercise())


def test_cli_rejects_external_paths_with_parseable_json(tmp_path):
    child = subprocess.run([sys.executable, "-m", "sphinxsim", "project", "../outside.json",
        "--root", str(tmp_path), "--json"], capture_output=True, timeout=20)
    assert child.returncode == 1
    assert "forbidden" in json.loads(child.stdout)["error"]["message"]


@pytest.mark.skipif(os.name != "posix", reason="Detached local jobs explicitly require POSIX")
def test_cancel_is_a_real_supervisor_request_and_does_not_replay(tmp_path):
    from sphinxsim.operations import Operations
    operations = Operations(tmp_path)
    job = operations.call("geometry", {"recipe": {"version": 1, "operations": [
        {"id": "body", "op": "box", "args": {"size": [1, 1, 1]}}], "output": "body"}})
    reference = job["reference"]
    operations.call("job_cancel", {"reference": reference})
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        state = operations.call("job_status", {"reference": reference})
        if state["state"] == "canceled":
            break
        time.sleep(.1)
    assert state["state"] == "canceled", state
    assert operations.call("job_cancel", {"reference": reference})["state"] == "canceled"
    assert len(list(operations.jobs.directory.glob("*/request.json"))) == 1
