"""Real stdio MCP and CLI consumers; no GPU/Qt E2E is claimed here."""
import asyncio
import base64
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


class ProtocolTests(unittest.IsolatedAsyncioTestCase):
    async def test_external_mcp_discovers_validates_and_generates_verified_cad_without_platform_services(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            case = root / "case"
            case.mkdir()
            fixture = Path(__file__).parent / "fixtures/fixed-ship.json"
            (case / "config.json").write_bytes(fixture.read_bytes())
            args = ["-m", "sphinxsim_agent.cli", "--root", str(root), "mcp"]
            async with stdio_client(StdioServerParameters(command=sys.executable, args=args, env=dict(os.environ))) as streams:
                async with ClientSession(*streams) as client:
                    initialized = await client.initialize()
                    self.assertEqual(initialized.serverInfo.name, "sphinxsim-agent")
                    tools = (await client.list_tools()).tools
                    self.assertIn("submit", [tool.name for tool in tools])
                    self.assertNotIn("desktop", [tool.name for tool in tools])
                    capabilities = await client.call_tool("capabilities", {})
                    self.assertIn("loft", capabilities.structuredContent["modeling"]["operations"])
                    valid = await client.call_tool("validate", {"entry": "case/config.json"})
                    self.assertEqual(valid.structuredContent["report"]["dimension"], 3)
                    self.assertEqual(valid.structuredContent["configuration"]["gravity"], [0, -9.81, 0])
                    rejected = await client.call_tool("validate", {"entry": "../outside.json"})
                    self.assertTrue(rejected.isError)
                    forbidden = await client.call_tool("desktop", {"operation": "observe"})
                    self.assertTrue(forbidden.isError)
                    resource = await client.read_resource("sphinxsim://schemas/contracts-v1")
                    self.assertIn("execution", json.loads(resource.contents[0].text)["$defs"])
                    job = await client.call_tool("geometry_generate", {"recipe": {"version": 1, "operations": [
                        {"id": "outer", "op": "box", "args": {"size": [2, 1, 1]}},
                        {"id": "inner", "op": "box", "args": {"size": [1.8, .8, .8]}},
                        {"id": "shell", "op": "subtract", "args": {"inputs": ["outer", "inner"]}}
                    ], "output": "shell"}})
                    self.assertFalse(job.isError)
                    job_id = job.structuredContent["id"]
            # Closing the external Agent connection must not cancel persistent work.
            from sphinxsim_agent.jobs import JobStore
            store = JobStore(root)
            for _ in range(300):
                state = store.status(job_id)
                if state["state"] in ("failed", "succeeded"):
                    break
                await asyncio.sleep(.1)
            self.assertEqual(state["state"], "succeeded", store.logs(job_id))
            result = store.results(job_id)
            self.assertAlmostEqual(result["geometry"]["volume"], .848)
            async with stdio_client(StdioServerParameters(command=sys.executable, args=args, env=dict(os.environ))) as streams:
                async with ClientSession(*streams) as client:
                    await client.initialize()
                    templates = (await client.list_resource_templates()).resourceTemplates
                    self.assertIn("sphinxsim://jobs/{jobId}/artifacts/{name}", [item.uriTemplate for item in templates])
                    resumed = await client.call_tool("results_inspect", {"jobId": job_id})
                    artifact = next(item for item in resumed.structuredContent["geometry"]["files"] if item["path"] == "model.stl")
                    resource = await client.read_resource(artifact["resource"])
                    self.assertEqual(base64.b64decode(resource.contents[0].blob), (store.job_path(job_id) / "cad/model.stl").read_bytes())
                    with self.assertRaises(Exception):
                        await client.read_resource(f"sphinxsim://jobs/{job_id}/artifacts/job.json")
            stl = store.job_path(job_id) / "cad/model.stl"
            stl.write_bytes(stl.read_bytes() + b"corrupt")
            with self.assertRaisesRegex(ValueError, "hash mismatch"):
                store.results(job_id)

    async def test_cli_rejects_external_paths_and_keeps_unsupplied_defaults_native(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            args = root / "args.json"
            args.write_text(json.dumps({"entry": "/etc/passwd"}))
            child = subprocess.run([sys.executable, "-m", "sphinxsim_agent.cli", "--root", str(root), "validate", "--input", str(args)], capture_output=True, timeout=20)
            self.assertEqual(child.returncode, 1)
            self.assertIn("relative to", json.loads(child.stdout)["error"]["message"])
            self.assertFalse((root / ".sphinxsim/jobs/job.json").exists())


if __name__ == "__main__":
    unittest.main()
