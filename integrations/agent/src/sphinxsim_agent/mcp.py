"""Official SDK MCP stdio adapter; no proprietary handshake or remote listener."""
import asyncio
import base64
import json
from urllib.parse import urlparse, unquote
from pathlib import Path

from mcp import types
from mcp.server import Server
from mcp.server.stdio import stdio_server
from importlib.resources import files

from .service import TOOLS


async def serve(service):
    server = Server("sphinxsim-agent")

    @server.list_tools()
    async def list_tools():
        return [types.Tool(name=name, description=description, inputSchema=schema, outputSchema={"type": "object"})
                for name, (description, schema) in TOOLS.items() if name != "desktop" or service.desktop]

    @server.call_tool()
    async def call_tool(name, arguments):
        try:
            result = await asyncio.to_thread(service.call, name, arguments or {})
            images = []
            def references(value):
                if isinstance(value, dict):
                    if value.get("type") == "image" and isinstance(value.get("data"), str):
                        base64.b64decode(value["data"], validate=True)
                        images.append(types.ImageContent(type="image", mimeType=value["mimeType"], data=value["data"]))
                        value.clear()
                        value.update(type="imageReference", contentIndex=len(images))
                        return
                    for key, child in value.items():
                        if key == "resource" and isinstance(child, str) and child.startswith("file:"):
                            relative = Path(unquote(urlparse(child).path)).relative_to(service.jobs.directory)
                            value[key] = f"sphinxsim://jobs/{relative.parts[0]}/artifacts/{relative.name}"
                        else:
                            references(child)
                elif isinstance(value, list):
                    for child in value:
                        references(child)
            references(result)
            return types.CallToolResult(content=[types.TextContent(type="text", text=json.dumps(result, allow_nan=False)), *images], structuredContent=result)
        except Exception as exc:
            return types.CallToolResult(isError=True, content=[types.TextContent(type="text", text=str(exc)[:1000])])

    @server.list_resources()
    async def list_resources():
        return [types.Resource(uri="sphinxsim://schemas/contracts-v1", name="SPHinXsim agent contracts v1", mimeType="application/schema+json")]

    @server.list_resource_templates()
    async def list_resource_templates():
        return [types.ResourceTemplate(uriTemplate="sphinxsim://jobs/{jobId}/artifacts/{name}", name="Verified job artifact", description="Published artifacts only, at most 1 MiB; large files remain outside model context")]

    @server.read_resource()
    async def read_resource(uri):
        from mcp.server.lowlevel.helper_types import ReadResourceContents
        if str(uri) == "sphinxsim://schemas/contracts-v1":
            return [ReadResourceContents(content=files("sphinxsim_agent").joinpath("schemas/contracts-v1.json").read_text(), mime_type="application/schema+json")]
        parsed = urlparse(str(uri))
        parts = parsed.path.strip("/").split("/")
        if parsed.scheme != "sphinxsim" or parsed.netloc != "jobs" or len(parts) != 3 or parts[1] != "artifacts":
            raise ValueError("Unknown resource")
        raw = await asyncio.to_thread(service.jobs.artifact, parts[0], parts[2])
        return [ReadResourceContents(content=raw, mime_type="application/octet-stream")]

    async with stdio_server() as (read, write):
        await server.run(read, write, server.create_initialization_options())
