"""Real Qt/WebEngine/Chainlit/native-solver smoke driver, run in an isolated process."""
from pathlib import Path
import json
import os
import sys
import time
import traceback

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ["SPHINXSIM_LLM_PROVIDER"] = "mock"
os.environ.setdefault("QT_API", "pyside6")

from PySide6 import QtCore, QtGui, QtWidgets, QtTest
QtCore.QCoreApplication.setAttribute(QtCore.Qt.ApplicationAttribute.AA_ShareOpenGLContexts)
app = QtWidgets.QApplication([])
from sphinxsim.ui.workbench.window import WorkbenchWindow

workspace = Path(sys.argv[1]).resolve()
workspace.mkdir(parents=True, exist_ok=True)
case = workspace / "case.json"
payload = json.loads((ROOT / "tests/test_simulation/test_2d_simulation/data/column_collapse.json").read_text(encoding="utf-8"))
payload["geometries"]["global_resolution"]["particle_spacing"] = 0.008
payload["solver_parameters"]["end_time"] = 0.00004
payload["solver_parameters"]["output_interval"] = 0.00004
case.write_text(json.dumps(payload, indent=2), encoding="utf-8")
window = WorkbenchWindow(ROOT, workspace, case)
window.show()
print("WINDOW_READY", flush=True)
window.web.loadFinished.connect(lambda ok: print('WEB_LOAD', ok, window.web.url().toString(), flush=True))
result = {"errors": [], "checks": []}
deadline = time.monotonic() + 180
stage = "startup"
busy = False
sent = 0
last_heartbeat = 0


