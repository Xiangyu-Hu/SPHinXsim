"""Machine-readable registry for deterministic Agent tool contracts."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any

from pydantic import BaseModel, ValidationError

from ..models import PlanAction


class ToolRegistryError(ValueError):
    """Raised for unknown, duplicate, or invalid tool contracts."""


class ToolCost(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class ToolSideEffect(str, Enum):
    READ_ONLY = "read_only"
    WRITES_CASE = "writes_case"
    STARTS_SIMULATION = "starts_simulation"
    WRITES_ANALYSIS = "writes_analysis"


@dataclass(frozen=True)
class ToolDefinition:
    name: str
    description: str
    action: PlanAction
    input_model: type[BaseModel]
    output_model: type[BaseModel]
    cost: ToolCost
    side_effect: ToolSideEffect
    deterministic: bool = True
    approval_required_by_default: bool = False
    tags: tuple[str, ...] = ()

    def validate_input(self, value: Any) -> BaseModel:
        try:
            return self.input_model.model_validate(value)
        except ValidationError as exc:
            raise ToolRegistryError(f"invalid input for tool {self.name}: {exc}") from exc

    def validate_output(self, value: Any) -> BaseModel:
        try:
            return self.output_model.model_validate(value)
        except ValidationError as exc:
            raise ToolRegistryError(f"invalid output for tool {self.name}: {exc}") from exc

    def as_capability(self) -> dict[str, Any]:
        operations = {
            "prepare_case": ["case_prepare"], "set_friction_angle": ["set_friction_angle"],
            "validate_case": ["validate"], "run_case": ["project_inspect", "submit", "job_status", "results_inspect"],
            "analyze_result": ["analyze_result"], "analyze_runout": ["analyze_result"],
            "compare_sweep_results": ["compare_results"],
        }
        return {
            "name": self.name,
            "description": self.description,
            "plan_action": self.action.value,
            "input_schema": self.input_model.model_json_schema(),
            "output_schema": self.output_model.model_json_schema(),
            "cost": self.cost.value,
            "side_effect": self.side_effect.value,
            "deterministic": self.deterministic,
            "approval_required_by_default": self.approval_required_by_default,
            "tags": list(self.tags),
            "mcp_operations": operations.get(self.name, []),
        }


class ToolRegistry:
    """Register and validate the only tools an LLM may request."""

    def __init__(self) -> None:
        self._definitions: dict[str, ToolDefinition] = {}

    def register(self, definition: ToolDefinition) -> None:
        if not definition.name or not definition.name.replace("_", "").isalnum():
            raise ToolRegistryError("tool names must contain letters, digits, and '_'")
        if definition.name in self._definitions:
            raise ToolRegistryError(f"tool already registered: {definition.name}")
        self._definitions[definition.name] = definition

    def get(self, name: str) -> ToolDefinition:
        try:
            return self._definitions[name]
        except KeyError as exc:
            raise ToolRegistryError(f"unknown Agent tool: {name}") from exc

    def validate_input(self, name: str, value: Any) -> BaseModel:
        return self.get(name).validate_input(value)

    def names(self) -> tuple[str, ...]:
        return tuple(sorted(self._definitions))

    def definitions(self) -> tuple[ToolDefinition, ...]:
        return tuple(self._definitions[name] for name in self.names())

    def export_capabilities(self) -> list[dict[str, Any]]:
        return [definition.as_capability() for definition in self.definitions()]
