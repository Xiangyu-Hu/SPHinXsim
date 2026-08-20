"""Tests for safe data-only extractor compilation across different cases."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from sphinxsim.agent import (
    AnalysisError,
    DeclarativeExtractorCatalog,
    DeclarativeExtractorCompiler,
    DeclarativeExtractorSpec,
    ExtractorRegistry,
)


def write_vtp(
    root: Path,
    body_name: str,
    positions: str,
    *,
    components: int,
    points: int,
) -> None:
    output = root / "output"
    output.mkdir(parents=True, exist_ok=True)
    (output / f"{body_name}_0000001000.vtp").write_text(
        f'''<?xml version="1.0"?>
<VTKFile type="PolyData" version="1.0" byte_order="LittleEndian">
  <PolyData><FieldData><DataArray Name="TimeValue" format="ascii">1.5</DataArray></FieldData>
    <Piece Name="{body_name}" NumberOfPoints="{points}"><Points>
      <DataArray Name="Position" NumberOfComponents="{components}" format="ascii">
        {positions}
      </DataArray>
    </Points></Piece>
  </PolyData>
</VTKFile>
''',
        encoding="utf-8",
    )


def test_compiled_water_front_extractor_reads_2d_case(build_temp_path: Path) -> None:
    run = build_temp_path / "water_case"
    write_vtp(run, "WaterBody", "0.1 0.0 0.8 0.2 0.4 0.5", components=2, points=3)
    spec = DeclarativeExtractorSpec(
        extractor_id="water_front",
        description="Extract the final water front and maximum height.",
        body_name="WaterBody",
        outputs=[
            {"output_name": "front_x", "component_index": 0, "reduction": "maximum"},
            {"output_name": "height_y", "component_index": 1, "reduction": "maximum"},
        ],
    )
    registry = ExtractorRegistry()
    DeclarativeExtractorCompiler().register(registry, spec)

    result = registry.analyze("unused.json", run, {"metric": "water_front"})

    assert result.values == {"front_x": pytest.approx(0.8), "height_y": pytest.approx(0.5)}
    assert result.particle_count == 3
    assert result.extractor_fingerprint == spec.fingerprint


def test_compiled_deposit_extent_extractor_reads_3d_case(build_temp_path: Path) -> None:
    run = build_temp_path / "deposit_case"
    write_vtp(
        run,
        "DepositBody",
        "-0.2 0.1 0.0 1.4 0.6 0.9 0.3 -0.1 0.4",
        components=3,
        points=3,
    )
    spec = DeclarativeExtractorSpec(
        extractor_id="deposit_extent",
        description="Extract 3D deposit coordinate bounds.",
        body_name="DepositBody",
        outputs=[
            {"output_name": "minimum_x", "component_index": 0, "reduction": "minimum"},
            {"output_name": "maximum_z", "component_index": 2, "reduction": "maximum"},
        ],
    )
    registry = DeclarativeExtractorCatalog(extractors=[spec]).build_registry()

    result = registry.analyze("unused.json", run, {"metric": "deposit_extent"})

    assert result.values["minimum_x"] == pytest.approx(-0.2)
    assert result.values["maximum_z"] == pytest.approx(0.9)


def test_catalog_round_trip_recompiles_same_fingerprint(
    build_temp_path: Path,
) -> None:
    spec = DeclarativeExtractorSpec(
        extractor_id="body_height",
        description="Extract final height.",
        body_name="Body",
        outputs=[
            {"output_name": "height", "component_index": 1, "reduction": "maximum"}
        ],
    )
    path = DeclarativeExtractorCatalog(extractors=[spec]).save(
        build_temp_path / "extractors.json"
    )
    loaded = DeclarativeExtractorCatalog.load(path)

    assert loaded.extractors[0].fingerprint == spec.fingerprint
    assert loaded.build_registry().names() == ("body_height",)


@pytest.mark.parametrize(
    "unsafe_field",
    [
        {"python_code": "import os"},
        {"expression": "__import__('os').system('whoami')"},
        {"file_path": "C:/secret.txt"},
    ],
)
def test_spec_rejects_executable_or_arbitrary_file_fields(unsafe_field: dict) -> None:
    payload = {
        "extractor_id": "safe_metric",
        "description": "Safe metric.",
        "body_name": "Body",
        "outputs": [
            {"output_name": "front", "component_index": 0, "reduction": "maximum"}
        ],
        **unsafe_field,
    }

    with pytest.raises(ValidationError, match="extra"):
        DeclarativeExtractorSpec.model_validate(payload)


def test_compiled_extractor_rejects_component_outside_actual_dimension(
    build_temp_path: Path,
) -> None:
    run = build_temp_path / "two_dimensional"
    write_vtp(run, "Body", "0.0 0.0 1.0 1.0", components=2, points=2)
    spec = DeclarativeExtractorSpec(
        extractor_id="invalid_z",
        description="Request unavailable z.",
        body_name="Body",
        outputs=[
            {"output_name": "z", "component_index": 2, "reduction": "maximum"}
        ],
    )
    registry = DeclarativeExtractorCatalog(extractors=[spec]).build_registry()

    with pytest.raises(AnalysisError, match="exceeds the VTP dimension"):
        registry.analyze("unused.json", run, {"metric": "invalid_z"})
