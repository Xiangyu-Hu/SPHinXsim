"""Qt editor boundary tests plus an opt-in real native-window process test.

The editor tests use a real QMainWindow and native property editor with a
renderer facade. Only the last test claims native rendering/process coverage.
"""
import base64
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import uuid
from types import SimpleNamespace

import pytest

pytest.importorskip("qtpy")
pytest.importorskip("PySide6")
if not os.environ.get("DISPLAY"):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from qtpy import QtCore, QtGui, QtTest, QtWidgets

from sphinxsim.cli import _ShellPreviewRuntime
from sphinxsim.config.schemas import SimulationConfig
from sphinxsim.desktop.session import Session
from sphinxsim.desktop.presentation import Presentation


@pytest.fixture
def editor(tmp_path):
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    path = tmp_path / "config.json"
    path.write_bytes((Path(__file__).parent / "fixtures/fixed-ship.json").read_bytes())
    config = SimulationConfig.model_validate_json(path.read_text())
    window = QtWidgets.QMainWindow()
    window.setCentralWidget(QtWidgets.QLabel("Native editor boundary"))
    window.resize(1400, 900)
    window.show()
    runtime = _ShellPreviewRuntime()
    runtime.plotter = SimpleNamespace(app_window=window, reset_camera=lambda: None)
    runtime._using_background_plotter = True
    runtime.show_or_update = lambda *args, **kwargs: 0
    runtime._install_json_editor(config, config_path=path, with_particles=False)
    session = Session(tmp_path, "config.json", runtime)
    runtime.write_config = session.save
    window.activateWindow()
    assert QtTest.QTest.qWaitForWindowActive(window), "Native editor did not receive keyboard focus"
    app.processEvents()
    yield app, runtime, session, path
    window.close()
    app.processEvents()


def spacing_field(runtime):
    tree = runtime._json_editor["tree"]
    iterator = QtWidgets.QTreeWidgetItemIterator(tree)
    while iterator.value():
        item = iterator.value()
        if item.data(0, QtCore.Qt.ItemDataRole.UserRole) == "geometries.global_resolution.particle_spacing":
            tree.scrollToItem(item)
            return tree.itemWidget(item, 1)
        iterator += 1
    raise AssertionError("Native particle spacing field is missing")


def edit_spacing(runtime, value):
    field = spacing_field(runtime)
    field.setFocus()
    QtTest.QTest.keySequence(field, QtGui.QKeySequence.StandardKey.SelectAll)
    QtTest.QTest.keyClicks(field, value)
    return field


def test_human_edits_invalidate_old_commands_and_native_save_really_persists(editor):
    app, runtime, session, path = editor
    observed = session.handle({"operation": "status"})
    field = edit_spacing(runtime, "0.06")
    stale = {"instance_id": observed["instance_id"], "version": observed["version"], "automation_id": "turn-1"}
    with pytest.raises(ValueError, match="Editor changed"):
        session.handle({**stale, "operation": "apply", "configuration": observed["configuration"], "expected_hash": observed["saved_hash"]})
    assert session.status()["dirty"]
    assert session.status()["configuration"]["geometries"]["global_resolution"]["particle_spacing"] == .06
    QtTest.QTest.keySequence(field, QtGui.QKeySequence.StandardKey.Save)
    app.processEvents()
    assert json.loads(path.read_text())["geometries"]["global_resolution"]["particle_spacing"] == .06
    assert not session.status()["dirty"]
    current = session.status()
    payload = current["configuration"]
    payload["geometries"]["global_resolution"]["particle_spacing"] = .07
    result = session.handle({"operation": "apply", "instance_id": current["instance_id"], "version": current["version"],
        "automation_id": "turn-2", "configuration": payload, "expected_hash": current["saved_hash"]})
    assert result["configuration"]["geometries"]["global_resolution"]["particle_spacing"] == .07
    assert json.loads(path.read_text())["geometries"]["global_resolution"]["particle_spacing"] == .07


def test_stopping_an_automation_does_not_cancel_jobs_or_prevent_human_editing(editor):
    _, runtime, session, path = editor
    observed = session.status()
    result = session.handle({"operation": "stop", "instance_id": observed["instance_id"], "automation_id": "turn"})
    assert result["stopped"]
    with pytest.raises(ValueError, match="stopped automation"):
        session.handle({"operation": "preview", "instance_id": observed["instance_id"], "version": observed["version"],
            "automation_id": "turn", "expected_hash": observed["saved_hash"]})
    assert not list(session.jobs.directory.glob("*/cancel.json"))
    edit_spacing(runtime, "0.08")
    assert session.status()["dirty"]
    assert session.status()["configuration"]["geometries"]["global_resolution"]["particle_spacing"] == .08
    assert json.loads(path.read_text())["geometries"]["global_resolution"]["particle_spacing"] != .08


