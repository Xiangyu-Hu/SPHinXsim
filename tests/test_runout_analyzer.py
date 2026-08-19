"""Tests for deterministic runout calculation from JSON geometry and VTP output."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from sphinxsim.agent.analysis import AnalysisError, AnalysisSpec, RunoutAnalyzer


def _write_config(path: Path, *, shape_type: str = "bounding_box") -> None:
    path.write_text(
        json.dumps(
            {
                "geometries": {
                    "shapes": [
                        {
                            "name": "GranularBody",
                            "type": shape_type,
                            "lower_bound": [0.0, 0.0],
                            "upper_bound": [0.2, 0.1],
                        }
                    ]
                }
            }
        ),
        encoding="utf-8",
    )


def _write_final_vtp(path: Path) -> None:
    path.write_text(
        '''<?xml version="1.0"?>
<VTKFile type="PolyData" version="1.0" byte_order="LittleEndian">
  <PolyData>
    <FieldData><DataArray Name="TimeValue" format="ascii">0.8</DataArray></FieldData>
    <Piece Name="GranularBody" NumberOfPoints="3">
      <Points><DataArray Name="Position" NumberOfComponents="3" format="ascii">
        0.10 0.00 0.00  0.40 0.02 0.00  0.30 0.05 0.00
      </DataArray></Points>
    </Piece>
  </PolyData>
</VTKFile>
''',
        encoding="utf-8",
    )


def test_calculates_column_collapse_front_and_extension(build_temp_path: Path) -> None:
    config = build_temp_path / "case.json"
    _write_config(config)
    output = build_temp_path / "run_001" / "output"
    output.mkdir(parents=True, exist_ok=True)
    _write_final_vtp(output / "GranularBody_0001.vtp")
    spec = AnalysisSpec(
        body_name="GranularBody",
        direction_vector=(1.0, 0.0),
    )

    result = RunoutAnalyzer().analyze(config, output.parent, spec)

    assert result.initial_front == pytest.approx(0.2)
    assert result.final_front == pytest.approx(0.4)
    assert result.runout_extension == pytest.approx(0.2)
    assert result.simulation_time == pytest.approx(0.8)
    assert result.particle_count == 3
    assert result.direction_vector == pytest.approx((1.0, 0.0))
    assert result.unit == "m"


def test_direction_vector_uses_directional_projection(build_temp_path: Path) -> None:
    config = build_temp_path / "case.json"
    _write_config(config)
    output = build_temp_path / "output"
    output.mkdir(parents=True, exist_ok=True)
    _write_final_vtp(output / "GranularBody_0001.vtp")
    spec = AnalysisSpec(
        body_name="GranularBody",
        direction_vector=(1.0, 1.0),
    )

    result = RunoutAnalyzer().analyze(config, output, spec)

    assert result.initial_front == pytest.approx(0.3 / 2**0.5)
    assert result.final_front == pytest.approx(0.42 / 2**0.5)


def test_unsupported_initial_geometry_is_reported(build_temp_path: Path) -> None:
    config = build_temp_path / "case.json"
    _write_config(config, shape_type="polygon")
    output = build_temp_path / "output"
    output.mkdir(parents=True, exist_ok=True)
    _write_final_vtp(output / "GranularBody_0001.vtp")
    spec = AnalysisSpec(
        body_name="GranularBody",
        direction_vector=(1.0, 0.0),
    )

    with pytest.raises(AnalysisError, match="bounding_box only"):
        RunoutAnalyzer().analyze(config, output, spec)
