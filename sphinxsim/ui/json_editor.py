"""Shared tree property editor for the shell Preview and desktop workbench.

Keep editing separate from persistence: the host owns saving, reloading and
preview generation. Drafts contain only fields present in the source JSON.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path

from qtpy import QtCore, QtGui, QtWidgets

from sphinxsim.config.schemas import SimulationConfig
from sphinxsim.json_format import format_json_like_source, load_json_preserving_floats


FIELD_LABELS = {
    "simulation_type": "Simulation type", "particle_spacing": "Particle spacing [m]",
    "characteristic_length_particles": "Characteristic particles",
    "lower_bound": "Lower bound [m]", "upper_bound": "Upper bound [m]",
    "half_size": "Half size [m]", "translation": "Translation [m]", "center": "Center [m]",
    "radius": "Radius [m]", "density": "Density [kg/m³]", "sound_speed": "Sound speed [m/s]",
    "viscosity": "Viscosity [Pa·s]", "youngs_modulus": "Young's modulus [Pa]",
    "yield_stress": "Yield stress [Pa]", "cohesion": "Cohesion [Pa]",
    "friction_angle": "Friction angle [rad]", "dilatancy_angle": "Dilatancy angle [rad]",
    "end_time": "End time [s]", "output_interval": "Output interval [s]",
    "screen_interval": "Screen interval", "inflow_speed": "Inflow speed [m/s]",
    "gravity": "Gravity [m/s²]",
}
ENUM_CHOICES = {
    "simulation_type": ["fluid_dynamics", "continuum_dynamics"],
    "surface_type": ["free_surface", "confined", "open_boundary", "free_stream"],
    "kernel_correction": ["linear", "none"],
}


class JsonPropertyEditor(QtWidgets.QWidget):
    textChanged = QtCore.Signal(str)
    saveRequested = QtCore.Signal()
    reloadRequested = QtCore.Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("sphinxsim-json-editor-content")
        self._text = self._saved_text = self._layout_text = "{}"
        self._payload = {}
        self._history = []
        self._expanded = set()
        self._fields = {}
        self._items = {}
        self._lists = set()
        self._invalid = {}
        self.config_path = None
        self.setStyleSheet("""
            QWidget#sphinxsim-json-editor-content { background: #181a1f; color: #e6e8ec; }
            QLabel { color: #d7dae0; }
            QToolButton { min-height: 24px; padding: 2px 8px; border: 1px solid #4a505b;
                border-radius: 3px; background: #24272d; color: #d7dae0; }
            QToolButton:hover { background: #30343c; border-color: #75a9d0; }
            QToolButton:disabled { color: #686e79; border-color: #343a43; }
            QToolButton#sphinxsim-json-editor-list-action { background: #2f6f9f; color: white; }
            QToolButton#sphinxsim-json-editor-list-action:disabled { background: #25313b; color: #788895; }
            QLineEdit, QComboBox { min-height: 25px; padding: 1px 6px; border: 1px solid #4b515c;
                border-radius: 3px; background: #181a1f; color: #e6e8ec; }
            QLineEdit:focus, QComboBox:focus { border-color: #5794cf; }
            QComboBox QAbstractItemView { background: #24272d; color: #e6e8ec;
                selection-background-color: #315f8c; }
            QTreeWidget { border: 1px solid #454b55; border-radius: 3px;
                background: #202329; color: #e1e4e8; }
            QTreeWidget::item { min-height: 30px; }
            QTreeWidget::item:alternate { background: #252930; }
            QTreeWidget::item:selected { background: #315f8c; color: white; }
            QHeaderView::section { padding: 5px 7px; border: 0; border-bottom: 1px solid #454b55;
                background: #292d34; color: #f0f2f5; font-weight: 600; }
        """)
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        self.filter = QtWidgets.QLineEdit()
        self.filter.setObjectName("sphinxsim-json-editor-filter")
        self.filter.setPlaceholderText("Filter settings…")
        self.tools = QtWidgets.QGridLayout()
        self.buttons = {}
        callbacks = {"Expand": lambda: self.tree.expandAll(), "Collapse": lambda: self.tree.collapseAll(),
                     "Revert": self.revert, "Reload": self.reloadRequested.emit,
                     **{label: lambda checked=False, operation=operation: self.edit_array(operation)
                        for label, operation in (("Add", "add"), ("Duplicate", "duplicate"),
                            ("Remove", "remove"), ("Move up", "up"), ("Move down", "down"))}}
        for label, callback in callbacks.items():
            button = QtWidgets.QToolButton()
            button.setText(label)
            button.clicked.connect(callback)
            if label in ("Add", "Duplicate", "Remove", "Move up", "Move down"):
                button.setObjectName("sphinxsim-json-editor-list-action")
            self.buttons[label] = button
        layout.addLayout(self.tools)
        self._tool_columns = None
        self.tree = QtWidgets.QTreeWidget()
        self.tree.setObjectName("sphinxsim-json-editor-tree")
        self.tree.setColumnCount(2)
        self.tree.setHeaderLabels(["Setting", "Value"])
        self.tree.setAlternatingRowColors(True)
        self.tree.setUniformRowHeights(True)
        self.tree.setIndentation(16)
        self.tree.setColumnWidth(0, 340)
        self.tree.header().setStretchLastSection(True)
        layout.addWidget(self.tree, 1)
        self.status = QtWidgets.QLabel("Loaded configuration. Ctrl+S saves and refreshes Preview.")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.filter.textChanged.connect(self._filter)
        self.tree.currentItemChanged.connect(self._update_actions)
        self.undo_shortcut = QtGui.QShortcut(QtGui.QKeySequence.StandardKey.Undo, self)
        self.undo_shortcut.setContext(QtCore.Qt.ShortcutContext.WidgetWithChildrenShortcut)
        self.undo_shortcut.activated.connect(self.revert)
        self.save_shortcut = QtGui.QShortcut(QtGui.QKeySequence.StandardKey.Save, self)
        self.save_shortcut.setContext(QtCore.Qt.ShortcutContext.WidgetWithChildrenShortcut)
        self.save_shortcut.activated.connect(self.saveRequested.emit)
        self._arrange_tools()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._arrange_tools()

    def _arrange_tools(self):
        columns = 9 if self.width() >= 850 else 5
        if columns == self._tool_columns:
            return
        self._tool_columns = columns
        self.tools.removeWidget(self.filter)
        for column in range(10):
            self.tools.setColumnStretch(column, 0)
        if columns == 9:
            self.tools.addWidget(self.filter, 0, 0)
            self.tools.setColumnStretch(0, 1)
        else:
            self.tools.addWidget(self.filter, 0, 0, 1, 5)
        for index, button in enumerate(self.buttons.values()):
            self.tools.removeWidget(button)
            row, column = (0, index + 1) if columns == 9 else (1 + index // columns, index % columns)
            self.tools.addWidget(button, row, column)

    @property
    def dirty(self):
        return self._text != self._saved_text

    def text(self):
        return self._text

    def set_text(self, text, *, config_path=None, saved_text=None, remember=False):
        payload = load_json_preserving_floats(text)
        if not isinstance(payload, dict):
            raise ValueError("The simulation JSON must be an object.")
        if remember:
            self._history.append(self._text)
        else:
            self._history.clear()
        if config_path is not None:
            self.config_path = Path(config_path)
        if saved_text is not None:
            self._saved_text = saved_text
        self._text = self._layout_text = text
        self._payload = payload
        self._invalid.clear()
        self._build_tree()
        self.textChanged.emit(self._text)

    def mark_saved(self):
        self._saved_text = self._text
        self.status.setText("Saved configuration.")
        self._update_actions()

    def _at(self, path):
        value = self._payload
        for part in path:
            value = value[part]
        return value

    def _build_tree(self):
        expand_defaults = not self._items
        if self._items:
            self._expanded = {path for path, item in self._items.items() if item.isExpanded()}
        self._fields, self._items, self._lists = {}, {}, set()
        self.tree.clear()

        def add(parent, key, value, path):
            label = FIELD_LABELS.get(str(key), str(key).replace("_", " ").capitalize())
            if isinstance(path[-1], int):
                label = next((value[name] for name in ("name", "body_name", "observed_body", "oriented_box")
                              if isinstance(value, dict) and isinstance(value.get(name), str)), f"Item {path[-1] + 1}")
            item = QtWidgets.QTreeWidgetItem([label, ""])
            (parent.addChild if parent is not None else self.tree.addTopLevelItem)(item)
            item.setData(0, QtCore.Qt.ItemDataRole.UserRole, path)
            item.setToolTip(0, ".".join(map(str, path)))
            self._items[path] = item
            if isinstance(value, (dict, list)):
                item.setText(1, f"{'Object' if isinstance(value, dict) else 'List'} · {len(value)}")
                font = item.font(0)
                font.setBold(True)
                item.setFont(0, font)
                item.setExpanded(path in self._expanded or (expand_defaults and len(path) < 3))
                if isinstance(value, list):
                    self._lists.add(path)
                for child_key, child in (value.items() if isinstance(value, dict) else enumerate(value)):
                    add(item, child_key, child, (*path, child_key))
                return
            if isinstance(value, bool):
                widget = QtWidgets.QCheckBox()
                widget.setChecked(value)
                signal = widget.toggled
            elif key in ENUM_CHOICES and isinstance(value, str):
                widget = QtWidgets.QComboBox()
                widget.addItems(ENUM_CHOICES[key])
                if value not in ENUM_CHOICES[key]:
                    widget.addItem(value)
                widget.setCurrentText(value)
                signal = widget.currentTextChanged
            else:
                widget = QtWidgets.QLineEdit(str(value) if value is not None else "null")
                signal = widget.textChanged
            widget.setToolTip(".".join(map(str, path)))
            self.tree.setItemWidget(item, 1, widget)
            self._fields[path] = widget
            signal.connect(lambda *_, p=path, original=value, w=widget: self._field_changed(p, original, w))

        for key, value in self._payload.items():
            add(None, key, value, (key,))
        self._filter(self.filter.text())
        self._update_actions()

    def _format(self):
        return format_json_like_source(self._payload, self._layout_text, self.config_path)

    def _field_changed(self, path, original, widget):
        self._history.append(self._text)
        raw = widget.isChecked() if isinstance(widget, QtWidgets.QCheckBox) else (
              widget.currentText() if isinstance(widget, QtWidgets.QComboBox) else widget.text())
        try:
            if isinstance(original, bool):
                value = raw
            elif isinstance(original, int):
                value = int(raw)
            elif isinstance(original, float):
                value = float(raw)
            elif original is None:
                value = json.loads(raw)
            else:
                value = raw
            self._invalid.pop(path, None)
            widget.setStyleSheet("")
        except (ValueError, TypeError):
            value = raw
            self._invalid[path] = f"Invalid value at {'.'.join(map(str, path))}: {raw}"
            widget.setStyleSheet("border: 2px solid #ff6b5f; background: #472522;")
        self._at(path[:-1])[path[-1]] = value
        self._text = self._format()
        self._items[path].setBackground(0, QtGui.QColor("#594a24"))
        self.status.setText(next(iter(self._invalid.values()), "Unsaved draft. Ctrl+S validates, saves and refreshes Preview."))
        self._update_actions()
        self.textChanged.emit(self._text)

    def validate_config(self):
        try:
            if self._invalid:
                raise ValueError(next(iter(self._invalid.values())))
            return SimulationConfig.model_validate_json(self._text)
        except ValueError as exc:
            errors = exc.errors(include_url=False) if hasattr(exc, "errors") else []
            path = tuple(errors[0]["loc"]) if errors else next(iter(self._invalid), ())
            if path in self._fields:
                self._fields[path].setStyleSheet("border: 2px solid #ff6b5f; background: #472522;")
                self._fields[path].setToolTip(str(exc))
                self.tree.scrollToItem(self._items[path])
            self.status.setText(str(exc).splitlines()[0])
            raise

    def _filter(self, query):
        query = query.strip().lower()
        def visit(item):
            path = item.data(0, QtCore.Qt.ItemDataRole.UserRole)
            widget = self._fields.get(path)
            value = widget.currentText() if isinstance(widget, QtWidgets.QComboBox) else (
                    widget.text() if isinstance(widget, QtWidgets.QLineEdit) else item.text(1))
            match = not query or query in f"{item.text(0)} {value} {'.'.join(map(str, path))}".lower()
            children = [visit(item.child(index)) for index in range(item.childCount())]
            visible = match or any(children)
            item.setHidden(not visible)
            if query and any(children):
                item.setExpanded(True)
            return visible
        for index in range(self.tree.topLevelItemCount()):
            visit(self.tree.topLevelItem(index))

    def _update_actions(self, *_):
        item = self.tree.currentItem()
        path = item.data(0, QtCore.Qt.ItemDataRole.UserRole) if item else ()
        entry = bool(path) and path[:-1] in self._lists and isinstance(path[-1], int)
        self.buttons["Add"].setEnabled(path in self._lists)
        for label in ("Duplicate", "Remove"):
            self.buttons[label].setEnabled(entry)
        self.buttons["Move up"].setEnabled(entry and path[-1] > 0)
        self.buttons["Move down"].setEnabled(entry and path[-1] < len(self._at(path[:-1])) - 1)
        self.buttons["Revert"].setEnabled(bool(self._history) or self.dirty)

    def edit_array(self, operation):
        if self._invalid:
            self.status.setText("Correct invalid field values before changing a list.")
            return
        item = self.tree.currentItem()
        path = item.data(0, QtCore.Qt.ItemDataRole.UserRole) if item else ()
        before = self._text
        if operation == "add" and path in self._lists:
            target = self._at(path)
            if target:
                value = copy.deepcopy(target[-1])
            else:
                # Reuse the existing dimension-aware templates from Preview.
                from sphinxsim.cli import _config_spatial_dim, _new_json_editor_list_item
                value = _new_json_editor_list_item(path, _config_spatial_dim(self.validate_config()))
            target.append(value)
        elif path and path[:-1] in self._lists:
            target, index = self._at(path[:-1]), path[-1]
            if operation == "duplicate":
                target.insert(index + 1, copy.deepcopy(target[index]))
            elif operation == "remove":
                target.pop(index)
            elif operation == "up" and index > 0:
                target[index - 1], target[index] = target[index], target[index - 1]
            elif operation == "down" and index < len(target) - 1:
                target[index + 1], target[index] = target[index], target[index + 1]
            else:
                return
        else:
            return
        self._history.append(before)
        self._text = self._format()
        self._build_tree()
        self.status.setText("Unsaved list changes. Ctrl+S validates and saves.")
        self.textChanged.emit(self._text)

    def revert(self):
        text = self._history.pop() if self._history else self._saved_text
        self._text = text
        self._payload = load_json_preserving_floats(text)
        self._invalid.clear()
        self._build_tree()
        self.status.setText("Reverted the latest local edit.")
        self.textChanged.emit(self._text)
