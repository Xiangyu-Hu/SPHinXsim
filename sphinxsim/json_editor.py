"""Qt-backed JSON configuration editor for preview windows."""

from __future__ import annotations

import copy
import json
import sys
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from sphinxsim.config.schemas import SimulationConfig
from sphinxsim.json_format import (
    find_prettier_options,
    format_json,
    load_json_preserving_floats,
    object_layout,
)
from sphinxsim.llm.common import dump_simulation_config_json


def _apply_payload_diff(raw: Any, base: Any, edited: Any) -> Any:
    """Return *raw* with only the differences between *base* and *edited* applied."""
    if base == edited:
        return raw
    if isinstance(raw, dict) and isinstance(base, dict) and isinstance(edited, dict):
        result = dict(raw)
        for key, value in edited.items():
            if key not in base:
                result[key] = value
            elif key not in raw:
                if value != base[key]:
                    result[key] = value
            else:
                result[key] = _apply_payload_diff(raw[key], base[key], value)
        for key in base:
            if key not in edited:
                result.pop(key, None)
        return result
    if (
        isinstance(raw, list)
        and isinstance(base, list)
        and isinstance(edited, list)
        and len(raw) == len(base) == len(edited)
    ):
        return [_apply_payload_diff(r, b, e) for r, b, e in zip(raw, base, edited)]
    return edited


def _config_spatial_dim(config: SimulationConfig) -> int:
    """Infer whether a validated config should use the 2-D or 3-D native module."""
    geo = config.geometries

    if geo.system_domain is not None:
        return len(geo.system_domain.lower_bound)

    if config.gravity is not None:
        return len(config.gravity)

    for constraint in config.body_constraints:
        if constraint.type.value == "simbody":
            if (constraint.mobilized_body or "").lower() == "planar":
                return 2
            if constraint.velocity is not None:
                return len(constraint.velocity)

    for shape in geo.shapes:
        for vec in (shape.lower_bound, shape.upper_bound, shape.half_size):
            if vec is not None:
                return len(vec)
        if shape.transform is not None:
            return len(shape.transform.translation)
        if shape.translation is not None:
            return len(shape.translation)

    for oriented_box in geo.oriented_boxes:
        for vec in (oriented_box.center, oriented_box.normal, oriented_box.half_size):
            if vec is not None:
                return len(vec)
        if oriented_box.transform is not None:
            return len(oriented_box.transform.translation)

    return 3


def _new_json_editor_list_item(path: tuple[Any, ...], spatial_dim: int) -> Any:
    """Return an editable starter value for an empty JSON editor list."""
    normalized_path = tuple(part for part in path if not isinstance(part, int))
    vector = [0.0] * spatial_dim
    unit_vector = [1.0] * spatial_dim
    transform = {
        "translation": vector,
        "rotation_angle": 0.0,
    }
    if spatial_dim == 3:
        transform["rotation_axis"] = [0.0, 0.0, 1.0]

    templates: dict[tuple[Any, ...], Any] = {
        ("characteristic_dimensions",): {
            "name": "Length",
            "value": 1.0,
            "hint": "Characteristic length",
        },
        ("geometries", "primitives"): {
            "name": "New primitive",
            "type": "box",
            "half_size": unit_vector,
            "transform": transform,
        },
        ("geometries", "shapes"): {
            "name": "New shape",
            "type": "bounding_box",
            "lower_bound": vector,
            "upper_bound": unit_vector,
        },
        ("geometries", "shapes", "polygons"): {
            "operation": "union",
            "type": "bounding_box",
            "lower_bound": vector,
            "upper_bound": unit_vector,
        },
        ("geometries", "oriented_boxes"): {
            "name": "New region",
            "type": "region",
            "half_size": unit_vector,
            "transform": transform,
        },
        ("particle_generation", "settings", "bodies"): {"name": "New body"},
        ("particle_generation", "settings", "relaxation_constraints"): {
            "body_name": "",
            "oriented_box": "",
            "type": "",
        },
        ("fluid_bodies",): {
            "name": "New fluid body",
            "material": {"type": "weakly_compressible_fluid", "density": 1000.0},
        },
        ("continuum_bodies",): {
            "name": "New continuum body",
            "material": {
                "type": "general_continuum",
                "density": 1000.0,
                "sound_speed": 100.0,
                "youngs_modulus": 1000000.0,
                "poisson_ratio": 0.3,
            },
        },
        ("solid_bodies",): {
            "name": "New solid body",
            "material": {"type": "rigid_body"},
        },
        ("observers",): {
            "name": "New observer",
            "observed_body": "",
            "variable": {"real_type": "Pressure"},
            "positions": [vector],
        },
        ("fluid_boundary_conditions",): {
            "body_name": "",
            "oriented_box": "",
            "type": "emitter",
            "inflow_speed": 1.0,
        },
        ("body_constraints",): {"body_name": "", "type": "fixed"},
        ("initial_conditions",): {
            "body_name": "",
            "assignments": [{"variable": {"real_type": "Pressure"}, "value": 0.0}],
        },
        ("initial_conditions", "assignments"): {
            "variable": {"real_type": "Pressure"},
            "value": 0.0,
        },
        ("extra_state_recording",): {
            "name": "New recording",
            "variables": [{"real_type": ["Pressure"]}],
        },
        ("extra_state_recording", "variables"): {"real_type": ["Pressure"]},
        ("energy_recording",): {"name": "New energy recording", "body": ""},
    }
    return copy.deepcopy(templates.get(normalized_path, {}))


