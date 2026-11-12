"""Grounded, size-bounded context construction for structured planners."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from .capabilities import CapabilityRegistry
from .knowledge import LocalKnowledgeBase, RetrievedKnowledge
from .tools import ToolRegistry


class PlannerEvidence(BaseModel):
    """One attributable knowledge fragment provided to a planner."""

    model_config = ConfigDict(extra="forbid")

    document_id: str
    title: str
    text: str
    source: str
    score: float = Field(..., gt=0.0)


class PlannerContext(BaseModel):
    """Validated facts supplied to mock and production structured planners."""

    model_config = ConfigDict(extra="forbid")

    user_prompt: str = Field(..., min_length=1)
    tool_capabilities: list[dict[str, Any]]
    native_mcp_tools: list[dict[str, Any]] = Field(default_factory=list)
    simulation_dimensions: list[int]
    parameters: dict[str, dict[str, Any]]
    metrics: dict[str, dict[str, Any]]
    artifact_formats: dict[str, dict[str, Any]]
    evidence: list[PlannerEvidence]
    capability_version: str
    knowledge_version: str
    knowledge_fingerprint: str


class PlannerContextBuilder:
    """Combine registries and retrieved evidence without executing any tool."""

    def __init__(
        self,
        tool_registry: ToolRegistry,
        capabilities: CapabilityRegistry,
        knowledge: LocalKnowledgeBase,
        *,
        retrieval_limit: int = 5,
    ) -> None:
        if retrieval_limit < 1:
            raise ValueError("retrieval_limit must be at least one")
        registered = set(tool_registry.names())
        exported = {item["name"] for item in capabilities.tools}
        if registered != exported:
            raise ValueError("tool and capability registries are inconsistent")
        self.tool_registry = tool_registry
        self.capabilities = capabilities
        self.knowledge = knowledge
        self.retrieval_limit = retrieval_limit
        self.native_mcp_tools = None

    def build(self, user_prompt: str) -> PlannerContext:
        from sphinxsim.operations import TOOLS
        cleaned = user_prompt.strip()
        if not cleaned:
            raise ValueError("user_prompt must not be empty")
        query = (
            f"{cleaned} available tool capability parameter metric definition "
            "unit runout extension initial front final front calibration"
        )
        retrieved = self.knowledge.retrieve(query, limit=self.retrieval_limit)
        return PlannerContext(
            user_prompt=cleaned,
            tool_capabilities=self.tool_registry.export_capabilities(),
            native_mcp_tools=self.native_mcp_tools if self.native_mcp_tools is not None else [
                {"name": name, "description": description, "input_schema": schema}
                for name, (description, schema) in TOOLS.items() if name not in ("desktop", "workbench")],
            simulation_dimensions=list(self.capabilities.simulation_dimensions),
            parameters=self.capabilities.parameters,
            metrics=self.capabilities.metrics,
            artifact_formats=self.capabilities.artifact_formats,
            evidence=[self._evidence(item) for item in retrieved],
            capability_version=self.capabilities.version,
            knowledge_version=self.knowledge.version,
            knowledge_fingerprint=self.knowledge.manifest.fingerprint,
        )

    @staticmethod
    def _evidence(item: RetrievedKnowledge) -> PlannerEvidence:
        document = item.document
        return PlannerEvidence(
            document_id=document.document_id,
            title=document.title,
            text=document.text,
            source=document.source,
            score=item.score,
        )
