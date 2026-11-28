"""Tests for sphinxsim templates, init, results, and preview --watch."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from sphinxsim.cli import _ShellPreviewRuntime, main


def _write_vtp_with_fields(
    path: Path,
    *,
    body: str,
    time: float,
    points: list[tuple[float, float, float]],
    pressures: list[float],
) -> None:
    pts_text = " ".join(f"{x} {y} {z}" for x, y, z in points)
    pres_text = " ".join(str(p) for p in pressures)
    n_pts = len(points)
    path.write_text(
        f"""<VTKFile type="PolyData" version="1.0" byte_order="LittleEndian">
  <PolyData>
    <FieldData>
      <DataArray type="Float64" Name="TimeValue" NumberOfTuples="1" format="ascii">{time}</DataArray>
    </FieldData>
    <Piece Name="{body}" NumberOfPoints="{n_pts}" NumberOfVerts="0" NumberOfLines="0" NumberOfStrips="0" NumberOfPolys="0">
      <PointData>
        <DataArray type="Float64" Name="Pressure" format="ascii">{pres_text}</DataArray>
      </PointData>
      <Points>
        <DataArray type="Float64" Name="Position" NumberOfComponents="3" format="ascii">{pts_text}</DataArray>
      </Points>
    </Piece>
  </PolyData>
