"""Qt-backed JSON editing widget and preview runtime integration."""

from __future__ import annotations

import copy
import json
import sys
import types
from enum import Enum
from pathlib import Path
from typing import Any, Literal, Union, get_args, get_origin

from pydantic import BaseModel, TypeAdapter, ValidationError

from sphinxsim.config.schemas import SimulationConfig
from sphinxsim.json_editor.json_format import (
    format_json_like_source,
    load_json_preserving_floats,
)
from sphinxsim.json_editor.model import (
    delete_value_at_path,
    edit_list,
    parse_field_value,
    pop_undo_snapshot,
    set_value_at_path,
    snapshot_payload,
    validate_payload,
    value_at_path,
)
from sphinxsim.llm.common import dump_simulation_config_json

from qtpy import QtCore, QtGui, QtWidgets


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
    dimension = config._infer_spatial_dim()
    if dimension is not None:
        return dimension
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


def _new_json_editor_list_item(
    path: tuple[Any, ...], spatial_dim: int, variant: str | None = None
) -> Any:
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
            "type": variant or "box",
            **(
                {"half_size": unit_vector, "transform": transform}
                if variant != "bounding_box"
                else {"lower_bound": vector, "upper_bound": unit_vector}
            ),
        },
        ("geometries", "shapes"): {
            "name": "New shape",
            "type": variant or "bounding_box",
            **{
                "box": {"half_size": unit_vector, "transform": transform},
                "bounding_box": {"lower_bound": vector, "upper_bound": unit_vector},
                "expanded_box": {"original": "", "expansion": 0.1},
                "extrude_shape": {"original": "", "thickness": 0.1},
                "complex_shape": {"sub_shapes": [], "operations": []},
                "multipolygon": {
                    "polygons": [
                        {
                            "operation": "union",
                            "type": "container_box",
                            "inner_box": {
                                "lower_bound": vector,
                                "upper_bound": unit_vector,
                            },
                            "thickness": 0.1,
                        }
                    ]
                },
                "cylinder": {
                    "radius": 1.0,
                    "half_height": 1.0,
                    "transform": transform,
                },
                "triangle_mesh": {"file_name": "mesh.stl"},
            }.get(variant or "bounding_box", {}),
        },
        ("geometries", "shapes", "polygons"): {
            "operation": "union",
            "type": variant or "bounding_box",
            **{
                "box": {"half_size": unit_vector, "transform": transform},
                "bounding_box": {"lower_bound": vector, "upper_bound": unit_vector},
                "container_box": {
                    "inner_box": {
                        "lower_bound": vector,
                        "upper_bound": unit_vector,
                    },
                    "thickness": 0.1,
                },
                "circle": {"center": vector, "radius": 1.0, "resolution": 32},
                "triangle": {"half_size": unit_vector, "transform": transform},
                "clockwise_points": {
                    "points": [vector, unit_vector, [0.0] * spatial_dim]
                },
                "data_file": {"file_name": "polygon.dat"},
            }.get(variant or "bounding_box", {}),
        },
        ("geometries", "oriented_boxes"): {
            "name": "New region",
            "type": variant or "region",
            **(
                {"half_size": unit_vector, "transform": transform}
                if variant != "boundary"
                else {
                    "center": vector,
                    "normal": [0.0, 0.0, 1.0]
                    if spatial_dim == 3
                    else [0.0, 1.0],
                    "radius": 1.0,
                }
            ),
        },
        ("particle_generation", "bodies"): {"name": "New body"},
        ("particle_generation", "relaxation_constraints"): {
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
            "type": variant or "emitter",
            **{
                "emitter": {"inflow_speed": 1.0},
                "bi_directional": {"pressure": 0.0},
                "free_stream": {
                    "buffer_box": "",
                    "disposer_box": "",
                    "target_speed": 1.0,
                    "t_ref": 1.0,
                },
                "disposer": {},
                "wall_bounding": {},
            }.get(variant or "emitter", {}),
        },
        ("body_constraints",): {
            "body_name": "",
            "type": variant or "fixed",
            **(
                {"mobilized_body": "pin", "angular_velocity": 0.0}
                if variant == "simbody"
                else {}
            ),
        },
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


def _unwrap_schema_type(annotation: Any) -> Any:
    """Unwrap optional and single-model unions used by the configuration schema."""
    origin = get_origin(annotation)
    if origin in (Union, types.UnionType):
        candidates = [arg for arg in get_args(annotation) if arg is not type(None)]
        if len(candidates) == 1:
            return _unwrap_schema_type(candidates[0])
        model_candidates = [
            candidate
            for candidate in candidates
            if isinstance(candidate, type) and issubclass(candidate, BaseModel)
        ]
        if len(model_candidates) == 1:
            return model_candidates[0]
    return annotation


def _schema_type_at_path(path: tuple[Any, ...]) -> Any:
    """Resolve a JSON payload path to its declared Pydantic field type."""
    current: Any = SimulationConfig
    for part in path:
        current = _unwrap_schema_type(current)
        if isinstance(part, int):
            args = get_args(current)
            current = args[0] if get_origin(current) is list and args else Any
        elif isinstance(current, type) and issubclass(current, BaseModel):
            field = current.model_fields.get(str(part))
            current = field.annotation if field is not None else Any
        else:
            return Any
    return _unwrap_schema_type(current)


