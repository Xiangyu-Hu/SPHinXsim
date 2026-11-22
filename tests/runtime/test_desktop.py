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
from sphinxsim.json_editor import JsonPropertyEditor


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


def test_json_editor_tree_columns_share_width_equally(editor):
    app, runtime, _, _ = editor
    app.processEvents()
    tree = runtime._json_editor["tree"]
    header = tree.header()
    assert header.sectionResizeMode(0) == QtWidgets.QHeaderView.ResizeMode.Stretch
    assert header.sectionResizeMode(1) == QtWidgets.QHeaderView.ResizeMode.Stretch
    assert abs(header.sectionSize(0) - header.sectionSize(1)) <= 1


def test_preview_hosts_the_shared_property_editor_widget(editor):
    _, runtime, _, _ = editor
    hooks = runtime._json_editor
    widget = hooks["widget"]

    assert isinstance(widget, JsonPropertyEditor)
    assert hooks["dock"].widget() is widget
    assert hooks["tree"] is widget.tree
    assert hooks["filter"] is widget.filter
    assert hooks["status"] is widget.status
    assert not widget.buttons
    assert not widget.save_shortcut.isEnabled()


def test_annotation_reveal_expands_selects_and_filters_json_path(editor):
    app, runtime, _, _ = editor
    app.processEvents()
    hooks = runtime._json_editor
    tree = hooks["tree"]
    filter_box = hooks["filter"]
    path = "geometries.global_resolution.particle_spacing"

    filter_box.setText("not a matching setting")
    assert hooks["reveal_path"](path)
    app.processEvents()

    item = tree.currentItem()
    assert item is not None
    assert item.data(0, QtCore.Qt.ItemDataRole.UserRole) == path
    assert item.isSelected()
    assert item.parent().isExpanded()
    assert item.parent().parent().isExpanded()
    assert filter_box.text() == ""


def test_json_context_menu_toggles_matching_preview_elements(editor):
    app, runtime, _, _ = editor
    app.processEvents()
    hooks = runtime._json_editor
    tree = hooks["tree"]

    class Actor:
        def __init__(self):
            self.visible = True

        def GetVisibility(self):
            return self.visible

        def SetVisibility(self, visible):
            self.visible = bool(visible)

    annotation = Actor()
    visualization = Actor()
    hooks["set_preview_elements"](
        {
            "gravity": {
                "annotation": [annotation],
                "visualization": [visualization],
            }
        }
    )
    gravity_item = next(
        item for item in _tree_items(tree)
        if item.data(0, QtCore.Qt.ItemDataRole.UserRole) == "gravity"
    )
    tree.scrollToItem(gravity_item)
    tree.customContextMenuRequested.emit(
        tree.visualItemRect(gravity_item).center()
    )
    app.processEvents()

    annotation_action = tree.findChild(
        QtGui.QAction, "sphinxsim-json-editor-show-annotation"
    )
    visualization_action = tree.findChild(
        QtGui.QAction, "sphinxsim-json-editor-show-visualization"
    )
    assert annotation_action.isEnabled() and annotation_action.isChecked()
    assert visualization_action.isEnabled() and visualization_action.isChecked()

    annotation_action.trigger()
    app.processEvents()
    assert not annotation.visible
    assert visualization.visible

    visualization_action.trigger()
    app.processEvents()
    assert not visualization.visible

    spacing_item = next(
        item for item in _tree_items(tree)
        if item.data(0, QtCore.Qt.ItemDataRole.UserRole)
        == "geometries.global_resolution.particle_spacing"
    )
    tree.customContextMenuRequested.emit(
        tree.visualItemRect(spacing_item).center()
    )
    app.processEvents()
    assert not annotation_action.isEnabled()
    assert not visualization_action.isEnabled()
    tree.findChild(
        QtWidgets.QMenu, "sphinxsim-json-editor-list-context-menu"
    ).hide()


