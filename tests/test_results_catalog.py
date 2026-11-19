from pathlib import Path

import pytest

from sphinxsim.results import AnalysisError, ResultCatalog, VTPPositionReader


def write_vtp(path: Path, *, time: float, body: str = "Body") -> None:
    path.write_text(
        f'''<VTKFile xmlns="urn:vtk"><PolyData>
        <FieldData><DataArray Name="TimeValue">{time}</DataArray></FieldData>
        <Piece Name="{body}" NumberOfPoints="1"><Points>
        <DataArray Name="Position" NumberOfComponents="3" format="ascii">1 2 3</DataArray>
        </Points></Piece></PolyData></VTKFile>''',
        encoding="utf-8",
    )


def test_catalog_prefers_latest_solver_frame_and_handles_xml_namespace(tmp_path):
    write_vtp(tmp_path / "Body_ite_9.vtp", time=99)
    write_vtp(tmp_path / "Body_0002.vtp", time=0.2)
    write_vtp(tmp_path / "Body_0001.vtp", time=0.1)

    frame = ResultCatalog().latest(
        tmp_path, ["Body"], include_generation=True
    )["Body"]

    assert frame.path.name == "Body_0002.vtp"
    assert frame.time == pytest.approx(0.2)
    assert frame.generation is False


def test_incomplete_live_frame_is_ignored(tmp_path):
    write_vtp(tmp_path / "Body_0001.vtp", time=0.1)
    (tmp_path / "Body_0002.vtp").write_text("<VTKFile>", encoding="utf-8")

    frame = ResultCatalog().latest(tmp_path, ["Body"])["Body"]

    assert frame.path.name == "Body_0001.vtp"


def test_position_reader_rejects_wrong_body(tmp_path):
    source = tmp_path / "Body_0001.vtp"
    write_vtp(source, time=0.1, body="Other")

    with pytest.raises(AnalysisError, match="body name"):
        VTPPositionReader().read_summary(source, "Body")