def _schema_enum_values(annotation: Any) -> list[str]:
    annotation = _unwrap_schema_type(annotation)
    if get_origin(annotation) is Literal:
        return [str(value) for value in get_args(annotation)]
    if isinstance(annotation, type) and issubclass(annotation, Enum):
        return [str(value.value) for value in annotation]
    return []


def _schema_default_value(
    annotation: Any, path: tuple[Any, ...], spatial_dim: int
) -> Any:
    """Build a simple starter value for an absent schema property."""
    annotation = _unwrap_schema_type(annotation)
    normalized_path = tuple(part for part in path if not isinstance(part, int))

    if normalized_path == ("geometries", "system_domain"):
        return {
            "lower_bound": [0.0] * spatial_dim,
            "upper_bound": [1.0] * spatial_dim,
        }
    if normalized_path == ("geometries", "global_resolution"):
        return {"particle_spacing": 0.1}
    if normalized_path and get_origin(annotation) is list:
        item_type = (get_args(annotation) or (Any,))[0]
        if isinstance(item_type, type) and issubclass(item_type, BaseModel):
            return [_new_json_editor_list_item(normalized_path, spatial_dim)]
        return []
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        result: dict[str, Any] = {}
        for name, field in annotation.model_fields.items():
            default = field.get_default(call_default_factory=True)
            if default is not None and not field.is_required():
                result[name] = default
            elif field.is_required():
                result[name] = _schema_default_value(
                    field.annotation, (*path, name), spatial_dim
                )
        return result
    values = _schema_enum_values(annotation)
    if values:
        return values[0]
    if annotation is bool:
        return False
    if annotation is int:
        return 1
    if annotation is float:
        return 1.0
    if annotation is str:
        return ""
    if get_origin(annotation) is list:
        return []
    return {}


def _schema_model_for_path(path: tuple[Any, ...]) -> type[BaseModel] | None:
    annotation = _unwrap_schema_type(_schema_type_at_path(path))
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        return annotation
    return None


def _schema_is_fixed_vector(path: tuple[Any, ...]) -> bool:
    """Whether a field is a bounded numeric vector rather than an entry list."""
    parent_model = _schema_model_for_path(path[:-1])
    if parent_model is None:
        return False
    field = parent_model.model_fields.get(str(path[-1]))
    if field is None or not any(
        getattr(metadata, "max_length", None) is not None
        for metadata in field.metadata
    ):
        return False
    annotation = _unwrap_schema_type(field.annotation)
    if get_origin(annotation) is not list:
        return False
    item_type = _unwrap_schema_type((get_args(annotation) or (Any,))[0])
    return item_type in (int, float)


def _schema_field_is_removable(path: tuple[Any, ...]) -> bool:
    if not path or isinstance(path[-1], int):
        return False
    parent_model = _schema_model_for_path(path[:-1])
    if parent_model is None:
        return False
    field = parent_model.model_fields.get(str(path[-1]))
    return field is not None and not field.is_required()