def finish(error=None):
    global stage
    if stage == "finished":
        return
    stage = "finished"
    if error:
        result["errors"].append(str(error))
    window.document.saved_text = window.document.text
    if window.run_job and window.run_job.state() != QtCore.QProcess.ProcessState.NotRunning:
        window.stop_run()
    window.raise_()
    window.activateWindow()
    window.plotter.render()
    def capture_and_close():
        pos = window.mapToGlobal(QtCore.QPoint(0, 0))
        window.plotter.screenshot(str(workspace / "canvas.png"))
        window.grab().save(str(workspace / "workbench.png"))
        select_sidebar(window.editor_panel)
        window.grab().save(str(workspace / "workbench-editor.png"))
        result["geometry"] = {"window": [window.width(), window.height()],
            "available": [window.screen().availableGeometry().width(), window.screen().availableGeometry().height()],
            "sidebar": [window.side_tabs.width(), window.side_tabs.height()],
            "minimum": [window.minimumSizeHint().width(), window.minimumSizeHint().height()]}
        (workspace / "probe.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
        window.close()
        app.quit()
    QtCore.QTimer.singleShot(700, capture_and_close)


def select_sidebar(widget):
    bar = window.side_tabs.tabBar()
    index = window.side_tabs.indexOf(widget)
    QtTest.QTest.mouseClick(bar, QtCore.Qt.MouseButton.LeftButton, pos=bar.tabRect(index).center())
    QtWidgets.QApplication.processEvents()
    assert window.side_tabs.currentWidget() is widget


def send_message(text):
    global busy
    busy = True
    select_sidebar(window.chat_panel)
    script = """(() => {
        const el = document.querySelector('#chat-input') || document.querySelector('[contenteditable="true"]') || document.querySelector('textarea');
        if (!el) return false;
        el.focus(); return true;
    })()"""
    def focused(ok):
        global busy
        if not ok:
            busy = False
            return
        target = window.web.focusProxy() or window.web
        QtTest.QTest.keyClicks(target, text, delay=1)
        QtTest.QTest.keyClick(target, QtCore.Qt.Key.Key_Return)
        busy = False
    QtCore.QTimer.singleShot(100, lambda: window.web.page().runJavaScript(script, focused))


def tick():
    global stage, busy, sent, last_heartbeat
    if time.monotonic() - last_heartbeat > 10:
        last_heartbeat = time.monotonic()
        print(stage, window.chat_loaded, window.web.size(), window.display_label.text(), window.statusBar().currentMessage(), flush=True)
        window.grab().save(str(workspace / "progress.png"))
    if time.monotonic() > deadline:
        finish("Timed out at " + stage + ": " + window.statusBar().currentMessage())
        return
    if stage == "finished" or busy:
        return
    try:
        if stage == "startup" and window.chat_loaded and window.display_label.text().startswith("Draft preview"):
            select_sidebar(window.chat_panel)
            busy = True
            def inspect(text):
                global stage, busy
                busy = False
                if "SPHinXsim Workbench" in text:
                    result["checks"].append("native geometry and embedded Chainlit greeting rendered")
                    from sphinxsim.ui.json_editor import JsonPropertyEditor
                    assert isinstance(window.editor, JsonPropertyEditor)
                    assert window.centralWidget() is window.horizontal_split
                    assert window.horizontal_split.orientation() == QtCore.Qt.Orientation.Horizontal
                    assert window.horizontal_split.count() == 2
                    assert window.side_tabs.count() == 2
                    assert window.side_tabs.tabText(1) == "Chat"
                    web, page, server_pid = window.web, window.web.page(), window.chat_server.process.pid
                    select_sidebar(window.editor_panel)
                    assert window.editor.isVisible() and not window.web.isVisible()
                    assert window.editor.mapTo(window, QtCore.QPoint()).x() > window.viewport.mapTo(window, QtCore.QPoint()).x() + window.viewport.width()
                    end_path = ("solver_parameters", "end_time")
                    original_file = case.read_bytes()
                    field = window.editor._fields[end_path]
                    field.setText("invalid")
                    window.save()
                    assert case.read_bytes() == original_file
                    field.setText("0.00006")
                    assert json.loads(window.document.text)["solver_parameters"]["end_time"] == 0.00006
                    assert window.validate()
                    assert case.read_bytes() == original_file
                    selected = window.editor._items[end_path]
                    selected.parent().setExpanded(True)
                    window.editor.tree.setCurrentItem(selected)
                    window.editor.filter.setText("end_time")
                    draft = window.document.text
                    select_sidebar(window.chat_panel)
                    assert window.web.isVisible() and not window.editor.isVisible()
                    select_sidebar(window.editor_panel)
                    assert window.document.text == draft
                    assert window.editor.tree.currentItem() is selected
                    assert window.editor.filter.text() == "end_time"
                    assert window.web is web and window.web.page() is page
                    assert window.chat_server.process.pid == server_pid
                    assert window.side_tabs.tabText(0).endswith("*")
                    window.editor.filter.clear()
                    result["checks"].append("peer right-sidebar tabs switch by click without losing draft, editor selection or chat page")
                    result["checks"].append("shared property tree edits the live draft and blocks saving invalid field values")
                    assert len(window.plotter.renderers) == 1
                    assert window.plotter.renderer.GetViewport() == (0.0, 0.0, 1.0, 1.0)
                    assert not any("sidebar" in name for name in window.plotter.renderer.actors)
                    import re
                    assert not re.search(r"[\u4e00-\u9fff]", text), "Chainlit UI is not English"
                    result["checks"].append("full-width single Preview has no Information sidebar; embedded chat uses English")
                    window.grab().save(str(workspace / "startup.png"))
                    stage = "proposal"
                    send_message("/config change end time to 0.00008 seconds")
            window.web.page().toPlainText(inspect)
        elif stage == "proposal" and window.document.pending_proposal():
            original = window.document.path.read_text(encoding="utf-8")
            assert json.loads(original)["solver_parameters"]["end_time"] != 0.00008
            window._tick()
            assert window.apply_button.isEnabled()
            assert window.side_tabs.currentWidget() is window.chat_panel, "Proposal stole focus from Chat"
            assert window.side_tabs.tabText(0).endswith("•")
            select_sidebar(window.editor_panel)
            assert window.diff.isVisible()
            window.grab().save(str(workspace / "proposal.png"))
            window.apply_proposal()
            assert window.side_tabs.currentWidget() is window.editor_panel
            assert window.editor.isVisible()
            assert window.side_tabs.tabText(0).endswith("*")
            assert json.loads(window.editor.text())["solver_parameters"]["end_time"] == 0.00008
            end_item = window.editor._items[("solver_parameters", "end_time")]
            end_item.parent().setExpanded(True)
            window.editor.tree.scrollToItem(end_item)
            assert window.document.path.read_text(encoding="utf-8") == original
            result["checks"].append("real browser chat -> validated diff -> Apply to draft without saving")
            window.save()
            stage = "saved"
        elif stage == "saved" and not window.document.snapshot()["dirty"] and window.preview_job.state() == QtCore.QProcess.ProcessState.NotRunning:
            assert json.loads(window.document.path.read_text(encoding="utf-8"))["solver_parameters"]["end_time"] == 0.00008
            result["checks"].append("Save + Preview persists and rebuilds native geometry")
            window.run_saved()
            assert window.run_job is not None
            stage = "run"
        elif stage == "run" and window.run_job.state() == QtCore.QProcess.ProcessState.NotRunning:
            assert window.run_job.exitCode() == 0, "Native run failed; see manual_runs/*/solver.log"
            window._tick()
            assert window.observer is not None
            assert any(name.startswith("particle-preview-") for name in window.plotter.renderer.actors)
            result["checks"].append("real C++ case run renders VTP particles in the same canvas")
            # Validate the displayed Qt pixels as well as VTK actors. A native
            # OpenGL surface can render correctly while its desktop pane is blank.
            QtWidgets.QApplication.processEvents()
            import numpy as np
            frame = window.viewport.grab().toImage().convertToFormat(QtGui.QImage.Format.Format_RGB888)
            pixels = np.frombuffer(frame.bits(), dtype=np.uint8).reshape(frame.height(), frame.bytesPerLine())
            assert pixels.std() > 10, "The visible Qt Preview pane is blank"
            window.plotter.subplot(0, 0)
            camera = window.plotter.camera
            before = (camera.parallel_scale, camera.position)
            point = QtCore.QPointF(200, 200)
            wheel = QtGui.QWheelEvent(point, QtCore.QPointF(window.viewport.mapToGlobal(point.toPoint())),
                        QtCore.QPoint(), QtCore.QPoint(0, 120), QtCore.Qt.MouseButton.NoButton,
                        QtCore.Qt.KeyboardModifier.NoModifier, QtCore.Qt.ScrollPhase.NoScrollPhase, False)
            QtWidgets.QApplication.sendEvent(window.viewport, wheel)
            assert (camera.parallel_scale, camera.position) != before, "Wheel did not change the Preview camera"
            QtTest.QTest.keyClick(window.viewport, QtCore.Qt.Key.Key_R)
            result["checks"].append("visible Qt Preview pixels and real mouse-wheel camera interaction verified")
            window.raise_()
            window.activateWindow()
            window.plotter.screenshot(str(workspace / "run-canvas.png"))
            QtWidgets.QApplication.processEvents()
            pos = window.mapToGlobal(QtCore.QPoint(0, 0))
            window.grab().save(str(workspace / "run.png"))
            select_sidebar(window.chat_panel)
            stage = "chat_restore"
        elif stage == "chat_restore":
            busy = True
            def restored(text):
                global stage, busy
                busy = False
                if "Validated proposal" in text and "/config change end time to 0.00008 seconds" in text:
                    result["checks"].append("Chat restores the prior user message and assistant reply after editing and running in another tab")
                    window.grab().save(str(workspace / "run-chat.png"))
                    stage = "plan"
                    send_message("agent start Calibrate a 2D column-collapse runout extension to 0.2 m.")
            window.web.page().toPlainText(restored)
        elif stage == "plan":
            paths = list((workspace / "store/workflows").glob("*/workflow.json"))
            if paths:
                workflow = json.loads(paths[0].read_text(encoding="utf-8"))
                if workflow.get("status") == "awaiting_approval" or workflow.get("plan_approved") is False:
                    result["checks"].append("embedded Agent start persists a plan requiring approval")
                    window.observe_workflow(paths[0].parent.name)
                    window._tick()
                    assert window.observer.source.workflow_id == paths[0].parent.name
                    assert json.loads(window.editor.text())["solver_parameters"]["end_time"] == 0.00008
                    result["checks"].append("Agent attaches to shared canvas without overwriting the draft")
                    three_d = json.loads((ROOT / "tests/test_simulation/test_3d_simulation/data/dambreak.json").read_text(encoding="utf-8"))
                    three_d["geometries"]["global_resolution"]["particle_spacing"] = 0.1
                    window.editor.set_text(json.dumps(three_d, indent=2), saved_text=window.document.saved_text)
                    window.refresh_preview()
                    stage = "three_d"
        elif stage == "three_d" and window.preview_job.state() == QtCore.QProcess.ProcessState.NotRunning:
            assert window.display_label.text().startswith("Draft preview"), window.editor_status.text()
            assert window.preview_dimension == 3
            assert len(window.plotter.renderer.actors) > 0
            assert len(window.plotter.renderers) == 1
            window.plotter.subplot(0, 0)
            before = window.plotter.camera_position
            QtTest.QTest.mousePress(window.viewport, QtCore.Qt.MouseButton.LeftButton, pos=QtCore.QPoint(160, 160))
            QtTest.QTest.mouseMove(window.viewport, QtCore.QPoint(240, 220), delay=100)
            QtTest.QTest.mouseRelease(window.viewport, QtCore.Qt.MouseButton.LeftButton, pos=QtCore.QPoint(240, 220))
            assert window.plotter.camera_position != before, "3D camera drag did not rotate the scene"
            assert json.loads(case.read_text(encoding="utf-8"))["solver_parameters"]["end_time"] == 0.00008
            result["checks"].append("real 3D native geometry preview and mouse-drag rotation without saving the draft")
            finish()
    except Exception:
        finish(traceback.format_exc())


timer = QtCore.QTimer()
timer.timeout.connect(tick)
timer.start(500)
app.exec()
print(json.dumps(result, indent=2), flush=True)
raise SystemExit(1 if result["errors"] else 0)
