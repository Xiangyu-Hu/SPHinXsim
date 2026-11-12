"""Structured LLM tool selection uses the running native MCP inventory."""
import json

import pytest

from sphinxsim.llm.mcp_tools import MCPToolConversation


class Tools:
    def __init__(self):
        self.calls = []

    def inventory(self):
        return [{"name": "capabilities", "input_schema": {"type": "object"}}]

    def call(self, name, arguments):
        self.calls.append((name, arguments))
        return {"execution": {"detached_local_jobs": True}}


class Backend:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.payloads = []

    def complete_json(self, *, system_prompt, payload):
        self.payloads.append(payload)
        return next(self.responses)


class LLM:
    def chat(self, messages):
        self.messages = messages
        return "Native tools are available."


def test_llm_selects_discovered_tools_and_receives_actual_results():
    client, llm = Tools(), LLM()
    backend = Backend([{"tool": "capabilities", "arguments": {}}, {"tool": None, "arguments": {}}])
    conversation = MCPToolConversation(client, llm, "mock", backend=backend)

    answer = conversation.respond([{"role": "user", "content": "Which operations can I run?"}])

    assert client.calls == [("capabilities", {})]
    assert "Native tools" in answer
    evidence = next(item["content"] for item in llm.messages if item["role"] == "system")
    assert '"detached_local_jobs": true' in evidence.replace('\\"', '"')
    assert backend.payloads[0]["tools"][0]["name"] == "capabilities"


def test_unadvertised_tool_is_rejected_before_execution():
    client = Tools()
    backend = Backend([{"tool": "shell", "arguments": {"command": "anything"}}])
    with pytest.raises(Exception):
        MCPToolConversation(client, LLM(), "mock", backend=backend).respond([
            {"role": "user", "content": "Inspect capabilities"}])
    assert client.calls == []


def test_explicit_mcp_command_works_with_mock_provider():
    client = Tools()
    result = MCPToolConversation(client, LLM(), "mock").respond([
        {"role": "user", "content": "/mcp capabilities {}"}])
    assert json.loads(result)["execution"]["detached_local_jobs"] is True
