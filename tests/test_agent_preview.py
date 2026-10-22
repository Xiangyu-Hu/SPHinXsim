"""Run observation must never start or control a solver."""

import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

from sphinxsim.visualization.agent_preview import (
    AgentPreviewObserver, WorkflowPreviewSource, launch_agent_preview,
)


CASE = Path(__file__).parent / "test_simulation/test_2d_simulation/data/column_collapse.json"


def prepare_run(root, index, time=0.1):
    task = f"demo_iteration_{index:03d}"
    case = root / "store" / task / "cases/case.json"
    case.parent.mkdir(parents=True, exist_ok=True)
    case.write_text(CASE.read_text(encoding="utf-8"), encoding="utf-8")
    output = root / "store" / task / "runs/run_001"
    frames = output / "output"
    frames.mkdir(parents=True, exist_ok=True)
    (frames / "GranularBody_0001.vtp").write_text(
        f'''<VTKFile type="PolyData" version="1.0" byte_order="LittleEndian"><PolyData>
        <FieldData><DataArray type="Float64" Name="TimeValue" NumberOfTuples="1" format="ascii">{time}</DataArray></FieldData>
        <Piece Name="GranularBody" NumberOfPoints="2" NumberOfVerts="0" NumberOfLines="0" NumberOfStrips="0" NumberOfPolys="0">
        <Points><DataArray type="Float64" Name="Position" NumberOfComponents="3" format="ascii">0.1 0.02 0 0.3 0.01 0</DataArray></Points>
        </Piece></PolyData></VTKFile>''', encoding="utf-8",
    )
    record = root / "execution/demo/attempts" / str(index) / "attempt.json"
    record.parent.mkdir(parents=True, exist_ok=True)
    record.write_text(json.dumps(dict(operation="run", started_at=f"2026-10-08T10:00:0{index}",
        status="running", config_path=str(case), output_dir=str(output))), encoding="utf-8")
    return dict(iteration=index, parameter_value=index * 10, metric_value=0.2,
                absolute_error=0.02 / index, task_id=task, run_id="run_001", case_path=str(case))


def save_workflow(root, iterations=None, status="running"):
    path = root / "store/workflows/demo/workflow.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(dict(status=status, iterations=iterations or [])), encoding="utf-8")


def test_source_follows_newest_attempt_and_best_and_selected_candidate(tmp_path):
    first = prepare_run(tmp_path, 1)
    second = prepare_run(tmp_path, 2)
    save_workflow(tmp_path, [first, second], "converged")
    source = WorkflowPreviewSource(tmp_path, "demo")
    assert source.poll().config_path == Path(second["case_path"])
    source.selection = "best"
    assert source.poll().config_path == Path(second["case_path"])
    source.selection = "candidate:1"
    assert source.poll().config_path == Path(first["case_path"])
    assert source.poll().status == "converged"


def test_source_waits_for_run_and_accepts_legacy_attempt_metadata(tmp_path):
    save_workflow(tmp_path)
    source = WorkflowPreviewSource(tmp_path, "demo")
    assert source.poll() is None
    item = prepare_run(tmp_path, 1)
    path = tmp_path / "execution/demo/attempts/1/attempt.json"
    record = json.loads(path.read_text(encoding="utf-8"))
    record.pop("config_path")
    record["command"] = ["python", "-m", "sphinxsim", "run", item["case_path"]]
    path.write_text(json.dumps(record), encoding="utf-8")
    assert source.poll().config_path == Path(item["case_path"])


def test_source_marks_orphaned_worker_interrupted(tmp_path):
    prepare_run(tmp_path, 1)
    save_workflow(tmp_path)
    (tmp_path / "execution/demo/worker.json").write_text(
        json.dumps(dict(state="running", pid=99999999, created=0)), encoding="utf-8")
    assert WorkflowPreviewSource(tmp_path, "demo").poll().status == "interrupted"


def test_launch_reuses_live_viewer_and_never_submits_solver(tmp_path, monkeypatch):
    import sphinxsim.services.process_control as processes
    import sphinxsim.visualization.agent_preview as viewer
    monkeypatch.setattr(viewer.subprocess, "Popen", lambda *a, **k: SimpleNamespace(pid=123))
    monkeypatch.setattr(processes, "identity", lambda pid: dict(pid=pid, created=42))
    monkeypatch.setattr(processes, "alive", lambda record: True)
    first = launch_agent_preview(tmp_path, tmp_path, "demo")
    monkeypatch.setattr(viewer.subprocess, "Popen", lambda *a, **k: pytest.fail("duplicate viewer"))
    assert launch_agent_preview(tmp_path, tmp_path, "demo") == first
    assert not (tmp_path / "execution/demo/worker.json").exists()
    assert not (tmp_path / "execution/demo/cancel.request").exists()


