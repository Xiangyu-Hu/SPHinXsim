"""Builtin deterministic tools currently implemented by the Agent layer."""

from __future__ import annotations

from ..models import PlanAction
from .registry import (
    ToolCost,
    ToolDefinition,
    ToolRegistry,
    ToolSideEffect,
)
from .schemas import (
    AnalyzeRunoutInput,
    AnalyzeRunoutOutput,
    CompareSweepResultsInput,
    CompareSweepResultsOutput,
    PrepareCaseInput,
    PrepareCaseOutput,
    RunCaseInput,
    RunCaseOutput,
    SetFrictionAngleInput,
    SetFrictionAngleOutput,
    ValidateCaseInput,
    ValidateCaseOutput,
)


def build_builtin_tool_registry() -> ToolRegistry:
    """Return contracts for every deterministic tool implemented so far."""
    registry = ToolRegistry()
    definitions = [
        ToolDefinition(
            name="prepare_case",
            description="Copy an existing JSON configuration into an isolated task workspace.",
            action=PlanAction.PREPARE_CASE,
            input_model=PrepareCaseInput,
            output_model=PrepareCaseOutput,
            cost=ToolCost.LOW,
            side_effect=ToolSideEffect.WRITES_CASE,
            tags=("case", "filesystem"),
        ),
        ToolDefinition(
            name="set_friction_angle",
            description="Set a plastic-continuum body's friction angle in degrees.",
            action=PlanAction.UPDATE_CASE,
            input_model=SetFrictionAngleInput,
            output_model=SetFrictionAngleOutput,
            cost=ToolCost.LOW,
            side_effect=ToolSideEffect.WRITES_CASE,
            tags=("case", "material", "friction_angle"),
        ),
        ToolDefinition(
            name="validate_case",
            description="Validate a JSON configuration with the existing SPHinXsim CLI.",
            action=PlanAction.VALIDATE_CASE,
            input_model=ValidateCaseInput,
            output_model=ValidateCaseOutput,
            cost=ToolCost.LOW,
            side_effect=ToolSideEffect.READ_ONLY,
            tags=("validation", "cli"),
        ),
        ToolDefinition(
            name="run_case",
            description="Run SPHinXsim with an isolated output directory and persist a RunRecord.",
            action=PlanAction.RUN_CASE,
            input_model=RunCaseInput,
            output_model=RunCaseOutput,
            cost=ToolCost.HIGH,
            side_effect=ToolSideEffect.STARTS_SIMULATION,
            deterministic=False,
            approval_required_by_default=True,
            tags=("simulation", "expensive", "cli"),
        ),
        ToolDefinition(
            name="analyze_runout",
            description="Read the final ASCII VTP state and calculate directional runout metrics.",
            action=PlanAction.ANALYZE_RESULT,
            input_model=AnalyzeRunoutInput,
            output_model=AnalyzeRunoutOutput,
            cost=ToolCost.MEDIUM,
            side_effect=ToolSideEffect.WRITES_ANALYSIS,
            tags=("analysis", "vtp", "runout"),
        ),
        ToolDefinition(
            name="compare_sweep_results",
            description=(
                "Compare completed runout analyses against a runout-extension target."
            ),
            action=PlanAction.COMPARE_RESULTS,
            input_model=CompareSweepResultsInput,
            output_model=CompareSweepResultsOutput,
            cost=ToolCost.LOW,
            side_effect=ToolSideEffect.WRITES_ANALYSIS,
            tags=("analysis", "sweep", "comparison"),
        ),
    ]
    for definition in definitions:
        registry.register(definition)
    return registry
