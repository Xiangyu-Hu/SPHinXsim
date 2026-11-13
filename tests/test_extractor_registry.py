"""Tests for registered deterministic artifact extractors."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import BaseModel, ConfigDict

from sphinxsim.agent import (
    ExtractorDefinition,
    ExtractorRegistry,
    ExtractorRegistryError,
    build_builtin_extractor_registry,
)
from sphinxsim.agent.analysis import AnalysisSpec, RunoutResult


class StubExtractor:
    def analyze(self, config_path, output_dir, spec):
        return RunoutResult(
            body_name=spec.body_name,
            state_file=str(Path(output_dir) / "state.vtp"),
            simulation_time=1.0,
            particle_count=2,
            direction_vector=(1.0, 0.0),
            initial_front=0.2,
            final_front=0.4,
            runout_extension=0.2,
        )


def test_registry_dispatches_typed_extractor() -> None:
    registry = ExtractorRegistry()
    registry.register(
        ExtractorDefinition(
            name="runout",
            description="Test runout extractor.",
            spec_model=AnalysisSpec,
            result_model=RunoutResult,
            extractor=StubExtractor(),
        )
    )

    result = registry.analyze(
        "case.json",
        "run",
        {
            "metric": "runout",
            "body_name": "GranularBody",
            "direction_vector": [1.0, 0.0],
        },
    )

    assert result.runout_extension == pytest.approx(0.2)


def test_builtin_registry_contains_streaming_runout() -> None:
    definition = build_builtin_extractor_registry().get("runout")

    assert definition.spec_model is AnalysisSpec
    assert definition.result_model is RunoutResult
    assert definition.deterministic is True
    capability = build_builtin_extractor_registry().export_capabilities()[0]
    assert capability["name"] == "runout"
    assert capability["spec_schema"]["additionalProperties"] is False


def test_registry_rejects_unknown_and_duplicate_extractors() -> None:
    registry = build_builtin_extractor_registry()

    with pytest.raises(ExtractorRegistryError, match="unknown"):
        registry.analyze("case.json", "run", {"metric": "velocity_peak"})
    with pytest.raises(ExtractorRegistryError, match="already registered"):
        registry.register(registry.get("runout"))


def test_registry_rejects_extra_spec_fields_before_extractor_runs() -> None:
    with pytest.raises(ExtractorRegistryError, match="invalid spec"):
        build_builtin_extractor_registry().analyze(
            "case.json",
            "run",
            {
                "metric": "runout",
                "body_name": "GranularBody",
                "direction_vector": [1.0, 0.0],
                "python_code": "print('unsafe')",
            },
        )
