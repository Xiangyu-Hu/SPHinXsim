"""Tests for versioned local Agent knowledge and retrieval."""

from __future__ import annotations

from pathlib import Path

import pytest

from sphinxsim.agent.capabilities import build_capability_registry
from sphinxsim.agent.knowledge import (
    KnowledgeError,
    LocalKnowledgeBase,
    build_default_knowledge_base,
)
from sphinxsim.agent.tools import build_builtin_tool_registry


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def make_knowledge_base() -> LocalKnowledgeBase:
    tools = build_builtin_tool_registry()
    return build_default_knowledge_base(
        PROJECT_ROOT,
        tools,
        build_capability_registry(tools),
    )


def test_retrieves_friction_angle_units_and_constraints() -> None:
    result = make_knowledge_base().retrieve(
        "friction angle degree radian material valid range",
        limit=3,
    )

    assert result
    assert result[0].document.document_id == "domain.friction_angle"
    assert "friction" in result[0].matched_terms


def test_retrieves_streaming_vtp_runout_definition() -> None:
    result = make_knowledge_base().retrieve(
        "stream VTP Position final front runout extension initial front",
        limit=3,
    )

    assert result[0].document.document_id == "domain.runout"
    assert "vtp" in result[0].matched_terms


def test_manifest_and_snapshot_round_trip(tmp_path: Path) -> None:
    knowledge = make_knowledge_base()
    saved_path = knowledge.save(tmp_path / "agent_knowledge.json")
    loaded = LocalKnowledgeBase.load(saved_path)

    assert loaded.manifest == knowledge.manifest
    assert loaded.retrieve("bounded calibration midpoint", limit=1)[0].document.document_id == (
        "workflow.calibration"
    )


def test_retrieval_rejects_empty_query() -> None:
    with pytest.raises(KnowledgeError, match="searchable terms"):
        make_knowledge_base().retrieve("---")


def test_upstream_workflow_and_mcp_guides_are_available_to_planners():
    knowledge = make_knowledge_base()
    sources = set(knowledge.manifest.sources)

    assert "docs/agent-workflows.md" in sources
    assert "docs/mcp.md" in sources
    assert len({document.document_id for document in knowledge.documents}) == len(knowledge.documents)
    assert any("POSIX" in document.text for document in knowledge.documents)
