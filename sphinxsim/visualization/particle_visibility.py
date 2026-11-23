"""Per-scene particle visibility and interactive legend controls."""

from __future__ import annotations

import logging
from typing import Any

import numpy as np

from sphinxsim.visualization.particle_time import PARTICLE_OVERLAY_CENTER_Y
from sphinxsim.visualization.preview import _PARTICLE_DEFAULT_OPACITY

logger = logging.getLogger(__name__)

_SPHERE_SPLAT_SHADER = (
    "//VTK::Color::Impl\n"
    "float dist = dot(offsetVCVSOutput.xy,offsetVCVSOutput.xy);\n"
    "if (dist > 1.0) {\n"
    "  discard;\n"
    "} else {\n"
    "  float scale = (1.0 - dist);\n"
    "  ambientColor *= scale;\n"
    "  diffuseColor *= scale;\n"
    "}\n"
)
_DEFAULT_PALETTE = "Blue to red"
_PARTICLE_PALETTES = {
    _DEFAULT_PALETTE: (
        (0.23, 0.30, 0.75), (0.87, 0.87, 0.87), (0.71, 0.02, 0.15),
    ),
    "Viridis": (
        (0.267, 0.005, 0.329), (0.230, 0.322, 0.546), (0.128, 0.567, 0.551),
        (0.369, 0.789, 0.383), (0.993, 0.906, 0.144),
    ),
    "Grayscale": ((0.0, 0.0, 0.0), (1.0, 1.0, 1.0)),
}


def _sphere_splat_radius(mesh: Any, config: Any) -> float:
    spacing = config.geometries.global_resolution.particle_spacing
    if spacing is not None:
        return float(spacing) * 0.5

    bounds = mesh.bounds
    extents = [abs(float(bounds[i + 1]) - float(bounds[i])) for i in (0, 2, 4)]
    largest = max(extents)
    active = [extent for extent in extents if extent > largest * 1e-8]
    count = mesh.n_points
    if not active or count <= 0 or not np.all(np.isfinite(active)):
        raise ValueError("Cannot estimate particle spacing from this point cloud.")
    return float(np.prod(active) / count) ** (1.0 / len(active)) * 0.5