def test_qt_preview_process_opens_and_closes_without_controlling_worker(tmp_path):
    pytest.importorskip("pyvistaqt")
    pytest.importorskip("PySide6")
    prepare_run(tmp_path, 1)
    save_workflow(tmp_path)
    project = Path(__file__).resolve().parents[1]
    script = (
        "import sys; from qtpy import QtWidgets, QtCore; "
        "app=QtWidgets.QApplication([]); "
        "from sphinxsim.visualization.agent_preview import main; "
        "QtCore.QTimer.singleShot(2000, app.quit); "
        "sys.argv=['agent_preview',sys.argv[1],sys.argv[2],'demo']; sys.exit(main())"
    )
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    if os.name == "nt":
        env.pop("QT_QPA_PLATFORM", None)
    else:
        env.setdefault("QT_QPA_PLATFORM", "offscreen")
    result = subprocess.run([sys.executable, "-c", script, str(project), str(tmp_path)],
                            cwd=project, env=env, capture_output=True, timeout=25)
    assert result.returncode == 0, result.stderr.decode("utf-8", errors="replace")
    assert not (tmp_path / "execution/demo/cancel.request").exists()


def test_real_preview_observer_switches_run_and_preserves_camera(tmp_path, monkeypatch):
    pv = pytest.importorskip("pyvista")
    from sphinxsim.visualization.preview import ConfigVisualizer
    monkeypatch.setattr(ConfigVisualizer, "_try_build_geometries",
                        lambda *a, **k: pytest.fail("observer invoked native geometry generation"))
    first = prepare_run(tmp_path, 1)
    save_workflow(tmp_path)
    for body in ("GranularBody", "WallBoundary"):
        pv.Box(bounds=(0, 0.5, 0, 0.15, -0.001, 0.001)).save(
            str(tmp_path / "store/demo_iteration_001/runs/run_001/output" / f"Shape{body}.vtp"))
    source = WorkflowPreviewSource(tmp_path, "demo")
    plotter = pv.Plotter(off_screen=True, shape=(1, 2), window_size=(1000, 600))
    observer = AgentPreviewObserver(source, plotter, tmp_path)
    try:
        observer.tick()
        assert "particle-preview-GranularBody" in plotter.renderer.actors
        assert "preview-shape-WallBoundary" in plotter.renderer.actors
        assert "preview-shape-GranularBody" not in plotter.renderer.actors
        camera = plotter.camera_position
        prepare_run(tmp_path, 2, time=0.2)
        save_workflow(tmp_path, [first])
        observer.tick()
        assert observer.visualizer.config_path.parent.parent.name == "demo_iteration_002"
        assert plotter.camera_position == camera
        image = plotter.screenshot(str(tmp_path / "observer.png"))
        assert image.shape[0] > 0
        assert (tmp_path / "observer.png").stat().st_size > 1000
    finally:
        plotter.close()
    assert not (tmp_path / "execution/demo/cancel.request").exists()


@pytest.mark.integration
def test_observer_displays_real_cpp_solver_output(build_temp_path):
    pv = pytest.importorskip("pyvista")
    from sphinxsim.services import SPHinXsimCLIAdapter
    root = build_temp_path / "real_observer"
    item = prepare_run(root, 1)
    config_path = Path(item["case_path"])
    config = json.loads(config_path.read_text(encoding="utf-8"))
    config["solver_parameters"]["end_time"] = 0.04
    config["solver_parameters"]["output_interval"] = 0.02
    config["geometries"]["global_resolution"]["particle_spacing"] = 0.008
    config_path.write_text(json.dumps(config), encoding="utf-8")
    # Remove the synthetic fixture frame so the observer must read solver output.
    output_dir = root / "store/demo_iteration_001/runs/run_001"
    (output_dir / "output/GranularBody_0001.vtp").unlink()
    project = Path(__file__).resolve().parents[1]
    adapter = SPHinXsimCLIAdapter(cwd=project, control_dir=root / "execution/demo")
    result = adapter.run(config_path, output_dir=output_dir, timeout=60)
    assert result.succeeded, result.stderr
    save_workflow(root, [item], "converged")
    plotter = pv.Plotter(off_screen=True, shape=(1, 2), window_size=(1000, 600))
    observer = AgentPreviewObserver(WorkflowPreviewSource(root, "demo"), plotter, project)
    try:
        observer.tick()
        assert observer.versions.get("GranularBody")
        assert "preview-shape-WallBoundary" in plotter.renderer.actors
        plotter.screenshot(str(root / "real_solver_preview.png"))
        assert (root / "real_solver_preview.png").stat().st_size > 1000
    finally:
        plotter.close()
