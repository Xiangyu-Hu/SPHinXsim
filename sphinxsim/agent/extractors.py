"""Registered deterministic extractors for Agent simulation artifacts."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from pydantic import BaseModel, ValidationError

from .analysis import AnalysisError, AnalysisSpec, RunoutAnalyzer, RunoutResult


class ExtractorRegistryError(ValueError):
    """Raised for unknown, duplicate, or invalid extractor contracts."""


class AnalysisExtractor(Protocol):
    def analyze(
        self,
        config_path: str | Path,
        output_dir: str | Path,
        spec: BaseModel,
    ) -> BaseModel: ...


@dataclass(frozen=True)
class ExtractorDefinition:
    """One allow-listed analysis implementation and its typed contracts."""

    name: str
    description: str
    spec_model: type[BaseModel]
    result_model: type[BaseModel]
    extractor: AnalysisExtractor
    deterministic: bool = True


class ExtractorRegistry:
    """Validate and dispatch artifact analysis without case-name branching."""

    def __init__(self) -> None:
        self._definitions: dict[str, ExtractorDefinition] = {}

    def register(self, definition: ExtractorDefinition) -> None:
        if not definition.name or not definition.name.replace("_", "").isalnum():
            raise ExtractorRegistryError(
                "extractor names must contain letters, digits, and '_'"
            )
        if definition.name in self._definitions:
            raise ExtractorRegistryError(
                f"extractor already registered: {definition.name}"
            )
        self._definitions[definition.name] = definition

    def get(self, name: str) -> ExtractorDefinition:
        try:
            return self._definitions[name]
        except KeyError as exc:
            raise ExtractorRegistryError(f"unknown analysis extractor: {name}") from exc

    def names(self) -> tuple[str, ...]:
        return tuple(sorted(self._definitions))

    def export_capabilities(self) -> list[dict[str, Any]]:
        return [
            {
                "name": definition.name,
                "description": definition.description,
                "deterministic": definition.deterministic,
                "spec_schema": definition.spec_model.model_json_schema(),
                "result_schema": definition.result_model.model_json_schema(),
            }
            for definition in (
                self._definitions[name] for name in self.names()
            )
        ]

    def analyze(
        self,
        config_path: str | Path,
        output_dir: str | Path,
        raw_spec: dict[str, Any],
    ) -> BaseModel:
        if not isinstance(raw_spec, dict):
            raise ExtractorRegistryError("analysis extractor spec must be an object")
        name = raw_spec.get("metric")
        if not isinstance(name, str) or not name:
            raise ExtractorRegistryError(
                "analysis extractor spec requires a non-empty metric"
            )
        definition = self.get(name)
        try:
            spec = definition.spec_model.model_validate(raw_spec)
        except ValidationError as exc:
            raise ExtractorRegistryError(
                f"invalid spec for extractor {name}: {exc}"
            ) from exc
        try:
            result = definition.extractor.analyze(config_path, output_dir, spec)
            return definition.result_model.model_validate(result)
        except ValidationError as exc:
            raise ExtractorRegistryError(
                f"invalid output from extractor {name}: {exc}"
            ) from exc


def build_builtin_extractor_registry(
    *,
    runout_analyzer: RunoutAnalyzer | None = None,
) -> ExtractorRegistry:
    """Register the existing streaming runout analyzer as the first extractor."""
    registry = ExtractorRegistry()
    registry.register(
        ExtractorDefinition(
            name="runout",
            description=(
                "Stream the final VTP Position array and calculate initial front, "
                "final front, and runout extension."
            ),
            spec_model=AnalysisSpec,
            result_model=RunoutResult,
            extractor=runout_analyzer or RunoutAnalyzer(),
        )
    )
    return registry
