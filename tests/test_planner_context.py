"""Tests for deterministic planner context construction."""

from __future__ import annotations

from pathlib import Path

import pytest

from sphinxsim.agent import (
    PlannerContextBuilder,
    build_builtin_tool_registry,
    build_capability_registry,
    build_default_knowledge_base,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def make_builder(*, retrieval_limit: int = 5) -> PlannerContextBuilder:
    tools = build_builtin_tool_registry()
    capabilities = build_capability_registry(tools)
    knowledge = build_default_knowledge_base(PROJECT_ROOT, tools, capabilities)
    return PlannerContextBuilder(
        tools,
        capabilities,
        knowledge,
        retrieval_limit=retrieval_limit,
    )


def test_context_combines_allowlisted_tools_capabilities_and_evidence() -> None:
    context = make_builder().build(
        "Calibrate a column-collapse runout extension to 0.2 m by changing friction angle."
    )

    assert {item["name"] for item in context.tool_capabilities} == {
        "prepare_case",
        "set_friction_angle",
        "validate_case",
        "run_case",
        "analyze_runout",
        "compare_sweep_results",
    }
    assert context.parameters["friction_angle"]["input_units"] == ["degree"]
    assert context.metrics["runout"]["outputs"] == [
        "initial_front",
        "final_front",
        "runout_extension",
    ]
    evidence = {item.document_id: item for item in context.evidence}
    assert "domain.runout" in evidence
    assert "final_front minus initial_front" in evidence["domain.runout"].text
    assert len(context.knowledge_fingerprint) == 64


def test_context_is_bounded_and_rejects_empty_prompt() -> None:
    builder = make_builder(retrieval_limit=2)

    assert len(builder.build("Runout extension calibration.").evidence) <= 2
    with pytest.raises(ValueError, match="must not be empty"):
        builder.build("  ")
