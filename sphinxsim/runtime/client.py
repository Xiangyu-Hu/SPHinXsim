"""Official SDK client for the native SPHinXsim MCP stdio service."""
from contextlib import asynccontextmanager
from datetime import timedelta
import asyncio
import os
from pathlib import Path
import sys


class MCPClient:
    def __init__(self, root, *, environment=None, timeout=300):
        self.root = Path(root).resolve(strict=True)
        self.environment = dict(environment or {})
        self.timeout = timeout

    @asynccontextmanager
    async def session(self):
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client

        environment = os.environ.copy()
        environment.update(self.environment)
        source_root = str(Path(__file__).resolve().parents[2])
        environment["PYTHONPATH"] = source_root + os.pathsep + environment.get("PYTHONPATH", "")
        environment["PYTHONIOENCODING"] = "utf-8"
        parameters = StdioServerParameters(command=sys.executable,
            args=["-m", "sphinxsim", "mcp", "--root", str(self.root)], env=environment)
        async with stdio_client(parameters) as (reader, writer):
            async with ClientSession(reader, writer, read_timeout_seconds=timedelta(seconds=self.timeout)) as session:
                await session.initialize()
                yield session

    @staticmethod
    async def request(session, name, arguments):
        result = await session.call_tool(name, arguments)
        if result.isError:
            raise ValueError("\n".join(item.text for item in result.content if item.type == "text"))
        if result.structuredContent is None:
            raise ValueError(f"MCP operation {name} returned no structured result")
        return result.structuredContent

    def call(self, name, arguments, *, timeout=None):
        async def execute():
            async with self.session() as session:
                return await self.request(session, name, arguments)
        async def bounded():
            return await asyncio.wait_for(execute(), timeout=timeout or self.timeout)
        return self._run(bounded())

    @staticmethod
    def _run(coroutine):
        try:
            return asyncio.run(coroutine)
        except Exception as error:
            while getattr(error, "exceptions", None):
                error = error.exceptions[0]
            if isinstance(error, (ValueError, OSError, TimeoutError)):
                raise error
            raise RuntimeError(f"Native MCP request failed: {error}") from error

    def inventory(self):
        async def discover():
            async with self.session() as session:
                response = await session.list_tools()
                return [{"name": tool.name, "description": tool.description, "input_schema": tool.inputSchema}
                        for tool in response.tools]
        return self._run(discover())

    def relative(self, value):
        path = Path(value)
        if not path.is_absolute():
            path = self.root / path
        return path.resolve().relative_to(self.root).as_posix()