def test_list_context_menu_adds_to_the_right_clicked_list(editor):
    app, runtime, _, _ = editor
    app.processEvents()
    hooks = runtime._json_editor
    tree = hooks["tree"]
    toolbar_texts = {
        button.text()
        for button in hooks["dock"].findChildren(QtWidgets.QToolButton)
    }
    assert toolbar_texts.isdisjoint(
        {"Add", "Duplicate", "Remove", "Move up", "Move down",
         "Expand", "Collapse", "Revert", "Reload"}
    )
    assert set(hooks["context_actions"]) == {
        "add", "duplicate", "remove", "up", "down"
    }
    list_item = None
    iterator = QtWidgets.QTreeWidgetItemIterator(tree)
    while iterator.value():
        candidate = iterator.value()
        if candidate.data(0, QtCore.Qt.ItemDataRole.UserRole) == "solid_bodies":
            list_item = candidate
            break
        iterator += 1
    assert list_item is not None
    tree.scrollToItem(list_item)
    app.processEvents()

    menu = tree.findChild(QtWidgets.QMenu, "sphinxsim-json-editor-list-context-menu")
    assert menu is not None
    position = tree.visualItemRect(list_item).center()
    tree.customContextMenuRequested.emit(position)
    app.processEvents()

    assert tree.currentItem() is list_item
    assert menu.isVisible()
    add_action = next(action for action in menu.actions() if action.text() == "Add")
    assert add_action.isEnabled()
    duplicate_action = next(
        action for action in menu.actions() if action.text() == "Duplicate"
    )
    assert not duplicate_action.isEnabled()
    assert "QMenu::item:enabled" in menu.styleSheet()
    assert "QMenu::item:disabled" in menu.styleSheet()
    add_action.trigger()
    app.processEvents()

    assert len(hooks["payload"]()["solid_bodies"]) == 3
    assert hooks["is_dirty"]()
    menu.hide()


def test_geometry_context_menu_offers_only_missing_properties(editor):
    app, runtime, _, _ = editor
    app.processEvents()
    hooks = runtime._json_editor
    tree = hooks["tree"]
    geometry = next(
        item
        for item in _tree_items(tree)
        if item.data(0, QtCore.Qt.ItemDataRole.UserRole) == "geometries"
    )
    menu = tree.findChild(QtWidgets.QMenu, "sphinxsim-json-editor-list-context-menu")
    assert menu is not None
    tree.customContextMenuRequested.emit(tree.visualItemRect(geometry).center())
    app.processEvents()

    property_menu = hooks["property_menu"]
    property_actions = {action.data(): action for action in property_menu.actions()}
    assert "primitives" in property_actions
    assert "shapes" not in property_actions
    property_actions["primitives"].trigger()
    app.processEvents()

    payload = hooks["payload"]()
    assert payload["geometries"]["primitives"] == [
        {
            "name": "New primitive",
            "type": "box",
            "half_size": [1.0, 1.0, 1.0],
            "transform": {
                "translation": [0.0, 0.0, 0.0],
                "rotation_angle": 0.0,
                "rotation_axis": [0.0, 0.0, 1.0],
            },
        }
    ]
    SimulationConfig.model_validate(payload)
    assert hooks["is_dirty"]()
    menu.hide()


def test_geometry_context_menu_adds_missing_property_without_crashing(editor):
    app, runtime, _, _ = editor
    app.processEvents()
    hooks = runtime._json_editor
    tree = hooks["tree"]
    geometry = next(
        item
        for item in _tree_items(tree)
        if item.data(0, QtCore.Qt.ItemDataRole.UserRole) == "geometries"
    )
    menu = tree.findChild(QtWidgets.QMenu, "sphinxsim-json-editor-list-context-menu")
    assert menu is not None
    tree.customContextMenuRequested.emit(tree.visualItemRect(geometry).center())
    app.processEvents()

    action = next(
        action
        for action in hooks["property_menu"].actions()
        if action.data() == "primitives"
    )
    menu.hide()
    action.trigger()
    app.processEvents()

    payload = hooks["payload"]()
    assert payload["geometries"]["primitives"][0]["type"] == "box"
    assert hooks["is_dirty"]()


def test_shape_list_context_menu_adds_selected_shape_variant(editor):
    app, runtime, _, _ = editor
    app.processEvents()
    hooks = runtime._json_editor
    tree = hooks["tree"]
    shapes = next(
        item
        for item in _tree_items(tree)
        if item.data(0, QtCore.Qt.ItemDataRole.UserRole) == "geometries.shapes"
    )
    menu = tree.findChild(QtWidgets.QMenu, "sphinxsim-json-editor-list-context-menu")
    assert menu is not None
    tree.customContextMenuRequested.emit(tree.visualItemRect(shapes).center())
    app.processEvents()

    variant_menu = hooks["variant_menu"]
    cylinder = next(
        action
        for action in variant_menu.actions()
        if action.data() == "cylinder"
    )
    cylinder.trigger()
    app.processEvents()

    payload = hooks["payload"]()
    added_shape = payload["geometries"]["shapes"][-1]
    assert added_shape["name"] == "New shape"
    assert added_shape["type"] == "cylinder"
    assert added_shape["radius"] == 1.0
    assert added_shape["half_height"] == 1.0
    assert added_shape["transform"]["translation"] == [0.0, 0.0, 0.0]
    SimulationConfig.model_validate(payload)
    assert hooks["is_dirty"]()
    menu.hide()