def _set_windows_dark_title_bar(window: Any) -> None:
    """Request Windows dark mode for a Qt top-level window's native title bar."""
    if sys.platform != "win32":
        return

    try:
        import ctypes

        hwnd = int(window.winId())
        enabled = ctypes.c_int(1)
        dwmapi = ctypes.windll.dwmapi
        for attribute in (20, 19):  # Windows 10 build-dependent dark-mode IDs.
            result = dwmapi.DwmSetWindowAttribute(
                hwnd,
                attribute,
                ctypes.byref(enabled),
                ctypes.sizeof(enabled),
            )
            if result == 0:
                break
    except Exception:
        pass


class JsonEditorMixin:
    def _install_json_editor(
        self,
        config: SimulationConfig,
        *,
        config_path: Path,
        with_particles: bool,
    ) -> bool:
        """Create or rebind the save-only property editor for a preview config.

        Returns ``False`` when the user cancels a refresh that would discard
        unapplied editor changes.
        """
        if self.plotter is None:
            return True

        if self._json_editor is not None:
            refresh = self._json_editor.get("refresh")
            if callable(refresh):
                return refresh(config, config_path, with_particles) is not False
            return True

        try:
            from qtpy import QtCore, QtGui, QtWidgets
        except Exception:
            return True

        app_window = getattr(self.plotter, "app_window", None)
        if app_window is None:
            return True
        _set_windows_dark_title_bar(app_window)
        app_window.setStyleSheet(
            "QMainWindow { background: #181a1f; }"
            "QMenuBar { background: #202329; color: #e6e8ec; border-bottom: 1px solid #3b4049; }"
            "QMenuBar::item { padding: 5px 9px; background: transparent; }"
            "QMenuBar::item:selected { background: #30343c; color: #ffffff; }"
            "QMenu { background: #24272d; color: #e6e8ec; border: 1px solid #454b55; }"
            "QMenu::item { padding: 5px 24px 5px 10px; }"
            "QMenu::item:selected { background: #315f8c; color: #ffffff; }"
            "QToolBar { background: #202329; border: 0; border-bottom: 1px solid #3b4049; spacing: 3px; }"
            "QToolBar QToolButton { min-width: 26px; min-height: 26px; padding: 2px; "
            "background: #24272d; color: #f0f2f5; border: 1px solid #4a505b; border-radius: 3px; }"
            "QToolBar QToolButton:hover { background: #30343c; color: #ffffff; border-color: #75a9d0; }"
            "QToolBar QToolButton:pressed { background: #315f8c; color: #ffffff; border-color: #80bde8; }"
            "QToolBar QToolButton:disabled { background: #202329; color: #6f7680; border-color: #343a43; }"
            "QStatusBar { background: #202329; color: #c8ccd4; border-top: 1px solid #3b4049; }"
            "QSplitter::handle { background: #3b4049; }"
            "QSplitter::handle:hover { background: #5794cf; }"
        )

        dock = QtWidgets.QDockWidget("Simulation Properties", app_window)
        dock.setObjectName("sphinxsim-json-editor")
        dock.setStyleSheet(
            "QDockWidget { color: #e6e8ec; background: #181a1f; }"
            "QDockWidget::title { padding: 7px 8px; background: #202329; color: #f0f2f5; "
            "border-bottom: 1px solid #3b4049; text-align: left; }"
        )
        dock.setAllowedAreas(QtCore.Qt.DockWidgetArea.RightDockWidgetArea)
        content = QtWidgets.QWidget(dock)
        content.setObjectName("sphinxsim-json-editor-content")
        content.setStyleSheet(
            "QWidget#sphinxsim-json-editor-content {"
            "    background: #181a1f;"
            "    color: #e6e8ec;"
            "}"
            "QLabel {"
            "    color: #d7dae0;"
            "}"
            "QToolButton {"
            "    min-height: 24px;"
            "    padding: 2px 5px;"
            "    border: 1px solid #4a505b;"
            "    border-radius: 3px;"
            "    background: #24272d;"
            "    color: #d7dae0;"
            "}"
            "QToolButton:hover {"
            "    background: #30343c;"
            "    border: 1px solid #61707e;"
            "    color: #ffffff;"
            "}"
            "QToolButton:pressed {"
            "    background: #3a404a;"
            "    border: 1px solid #7b8997;"
            "    color: #ffffff;"
            "}"
            "QToolButton:disabled {"
            "    background: #202329;"
            "    border: 1px solid #343a43;"
            "    color: #686e79;"
            "}"
            "QToolButton#sphinxsim-json-editor-list-action {"
            "    min-height: 28px;"
            "    font-weight: 600;"
            "    background: #2f6f9f;"
            "    border: 1px solid #235a84;"
            "    color: #ffffff;"
            "}"
            "QToolButton#sphinxsim-json-editor-list-action:hover {"
            "    background: #245d88;"
            "    border-color: #164969;"
            "}"
            "QToolButton#sphinxsim-json-editor-list-action:pressed {"
            "    background: #1c4c70;"
            "    border-color: #123b59;"
            "}"
            "QToolButton#sphinxsim-json-editor-list-action:disabled {"
            "    background: #25313b;"
            "    border: 1px solid #374956;"
            "    color: #788895;"
            "}"
        )
        layout = QtWidgets.QVBoxLayout(content)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(8)
        filter_box = QtWidgets.QLineEdit(content)
        filter_box.setObjectName("sphinxsim-json-editor-filter")
        filter_box.setPlaceholderText("Filter settings…")
        filter_box.setStyleSheet(
            "min-height: 27px; padding: 1px 7px; border: 1px solid #454b55; border-radius: 3px; "
            "background: #24272d; color: #e6e8ec; selection-background-color: #315f8c;"
        )
        tools_grid = QtWidgets.QGridLayout()
        tools_grid.setHorizontalSpacing(4)
        tools_grid.setVerticalSpacing(4)
        tools_grid.setContentsMargins(0, 0, 0, 0)
        expand_button = QtWidgets.QToolButton(content)
        expand_button.setText("Expand")
        collapse_button = QtWidgets.QToolButton(content)
        collapse_button.setText("Collapse")
        revert_button = QtWidgets.QToolButton(content)
        revert_button.setText("Revert")
        reload_button = QtWidgets.QToolButton(content)
        reload_button.setText("Reload")
        add_item_button = QtWidgets.QToolButton(content)
        add_item_button.setText("Add")
        duplicate_item_button = QtWidgets.QToolButton(content)
        duplicate_item_button.setText("Duplicate")
        remove_item_button = QtWidgets.QToolButton(content)
        remove_item_button.setText("Remove")
        move_up_button = QtWidgets.QToolButton(content)
        move_up_button.setText("Move up")
        move_down_button = QtWidgets.QToolButton(content)
        move_down_button.setText("Move down")
        list_action_buttons = (
            add_item_button,
            duplicate_item_button,
            remove_item_button,
            move_up_button,
            move_down_button,
        )
        for button in list_action_buttons:
            button.setObjectName("sphinxsim-json-editor-list-action")
        tool_buttons = (
            expand_button,
            collapse_button,
            revert_button,
            reload_button,
            add_item_button,
            duplicate_item_button,
            remove_item_button,
            move_up_button,
            move_down_button,
        )
        for index, button in enumerate(tool_buttons):
            button.setToolButtonStyle(
                QtCore.Qt.ToolButtonStyle.ToolButtonTextOnly
            )
            button.setMinimumWidth(0)
            button.setSizePolicy(
                QtWidgets.QSizePolicy.Policy.Expanding,
                QtWidgets.QSizePolicy.Policy.Fixed,
            )
            row = 0 if index < 5 else 1
            column = index if index < 5 else index - 5
            tools_grid.addWidget(button, row, column)
            button.setVisible(True)

        for column in range(5):
            tools_grid.setColumnStretch(column, 1)
        tree = QtWidgets.QTreeWidget(content)
        tree.setObjectName("sphinxsim-json-editor-tree")
        tree.setColumnCount(2)
        tree.setHeaderLabels(["Setting", "Value"])
        tree.setRootIsDecorated(True)
        tree.setAlternatingRowColors(True)
        tree.setUniformRowHeights(True)
        tree.setIndentation(16)
        tree.setColumnWidth(0, 145)
        tree.header().setStretchLastSection(True)
        tree.setStyleSheet(
            "QTreeWidget { border: 1px solid #454b55; border-radius: 3px; background: #202329; color: #e1e4e8; }"
            "QHeaderView::section { padding: 5px 7px; border: 0; border-bottom: 1px solid #454b55; "
            "background: #292d34; color: #f0f2f5; font-weight: 600; }"
            "QTreeWidget::item { min-height: 30px; color: #e1e4e8; }"
            "QTreeWidget::item:alternate { background: #252930; }"
            "QTreeWidget::item:selected { background: #315f8c; color: #ffffff; }"
            "QLineEdit, QComboBox { min-height: 25px; padding: 1px 6px; "
            "border: 1px solid #4b515c; border-radius: 3px; background: #181a1f; color: #e6e8ec; }"
            "QLineEdit:focus, QComboBox:focus { border: 1px solid #5794cf; }"
            "QLineEdit::selection { background: #315f8c; color: #ffffff; }"
            "QComboBox QAbstractItemView { background: #24272d; color: #e6e8ec; border: 1px solid #4b515c; "
            "selection-background-color: #315f8c; selection-color: #ffffff; outline: 0; }"
        )
        status = QtWidgets.QLabel("Loaded configuration. Press Ctrl+S to save changes.")
        status.setWordWrap(True)
        status.setAlignment(
            QtCore.Qt.AlignmentFlag.AlignLeft | QtCore.Qt.AlignmentFlag.AlignVCenter
        )
        status.setStyleSheet(
            "padding: 7px 9px; border: 1px solid #3d6f9e; border-radius: 4px; "
            "background: #1f3042; color: #d9e8f5;"
        )
        layout.addWidget(filter_box)
        layout.addLayout(tools_grid)
        layout.addWidget(tree, 1)
        layout.addWidget(status)
        dock.setWidget(content)
        # Give the right dock both right-hand corners so it spans the same full
        # top-to-bottom extent as the preview area instead of appearing offset.
        app_window.setCorner(
            QtCore.Qt.Corner.TopRightCorner,
            QtCore.Qt.DockWidgetArea.RightDockWidgetArea,
        )
        app_window.setCorner(
            QtCore.Qt.Corner.BottomRightCorner,
            QtCore.Qt.DockWidgetArea.RightDockWidgetArea,
        )
        app_window.addDockWidget(QtCore.Qt.DockWidgetArea.RightDockWidgetArea, dock)

        json_editor_initial_width = 360
        json_editor_minimum_width = 260

        dock.setMinimumWidth(json_editor_minimum_width)
        dock.setMaximumWidth(16777215)
        content.setMinimumWidth(0)
        content.setMaximumWidth(16777215)
        tree.setMinimumWidth(0)
        tree.setMaximumWidth(16777215)

        dock.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Preferred,
            QtWidgets.QSizePolicy.Policy.Expanding,
        )
        content.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Expanding,
            QtWidgets.QSizePolicy.Policy.Expanding,
        )
        tree.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Expanding,
            QtWidgets.QSizePolicy.Policy.Expanding,
        )

        def _set_initial_json_editor_width() -> None:
            try:
                app_window.resizeDocks(
                    [dock],
                    [json_editor_initial_width],
                    QtCore.Qt.Orientation.Horizontal,
                )
            except Exception:
                pass

        QtCore.QTimer.singleShot(0, _set_initial_json_editor_width)
        QtCore.QTimer.singleShot(100, _set_initial_json_editor_width)

        editor_state: dict[str, Any] = {
            "payload": {},
            "config_path": config_path,
            "with_particles": with_particles,
            "fields": [],
            "original_json": "",
            "baseline_payload": {},
            "undo_stack": [],
            "dirty": False,
            "expanded_paths": set(),
            "item_paths": {},
            "list_paths": set(),
        }

        def _new_tree_item(
            parent: Any | None,
            label: str,
            summary: str = "",
        ) -> Any:
            item = QtWidgets.QTreeWidgetItem([label, summary])
            if parent is None:
                tree.addTopLevelItem(item)
            else:
                parent.addChild(item)
            return item

        field_labels = {
            "simulation_type": "Simulation type",
            "particle_spacing": "Particle spacing [m]",
            "characteristic_length_particles": "Characteristic particles",
            "lower_bound": "Lower bound [m]",
            "upper_bound": "Upper bound [m]",
            "half_size": "Half size [m]",
            "translation": "Translation [m]",
            "center": "Center [m]",
            "radius": "Radius [m]",
            "density": "Density [kg/m³]",
            "sound_speed": "Sound speed [m/s]",
            "viscosity": "Viscosity [Pa·s]",
            "youngs_modulus": "Young's modulus [Pa]",
            "yield_stress": "Yield stress [Pa]",
            "cohesion": "Cohesion [Pa]",
            "friction_angle": "Friction angle [rad]",
            "dilatancy_angle": "Dilatancy angle [rad]",
            "end_time": "End time [s]",
            "output_interval": "Output interval [s]",
            "screen_interval": "Screen interval",
            "inflow_speed": "Inflow speed [m/s]",
            "gravity": "Gravity [m/s²]",
        }
        enum_choices = {
            "simulation_type": ["fluid_dynamics", "continuum_dynamics"],
            "surface_type": ["free_surface", "confined", "open_boundary", "free_stream"],
            "kernel_correction": ["linear", "none"],
        }

        def _display_label(key: str) -> str:
            if key.startswith("["):
                return key
            return field_labels.get(key, key.replace("_", " ").capitalize())

        def _entry_label(value: Any, index: int) -> str:
            if isinstance(value, dict):
                for identity_key in ("name", "body_name", "observed_body", "oriented_box"):
                    identity = value.get(identity_key)
                    if isinstance(identity, str) and identity:
                        return identity
            return f"Item {index + 1}"

        def _mark_dirty(item: Any | None = None) -> None:
            editor_state["dirty"] = True
            revert_button.setEnabled(True)
            if item is not None:
                item.setBackground(0, QtGui.QColor("#594a24"))
            status.setText("Unsaved changes. Press Ctrl+S to validate, save, and refresh.")

        def _clear_field_errors() -> None:
            for _, _, widget, item in editor_state["fields"]:
                widget.setStyleSheet("")
                widget.setToolTip("")
                item.setToolTip(0, "")

        def _value_widget(value: Any, key: str) -> Any:
            if isinstance(value, bool):
                widget = QtWidgets.QCheckBox(tree)
                widget.setChecked(value)
                return widget

            if key in enum_choices and isinstance(value, str):
                widget = QtWidgets.QComboBox(tree)
                widget.addItems(enum_choices[key])
                if value not in enum_choices[key]:
                    widget.addItem(value)
                widget.setCurrentText(value)
                widget.view().setStyleSheet(
                    "QAbstractItemView { background: #24272d; color: #e6e8ec; "
                    "selection-background-color: #315f8c; selection-color: #ffffff; }"
                )
                return widget

            if isinstance(value, int):
                widget = QtWidgets.QLineEdit(tree)
                widget.setText(str(value))
                return widget

            if isinstance(value, float):
                widget = QtWidgets.QLineEdit(tree)
                widget.setText(str(value))
                return widget

            widget = QtWidgets.QLineEdit(tree)
            if isinstance(value, str):
                widget.setText(value)
            else:
                widget.setText(json.dumps(value, ensure_ascii=False))
            return widget

        def populate_editor(
            text: str,
            *,
            reset_history: bool = False,
            expand_defaults: bool = True,
            additional_expanded_paths: set[str] | None = None,
        ) -> None:
            expanded_paths: set[str] = set()
            iterator = QtWidgets.QTreeWidgetItemIterator(tree)
            while iterator.value() is not None:
                previous_item = iterator.value()
                path_key = previous_item.data(0, QtCore.Qt.ItemDataRole.UserRole)
                if path_key is not None and previous_item.isExpanded():
                    expanded_paths.add(path_key)
                iterator += 1
            expanded_paths.update(additional_expanded_paths or set())
            editor_state["expanded_paths"] = expanded_paths

            payload = json.loads(text)
            editor_state["payload"] = payload
            editor_state["fields"] = []
            editor_state["item_paths"] = {}
            editor_state["list_paths"] = set()
            if reset_history:
                editor_state["original_json"] = text
                editor_state["baseline_payload"] = copy.deepcopy(payload)
                editor_state["undo_stack"] = []
            editor_state["dirty"] = False
            revert_button.setEnabled(bool(editor_state["undo_stack"]))
            tree.clear()

            def add_value(parent: Any | None, label: str, value: Any, path: tuple[Any, ...], depth: int) -> None:
                if value is None:
                    return

                path_key = ".".join(str(part) for part in path)
                if isinstance(value, dict) and value:
                    item = _new_tree_item(parent, _display_label(label), f"Object · {len(value)}")
                    item.setData(0, QtCore.Qt.ItemDataRole.UserRole, path_key)
                    editor_state["item_paths"][id(item)] = path
                    item.setExpanded(
                        path_key in editor_state["expanded_paths"]
                        or (expand_defaults and depth < 1)
                    )
                    font = item.font(0)
                    font.setBold(True)
                    item.setFont(0, font)
                    for child_key, child_value in value.items():
                        add_value(item, str(child_key), child_value, (*path, child_key), depth + 1)
                    return
                if isinstance(value, list) and value:
                    item = _new_tree_item(parent, _display_label(label), f"List · {len(value)}")
                    item.setData(0, QtCore.Qt.ItemDataRole.UserRole, path_key)
                    editor_state["item_paths"][id(item)] = path
                    editor_state["list_paths"].add(path)
                    item.setExpanded(
                        path_key in editor_state["expanded_paths"]
                        or (expand_defaults and depth < 1)
                    )
                    font = item.font(0)
                    font.setBold(True)
                    item.setFont(0, font)
                    for index, child_value in enumerate(value):
                        add_value(
                            item,
                            _entry_label(child_value, index),
                            child_value,
                            (*path, index),
                            depth + 1,
                        )
                    return

                if isinstance(value, list):
                    item = _new_tree_item(parent, _display_label(label), "List · 0")
                    item.setData(0, QtCore.Qt.ItemDataRole.UserRole, path_key)
                    editor_state["item_paths"][id(item)] = path
                    editor_state["list_paths"].add(path)
                    return

                item = _new_tree_item(parent, _display_label(label))
                item.setData(0, QtCore.Qt.ItemDataRole.UserRole, path_key)
                editor_state["item_paths"][id(item)] = path
                if isinstance(value, list):
                    editor_state["list_paths"].add(path)
                widget = _value_widget(value, label)
                tree.setItemWidget(item, 1, widget)
                tooltip = path_key
                item.setToolTip(0, tooltip)
                widget.setToolTip(tooltip)
                if hasattr(widget, "textChanged"):
                    widget.textChanged.connect(lambda *_args, entry=item: _mark_dirty(entry))
                elif hasattr(widget, "valueChanged"):
                    widget.valueChanged.connect(lambda *_args, entry=item: _mark_dirty(entry))
                elif hasattr(widget, "toggled"):
                    widget.toggled.connect(lambda *_args, entry=item: _mark_dirty(entry))
                editor_state["fields"].append((path, value, widget, item))

            if isinstance(payload, dict):
                for key, value in payload.items():
                    add_value(None, str(key), value, (key,), 0)
            else:
                add_value(None, "Configuration", payload, (), 0)

        def filter_items(query: str) -> None:
            query = query.strip().lower()

            def visit(item: Any) -> bool:
                own_match = not query or query in item.text(0).lower() or query in item.text(1).lower()
                child_match = False
                for index in range(item.childCount()):
                    child_match = visit(item.child(index)) or child_match
                item.setHidden(not (own_match or child_match))
                if child_match and query:
                    item.setExpanded(True)
                return own_match or child_match

            for index in range(tree.topLevelItemCount()):
                visit(tree.topLevelItem(index))

        def collect_payload() -> dict[str, Any]:
            payload = editor_state["payload"]
            for path, original_value, widget, _ in editor_state["fields"]:
                if isinstance(original_value, bool):
                    value = widget.isChecked()
                else:
                    if isinstance(original_value, str):
                        value = widget.currentText() if isinstance(widget, QtWidgets.QComboBox) else widget.text()
                    elif isinstance(original_value, int):
                        value = int(widget.text())
                    elif isinstance(original_value, float):
                        value = float(widget.text())
                    else:
                        value = json.loads(widget.text())

                target = payload
                for part in path[:-1]:
                    target = target[part]
                if path:
                    target[path[-1]] = value
            return payload

        def reset_to_json(text: str, message: str) -> None:
            editor_state["expanded_paths"] = set()
            populate_editor(text)
            status.setText(message)

        def undo_changes() -> None:
            if not editor_state["undo_stack"]:
                reset_to_json(editor_state["original_json"], "Restored the last saved configuration.")
                return
            reset_to_json(editor_state["undo_stack"].pop(), "Reverted the latest local edit.")
            revert_button.setEnabled(bool(editor_state["undo_stack"]))

        def confirm_discard_changes(message: str) -> bool:
            if not editor_state["dirty"]:
                return True
            buttons = (
                QtWidgets.QMessageBox.StandardButton.Yes
                | QtWidgets.QMessageBox.StandardButton.No
            )
            answer = QtWidgets.QMessageBox.warning(
                content,
                "Discard unapplied changes?",
                message,
                buttons,
                QtWidgets.QMessageBox.StandardButton.No,
            )
            return answer == QtWidgets.QMessageBox.StandardButton.Yes

        def reload_from_disk() -> None:
            if not confirm_discard_changes(
                "The editor contains unapplied changes. Reloading from disk will discard them. Continue?"
            ):
                status.setText("Reload canceled. Your unapplied changes are preserved.")
                return
            active_config_path = editor_state["config_path"]
            try:
                source = active_config_path.read_text(encoding="utf-8")
                SimulationConfig(**json.loads(source))
            except (OSError, ValidationError, json.JSONDecodeError) as exc:
                status.setText(f"Could not reload configuration: {exc}")
                return
            editor_state["expanded_paths"] = set()
            populate_editor(source, reset_history=True)
            status.setText("Reloaded the saved configuration from disk.")

        def _payload_at(path: tuple[Any, ...]) -> Any:
            target = editor_state["payload"]
            for part in path:
                target = target[part]
            return target

        def update_array_actions() -> None:
            item = tree.currentItem()
            path = editor_state["item_paths"].get(id(item)) if item is not None else None
            is_list = path in editor_state["list_paths"] if path is not None else False
            is_list_entry = bool(path) and path[:-1] in editor_state["list_paths"] and isinstance(path[-1], int)
            add_item_button.setEnabled(is_list)
            duplicate_item_button.setEnabled(is_list_entry)
            remove_item_button.setEnabled(is_list_entry)
            move_up_button.setEnabled(is_list_entry and path[-1] > 0)
            if is_list_entry:
                try:
                    move_down_button.setEnabled(path[-1] < len(_payload_at(path[:-1])) - 1)
                except (KeyError, IndexError, TypeError):
                    move_down_button.setEnabled(False)
            else:
                move_down_button.setEnabled(False)

        def edit_array(operation: str) -> None:
            item = tree.currentItem()
            path = editor_state["item_paths"].get(id(item)) if item is not None else None
            if path is None:
                return
            try:
                collect_payload()
                before_edit = json.dumps(editor_state["payload"], ensure_ascii=False)
                if operation == "add" and path in editor_state["list_paths"]:
                    target_list = _payload_at(path)
                    if not target_list:
                        target_list.append(
                            _new_json_editor_list_item(path, _config_spatial_dim(config))
                        )
                    else:
                        target_list.append(copy.deepcopy(target_list[-1]))
                elif path[:-1] in editor_state["list_paths"] and isinstance(path[-1], int):
                    target_list = _payload_at(path[:-1])
                    index = path[-1]
                    if operation == "duplicate":
                        target_list.insert(index + 1, copy.deepcopy(target_list[index]))
                    elif operation == "remove":
                        target_list.pop(index)
                    elif operation == "up" and index > 0:
                        target_list[index - 1], target_list[index] = target_list[index], target_list[index - 1]
                    elif operation == "down" and index < len(target_list) - 1:
                        target_list[index + 1], target_list[index] = target_list[index], target_list[index + 1]
                    else:
                        return
                else:
                    return
            except (KeyError, IndexError, TypeError, ValueError, json.JSONDecodeError) as exc:
                status.setText(f"Could not update list: {exc}")
                return

            editor_state["undo_stack"].append(before_edit)
            populate_editor(
                json.dumps(editor_state["payload"], indent=2, ensure_ascii=False),
                expand_defaults=False,
                additional_expanded_paths={".".join(str(part) for part in path)},
            )
            _mark_dirty()
            update_array_actions()

        def apply_changes() -> None:
            active_config_path = editor_state["config_path"]
            active_with_particles = editor_state["with_particles"]
            _clear_field_errors()
            try:
                before_edit = json.dumps(editor_state["payload"], ensure_ascii=False)
                payload = collect_payload()
            except (TypeError, ValueError, json.JSONDecodeError) as exc:
                status.setText(f"Invalid value: {exc}")
                return

            try:
                updated_config = self.config_type(**payload)
            except ValidationError as exc:
                first_error = exc.errors(include_url=False)[0]
                location = ".".join(str(part) for part in first_error.get("loc", ("config",)))
                error_path = tuple(first_error.get("loc", ()))
                for path, _, widget, item in editor_state["fields"]:
                    if path == error_path:
                        message = str(first_error.get("msg"))
                        widget.setStyleSheet(
                            "border: 2px solid #ff6b5f; background: #472522; color: #ffffff;"
                        )
                        widget.setToolTip(message)
                        item.setToolTip(0, message)
                        tree.scrollToItem(item)
                        break
                status.setText(f"Configuration validation failed at {location}: {first_error.get('msg')}")
                return

            # Patch only the edited entries into the on-disk JSON so defaults
            # (empty lists, null-like entries) are not added to the file.
            canonical_json = dump_simulation_config_json(updated_config, indent=2)
            file_content: str | None = None
            try:
                raw_text = active_config_path.read_bytes().decode("utf-8")
                line_ending = "\r\n" if "\r\n" in raw_text else "\n"
                raw_text = raw_text.replace("\r\n", "\n")
                raw_payload = load_json_preserving_floats(raw_text)
                patched_payload = _apply_payload_diff(
                    raw_payload, editor_state["baseline_payload"], payload
                )
                patched_config = self.config_type(**patched_payload)
                if patched_config.model_dump(exclude_none=True) == updated_config.model_dump(
                    exclude_none=True
                ):
                    prettier = find_prettier_options(active_config_path)
                    if prettier is None:
                        file_content = json.dumps(patched_payload, indent=2, ensure_ascii=False)
                    else:
                        file_content = format_json(
                            patched_payload,
                            layout=object_layout(raw_text),
                            print_width=int(prettier.get("printWidth", 80)),
                            tab_width=int(prettier.get("tabWidth", 2)),
                        ) + ("\n" if raw_text.endswith("\n") else "")
                    file_content = file_content.replace("\n", line_ending)
            except (OSError, ValidationError, json.JSONDecodeError, TypeError, KeyError):
                file_content = None

            try:
                self.write_config(active_config_path, updated_config, file_content)
            except OSError as exc:
                status.setText(f"Could not save {active_config_path.name}: {exc}")
                return

            editor_state["undo_stack"].append(before_edit)
            editor_json = file_content if file_content is not None else canonical_json
            populate_editor(editor_json, expand_defaults=False)
            editor_state["original_json"] = editor_json
            editor_state["baseline_payload"] = copy.deepcopy(editor_state["payload"])
            status.setText("Saved and rebuilding preview geometry…")
            result = self.show_or_update(
                updated_config,
                resolved_config_path=active_config_path,
                with_particles=active_with_particles,
                force=True,
            )
            status.setText(
                "Saved successfully." if result == 0 else "Saved, but preview refresh failed; see terminal output."
            )

        shortcut = QtGui.QShortcut(QtGui.QKeySequence.StandardKey.Save, tree)
        shortcut.activated.connect(apply_changes)
        undo_shortcut = QtGui.QShortcut(QtGui.QKeySequence.StandardKey.Undo, content)
        undo_shortcut.activated.connect(undo_changes)
        expand_button.clicked.connect(tree.expandAll)
        collapse_button.clicked.connect(tree.collapseAll)
        revert_button.clicked.connect(undo_changes)
        reload_button.clicked.connect(reload_from_disk)
        add_item_button.clicked.connect(lambda: edit_array("add"))
        duplicate_item_button.clicked.connect(lambda: edit_array("duplicate"))
        remove_item_button.clicked.connect(lambda: edit_array("remove"))
        move_up_button.clicked.connect(lambda: edit_array("up"))
        move_down_button.clicked.connect(lambda: edit_array("down"))
        tree.currentItemChanged.connect(lambda *_args: update_array_actions())
        filter_box.textChanged.connect(filter_items)
        def refresh_editor(
            current_config: SimulationConfig,
            current_config_path: Path,
            current_with_particles: bool,
        ) -> bool:
            """Replace the editor session when shell ``preview`` changes files."""
            try:
                editor_config = SimulationConfig(**editor_state["payload"])
            except ValidationError:
                same_config = False
            else:
                same_config = (
                    current_config_path.resolve() == editor_state["config_path"].resolve()
                    and editor_config.model_dump(exclude_none=True)
                    == current_config.model_dump(exclude_none=True)
                    and not editor_state["dirty"]
                )
            if same_config:
                editor_state["with_particles"] = current_with_particles
                return True

            if not confirm_discard_changes(
                "The editor contains unapplied changes. Continuing Preview will discard them. Continue?"
            ):
                status.setText("Preview canceled. Your unapplied changes are preserved.")
                return False

            try:
                current_json = current_config_path.read_text(encoding="utf-8")
                json.loads(current_json)
            except (OSError, json.JSONDecodeError) as exc:
                status.setText(f"Could not load editor config {current_config_path.name}: {exc}")
                return False

            editor_state["config_path"] = current_config_path
            editor_state["with_particles"] = current_with_particles
            editor_state["expanded_paths"] = set()
            filter_box.clear()
            populate_editor(current_json, reset_history=True)
            update_array_actions()
            return True

        refresh_editor(config, config_path, with_particles)
        self._json_editor = {
            "dock": dock,
            "tree": tree,
            "filter": filter_box,
            "status": status,
            "shortcut": shortcut,
            "undo_shortcut": undo_shortcut,
            "refresh": refresh_editor,
            # Optional embedding hooks; default CLI behavior is unchanged.
            "is_dirty": lambda: bool(editor_state["dirty"]),
            "payload": lambda: copy.deepcopy(collect_payload()),
            "discard": lambda: populate_editor(editor_state["original_json"], reset_history=True),
        }
        return True