def test_translated_native_menus_keep_field_paths_and_trigger_original_actions(editor, tmp_path):
    app, runtime, _, _ = editor
    QtCore.QSettings.setPath(QtCore.QSettings.Format.NativeFormat, QtCore.QSettings.Scope.UserScope, str(tmp_path))
    intents = []
    action = runtime.plotter.app_window.addAction("Particles")
    action.triggered.connect(lambda: intents.append("particles"))
    adapter = Presentation(runtime, [("particles", "生成粒子", "Particles", action)])
    adapter.apply("zh", "light")
    app.processEvents()
    assert [a.text() for a in runtime.plotter.app_window.menuBar().actions()] == ["文件", "编辑", "视图", "仿真", "帮助"]
    assert not runtime._json_editor["is_dirty"]()
    item = next(item for item in adapter.items() if item.data(0, QtCore.Qt.ItemDataRole.UserRole) == "geometries.global_resolution.particle_spacing")
    assert item.text(0) == "粒子间距 [m]"
    menu = next(obj for obj, _, _ in adapter.labels if isinstance(obj, QtWidgets.QMenu) and obj.objectName() == "sphinxsim-menu-simulation")
    menu.popup(QtCore.QPoint(20, 20))
    app.processEvents()
    QtTest.QTest.mouseClick(menu, QtCore.Qt.MouseButton.LeftButton, pos=menu.actionGeometry(action).center())
    assert intents == ["particles"]
    adapter.apply("en", "dark")
    assert item.text(0) == "Particle spacing [m]"
    assert not runtime._json_editor["is_dirty"]()


@pytest.mark.skipif(os.environ.get("SPHINXSIM_DESKTOP_TESTS") != "1", reason="Real native window requires bindings and a display (native Linux CI uses Xvfb)")
def test_real_native_desktop_process_renders_verified_geometry_and_captures_png(tmp_path):
    from .test_jobs import require_native, configuration
    from sphinxsim.desktop.client import request
    require_native()
    (tmp_path / "config.json").write_text(json.dumps(configuration()))
    reference = tmp_path / ".desktop.json"
    log = tmp_path / "desktop.log"
    with log.open("wb") as output:
        process = subprocess.Popen([sys.executable, "-m", "sphinxsim", "desktop", "serve", "config.json",
            "--root", str(tmp_path), "--reference-file", str(reference)], stdout=output, stderr=subprocess.STDOUT)
    try:
        deadline = time.monotonic() + 50
        while not reference.exists() and time.monotonic() < deadline and process.poll() is None:
            time.sleep(.1)
        assert reference.exists(), log.read_text()
        assert reference.stat().st_mode & 0o077 == 0
        state = request(reference, {"operation": "status"})
        request(reference, {"operation": "preview", "instance_id": state["instance_id"], "version": state["version"],
            "expected_hash": state["saved_hash"], "automation_id": "test", "submission_id": str(uuid.uuid4())})
        while time.monotonic() < deadline:
            state = request(reference, {"operation": "status"})
            if state["manifest"]:
                break
            time.sleep(.2)
        assert state["manifest"] and not state["error"], (state, log.read_text())
        captured = request(reference, {"operation": "screenshot", "instance_id": state["instance_id"], "version": state["version"], "automation_id": "test"})
        image = QtGui.QImage.fromData(base64.b64decode(captured["content"][0]["data"]))
        assert not image.isNull() and image.width() >= 800 and image.height() >= 600
        colors = {image.pixel(x, y) for x in range(0, image.width(), 10) for y in range(0, image.height(), 10)}
        assert len(colors) > 20, "Native window capture is blank or unavailable"
        source = json.loads((tmp_path / "config.json").read_text())
        source["geometries"]["global_resolution"]["particle_spacing"] = .08
        (tmp_path / "config.json").write_text(json.dumps(source))
        state = request(reference, {"operation": "status"})
        request(reference, {"operation": "result", "instance_id": state["instance_id"], "version": state["version"],
            "reference": state["reference"], "automation_id": "test"})
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            state = request(reference, {"operation": "status"})
            if state["manifest"]:
                break
            time.sleep(.1)
        assert state["manifest"] and state["read_only"]
        assert state["configuration"]["geometries"]["global_resolution"]["particle_spacing"] == .1
        restored = request(reference, {"operation": "edit", "instance_id": state["instance_id"], "version": state["version"],
            "expected_hash": state["saved_hash"], "automation_id": "test"})
        assert not restored["read_only"]
        assert restored["configuration"]["geometries"]["global_resolution"]["particle_spacing"] == .08
    finally:
        process.terminate()
        process.wait(timeout=10)
