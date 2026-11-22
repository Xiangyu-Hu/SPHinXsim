"""Per-scene particle visibility and interactive legend controls."""

from __future__ import annotations

import logging
from typing import Any

import numpy as np

from sphinxsim.visualization.particle_time import PARTICLE_OVERLAY_CENTER_Y

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
        self.sphere_splats: set[str] = set()
        self._point_mappers: dict[str, tuple[Any, Any]] = {}
        self.scalar_bars: dict[str, Any] = {}
        self._colour_warnings: set[tuple[str, str]] = set()

    def set_scene(self, key: Any, config: Any, legend: Any = None) -> None:
        self._clear_scalar_bars()
        if key != self.scene_key:
            for body in self.sphere_splats:
                entry = self._point_mappers.get(body)
                if entry is not None:
                    entry[0].SetMapper(entry[1])
            self.hidden.clear()
            self.colouring.clear()
            self.sphere_splats.clear()
            self._point_mappers.clear()
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
            self._point_mappers[body] = (actor, actor.GetMapper())
            if body in self.sphere_splats:
                try:
                    self._set_actor_representation(body, actor, True)
                except ValueError as exc:
                    logger.warning("Cannot render sphere splats for %s: %s; showing points", body, exc)
        self.actors[body] = actor
        if initialized and mesh is not None:
            self.meshes[body] = mesh
        else:
            self.meshes.pop(body, None)
        actor.SetVisibility(body not in self.hidden)
        if body in self.colouring:
            self._apply_colouring(body)
        if body not in self.bodies:
            self.bodies.add(body)
            self._update_legend()

    def _set_actor_representation(self, body: str, actor: Any, splats: bool) -> None:
        point_mapper = self._point_mappers[body][1]
        if not splats:
            actor.SetMapper(point_mapper)
            return

        mesh = point_mapper.GetInput()
        if mesh is None:
            raise ValueError("The particle mesh is unavailable.")
        radius = _sphere_splat_radius(mesh, self.config)
        from pyvista import PointGaussianMapper

        mapper = PointGaussianMapper(emissive=False, scale_factor=radius)
        mapper.dataset = mesh
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
        colour_map.AddRGBPoint(low, 0.23, 0.30, 0.75)
        if not vector:
            colour_map.AddRGBPoint((low + high) / 2, 0.87, 0.87, 0.87)
        colour_map.AddRGBPoint(high, 0.71, 0.02, 0.15)
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

        def _body_at(self, pos: Any) -> str | None:
            width, height = controller.plotter.render_window.GetSize()
            if not widget.width() or not widget.height():
                return None
            return controller.body_at(
                pos.x() * width / widget.width(),
                (widget.height() - pos.y()) * height / widget.height(),
            )

        def eventFilter(self, watched: Any, event: Any) -> bool:
            if event.type() == QtCore.QEvent.Type.MouseButtonPress:
                if event.button() == QtCore.Qt.MouseButton.RightButton:
                    self.pressed = (event.pos(), self._body_at(event.pos()))
            elif event.type() == QtCore.QEvent.Type.MouseButtonRelease:
                if event.button() == QtCore.Qt.MouseButton.RightButton and self.pressed:
                    position, body = self.pressed
                    self.pressed = None
                    if (body is not None and body == self._body_at(event.pos())
                            and (event.pos() - position).manhattanLength()
                            <= QtWidgets.QApplication.startDragDistance()):
                        global_pos = widget.mapToGlobal(event.pos())
                        scene_key = controller.scene_key
                        QtCore.QTimer.singleShot(
                            0, lambda: self._show_menu(body, global_pos, scene_key)
                        )
            return False

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