class ParticleVisibility:
    """Keep particle visibility independent of frequently replaced VTK actors."""

    def __init__(self, plotter: Any) -> None:
        self.plotter = plotter
        self.scene_key: Any = None
        self.config: Any = None
        self.hidden: set[str] = set()
        self.bodies: set[str] = set()
        self.actors: dict[str, Any] = {}
        self.legend: Any = None
        self.meshes: dict[str, Any] = {}
        self.colouring: dict[str, str] = {}
        self.palettes: dict[str, str] = {}
        self.sphere_splats: set[str] = set()
        self.opaque_bodies: set[str] = set()
        self.clipped_bodies: set[str] = set()
        self.clip_origins: dict[str, tuple[float, float, float]] = {}
        self.clip_normals: dict[str, tuple[float, float, float]] = {}
        self.inverted_clip_bodies: set[str] = set()
        self._point_mappers: dict[str, tuple[Any, Any]] = {}
        self._particle_meshes: dict[str, Any] = {}
        self.scalar_bars: dict[str, Any] = {}
        self._colour_warnings: set[tuple[str, str]] = set()

    def set_scene(self, key: Any, config: Any, legend: Any = None) -> None:
        self._clear_scalar_bars()
        if key != self.scene_key:
            for body, actor in self.actors.items():
                entry = self._point_mappers.get(body)
                if entry is not None:
                    point_mapper = entry[1]
                    mesh = self._particle_meshes.get(body)
                    if mesh is not None:
                        point_mapper.SetInputData(mesh)
                    actor.SetMapper(point_mapper)
            self.hidden.clear()
            self.colouring.clear()
            self.palettes.clear()
            self.sphere_splats.clear()
            self.opaque_bodies.clear()
            self.clipped_bodies.clear()
            self.clip_origins.clear()
            self.clip_normals.clear()
            self.inverted_clip_bodies.clear()
            self._point_mappers.clear()
            self._particle_meshes.clear()
            self._colour_warnings.clear()
            self.scene_key = key
        self.config = config
        self.bodies.clear()
        self.actors.clear()
        self.meshes.clear()
        self.legend = legend

    def add_actor(self, body: str, actor: Any, mesh: Any = None, *, initialized: bool = False) -> None:
        previous = self._point_mappers.get(body)
        if previous is None or previous[0] is not actor:
            point_mapper = actor.GetMapper()
            self._point_mappers[body] = (actor, point_mapper)
        else:
            point_mapper = previous[1]
        source_mesh = mesh if mesh is not None else point_mapper.GetInput()
        source_mesh = self._wrap_particle_mesh(source_mesh)
        if source_mesh is None:
            self._particle_meshes.pop(body, None)
        else:
            self._particle_meshes[body] = source_mesh
        self.actors[body] = actor
        if initialized and mesh is not None:
            self.meshes[body] = mesh
        else:
            self.meshes.pop(body, None)
        actor.GetProperty().SetOpacity(
            1.0 if body in self.opaque_bodies else _PARTICLE_DEFAULT_OPACITY
        )
        actor.SetVisibility(body not in self.hidden)
        self._refresh_particle_mesh(body)
        if body in self.sphere_splats:
            try:
                self._set_actor_representation(body, actor, True)
            except ValueError as exc:
                logger.warning("Cannot render sphere splats for %s: %s; showing points", body, exc)
        if body in self.colouring:
            self._apply_colouring(body)
        if body not in self.bodies:
            self.bodies.add(body)
            self._update_legend()

    @staticmethod
    def _wrap_particle_mesh(mesh: Any) -> Any:
        if mesh is None or (
            hasattr(mesh, "points") and hasattr(mesh, "point_data")
        ):
            return mesh
        if not hasattr(mesh, "GetNumberOfPoints"):
            return mesh
        from pyvista import wrap

        return wrap(mesh)

    def _set_actor_representation(self, body: str, actor: Any, splats: bool) -> None:
        point_mapper = self._point_mappers[body][1]
        if not splats:
            actor.SetMapper(point_mapper)
            return

        mesh = self._particle_meshes.get(body)
        if mesh is None:
            raise ValueError("The particle mesh is unavailable.")
        radius = _sphere_splat_radius(mesh, self.config)
        from pyvista import PointGaussianMapper

        mapper = PointGaussianMapper(emissive=False, scale_factor=radius)
        mapper.dataset = point_mapper.GetInput()
        mapper.SetSplatShaderCode(_SPHERE_SPLAT_SHADER)
        mapper.ScalarVisibilityOff()
        actor.SetMapper(mapper)

    def set_sphere_splats(self, body: str, enabled: bool) -> None:
        actor = self.actors[body]
        self._set_actor_representation(body, actor, enabled)
        if enabled:
            self.sphere_splats.add(body)
        else:
            self.sphere_splats.discard(body)
        if body in self.colouring:
            self._apply_colouring(body)
        self.plotter.render()

    def clip_plane_settings(
        self, body: str
    ) -> tuple[tuple[float, float, float], tuple[float, float, float], bool]:
        origin = self.clip_origins.get(body)
        if origin is None:
            mesh = self._particle_meshes.get(body)
            if mesh is None:
                mapper = self.actors[body].GetMapper()
                mesh = self._wrap_particle_mesh(mapper.GetInput())
            bounds = getattr(mesh, "bounds", None)
            if bounds is None and mesh is not None and hasattr(mesh, "GetBounds"):
                bounds = mesh.GetBounds()
            try:
                bounds = np.asarray(bounds, dtype=float)
                if bounds.shape != (6,) or not np.all(np.isfinite(bounds)):
                    raise ValueError
                origin = tuple(
                    (float(bounds[index]) + float(bounds[index + 1])) * 0.5
                    for index in (0, 2, 4)
                )
            except (TypeError, ValueError):
                origin = (0.0, 0.0, 0.0)
        normal = self.clip_normals.get(body, (1.0, 0.0, 0.0))
        return origin, normal, body in self.inverted_clip_bodies

    def set_clip_plane(
        self,
        body: str,
        origin: tuple[float, float, float],
        normal: tuple[float, float, float],
        *,
        inverted: bool = False,
    ) -> None:
        if body not in self.bodies:
            raise ValueError(f"Particle body {body!r} is unavailable")
        try:
            origin_values = np.asarray(origin, dtype=float)
            normal_values = np.asarray(normal, dtype=float)
        except (TypeError, ValueError) as exc:
            raise ValueError("Clip plane origin and normal must be numeric 3D vectors") from exc
        if (
            origin_values.shape != (3,)
            or normal_values.shape != (3,)
            or not np.all(np.isfinite(origin_values))
            or not np.all(np.isfinite(normal_values))
        ):
            raise ValueError("Clip plane origin and normal must be finite 3D vectors")
        normal_length = float(np.linalg.norm(normal_values))
        if not np.isfinite(normal_length) or normal_length == 0:
            raise ValueError("Clip plane normal must have finite, non-zero length")

        self.clip_origins[body] = tuple(float(value) for value in origin_values)
        self.clip_normals[body] = tuple(float(value / normal_length) for value in normal_values)
        if inverted:
            self.inverted_clip_bodies.add(body)
        else:
            self.inverted_clip_bodies.discard(body)
        self._refresh_particle_mesh(body)
        self.plotter.render()

    def set_clipping(self, body: str, enabled: bool) -> None:
        if body not in self.bodies:
            raise ValueError(f"Particle body {body!r} is unavailable")
        if enabled:
            if body not in self.clip_origins:
                origin, normal, _ = self.clip_plane_settings(body)
                self.clip_origins[body] = origin
                self.clip_normals[body] = normal
            self.clipped_bodies.add(body)
        else:
            self.clipped_bodies.discard(body)
        self._refresh_particle_mesh(body)
        self.plotter.render()

    def _filtered_particle_mesh(self, body: str) -> Any:
        mesh = self._particle_meshes.get(body)
        if mesh is None or body not in self.clipped_bodies:
            return mesh
        points = np.asarray(mesh.points, dtype=float)
        origin, normal, inverted = self.clip_plane_settings(body)
        signed_distance = (points - np.asarray(origin)) @ np.asarray(normal)
        keep = signed_distance >= 0 if inverted else signed_distance <= 0
        import pyvista as pv

        filtered = pv.PolyData(points[keep])
        for name, values in mesh.point_data.items():
            filtered.point_data[name] = np.asarray(values)[keep]
        for name, values in mesh.field_data.items():
            filtered.field_data[name] = np.asarray(values)
        return filtered

    def _refresh_particle_mesh(self, body: str) -> None:
        entry = self._point_mappers.get(body)
        if entry is None:
            return
        mesh = self._filtered_particle_mesh(body)
        if mesh is None:
            return
        point_mapper = entry[1]
        point_mapper.SetInputData(mesh)
        point_mapper.Modified()
        actor = self.actors.get(body)
        if actor is not None and body in self.sphere_splats:
            mapper = actor.GetMapper()
            mapper.dataset = mesh
            mapper.Modified()

    def set_opaque(self, body: str, opaque: bool) -> None:
        actor = self.actors[body]
        actor.GetProperty().SetOpacity(1.0 if opaque else _PARTICLE_DEFAULT_OPACITY)
        if opaque:
            self.opaque_bodies.add(body)
        else:
            self.opaque_bodies.discard(body)
        self.plotter.render()

    def colourable_fields(self, body: str) -> dict[str, bool]:
        """Return point-data names and whether each is a vector magnitude."""
        mesh = self.meshes.get(body)
        if mesh is None:
            return {}
        fields = {}
        for name in mesh.point_data:
            if name.startswith("_sphinxsim_colour_"):
                continue
            values = np.asarray(mesh.point_data[name])
            if not np.issubdtype(values.dtype, np.number):
                continue
            if values.ndim == 1:
                fields[name] = False
            elif values.ndim == 2 and values.shape[1] in (2, 3):
                fields[name] = True
        return dict(sorted(fields.items()))

    def set_colour(self, body: str, field: str | None) -> None:
        if field is not None and field not in self.colourable_fields(body):
            raise ValueError(f"Particle field {field!r} is unavailable for {body!r}")
        if field is None:
            self.colouring.pop(body, None)
        else:
            self.colouring[body] = field
        self._apply_colouring(body)
        self.plotter.render()

    def set_palette(self, body: str, palette: str) -> None:
        if palette not in _PARTICLE_PALETTES:
            raise ValueError(f"Unknown particle color palette: {palette!r}")
        if body not in self.actors:
            raise ValueError(f"Particle body {body!r} is unavailable")
        self.palettes[body] = palette
        self._apply_colouring(body)
        self.plotter.render()

    def _remove_scalar_bar(self, body: str) -> None:
        bar = self.scalar_bars.pop(body, None)
        if bar is not None:
            self.plotter.renderers[0].RemoveActor(bar)
        self._layout_scalar_bars()

    def _clear_scalar_bars(self) -> None:
        for body in list(self.scalar_bars):
            self._remove_scalar_bar(body)

    def _layout_scalar_bars(self) -> None:
        visible = [(body, bar) for body, bar in sorted(self.scalar_bars.items())
                   if body not in self.hidden]
        for body, bar in self.scalar_bars.items():
            bar.SetVisibility(body not in self.hidden)
        height = min(0.1, 0.8 / len(visible)) if visible else 0
        for index, (_, bar) in enumerate(visible):
            bar.SetPosition(0.78, PARTICLE_OVERLAY_CENTER_Y - height / 2 + index * 0.85 / len(visible))
            bar.SetWidth(0.19)
            bar.SetHeight(height)

    def _apply_colouring(self, body: str) -> None:
        self._remove_scalar_bar(body)
        actor = self.actors.get(body)
        if actor is None:
            return
        mapper = actor.GetMapper()
        field = self.colouring.get(body)
        if field is None:
            mapper.ScalarVisibilityOff()
            return
        fields = self.colourable_fields(body)
        if field not in fields:
            mapper.ScalarVisibilityOff()
            warning = (body, field)
            if warning not in self._colour_warnings:
                logger.warning("Particle field %r is unavailable for %s; showing body colour until it returns", field, body)
                self._colour_warnings.add(warning)
            return
        self._colour_warnings.discard((body, field))
        mesh = self.meshes[body]
        vector = fields[field]
        values = np.asarray(mesh.point_data[field])
        values = np.linalg.norm(values.astype(float), axis=1) if vector else values.astype(float)
        finite = values[np.isfinite(values)]
        if not finite.size:
            mapper.ScalarVisibilityOff()
            logger.warning("Particle field %r for %s has no finite values; showing body colour", field, body)
            return
        reference_data = mesh.field_data.get("ScalingReference_" + field)
        reference = None
        if reference_data is not None:
            ref_values = np.asarray(reference_data).ravel()
            if (ref_values.size == 1 and np.issubdtype(ref_values.dtype, np.number)
                    and np.isfinite(ref_values[0]) and ref_values[0] > 0):
                reference = float(ref_values[0])
            else:
                logger.warning("Invalid scaling reference for particle field %r on %s; using data range", field, body)
        if reference is not None:
            low, high = (0.0 if vector else -reference), reference
        else:
            low, high = float(finite.min()), float(finite.max())
            if low == high:
                padding = max(abs(low) * 0.01, 1e-12)
                low, high = low - padding, high + padding

        from vtkmodules.vtkRenderingAnnotation import vtkScalarBarActor
        from vtkmodules.vtkRenderingCore import vtkColorTransferFunction

        colour_map = vtkColorTransferFunction()
        colour_map.SetColorSpaceToRGB()
        palette = self.palettes.get(body, _DEFAULT_PALETTE)
        colours = _PARTICLE_PALETTES[palette]
        if palette == _DEFAULT_PALETTE and vector:
            colours = (colours[0], colours[-1])
        for index, colour in enumerate(colours):
            position = low + (high - low) * index / (len(colours) - 1)
            colour_map.AddRGBPoint(position, *colour)
        colour_map.SetNanColor(0.5, 0.5, 0.5)
        array_name = "_sphinxsim_colour_" + field
        if body in self.sphere_splats and mapper.IsA("vtkPointGaussianMapper"):
            from vtkmodules.util.numpy_support import numpy_to_vtk, vtk_to_numpy

            # VTK's Gaussian mapper requires direct RGB(A), not mapped scalars.
            rgba = colour_map.MapScalars(numpy_to_vtk(values, deep=True), 0, 0)
            array_name += "_rgba"
            mesh.point_data[array_name] = vtk_to_numpy(rgba)
            mapper.SetColorModeToDirectScalars()
        else:
            mesh.point_data[array_name] = values
            mapper.SetColorModeToMapScalars()
        self._refresh_particle_mesh(body)
        mapper.SetScalarModeToUsePointFieldData()
        mapper.SelectColorArray(array_name)
        mapper.SetLookupTable(colour_map)
        mapper.SetScalarRange(low, high)
        mapper.ScalarVisibilityOn()
        bar = vtkScalarBarActor()
        bar.SetLookupTable(colour_map)
        bar.SetTitle(f"{body}: {field}" + (" magnitude" if vector else ""))
        bar.SetOrientationToHorizontal()
        bar.SetNumberOfLabels(3)
        self.plotter.renderers[0].AddActor(bar)
        self.scalar_bars[body] = bar
        self._layout_scalar_bars()

    def set_visible(self, body: str, visible: bool) -> None:
        if visible:
            self.hidden.discard(body)
        else:
            self.hidden.add(body)
        actor = self.actors.get(body)
        if actor is not None:
            actor.SetVisibility(visible)
        self._layout_scalar_bars()
        self._update_legend_colours()
        self.plotter.render()

    def _update_legend(self) -> None:
        from sphinxsim.visualization.preview import _draw_legend, _legend_entries_for_config

        entries = _legend_entries_for_config(self.config, self.bodies)
        self.plotter.subplot(0, 0)
        self.legend = _draw_legend(self.plotter, entries)
        self._update_legend_colours()

    def _update_legend_colours(self) -> None:
        from sphinxsim.visualization.preview import _body_colour

        if self.legend is not None:
            for index, body in enumerate(sorted(self.bodies)):
                colour = _body_colour(body, self.config)
                scale = 0.75 if body in self.hidden else 1.0
                self.legend.SetEntryColor(index, *(component * scale for component in colour))

    def body_at(self, x: float, y: float) -> str | None:
        """Find a legend row from VTK display coordinates (origin at bottom left)."""
        if self.legend is None or not self.bodies:
            return None
        renderer = self.plotter.renderers[0]
        left, bottom = self.legend.GetPositionCoordinate().GetComputedDoubleDisplayValue(renderer)
        right, top = self.legend.GetPosition2Coordinate().GetComputedDoubleDisplayValue(renderer)
        padding = self.legend.GetPadding()
        left += padding
        right -= padding
        bottom += padding
        top -= padding
        if not (left <= x <= right and bottom <= y <= top) or top <= bottom:
            return None
        names = sorted(self.bodies)
        index = min(int((top - y) * len(names) / (top - bottom)), len(names) - 1)
        return names[index]

    def scalar_bar_at(self, x: float, y: float) -> str | None:
        renderer = self.plotter.renderers[0]
        for body, bar in self.scalar_bars.items():
            if not bar.GetVisibility():
                continue
            left, bottom = bar.GetPositionCoordinate().GetComputedDoubleDisplayValue(renderer)
            right, top = bar.GetPosition2Coordinate().GetComputedDoubleDisplayValue(renderer)
            if left <= x <= right and bottom <= y <= top:
                return body
        return None


