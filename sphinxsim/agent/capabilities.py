"""Machine-readable capabilities exposed to future RAG and LLM planning."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict

from .tools import ToolRegistry


class CapabilityRegistry(BaseModel):
    """Versioned description of currently implemented AgentCAE capabilities."""

    model_config = ConfigDict(extra="forbid")

    version: str
    tools: list[dict[str, Any]]
    simulation_dimensions: list[int]
    parameters: dict[str, dict[str, Any]]
    metrics: dict[str, dict[str, Any]]
    artifact_formats: dict[str, dict[str, Any]]


def build_capability_registry(tool_registry: ToolRegistry) -> CapabilityRegistry:
    """Build capabilities from tool schemas plus current verified domain support."""
    return CapabilityRegistry(
        version="1.0",
        tools=tool_registry.export_capabilities(),
        simulation_dimensions=[2, 3],
        parameters={
            "friction_angle": {
                "tool": "set_friction_angle",
                "supported_materials": ["plastic_continuum"],
                "input_units": ["degree"],
                "storage_unit": "radian",
                "input_range": {"minimum": 0.0, "maximum_exclusive": 90.0},
            }
        },
        metrics={
            "runout": {
                "tool": "analyze_runout",
                "outputs": ["initial_front", "final_front", "runout_extension"],
                "front_methods": ["maximum"],
                "initial_front_sources": ["bounding_box_geometry"],
                "geometry_representations": ["inline_bounds", "primitive_reference"],
                "direction": "normalized_2d_or_3d_vector",
                "unit": "m",
            }
        },
        artifact_formats={
            "simulation_config": {"format": "json"},
            "particle_state": {
                "format": "vtp",
                "encoding": ["ascii"],
                "required_array": "Position",
            },
        },
    )