def _schema_list_variants(path: tuple[Any, ...]) -> list[str]:
    item_type = _unwrap_schema_type(_schema_type_at_path((*path, 0)))
    if isinstance(item_type, type) and issubclass(item_type, BaseModel):
        type_field = item_type.model_fields.get("type")
        if type_field is not None:
            return _schema_enum_values(type_field.annotation)
    return []


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

        editor_host = getattr(self, "json_editor_host", None)
        dock = QtWidgets.QDockWidget("Simulation Properties", app_window)
        if editor_host is not None:
            self._embedded_json_dock = dock
        dock.setObjectName("sphinxsim-json-editor")
        dock.setStyleSheet(
            "QDockWidget { color: #e6e8ec; background: #181a1f; }"
            "QDockWidget::title { padding: 7px 8px; background: #202329; color: #f0f2f5; "
            "border-bottom: 1px solid #3b4049; text-align: left; }"
        )
        dock.setAllowedAreas(QtCore.Qt.DockWidgetArea.RightDockWidgetArea)
        content = JsonPropertyEditor(
            editor_host or dock,
            external_controller=True,
        )
        filter_box = content.filter
        tree = content.tree
        status = content.status
        filter_box.setStyleSheet(
            "min-height: 27px; padding: 1px 7px; border: 1px solid #454b55; border-radius: 3px; "
            "background: #24272d; color: #e6e8ec; selection-background-color: #315f8c;"
        )
        tree.setHeaderLabels(["Setting ▾", "Value"])
        tree.setRootIsDecorated(True)
        tree.header().setStretchLastSection(False)
        tree.header().setSectionsClickable(True)
        tree.header().setSortIndicatorShown(False)
        tree.header().setSectionResizeMode(
            0, QtWidgets.QHeaderView.ResizeMode.Stretch
        )
        tree.header().setSectionResizeMode(
            1, QtWidgets.QHeaderView.ResizeMode.Stretch
        )
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
        status.setAlignment(
            QtCore.Qt.AlignmentFlag.AlignLeft | QtCore.Qt.AlignmentFlag.AlignVCenter
        )
        status.setStyleSheet(
            "padding: 7px 9px; border: 1px solid #3d6f9e; border-radius: 4px; "
            "background: #1f3042; color: #d9e8f5;"
        )
        if editor_host is None:
            dock.setWidget(content)
        # Give the right dock both right-hand corners so it spans the same full
        # top-to-bottom extent as the preview area instead of appearing offset.
        if editor_host is None:
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

        if editor_host is None:
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
        context_menu = QtWidgets.QMenu(tree)
        context_menu.setObjectName("sphinxsim-json-editor-list-context-menu")
        context_menu.setStyleSheet(
            "QMenu { background: #24272d; color: #e6e8ec; border: 1px solid #454b55; }"
            "QMenu::item { padding: 6px 24px 6px 10px; }"
            "QMenu::item:enabled { color: #9ac7ef; font-weight: 600; }"
            "QMenu::item:enabled:selected { background: #315f8c; color: #ffffff; }"
            "QMenu::item:disabled { color: #686e79; }"
        )
        value_actions = {
            "copy": context_menu.addAction("Copy value"),
            "paste": context_menu.addAction("Paste value"),
        }
        for operation, action in value_actions.items():
            action.setObjectName(f"sphinxsim-json-editor-value-{operation}")
        context_menu.addSeparator()
        remove_property_action = context_menu.addAction("Remove property")
        remove_property_action.setObjectName(
            "sphinxsim-json-editor-remove-property"
        )
        context_menu.addSeparator()
        context_actions: dict[str, Any] = {}
        context_action_labels = {
            "add": "Add",
            "duplicate": "Duplicate",
            "remove": "Remove",
            "up": "Move up",
            "down": "Move down",
        }
        for operation, label in context_action_labels.items():
            if operation == "up":
                context_menu.addSeparator()
            action = context_menu.addAction(label)
            action.setObjectName(f"sphinxsim-json-editor-list-{operation}")
            context_actions[operation] = action
        property_menu = context_menu.addMenu("Add property")
        property_menu.setObjectName("sphinxsim-json-editor-add-property-menu")
        variant_menu = context_menu.addMenu("Add as")
        variant_menu.setObjectName("sphinxsim-json-editor-add-variant-menu")
        context_menu.insertMenu(context_actions["duplicate"], property_menu)
        context_menu.insertMenu(context_actions["duplicate"], variant_menu)
        property_menu.menuAction().setVisible(False)
        variant_menu.menuAction().setVisible(False)
        settings_menu = QtWidgets.QMenu(tree.header())
        settings_menu.setObjectName("sphinxsim-json-editor-settings-menu")
        settings_menu.setStyleSheet(context_menu.styleSheet())
        settings_actions = {
            operation: settings_menu.addAction(label)
            for operation, label in (
                ("expand", "Expand"),
                ("collapse", "Collapse"),
                ("revert", "Revert"),
                ("reload", "Reload"),
            )
        }
        for operation, action in settings_actions.items():
            action.setObjectName(f"sphinxsim-json-editor-{operation}")

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
            settings_actions["revert"].setEnabled(True)
            if item is not None:
                item.setBackground(0, QtGui.QColor("#594a24"))
            status.setText("Unsaved changes. Press Ctrl+S to validate, save, and refresh.")
            changed = getattr(self, "json_editor_changed", None)
            if callable(changed):
                changed()

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
            def expanded_tree_paths() -> set[str]:
                paths: set[str] = set()
                iterator = QtWidgets.QTreeWidgetItemIterator(tree)
                while iterator.value() is not None:
                    item = iterator.value()
                    path_key = item.data(0, QtCore.Qt.ItemDataRole.UserRole)
                    if path_key is not None and item.isExpanded():
                        paths.add(path_key)
                    iterator += 1
                return paths

            expanded_paths = expanded_tree_paths()
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
            settings_actions["revert"].setEnabled(bool(editor_state["undo_stack"]))

            def detach_item_widgets() -> None:
                iterator = QtWidgets.QTreeWidgetItemIterator(tree)
                while iterator.value() is not None:
                    item = iterator.value()
                    for column in range(tree.columnCount()):
                        widget = tree.itemWidget(item, column)
                        if widget is not None:
                            tree.removeItemWidget(item, column)
                    iterator += 1

            detach_item_widgets()
            tree.clear()

            def add_value(parent: Any | None, label: str, value: Any, path: tuple[Any, ...], depth: int) -> None:
                if value is None:
                    return

                path_key = ".".join(str(part) for part in path)
                if isinstance(value, dict):
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
                if isinstance(value, list) and _schema_is_fixed_vector(path):
                    item = _new_tree_item(parent, _display_label(label), f"Vector · {len(value)}")
                    item.setData(0, QtCore.Qt.ItemDataRole.UserRole, path_key)
                    editor_state["item_paths"][id(item)] = path
                    widget = _value_widget(value, label)
                    tree.setItemWidget(item, 1, widget)
                    item.setToolTip(0, path_key)
                    widget.setToolTip(path_key)
                    widget.textChanged.connect(
                        lambda *_args, entry=item: _mark_dirty(entry)
                    )
                    editor_state["fields"].append((path, value, widget, item))
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
            content._fields = {
                path: widget for path, _value, widget, _item in editor_state["fields"]
            }
            content._items = {}
            iterator = QtWidgets.QTreeWidgetItemIterator(tree)
            while iterator.value() is not None:
                item = iterator.value()
                path = editor_state["item_paths"].get(id(item))
                if path is not None:
                    content._items[path] = item
                iterator += 1
            update_array_actions()

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

        def _field_value(original_value: Any, widget: Any) -> Any:
            if isinstance(original_value, bool):
                raw = widget.isChecked()
            elif isinstance(original_value, str):
                raw = (
                    widget.currentText()
                    if isinstance(widget, QtWidgets.QComboBox)
                    else widget.text()
                )
            elif isinstance(original_value, (int, float)):
                raw = widget.text()
            else:
                raw = widget.text()
            return parse_field_value(original_value, raw)

        def _field_at_path(path: tuple[Any, ...]) -> tuple[Any, Any, Any] | None:
            for field_path, original_value, widget, item in editor_state["fields"]:
                if field_path == path:
                    return original_value, widget, item
            return None

        def _object_at_path(path: tuple[Any, ...]) -> bool:
            return (
                _schema_model_for_path(path) is not None
                and isinstance(_payload_at(path), dict)
            )

        def collect_payload() -> dict[str, Any]:
            payload = editor_state["payload"]
            for path, original_value, widget, _ in editor_state["fields"]:
                value = _field_value(original_value, widget)
                set_value_at_path(payload, path, value)
            return payload

        def copy_field_value() -> None:
            item = tree.currentItem()
            path = editor_state["item_paths"].get(id(item)) if item is not None else None
            field = _field_at_path(path) if path is not None else None
            if field is None and (path is None or not _object_at_path(path)):
                return
            try:
                collect_payload()
                value = (
                    _field_value(field[0], field[1])
                    if field is not None
                    else copy.deepcopy(_payload_at(path))
                )
                text = json.dumps(value, ensure_ascii=False)
            except (TypeError, ValueError, json.JSONDecodeError) as exc:
                status.setText(f"Could not copy field value: {exc}")
                return
            QtWidgets.QApplication.clipboard().setText(text)
            status.setText(f"Copied value from {'.'.join(str(part) for part in path)}.")

        def paste_field_value() -> None:
            item = tree.currentItem()
            path = editor_state["item_paths"].get(id(item)) if item is not None else None
            field = _field_at_path(path) if path is not None else None
            is_object = path is not None and _object_at_path(path)
            if field is None and not is_object:
                return

            clipboard_text = QtWidgets.QApplication.clipboard().text()
            try:
                try:
                    value = json.loads(clipboard_text)
                except json.JSONDecodeError:
                    annotation = _unwrap_schema_type(_schema_type_at_path(path))
                    if annotation is str:
                        value = clipboard_text
                    else:
                        raise ValueError("clipboard does not contain a JSON value")

                annotation = _unwrap_schema_type(_schema_type_at_path(path))
                if isinstance(annotation, type) and issubclass(annotation, Enum):
                    value = annotation(value).value
                elif isinstance(annotation, type) and issubclass(annotation, BaseModel):
                    validated = TypeAdapter(annotation).validate_python(value)
                    value = validated.model_dump(exclude_unset=True)
                else:
                    value = TypeAdapter(_schema_type_at_path(path)).validate_python(
                        value, strict=True
                    )
                if (
                    field is not None
                    and _schema_is_fixed_vector(path)
                    and len(value) != len(field[0])
                ):
                    raise ValueError(
                        f"expected a vector with {len(field[0])} components"
                    )
            except (TypeError, ValueError, ValidationError) as exc:
                status.setText(
                    f"Cannot paste value into {'.'.join(str(part) for part in path)}: {exc}"
                )
                return

            try:
                collect_payload()
                before_edit = snapshot_payload(editor_state["payload"])
            except (TypeError, ValueError, json.JSONDecodeError) as exc:
                status.setText(f"Cannot paste value while another field is invalid: {exc}")
                return

            set_value_at_path(editor_state["payload"], path, value)
            editor_state["undo_stack"].append(before_edit)
            _mark_dirty()
            _refresh_after_structural_edit(
                {".".join(str(part) for part in path[:-1])}
            )

        def reset_to_json(text: str, message: str) -> None:
            editor_state["expanded_paths"] = set()
            populate_editor(text)
            status.setText(message)

        def undo_changes() -> None:
            had_history = bool(editor_state["undo_stack"])
            text = pop_undo_snapshot(
                editor_state["undo_stack"], editor_state["original_json"]
            )
            message = (
                "Restored the last saved configuration."
                if not had_history
                else "Reverted the latest local edit."
            )
            reset_to_json(text, message)
            settings_actions["revert"].setEnabled(bool(editor_state["undo_stack"]))

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
            return value_at_path(editor_state["payload"], path)

        def _new_list_entry(path: tuple[Any, ...], variant: str | None = None) -> Any:
            spatial_dim = _config_spatial_dim(config)
            item_path = (*path, 0)
            item_type = _schema_type_at_path(item_path)
            starter = _new_json_editor_list_item(path, spatial_dim, variant)
            schema_default = _schema_default_value(item_type, item_path, spatial_dim)
            if isinstance(schema_default, dict) and isinstance(starter, dict):
                schema_default.update(starter)
                starter = schema_default
            elif starter == {}:
                starter = schema_default

            if not isinstance(starter, dict):
                return starter

            if variant is not None:
                starter["type"] = variant
            current_items = _payload_at(path) if path in editor_state["list_paths"] else []
            existing_names = {
                entry.get("name")
                for entry in current_items
                if isinstance(entry, dict) and isinstance(entry.get("name"), str)
            }
            if isinstance(starter.get("name"), str):
                base_name = starter["name"] or "New entry"
                name = base_name
                suffix = 2
                while name in existing_names:
                    name = f"{base_name} {suffix}"
                    suffix += 1
                starter["name"] = name

            if path == ("geometries", "shapes"):
                names = [
                    entry.get("name")
                    for entry in current_items
                    if isinstance(entry, dict) and entry.get("name")
                ]
                if starter.get("type") in ("expanded_box", "extrude_shape") and names:
                    starter["original"] = names[0]
                elif starter.get("type") == "complex_shape" and names:
                    starter["sub_shapes"] = [names[0]]
                    starter["operations"] = ["union"]
            elif path == ("fluid_boundary_conditions",):
                body_names = editor_state["payload"].get("particle_generation", {}).get(
                    "bodies", []
                )
                oriented_boxes = editor_state["payload"].get("geometries", {}).get(
                    "oriented_boxes", []
                )
                if body_names and isinstance(body_names[0], dict):
                    starter["body_name"] = body_names[0].get("name", "")
                if oriented_boxes and isinstance(oriented_boxes[0], dict):
                    starter["oriented_box"] = oriented_boxes[0].get("name", "")
            return starter

        def _new_property_value(parent_path: tuple[Any, ...], key: str) -> Any:
            field_type = _schema_type_at_path((*parent_path, key))
            normalized_type = _unwrap_schema_type(field_type)
            if get_origin(normalized_type) is list:
                item_type = _unwrap_schema_type(_schema_type_at_path((*parent_path, key, 0)))
                if isinstance(item_type, type) and issubclass(item_type, BaseModel):
                    return [_new_list_entry((*parent_path, key))]
            return _schema_default_value(
                field_type, (*parent_path, key), _config_spatial_dim(config)
            )

        def reveal_path(path_key: str) -> bool:
            """Expand and select the tree entry identified by its payload path."""
            item = None
            iterator = QtWidgets.QTreeWidgetItemIterator(tree)
            while iterator.value() is not None:
                candidate = iterator.value()
                if candidate.data(0, QtCore.Qt.ItemDataRole.UserRole) == path_key:
                    item = candidate
                    break
                iterator += 1
            if item is None:
                return False

            if item.isHidden():
                filter_box.clear()

            ancestor = item.parent()
            while ancestor is not None:
                ancestor.setExpanded(True)
                ancestor = ancestor.parent()
            item.setExpanded(True)
            tree.setCurrentItem(item)
            item.setSelected(True)
            tree.scrollToItem(item)
            return True

        def _refresh_after_structural_edit(
            additional_expanded_paths: set[str]
        ) -> None:
            text = json.dumps(editor_state["payload"], indent=2, ensure_ascii=False)

            def rebuild() -> None:
                populate_editor(
                    text,
                    expand_defaults=False,
                    additional_expanded_paths=additional_expanded_paths,
                )
                _mark_dirty()
                update_array_actions()

            QtCore.QTimer.singleShot(0, rebuild)

        def update_array_actions() -> None:
            item = tree.currentItem()
            path = editor_state["item_paths"].get(id(item)) if item is not None else None
            is_list = path in editor_state["list_paths"] if path is not None else False
            is_list_entry = bool(path) and path[:-1] in editor_state["list_paths"] and isinstance(path[-1], int)
            is_field = path is not None and _field_at_path(path) is not None
            remove_property_action.setEnabled(
                path is not None
                and _schema_field_is_removable(path)
                and isinstance(_payload_at(path[:-1]), dict)
                and path[-1] in _payload_at(path[:-1])
            )
            is_object = path is not None and _object_at_path(path)
            value_actions["copy"].setEnabled(is_field or is_object)
            value_actions["paste"].setEnabled(
                (is_field or is_object)
                and bool(QtWidgets.QApplication.clipboard().text())
            )
            context_actions["add"].setEnabled(is_list)
            property_menu.clear()
            variant_menu.clear()
            model = _schema_model_for_path(path) if path is not None else None
            missing_fields = (
                [name for name in model.model_fields if name not in _payload_at(path)]
                if model is not None and isinstance(_payload_at(path), dict)
                else []
            )
            for name in missing_fields:
                action = property_menu.addAction(_display_label(name))
                action.setData(name)
                action.triggered.connect(
                    lambda _checked=False, field_name=name, parent_path=path: add_property(
                        parent_path, field_name
                    )
                )
            property_menu.menuAction().setVisible(bool(missing_fields))
            variants = _schema_list_variants(path) if is_list else []
            for variant in variants:
                action = variant_menu.addAction(variant.replace("_", " ").capitalize())
                action.setData(variant)
                action.triggered.connect(
                    lambda _checked=False, selected_variant=variant, list_path=path: edit_array(
                        "add", selected_variant
                    )
                )
            variant_menu.menuAction().setVisible(is_list and len(variants) > 1)
            context_actions["duplicate"].setEnabled(is_list_entry)
            context_actions["remove"].setEnabled(is_list_entry)
            context_actions["up"].setEnabled(is_list_entry and path[-1] > 0)
            if is_list_entry:
                try:
                    context_actions["down"].setEnabled(
                        path[-1] < len(_payload_at(path[:-1])) - 1
                    )
                except (KeyError, IndexError, TypeError):
                    context_actions["down"].setEnabled(False)
            else:
                context_actions["down"].setEnabled(False)

        def edit_array(operation: str, variant: str | None = None) -> None:
            item = tree.currentItem()
            path = editor_state["item_paths"].get(id(item)) if item is not None else None
            if path is None:
                return
            try:
                collect_payload()
                before_edit = snapshot_payload(editor_state["payload"])
                if operation == "add" and path in editor_state["list_paths"]:
                    target_list = _payload_at(path)
                    if variant is None and target_list:
                        last = target_list[-1]
                        variant = last.get("type") if isinstance(last, dict) else None
                    changed = edit_list(
                        editor_state["payload"],
                        path,
                        operation,
                        new_item=_new_list_entry(path, variant),
                    )
                elif path[:-1] in editor_state["list_paths"] and isinstance(path[-1], int):
                    changed = edit_list(editor_state["payload"], path, operation)
                    if not changed:
                        return
                else:
                    return
            except (KeyError, IndexError, TypeError, ValueError, json.JSONDecodeError) as exc:
                status.setText(f"Could not update list: {exc}")
                return

            if not changed:
                return
            editor_state["undo_stack"].append(before_edit)
            _mark_dirty()
            _refresh_after_structural_edit(
                {".".join(str(part) for part in path)}
            )

        def add_property(parent_path: tuple[Any, ...], key: str) -> None:
            try:
                collect_payload()
                before_edit = snapshot_payload(editor_state["payload"])
                target = _payload_at(parent_path)
                if not isinstance(target, dict) or key in target:
                    return
                set_value_at_path(
                    editor_state["payload"],
                    (*parent_path, key),
                    _new_property_value(parent_path, key),
                )
            except (KeyError, IndexError, TypeError, ValueError, json.JSONDecodeError) as exc:
                status.setText(f"Could not add property: {exc}")
                return

            editor_state["undo_stack"].append(before_edit)
            _mark_dirty()
            _refresh_after_structural_edit(
                {".".join(str(part) for part in parent_path)}
            )

        def remove_property() -> None:
            item = tree.currentItem()
            path = editor_state["item_paths"].get(id(item)) if item is not None else None
            if (
                path is None
                or not _schema_field_is_removable(path)
                or not path
            ):
                return
            try:
                collect_payload()
                before_edit = snapshot_payload(editor_state["payload"])
                parent = _payload_at(path[:-1])
                if not isinstance(parent, dict) or path[-1] not in parent:
                    return
                delete_value_at_path(editor_state["payload"], path)
            except (KeyError, IndexError, TypeError, ValueError, json.JSONDecodeError) as exc:
                status.setText(f"Could not remove property: {exc}")
                return

            editor_state["undo_stack"].append(before_edit)
            _mark_dirty()
            _refresh_after_structural_edit(
                {".".join(str(part) for part in path[:-1])}
            )

        def show_list_context_menu(position: Any) -> None:
            item = tree.itemAt(position)
            if item is None:
                return
            tree.setCurrentItem(item)
            update_array_actions()
            context_menu.popup(tree.viewport().mapToGlobal(position))

        def show_settings_menu(section: int) -> None:
            if section != 0:
                return
            update_settings_actions()
            header = tree.header()
            left = header.sectionViewportPosition(section)
            bottom = header.viewport().height()
            settings_menu.popup(
                header.viewport().mapToGlobal(QtCore.QPoint(left, bottom))
            )

        def update_settings_actions() -> None:
            has_items = tree.topLevelItemCount() > 0
            settings_actions["expand"].setEnabled(has_items)
            settings_actions["collapse"].setEnabled(has_items)
            settings_actions["revert"].setEnabled(
                bool(editor_state["dirty"] or editor_state["undo_stack"])
            )
            settings_actions["reload"].setEnabled(True)

        def apply_changes() -> None:
            active_config_path = editor_state["config_path"]
            active_with_particles = editor_state["with_particles"]
            _clear_field_errors()
            try:
                before_edit = snapshot_payload(editor_state["payload"])
                payload = collect_payload()
            except (TypeError, ValueError, json.JSONDecodeError) as exc:
                status.setText(f"Invalid value: {exc}")
                return

            try:
                updated_config = validate_payload(payload, self.config_type)
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
                    file_content = format_json_like_source(
                        patched_payload, raw_text, active_config_path
                    )
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
        settings_actions["expand"].triggered.connect(tree.expandAll)
        settings_actions["collapse"].triggered.connect(tree.collapseAll)
        settings_actions["revert"].triggered.connect(undo_changes)
        settings_actions["reload"].triggered.connect(reload_from_disk)
        tree.currentItemChanged.connect(lambda *_args: update_array_actions())
        tree.setContextMenuPolicy(QtCore.Qt.ContextMenuPolicy.CustomContextMenu)
        tree.customContextMenuRequested.connect(show_list_context_menu)
        tree.header().setToolTip("Click Setting for editor actions")
        tree.header().sectionClicked.connect(show_settings_menu)
        for operation, action in context_actions.items():
            action.triggered.connect(
                lambda _checked=False, selected_operation=operation: edit_array(
                    selected_operation
                )
            )
        value_actions["copy"].triggered.connect(copy_field_value)
        value_actions["paste"].triggered.connect(paste_field_value)
        remove_property_action.triggered.connect(remove_property)
        QtWidgets.QApplication.clipboard().dataChanged.connect(update_array_actions)
        update_settings_actions()
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

        def editor_text() -> str:
            return format_json_like_source(
                collect_payload(),
                editor_state["original_json"],
                editor_state["config_path"],
            )

        def set_editor_text(
            text: str,
            *,
            config_path: Path | None = None,
            saved_text: str | None = None,
            remember: bool = False,
        ) -> None:
            if config_path is not None:
                editor_state["config_path"] = Path(config_path)
            populate_editor(text, reset_history=not remember)
            if remember:
                editor_state["dirty"] = True
            if saved_text is not None:
                editor_state["dirty"] = text != saved_text
            update_array_actions()

        def mark_editor_saved() -> None:
            editor_state["original_json"] = editor_text()
            editor_state["baseline_payload"] = copy.deepcopy(
                collect_payload()
            )
            editor_state["dirty"] = False
            status.setText("Saved configuration.")
            update_settings_actions()

        def set_editor_text_and_notify(*args, **kwargs):
            set_editor_text(*args, **kwargs)
            changed = getattr(self, "json_editor_changed", None)
            if callable(changed):
                changed()

        content.text = editor_text
        content.set_text = set_editor_text_and_notify
        refresh_editor(config, config_path, with_particles)
        self._json_editor = {
            "dock": dock,
            "tree": tree,
            "filter": filter_box,
            "status": status,
            "shortcut": shortcut,
            "undo_shortcut": undo_shortcut,
            "refresh": refresh_editor,
            "widget": content,
            # Optional embedding hooks; default CLI behavior is unchanged.
            "is_dirty": lambda: bool(editor_state["dirty"]),
            "payload": lambda: copy.deepcopy(collect_payload()),
            "text": editor_text,
            "set_text": set_editor_text_and_notify,
            "validate": lambda: validate_payload(collect_payload(), self.config_type),
            "mark_saved": mark_editor_saved,
            "discard": lambda: populate_editor(editor_state["original_json"], reset_history=True),
            "reveal_path": reveal_path,
            "context_actions": context_actions,
            "value_actions": value_actions,
            "remove_property_action": remove_property_action,
            "property_menu": property_menu,
            "variant_menu": variant_menu,
            "settings_actions": settings_actions,
            "settings_menu": settings_menu,
        }
        return True