def test_field_context_menu_copies_and_pastes_values(editor):
    app, runtime, _, path = editor
    app.processEvents()
    QtWidgets.QApplication.clipboard().clear()
    hooks = runtime._json_editor
    tree = hooks["tree"]
    payload = json.loads(path.read_text())
    payload["geometries"]["oriented_boxes"] = [
        {
            "name": "RegionA",
            "type": "region",
            "half_size": [1.0, 1.0, 1.0],
            "transform": {
                "translation": [0.0, 0.0, 0.0],
                "rotation_angle": 0.0,
                "rotation_axis": [0.0, 0.0, 1.0],
            },
        },
        {
            "name": "RegionB",
            "type": "region",
            "half_size": [1.0, 1.0, 1.0],
            "transform": {
                "translation": [0.0, 0.0, 0.0],
                "rotation_angle": 0.0,
                "rotation_axis": [0.0, 0.0, 1.0],
            },
        },
    ]
    config = SimulationConfig.model_validate(payload)
    path.write_text(json.dumps(payload))
    assert hooks["refresh"](config, path, False)
    app.processEvents()

    def field_item(path):
        return next(
            item
            for item in _tree_items(tree)
            if item.data(0, QtCore.Qt.ItemDataRole.UserRole) == path
        )

    source_path = "geometries.oriented_boxes.0.half_size"
    target_path = "geometries.oriented_boxes.1.half_size"
    assert hooks["reveal_path"](source_path)
    app.processEvents()
    source_item = field_item(source_path)
    source_widget = tree.itemWidget(source_item, 1)
    source_widget.setText("[0.2, 0.3, 0.4]")
    app.processEvents()

    value_actions = hooks["value_actions"]
    tree.customContextMenuRequested.emit(tree.visualItemRect(source_item).center())
    app.processEvents()
    assert value_actions["copy"].isEnabled()
    assert not value_actions["paste"].isEnabled()
    menu = tree.findChild(
        QtWidgets.QMenu, "sphinxsim-json-editor-list-context-menu"
    )
    assert menu is not None
    menu.hide()
    value_actions["copy"].trigger()
    app.processEvents()
    assert QtWidgets.QApplication.clipboard().text() == "[0.2, 0.3, 0.4]"

    assert hooks["reveal_path"](target_path)
    app.processEvents()
    target_item = field_item(target_path)
    tree.customContextMenuRequested.emit(tree.visualItemRect(target_item).center())
    app.processEvents()
    assert value_actions["paste"].isEnabled()
    menu.hide()
    value_actions["paste"].trigger()
    app.processEvents()
    assert hooks["payload"]()["geometries"]["oriented_boxes"][1]["half_size"] == [
        0.2,
        0.3,
        0.4,
    ]
    assert hooks["is_dirty"]()
    assert hooks["settings_actions"]["revert"].isEnabled()


def test_object_context_menu_copies_and_pastes_transform(editor):
    app, runtime, _, path = editor
    app.processEvents()
    QtWidgets.QApplication.clipboard().clear()
    hooks = runtime._json_editor
    tree = hooks["tree"]
    payload = json.loads(path.read_text())
    payload["geometries"]["oriented_boxes"] = [
        {
            "name": "RegionA",
            "type": "region",
            "half_size": [1.0, 1.0, 1.0],
            "transform": {
                "translation": [0.2, 0.3, 0.4],
                "rotation_angle": 0.5,
                "rotation_axis": [0.0, 0.0, 1.0],
            },
        },
        {
            "name": "RegionB",
            "type": "region",
            "half_size": [2.0, 2.0, 2.0],
            "transform": {
                "translation": [0.0, 0.0, 0.0],
                "rotation_angle": 0.0,
                "rotation_axis": [1.0, 0.0, 0.0],
            },
        },
    ]
    config = SimulationConfig.model_validate(payload)
    path.write_text(json.dumps(payload))
    assert hooks["refresh"](config, path, False)
    app.processEvents()

    def select_path(path_key):
        assert hooks["reveal_path"](path_key)
        app.processEvents()
        item = tree.currentItem()
        assert item is not None
        assert item.data(0, QtCore.Qt.ItemDataRole.UserRole) == path_key
        tree.customContextMenuRequested.emit(tree.visualItemRect(item).center())
        app.processEvents()
        return item

    menu = tree.findChild(
        QtWidgets.QMenu, "sphinxsim-json-editor-list-context-menu"
    )
    assert menu is not None
    value_actions = hooks["value_actions"]
    select_path("geometries.oriented_boxes.0.transform")
    assert value_actions["copy"].isEnabled()
    menu.hide()
    value_actions["copy"].trigger()
    app.processEvents()
    assert json.loads(QtWidgets.QApplication.clipboard().text()) == payload[
        "geometries"
    ]["oriented_boxes"][0]["transform"]

    select_path("geometries.oriented_boxes.1.transform")
    assert value_actions["paste"].isEnabled()
    menu.hide()
    value_actions["paste"].trigger()
    app.processEvents()

    updated = hooks["payload"]()["geometries"]["oriented_boxes"]
    assert updated[1]["transform"] == updated[0]["transform"]
    SimulationConfig.model_validate(hooks["payload"]())
    assert hooks["is_dirty"]()


