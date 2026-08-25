"""Tests for the product-facing AgentCAE facade and report."""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest

from sphinxsim.agent import (
    AgentCAE,
    AgentCAEAgentService,
    AgentRuntimeSettings,
    AgentServiceError,
    ExtractorPlanningContext,
    MockColumnCollapseCalibrationPlanner,
    MockExtractorPlannerClient,
)
from tests.test_deterministic_calibration_workflow import DeterministicArtifactAdapter


def make_facade(build_temp_path: Path) -> AgentCAE:
    return AgentCAE(
        Path(__file__).resolve().parents[1],
        build_temp_path / f"agent_cae_facade_{uuid4().hex}",
    )


def persist_completed_workflow(facade: AgentCAE, workflow_id: str) -> None:
    settings = AgentRuntimeSettings(
        workflow_id=workflow_id,
        require_approval=False,
    )
    facade.runtime.save_settings(settings)
    service = AgentCAEAgentService(
        facade.runtime.store,
        facade.runtime.database_path,
        MockColumnCollapseCalibrationPlanner(),
        DeterministicArtifactAdapter(),
        require_approval=False,
    )
    status = service.start(
        "Calibrate runout extension to 0.2 m.",
        workflow_id=workflow_id,
    )
    assert status.status.value == "converged"


def test_report_is_built_from_persisted_workflow_and_saved(
    build_temp_path: Path,
) -> None:
    facade = make_facade(build_temp_path)
    persist_completed_workflow(facade, "facade_report")

    report = facade.report("facade_report")

    assert report.succeeded is True
    assert report.plan_summary.case_name == "column_collapse"
    assert report.plan_summary.target_value == pytest.approx(0.2)
    assert report.plan_approved is False
    assert report.target_metric == "runout_extension"
    assert report.target_value == pytest.approx(0.2)
    assert report.best_result is not None
    assert report.best_result.absolute_error == pytest.approx(0.0)
    assert len(report.iterations) == 4
    assert report.iterations[0].task_id == "facade_report_iteration_001"
    assert report.iterations[0].metric_name == "runout_extension"
    saved = facade.runtime.workspace / "reports" / "facade_report.json"
    assert saved.is_file()
    assert "runout_extension" in saved.read_text(encoding="utf-8")


def test_facade_plans_persists_and_executes_safe_extractor(
    build_temp_path: Path,
) -> None:
    facade = make_facade(build_temp_path)
    response = {
        "extractor_id": "deposit_height",
        "description": "Extract the final deposit height.",
        "body_name": "GranularBody",
        "outputs": [
            {"output_name": "height_y", "component_index": 1, "reduction": "maximum"}
        ],
    }
    context = ExtractorPlanningContext(
        body_names=["GranularBody"],
        spatial_dimension=2,
        available_components={"x": 0, "y": 1},
    )
    spec = facade.plan_extractor(
        "Measure final deposit height.",
        context,
        MockExtractorPlannerClient(response),
    )
    run = build_temp_path / f"facade_artifact_{uuid4().hex}"
    output = run / "output"
    output.mkdir(parents=True)
    (output / "GranularBody_0000001000.vtp").write_text(
        '<VTKFile type="PolyData"><PolyData><FieldData><DataArray '
        'Name="TimeValue" format="ascii">1.0</DataArray></FieldData><Piece '
        'NumberOfPoints="2"><Points><DataArray Name="Position" '
        'NumberOfComponents="2" format="ascii">0.1 0.2 0.4 0.7'
        '</DataArray></Points></Piece></PolyData></VTKFile>',
        encoding="utf-8",
    )

    result = facade.analyze(spec.extractor_id, "unused.json", run)

    assert facade.load_extractors().extractors[0].fingerprint == spec.fingerprint
    assert result.values == {"height_y": pytest.approx(0.7)}


def test_facade_rejects_duplicate_extractor_id(build_temp_path: Path) -> None:
    facade = make_facade(build_temp_path)
    response = {
        "extractor_id": "front",
        "description": "Extract the front.",
        "body_name": "Body",
        "outputs": [
            {"output_name": "front_x", "component_index": 0, "reduction": "maximum"}
        ],
    }
    context = ExtractorPlanningContext(
        body_names=["Body"],
        spatial_dimension=1,
        available_components={"x": 0},
    )
    facade.plan_extractor("Measure front.", context, MockExtractorPlannerClient(response))

    with pytest.raises(AgentServiceError, match="already exists"):
        facade.plan_extractor(
            "Measure front.",
            context,
            MockExtractorPlannerClient(response),
        )