FIELD_LABELS = {
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
ENUM_CHOICES = {
    "simulation_type": ["fluid_dynamics", "continuum_dynamics"],
    "surface_type": ["free_surface", "confined", "open_boundary", "free_stream"],
    "kernel_correction": ["linear", "none"],
}
PATH_ROLE = QtCore.Qt.ItemDataRole.UserRole + 1


class JsonPropertyEditor(QtWidgets.QWidget):
    """Tree-based JSON editor widget shared by the preview and workbench."""

    textChanged = QtCore.Signal(str)
    saveRequested = QtCore.Signal()
    reloadRequested = QtCore.Signal()

    def __init__(self, parent=None, *, external_controller: bool = False):
        super().__init__(parent)
        self.external_controller = external_controller
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
        if not external_controller:
            callbacks = {
                "Expand": lambda: self.tree.expandAll(),
                "Collapse": lambda: self.tree.collapseAll(),
                "Revert": self.revert,
                "Reload": self.reloadRequested.emit,
                **{
                    label: lambda checked=False, operation=operation: self.edit_array(operation)
                    for label, operation in (
                        ("Add", "add"),
                        ("Duplicate", "duplicate"),
                        ("Remove", "remove"),
                        ("Move up", "up"),
                        ("Move down", "down"),
                    )
                },
            }
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
        self.status = QtWidgets.QLabel(
            "Loaded configuration. Ctrl+S saves and refreshes Preview."
        )
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        if not external_controller:
            self.filter.textChanged.connect(self._filter)
            self.tree.currentItemChanged.connect(self._update_actions)
        self.undo_shortcut = QtGui.QShortcut(QtGui.QKeySequence.StandardKey.Undo, self)
        self.undo_shortcut.setContext(QtCore.Qt.ShortcutContext.WidgetWithChildrenShortcut)
        self.save_shortcut = QtGui.QShortcut(QtGui.QKeySequence.StandardKey.Save, self)
        self.save_shortcut.setContext(QtCore.Qt.ShortcutContext.WidgetWithChildrenShortcut)
        if external_controller:
            self.undo_shortcut.setEnabled(False)
            self.save_shortcut.setEnabled(False)
        else:
            self.undo_shortcut.activated.connect(self.revert)
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
        return value_at_path(self._payload, path)

    def _build_tree(self):
        expand_defaults = not self._items
        if self._items:
            self._expanded = {path for path, item in self._items.items() if item.isExpanded()}
        self._fields, self._items, self._lists = {}, {}, set()
        self.tree.clear()

        def add(parent, key, value, path):
            label = FIELD_LABELS.get(str(key), str(key).replace("_", " ").capitalize())
            if isinstance(path[-1], int):
                label = next(
                    (
                        value[name]
                        for name in ("name", "body_name", "observed_body", "oriented_box")
                        if isinstance(value, dict) and isinstance(value.get(name), str)
                    ),
                    f"Item {path[-1] + 1}",
                )
            item = QtWidgets.QTreeWidgetItem([label, ""])
            (parent.addChild if parent is not None else self.tree.addTopLevelItem)(item)
            item.setData(0, QtCore.Qt.ItemDataRole.UserRole, ".".join(map(str, path)))
            item.setData(0, PATH_ROLE, path)
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
                for child_key, child in (
                    value.items() if isinstance(value, dict) else enumerate(value)
                ):
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
            signal.connect(
                lambda *_, p=path, original=value, w=widget: self._field_changed(
                    p, original, w
                )
            )

        for key, value in self._payload.items():
            add(None, key, value, (key,))
        self._filter(self.filter.text())
        self._update_actions()

    def _format(self):
        return format_json_like_source(self._payload, self._layout_text, self.config_path)

    def _field_changed(self, path, original, widget):
        self._history.append(self._text)
        raw = (
            widget.isChecked()
            if isinstance(widget, QtWidgets.QCheckBox)
            else widget.currentText()
            if isinstance(widget, QtWidgets.QComboBox)
            else widget.text()
        )
        try:
            value = parse_field_value(original, raw)
            self._invalid.pop(path, None)
            widget.setStyleSheet("")
        except (ValueError, TypeError):
            value = raw
            self._invalid[path] = (
                f"Invalid value at {'.'.join(map(str, path))}: {raw}"
            )
            widget.setStyleSheet("border: 2px solid #ff6b5f; background: #472522;")
        set_value_at_path(self._payload, path, value)
        self._text = self._format()
        self._items[path].setBackground(0, QtGui.QColor("#594a24"))
        self.status.setText(
            next(
                iter(self._invalid.values()),
                "Unsaved draft. Ctrl+S validates, saves and refreshes Preview.",
            )
        )
        self._update_actions()
        self.textChanged.emit(self._text)

    def validate_config(self):
        try:
            if self._invalid:
                raise ValueError(next(iter(self._invalid.values())))
            return validate_payload(self._payload, SimulationConfig)
        except ValueError as exc:
            errors = exc.errors(include_url=False) if hasattr(exc, "errors") else []
            path = tuple(errors[0]["loc"]) if errors else next(iter(self._invalid), ())
            if path in self._fields:
                self._fields[path].setStyleSheet(
                    "border: 2px solid #ff6b5f; background: #472522;"
                )
                self._fields[path].setToolTip(str(exc))
                self.tree.scrollToItem(self._items[path])
            self.status.setText(str(exc).splitlines()[0])
            raise

    def _filter(self, query):
        query = query.strip().lower()

        def visit(item):
            path = item.data(0, PATH_ROLE)
            widget = self._fields.get(path)
            value = (
                widget.currentText()
                if isinstance(widget, QtWidgets.QComboBox)
                else widget.text()
                if isinstance(widget, QtWidgets.QLineEdit)
                else item.text(1)
            )
            match = not query or query in (
                f"{item.text(0)} {value} {'.'.join(map(str, path))}".lower()
            )
            children = [
                visit(item.child(index)) for index in range(item.childCount())
            ]
            visible = match or any(children)
            item.setHidden(not visible)
            if query and any(children):
                item.setExpanded(True)
            return visible

        for index in range(self.tree.topLevelItemCount()):
            visit(self.tree.topLevelItem(index))

    def _update_actions(self, *_):
        if self.external_controller:
            return
        item = self.tree.currentItem()
        path = item.data(0, PATH_ROLE) if item else ()
        entry = bool(path) and path[:-1] in self._lists and isinstance(path[-1], int)
        self.buttons["Add"].setEnabled(path in self._lists)
        for label in ("Duplicate", "Remove"):
            self.buttons[label].setEnabled(entry)
        self.buttons["Move up"].setEnabled(entry and path[-1] > 0)
        self.buttons["Move down"].setEnabled(
            entry and path[-1] < len(self._at(path[:-1])) - 1
        )
        self.buttons["Revert"].setEnabled(bool(self._history) or self.dirty)

    def edit_array(self, operation):
        if self._invalid:
            self.status.setText("Correct invalid field values before changing a list.")
            return
        item = self.tree.currentItem()
        path = item.data(0, PATH_ROLE) if item else ()
        before = self._text
        if operation == "add" and path in self._lists:
            target = self._at(path)
            if target:
                value = copy.deepcopy(target[-1])
            else:
                value = _new_json_editor_list_item(
                    path, _config_spatial_dim(self.validate_config())
                )
            if not edit_list(self._payload, path, operation, new_item=value):
                return
        elif not edit_list(self._payload, path, operation):
            return
        self._history.append(before)
        self._text = self._format()
        self._build_tree()
        self.status.setText("Unsaved list changes. Ctrl+S validates and saves.")
        self.textChanged.emit(self._text)

    def revert(self):
        text = pop_undo_snapshot(self._history, self._saved_text)
        self._text = text
        self._payload = load_json_preserving_floats(text)
        self._invalid.clear()
        self._build_tree()
        self.status.setText("Reverted the latest local edit.")
        self.textChanged.emit(self._text)