def test_field_context_menu_rejects_incompatible_paste(editor):
    app, runtime, _, _ = editor
    app.processEvents()
    hooks = runtime._json_editor
    tree = hooks["tree"]
    vector_item = next(
        item
        for item in _tree_items(tree)
        if item.data(0, QtCore.Qt.ItemDataRole.UserRole)
        == "geometries.system_domain.lower_bound"
    )
    QtWidgets.QApplication.clipboard().setText("1")
    tree.customContextMenuRequested.emit(tree.visualItemRect(vector_item).center())
    app.processEvents()
    hooks["value_actions"]["paste"].trigger()
    app.processEvents()

    assert hooks["payload"]()["geometries"]["system_domain"]["lower_bound"] == [
        0,
        0,
        0,
    ]
    assert "Cannot paste value" in hooks["status"].text()


def test_field_context_menu_removes_optional_geometry_properties(editor):
    app, runtime, _, path = editor
    app.processEvents()
    hooks = runtime._json_editor
    tree = hooks["tree"]
    payload = json.loads(path.read_text())
    payload["geometries"]["oriented_boxes"] = [
        {
            "name": "Emitter",
            "type": "region",
            "half_size": [0.1, 0.1, 0.1],
            "transform": {
                "translation": [0.0, 0.0, 0.0],
                "rotation_angle": 0.0,
                "rotation_axis": [0.0, 0.0, 1.0],
            },
        }
    ]
    config = SimulationConfig.model_validate(payload)
    path.write_text(json.dumps(payload))
    assert hooks["refresh"](config, path, False)
    app.processEvents()

    menu = tree.findChild(QtWidgets.QMenu, "sphinxsim-json-editor-list-context-menu")
    assert menu is not None
    remove_action = hooks["remove_property_action"]
    for field_name in ("half_size", "transform"):
        field_path = f"geometries.oriented_boxes.0.{field_name}"
        assert hooks["reveal_path"](field_path)
        app.processEvents()
        field_item = tree.currentItem()
        tree.customContextMenuRequested.emit(tree.visualItemRect(field_item).center())
        app.processEvents()
        assert remove_action.isEnabled()
        menu.hide()
        remove_action.trigger()
        app.processEvents()

    oriented_box = hooks["payload"]()["geometries"]["oriented_boxes"][0]
    assert "half_size" not in oriented_box
    assert "transform" not in oriented_box
    assert hooks["is_dirty"]()


def test_setting_header_menu_expands_and_collapses_tree(editor):
    app, runtime, _, _ = editor
    app.processEvents()
    hooks = runtime._json_editor
    tree = hooks["tree"]
    header = tree.header()
    menu = hooks["settings_menu"]
    assert menu.objectName() == "sphinxsim-json-editor-settings-menu"
    assert header.model().headerData(0, QtCore.Qt.Orientation.Horizontal) == "Setting ▾"
    position = QtCore.QPoint(
        header.sectionViewportPosition(0) + 10,
        header.viewport().height() // 2,
    )
    QtTest.QTest.mouseClick(
        header.viewport(), QtCore.Qt.MouseButton.LeftButton, pos=position
    )
    app.processEvents()

    actions = hooks["settings_actions"]
    assert menu.isVisible()
    assert actions["expand"].isEnabled()
    assert actions["collapse"].isEnabled()
    assert not actions["revert"].isEnabled()
    assert actions["reload"].isEnabled()

    actions["expand"].trigger()
    app.processEvents()
    assert all(item.isExpanded() for item in _tree_items(tree) if item.childCount())
    actions["collapse"].trigger()
    app.processEvents()
    assert all(not item.isExpanded() for item in _tree_items(tree) if item.childCount())
    menu.hide()


def _tree_items(tree):
    iterator = QtWidgets.QTreeWidgetItemIterator(tree)
    while iterator.value():
        yield iterator.value()
        iterator += 1


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
    context_actions = runtime._json_editor["context_actions"]
    settings_actions = runtime._json_editor["settings_actions"]
    assert context_actions["add"].text() == "添加"
    assert settings_actions["expand"].text() == "展开"
    assert settings_actions["reload"].text() == "重新载入"
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
    assert context_actions["add"].text() == "Add"
    assert settings_actions["expand"].text() == "Expand"
    assert settings_actions["reload"].text() == "Reload"
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