def install_particle_menu(widget: Any, controller: ParticleVisibility) -> Any:
    """Install a Qt event filter without stealing VTK's right-drag gesture."""
    from qtpy import QtCore, QtGui, QtWidgets

    if not isinstance(widget, QtWidgets.QWidget):
        return None

    class ParticleMenuFilter(QtCore.QObject):
        def __init__(self) -> None:
            super().__init__(widget)
            self.pressed = None
            self.menu = None

        def _target_at(self, pos: Any) -> tuple[str, str] | None:
            width, height = controller.plotter.render_window.GetSize()
            if not widget.width() or not widget.height():
                return None
            x = pos.x() * width / widget.width()
            y = (widget.height() - pos.y()) * height / widget.height()
            body = controller.scalar_bar_at(x, y)
            if body is not None:
                return ("palette", body)
            body = controller.body_at(x, y)
            return ("particle", body) if body is not None else None

        def eventFilter(self, watched: Any, event: Any) -> bool:
            if event.type() == QtCore.QEvent.Type.MouseButtonPress:
                if event.button() == QtCore.Qt.MouseButton.RightButton:
                    self.pressed = (event.pos(), self._target_at(event.pos()))
            elif event.type() == QtCore.QEvent.Type.MouseButtonRelease:
                if event.button() == QtCore.Qt.MouseButton.RightButton and self.pressed:
                    position, target = self.pressed
                    self.pressed = None
                    if (target is not None and target == self._target_at(event.pos())
                            and (event.pos() - position).manhattanLength()
                            <= QtWidgets.QApplication.startDragDistance()):
                        global_pos = widget.mapToGlobal(event.pos())
                        scene_key = controller.scene_key
                        kind, body = target
                        show_menu = self._show_palette_menu if kind == "palette" else self._show_menu
                        QtCore.QTimer.singleShot(
                            0, lambda: show_menu(body, global_pos, scene_key)
                        )
            return False

        def _show_palette_menu(self, body: str, position: Any, scene_key: Any) -> None:
            if scene_key != controller.scene_key or body not in controller.scalar_bars:
                return
            field = controller.colouring.get(body)
            if self.menu is not None:
                self.menu.close()
                self.menu.deleteLater()
            self.menu = QtWidgets.QMenu(widget)
            self.menu.setObjectName("sphinxsim-particle-colour-bar-menu")
            group = QtGui.QActionGroup(self.menu)
            group.setExclusive(True)
            for name in _PARTICLE_PALETTES:
                action = self.menu.addAction(name)
                action.setCheckable(True)
                action.setChecked(controller.palettes.get(body, _DEFAULT_PALETTE) == name)
                group.addAction(action)

                def choose_palette(_checked: bool, selected: str = name) -> None:
                    if (scene_key == controller.scene_key and body in controller.scalar_bars
                            and controller.colouring.get(body) == field):
                        controller.set_palette(body, selected)

                action.triggered.connect(choose_palette)
            self.menu.popup(position)

        def _show_menu(self, body: str, position: Any, scene_key: Any) -> None:
            if scene_key != controller.scene_key or body not in controller.bodies:
                return
            if self.menu is not None:
                self.menu.close()
                self.menu.deleteLater()
            self.menu = QtWidgets.QMenu(widget)
            self.menu.setObjectName("sphinxsim-particle-context-menu")
            action = self.menu.addAction("Show particles")
            action.setObjectName("sphinxsim-particle-visibility")
            action.setCheckable(True)
            action.setChecked(body not in controller.hidden)

            def toggle(visible: bool) -> None:
                if scene_key == controller.scene_key and body in controller.bodies:
                    controller.set_visible(body, visible)

            action.toggled.connect(toggle)
            clipping = self.menu.addMenu("Clip particles")
            clipping.setObjectName("sphinxsim-particle-clipping")
            clip_action = clipping.addAction("Enable clipping")
            clip_action.setObjectName("sphinxsim-particle-clip-enabled")
            clip_action.setCheckable(True)
            clip_action.setChecked(body in controller.clipped_bodies)

            def toggle_clipping(enabled: bool) -> None:
                if scene_key == controller.scene_key and body in controller.bodies:
                    controller.set_clipping(body, enabled)

            clip_action.toggled.connect(toggle_clipping)
            configure_action = clipping.addAction("Configure clip plane...")
            configure_action.setObjectName("sphinxsim-particle-clip-configure")

            def configure_clip_plane() -> None:
                if scene_key != controller.scene_key or body not in controller.bodies:
                    return
                origin, normal, inverted = controller.clip_plane_settings(body)
                dialog = QtWidgets.QDialog(widget)
                dialog.setObjectName("sphinxsim-particle-clip-plane-dialog")
                dialog.setWindowTitle(f"Clip particles: {body}")
                dialog.setStyleSheet("""
                    QDialog#sphinxsim-particle-clip-plane-dialog {
                        background: #202329;
                        color: #e6e8ec;
                    }
                    QLabel, QCheckBox {
                        color: #e6e8ec;
                    }
                    QDoubleSpinBox {
                        min-height: 22px;
                        padding: 2px 6px;
                        border: 1px solid #4b515c;
                        border-radius: 3px;
                        background: #181a1f;
                        color: #e6e8ec;
                    }
                    QDoubleSpinBox:focus {
                        border-color: #5794cf;
                    }
                    QCheckBox {
                        spacing: 8px;
                    }
                    QCheckBox::indicator {
                        width: 14px;
                        height: 14px;
                        border: 1px solid #4b515c;
                        background: #181a1f;
                    }
                    QCheckBox::indicator:checked {
                        background: #315f8c;
                        border-color: #5794cf;
                    }
                    QPushButton {
                        min-width: 56px;
                        padding: 5px 10px;
                        border: 1px solid #4a505b;
                        border-radius: 3px;
                        background: #24272d;
                        color: #e6e8ec;
                    }
                    QPushButton:hover {
                        background: #30343c;
                        border-color: #75a9d0;
                    }
                    QPushButton:pressed {
                        background: #315f8c;
                        border-color: #80bde8;
                    }
                """)
                layout = QtWidgets.QFormLayout(dialog)
                fields: dict[str, QtWidgets.QDoubleSpinBox] = {}
                for vector_name, vector, limit in (
                    ("origin", origin, 1e12),
                    ("normal", normal, 1e6),
                ):
                    for axis, value in zip("xyz", vector):
                        field = QtWidgets.QDoubleSpinBox(dialog)
                        field.setObjectName(f"sphinxsim-clip-{vector_name}-{axis}")
                        field.setDecimals(6)
                        field.setRange(-limit, limit)
                        field.setSingleStep(0.1)
                        field.setValue(value)
                        fields[f"{vector_name}_{axis}"] = field
                        layout.addRow(f"{vector_name.title()} {axis.upper()}", field)
                preset_widget = QtWidgets.QWidget(dialog)
                preset_layout = QtWidgets.QHBoxLayout(preset_widget)
                preset_layout.setContentsMargins(0, 0, 0, 0)
                for selected_axis in "xyz":
                    preset = QtWidgets.QPushButton(f"+{selected_axis.upper()}", preset_widget)
                    preset.setObjectName(
                        f"sphinxsim-clip-normal-preset-{selected_axis}"
                    )

                    def select_normal(
                        _checked: bool = False, axis: str = selected_axis
                    ) -> None:
                        for component in "xyz":
                            fields[f"normal_{component}"].setValue(
                                1.0 if component == axis else 0.0
                            )

                    preset.clicked.connect(select_normal)
                    preset_layout.addWidget(preset)
                layout.addRow("Normal preset", preset_widget)
                invert = QtWidgets.QCheckBox("Keep the opposite side", dialog)
                invert.setObjectName("sphinxsim-particle-clip-invert")
                invert.setChecked(inverted)
                layout.addRow(invert)
                buttons = QtWidgets.QDialogButtonBox(
                    QtWidgets.QDialogButtonBox.StandardButton.Ok
                    | QtWidgets.QDialogButtonBox.StandardButton.Cancel,
                    parent=dialog,
                )
                buttons.accepted.connect(dialog.accept)
                buttons.rejected.connect(dialog.reject)
                layout.addRow(buttons)
                from sphinxsim.json_editor.editor import _set_windows_dark_title_bar

                _set_windows_dark_title_bar(dialog)
                if dialog.exec() != QtWidgets.QDialog.DialogCode.Accepted:
                    return
                try:
                    controller.set_clip_plane(
                        body,
                        tuple(fields[f"origin_{axis}"].value() for axis in "xyz"),
                        tuple(fields[f"normal_{axis}"].value() for axis in "xyz"),
                        inverted=invert.isChecked(),
                    )
                except ValueError as exc:
                    QtWidgets.QMessageBox.warning(widget, "Particle clipping", str(exc))

            configure_action.triggered.connect(configure_clip_plane)
            representation = self.menu.addMenu("Representation")
            representation.setObjectName("sphinxsim-particle-representation")
            representation_group = QtGui.QActionGroup(representation)
            representation_group.setExclusive(True)
            for label, splats in (("Points", False), ("Sphere splats", True)):
                choice = representation.addAction(label)
                choice.setCheckable(True)
                choice.setChecked((body in controller.sphere_splats) == splats)
                representation_group.addAction(choice)

                def choose_representation(_checked: bool, enabled: bool = splats) -> None:
                    if scene_key == controller.scene_key and body in controller.bodies:
                        try:
                            controller.set_sphere_splats(body, enabled)
                        except ValueError as exc:
                            QtWidgets.QMessageBox.warning(widget, "Particle representation", str(exc))

                choice.triggered.connect(choose_representation)
            transparency = self.menu.addMenu("Transparency")
            transparency.setObjectName("sphinxsim-particle-transparency")
            transparency_group = QtGui.QActionGroup(transparency)
            transparency_group.setExclusive(True)
            for label, opaque in (("Opaque (0%)", True), ("5% transparent", False)):
                choice = transparency.addAction(label)
                choice.setCheckable(True)
                choice.setChecked((body in controller.opaque_bodies) == opaque)
                transparency_group.addAction(choice)

                def choose_opacity(_checked: bool, selected: bool = opaque) -> None:
                    if scene_key == controller.scene_key and body in controller.bodies:
                        controller.set_opaque(body, selected)

                choice.triggered.connect(choose_opacity)
            fields = controller.colourable_fields(body)
            if body in controller.meshes:
                submenu = self.menu.addMenu("Color by")
                submenu.setObjectName("sphinxsim-particle-colouring")
                group = QtGui.QActionGroup(submenu)
                group.setExclusive(True)
                body_action = submenu.addAction("Body color")
                body_action.setCheckable(True)
                body_action.setChecked(body not in controller.colouring)
                group.addAction(body_action)

                def choose(field: str | None) -> None:
                    if scene_key == controller.scene_key and body in controller.bodies:
                        if field is not None and field not in controller.colourable_fields(body):
                            logger.warning("Particle field %r is no longer available for %s", field, body)
                            return
                        controller.set_colour(body, field)

                body_action.triggered.connect(lambda _checked: choose(None))
                selected = controller.colouring.get(body)
                if selected is not None and selected not in fields:
                    missing_action = submenu.addAction(f"{selected} (unavailable)")
                    missing_action.setCheckable(True)
                    missing_action.setChecked(True)
                    missing_action.setEnabled(False)
                    group.addAction(missing_action)
                for name, vector in fields.items():
                    field_action = submenu.addAction(name + (" magnitude" if vector else ""))
                    field_action.setCheckable(True)
                    field_action.setChecked(controller.colouring.get(body) == name)
                    group.addAction(field_action)
                    field_action.triggered.connect(
                        lambda _checked, field=name: choose(field)
                    )
            self.menu.popup(position)

    event_filter = ParticleMenuFilter()
    widget.installEventFilter(event_filter)
    return event_filter
