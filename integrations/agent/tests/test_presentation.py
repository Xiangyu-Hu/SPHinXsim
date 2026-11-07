"""Run with Qt installed from the SPHinXsim root. Native editor boundary only.

The renderer facade intentionally omits GPU rendering; this does not prove H20
geometry, solver or the full managed application. Original editor hooks are real.
"""
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace


def main():
    sys.path.insert(0, str(Path(__file__).parent))
    from qtpy import QtCore, QtTest, QtWidgets
    from sphinxsim import cli
    from sphinxsim.config.schemas import SimulationConfig
    from sphinxsim_agent.desktop.presentation import Presentation

    app = QtWidgets.QApplication([])
    with tempfile.TemporaryDirectory() as directory:
        entry = Path(directory) / "config.json"
        entry.write_bytes((Path(__file__).parent / "fixtures/fixed-ship.json").read_bytes())
        config = SimulationConfig.model_validate_json(entry.read_text())
        window = QtWidgets.QMainWindow()
        window.resize(1400, 900)
        window.setCentralWidget(QtWidgets.QLabel("Native editor boundary"))
        window.show()
        window.activateWindow()
        runtime = cli._ShellPreviewRuntime()
        runtime.plotter = SimpleNamespace(app_window=window, reset_camera=lambda: None)
        runtime._using_background_plotter = True
        rebuild = []
        runtime.show_or_update = lambda *args, **kwargs: rebuild.append(kwargs)
        runtime._install_json_editor(config, config_path=entry, with_particles=False)
        actions, intents = [], []
        for key in ("particles", "continue", "cancel"):
            action = window.addAction(key)
            action.triggered.connect(lambda _, name=key: intents.append(name))
            actions.append((key, key, key, action))
        adapter = Presentation(runtime, actions)
        adapter.apply("zh", "light")
        app.processEvents()
        QtTest.QTest.qWait(300)
        menu_names = [action.text() for action in window.menuBar().actions()]
        assert menu_names == ["文件", "编辑", "视图", "仿真", "帮助"]
        assert not runtime._json_editor["is_dirty"](), "Translation marked the config dirty"
        path = "geometries.global_resolution.particle_spacing"
        item = next(item for item in adapter.items() if item.data(0, QtCore.Qt.ItemDataRole.UserRole) == path)
        assert item.text(0) == "粒子间距 [m]" and item.toolTip(0) == path
        tree = runtime._json_editor["tree"]
        tree.scrollToItem(item)
        field = tree.itemWidget(item, 1)
        field.setFocus()
        QtTest.QTest.keyClick(field, QtCore.Qt.Key.Key_A, QtCore.Qt.KeyboardModifier.ControlModifier)
        QtTest.QTest.keyClicks(field, "0.06")
        assert runtime._json_editor["is_dirty"]()
        QtTest.QTest.keyClick(field, QtCore.Qt.Key.Key_S, QtCore.Qt.KeyboardModifier.ControlModifier)
        app.processEvents()
        assert json.loads(entry.read_text())["geometries"]["global_resolution"]["particle_spacing"] == 0.06, runtime._json_editor["status"].text()
        assert not runtime._json_editor["is_dirty"]()
        assert len(rebuild) == 1
        adapter.apply("en", "dark")
        app.processEvents()
        item = next(item for item in adapter.items() if item.data(0, QtCore.Qt.ItemDataRole.UserRole) == path)
        assert item.text(0) == "Particle spacing [m]"
        assert not runtime._json_editor["is_dirty"]()
        menu = next(obj for obj, _, _ in adapter.labels if isinstance(obj, QtWidgets.QMenu) and obj.objectName() == "sphinxsim-menu-simulation")
        menu.popup(QtCore.QPoint(20, 20))
        app.processEvents()
        QtTest.QTest.mouseClick(menu, QtCore.Qt.MouseButton.LeftButton, pos=menu.actionGeometry(actions[0][3]).center())
        app.processEvents()
        assert intents == ["particles"]
        assert json.loads(entry.read_text())["geometries"]["global_resolution"]["particle_spacing"] == 0.06
        window.close()
        print("PASS: translated menus/paths, no false dirty state, native Ctrl+S persisted, original intent QAction")


if __name__ == "__main__":
    main()