</VTKFile>""",
        encoding="utf-8",
    )


def test_templates_list_human_and_json(capsys):
    assert main(["templates"]) == 0
    human_out = capsys.readouterr().out
    assert "2d/column_collapse" in human_out
    assert "3d/fuel_tank" in human_out

    assert main(["templates", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    ids = {item["id"] for item in payload["data"]["templates"]}
    assert {"2d/column_collapse", "2d/dambreak", "3d/dambreak", "3d/fuel_tank"} <= ids


def test_init_copies_template_stages_stl_and_guards_overwrite(tmp_path, capsys):
    dest = tmp_path / "fuel_case" / "case.json"
    assert main(["init", "fuel_tank", str(dest), "--root", str(tmp_path), "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["data"]["template"] == "3d/fuel_tank"
    assert payload["data"]["assets"] == ["assets/triangle_prism.stl"]
    assert dest.is_file()
    assert (dest.parent / "assets" / "triangle_prism.stl").is_file()

    written = json.loads(dest.read_text(encoding="utf-8"))
    assert written["geometries"]["shapes"][0]["file_name"] == "assets/triangle_prism.stl"

    # Refuses overwrite without --overwrite
    assert main(["init", "3d/fuel_tank", str(dest), "--root", str(tmp_path), "--json"]) == 1
    err_payload = json.loads(capsys.readouterr().out)
    assert err_payload["ok"] is False
    assert "already exists" in err_payload["error"]["message"]

    # Ambiguous bare stem reports choices clearly
    ambig = tmp_path / "ambig.json"
    assert main(["init", "dambreak", str(ambig)]) == 1
    assert "Ambiguous template" in capsys.readouterr().err


def test_results_summarizes_vtp_dat_and_quality(tmp_path, capsys):
    case = tmp_path / "dambreak.json"
    assert main(["init", "2d/dambreak", str(case)]) == 0
    capsys.readouterr()

    # Use unscaled config with explicit system_domain so coverage/domain checks run
    cfg_data = json.loads(case.read_text(encoding="utf-8"))
    cfg_data.pop("characteristic_dimensions", None)
    cfg_data["geometries"]["system_domain"] = {"lower_bound": [0.0, 0.0], "upper_bound": [6.0, 6.0]}
    cfg_data["solver_parameters"]["end_time"] = 1.0
    cfg_data["solver_parameters"]["output_interval"] = 0.1
    case.write_text(json.dumps(cfg_data, indent=2), encoding="utf-8")

    out_dir = tmp_path / "run_out"
    vtp_dir = out_dir / "output"
    vtp_dir.mkdir(parents=True)

    _write_vtp_with_fields(
        vtp_dir / "WaterBody_ite_0001.vtp",
        body="WaterBody",
        time=0.0,
        points=[(0.1, 0.2, 0.0), (0.3, 0.4, 0.0)],
        pressures=[0.0, 0.0],
    )
    _write_vtp_with_fields(
        vtp_dir / "WaterBody_0001.vtp",
        body="WaterBody",
        time=0.0,
        points=[(0.1, 0.2, 0.0), (0.3, 0.4, 0.0)],
        pressures=[10.0, 20.0],
    )
    _write_vtp_with_fields(
        vtp_dir / "WaterBody_0002.vtp",
        body="WaterBody",
        time=0.2,
        points=[(0.2, 0.1, 0.0), (0.8, 0.3, 0.0)],
        pressures=[15.0, 42.5],
    )
    (vtp_dir / "FluidObserver_Pressure.dat").write_text(
        '"run_time"   "Pressure_0"\n0.0   10.0\n0.1   35.0\n0.2   25.0\n',
        encoding="utf-8",
    )

    assert main(["results", str(case), "--output-dir", str(out_dir), "--root", str(tmp_path), "--json"]) == 0
    res = json.loads(capsys.readouterr().out)
    assert res["ok"] is True
    data = res["data"]
    water = data["bodies"]["WaterBody"]
    assert water["solver_frames"] == 2
    assert water["relaxation_frames"] == 1
    assert water["last_time"] == pytest.approx(0.2)
    assert water["particle_count"] == 2
    assert water["bounds_max"] == pytest.approx([0.8, 0.3])
    assert "Pressure" in water["fields"]

    assert len(data["time_series"]) == 1
    ts = data["time_series"][0]
    assert ts["file"] == "FluidObserver_Pressure.dat"
    assert ts["rows"] == 3
    assert ts["series"]["Pressure_0"]["max"] == pytest.approx(35.0)
    assert ts["series"]["Pressure_0"]["final"] == pytest.approx(25.0)

    # Coverage gap detected because last_time (0.2) < end_time (1.0)
    assert data["quality"]["state"] == "warning"
    codes = {f["code"] for f in data["quality"]["findings"]}
    assert "saved_output_coverage_gap" in codes

    # Human output mode also succeeds cleanly
    assert main(["results", str(case), "--output-dir", str(out_dir)]) == 0
    human = capsys.readouterr().out
    assert "FluidObserver_Pressure.dat" in human
    assert "WaterBody" in human


def test_results_screenshot_with_color_by(tmp_path, capsys):
    pytest.importorskip("pyvista")
    case = tmp_path / "case.json"
    assert main(["init", "2d/column_collapse", str(case)]) == 0
    capsys.readouterr()

    out_dir = tmp_path / "sim_out" / "output"
    out_dir.mkdir(parents=True)
    _write_vtp_with_fields(
        out_dir / "GranularBody_0001.vtp",
        body="GranularBody",
        time=0.05,
        points=[(0.05, 0.05, 0.0), (0.15, 0.08, 0.0), (0.25, 0.02, 0.0)],
        pressures=[-50.0, 100.0, 250.0],
    )

    shot = tmp_path / "pressure.png"
    assert main([
        "results",
        str(case),
        "--output-dir",
        str(out_dir.parent),
        "--screenshot",
        str(shot),
        "--color-by",
        "Pressure",
        "--root",
        str(tmp_path),
        "--json",
    ]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["data"]["rendered"]["coloured_bodies"] == ["GranularBody"]
    assert shot.is_file() and shot.stat().st_size > 0

    # Unknown color_by field fails with helpful message
    bad_shot = tmp_path / "bad.png"
    assert main([
        "results",
        str(case),
        "--output-dir",
        str(out_dir.parent),
        "--screenshot",
        str(bad_shot),
        "--color-by",
        "NonExistentField",
    ]) == 1
    assert "available: Pressure" in capsys.readouterr().err


def test_preview_watch_reloads_when_clean_and_preserves_when_dirty(tmp_path):
    case = tmp_path / "watch_case.json"
    assert main(["init", "2d/column_collapse", str(case)]) == 0

    runtime = _ShellPreviewRuntime()
    runtime.plotter = MagicMock()
    calls = []

    def fake_show(cfg, *, resolved_config_path, with_particles, force=False):
        calls.append((cfg.solver_parameters.end_time, force))
        return 0

    runtime.show_or_update = fake_show  # type: ignore[method-assign]
    dirty = False
    status_label = MagicMock()
    runtime._json_editor = {"is_dirty": lambda: dirty, "status": status_label}

    runtime.start_file_watch(case)
    assert runtime.check_watched_files() is False

    # External edit while editor is clean -> auto-reloads
    data = json.loads(case.read_text(encoding="utf-8"))
    data["solver_parameters"]["end_time"] = 0.25
    case.write_text(json.dumps(data, indent=2), encoding="utf-8")
    assert runtime.check_watched_files() is True
    assert calls == [(pytest.approx(0.25), True)]

    # External edit while editor has unsaved changes -> preserved, warns in status
    dirty = True
    data["solver_parameters"]["end_time"] = 0.5
    case.write_text(json.dumps(data, indent=2), encoding="utf-8")
    assert runtime.check_watched_files() is False
    assert len(calls) == 1
    status_label.setText.assert_called_once()
    assert "External change detected" in status_label.setText.call_args[0][0]
