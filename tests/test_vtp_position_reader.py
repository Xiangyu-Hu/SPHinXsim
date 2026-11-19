"""Tests for streaming selection and reading of SPHinXsim VTP states."""

from __future__ import annotations

from pathlib import Path

import pytest

from sphinxsim.agent.analysis import AnalysisError, VTPPositionReader


def _write_vtp(
    path: Path,
    *,
    body_name: str = "GranularBody",
    time: float = 0.0,
    positions: tuple[tuple[float, float, float], ...] = ((0.0, 0.0, 0.0),),
) -> None:
    flat = " ".join(str(value) for point in positions for value in point)
    path.write_text(
        f'''<?xml version="1.0"?>
<VTKFile type="PolyData" version="1.0" byte_order="LittleEndian">
  <PolyData>
    <FieldData>
      <DataArray type="Float64" Name="TimeValue" NumberOfTuples="1" format="ascii">
        {time}
      </DataArray>
    </FieldData>
    <Piece Name="{body_name}" NumberOfPoints="{len(positions)}" NumberOfVerts="0">
      <Points>
        <DataArray Name="Position" type="Float32" NumberOfComponents="3" format="ascii">
          {flat}
        </DataArray>
      </Points>
    </Piece>
  </PolyData>
</VTKFile>
''',
        encoding="utf-8",
    )


def test_selects_latest_non_initialization_state_and_streams_bounds(
    build_temp_path: Path,
) -> None:
    output = build_temp_path / "run_001" / "output"
    output.mkdir(parents=True, exist_ok=True)
    _write_vtp(output / "GranularBody_0001.vtp", time=0.1)
    _write_vtp(
        output / "GranularBody_0002.vtp",
        time=0.8,
        positions=((0.1, 0.0, 0.0), (0.4, 0.2, 0.0), (0.3, -0.1, 0.0)),
    )
    _write_vtp(
        output / "GranularBody_ite_9999.vtp",
        time=2.0,
        positions=((9.0, 9.0, 0.0),),
    )

    summary = VTPPositionReader().read_final_summary(
        output.parent,
        "GranularBody",
        projection_direction=(1.0, 0.0),
    )

    assert summary.state_file.name == "GranularBody_0002.vtp"
    assert summary.time == pytest.approx(0.8)
    assert summary.particle_count == 3
    assert summary.component_count == 3
    assert summary.minimum == pytest.approx((0.1, -0.1, 0.0))
    assert summary.maximum == pytest.approx((0.4, 0.2, 0.0))
    assert summary.maximum_projection == pytest.approx(0.4)


def test_projection_direction_is_normalized(build_temp_path: Path) -> None:
    output = build_temp_path / "output"
    output.mkdir(parents=True, exist_ok=True)
    _write_vtp(
        output / "Soil_0001.vtp",
        body_name="Soil",
        time=1.0,
        positions=((3.0, 4.0, 0.0), (1.0, 1.0, 0.0)),
    )

    summary = VTPPositionReader().read_final_summary(
        output,
        "Soil",
        projection_direction=(3.0, 4.0),
    )

    assert summary.maximum_projection == pytest.approx(5.0)


def test_missing_body_state_is_reported(build_temp_path: Path) -> None:
    output = build_temp_path / "output"
    output.mkdir(parents=True, exist_ok=True)

    with pytest.raises(AnalysisError, match="no state VTP"):
        VTPPositionReader().read_final_summary(output, "MissingBody")


def test_position_count_must_match_piece_metadata(build_temp_path: Path) -> None:
    output = build_temp_path / "output"
    output.mkdir(parents=True, exist_ok=True)
    path = output / "GranularBody_0001.vtp"
    _write_vtp(path, time=1.0, positions=((0.0, 0.0, 0.0),))
    content = path.read_text(encoding="utf-8").replace(
        'NumberOfPoints="1"',
        'NumberOfPoints="2"',
    )
    path.write_text(content, encoding="utf-8")

    with pytest.raises(AnalysisError, match="NumberOfPoints"):
        VTPPositionReader().read_final_summary(output, "GranularBody")


def test_zero_projection_direction_is_rejected(build_temp_path: Path) -> None:
    output = build_temp_path / "output"
    output.mkdir(parents=True, exist_ok=True)
    _write_vtp(output / "GranularBody_0001.vtp", time=1.0)

    with pytest.raises(AnalysisError, match="must not be zero"):
        VTPPositionReader().read_final_summary(
            output,
            "GranularBody",
            projection_direction=(0.0, 0.0),
        )
