"""Tests for structured deterministic analysis instructions."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from sphinxsim.agent.analysis import (
    AnalysisMetric,
    AnalysisSpec,
    FrontMethod,
    InitialFrontSource,
    TimeSelection,
)


def test_runout_analysis_spec_accepts_supported_contract() -> None:
    spec = AnalysisSpec(
        metric="runout",
        body_name="GranularBody",
        time_selection="final",
        direction_vector=(1.0, 0.0),
        initial_front_source="geometry",
        front_method="maximum",
        unit="m",
    )

    assert spec.metric == AnalysisMetric.RUNOUT
    assert spec.time_selection == TimeSelection.FINAL
    assert spec.initial_front_source == InitialFrontSource.GEOMETRY
    assert spec.front_method == FrontMethod.MAXIMUM


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("direction_vector", (0.0, 0.0)),
        ("direction_vector", (1.0,)),
        ("direction_vector", (1.0, 0.0, 0.0, 0.0)),
        ("unit", "mm"),
        ("metric", "unknown"),
        ("front_method", "percentile"),
    ],
)
def test_analysis_spec_rejects_unsupported_or_invalid_values(
    field: str,
    value: object,
) -> None:
    payload = {
        "metric": "runout",
        "body_name": "GranularBody",
        "direction_vector": (1.0, 0.0),
    }
    payload[field] = value

    with pytest.raises(ValidationError):
        AnalysisSpec.model_validate(payload)


def test_analysis_spec_rejects_unregistered_extra_fields() -> None:
    with pytest.raises(ValidationError, match="extra"):
        AnalysisSpec.model_validate(
            {
                "body_name": "GranularBody",
                "direction_vector": (1.0, 0.0),
                "python_code": "print('unsafe')",
            }
        )
