"""Per-scene particle visibility and interactive legend controls."""

from __future__ import annotations

from typing import Any


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

    def set_scene(self, key: Any, config: Any, legend: Any = None) -> None:
        if key != self.scene_key:
            self.hidden.clear()
            self.scene_key = key
        self.config = config
        self.bodies.clear()
        self.actors.clear()
        self.legend = legend

    def add_actor(self, body: str, actor: Any) -> None:
        self.actors[body] = actor
        actor.SetVisibility(body not in self.hidden)
        if body not in self.bodies:
            self.bodies.add(body)
            self._update_legend()

    def set_visible(self, body: str, visible: bool) -> None:
        if visible:
            self.hidden.discard(body)
        else:
            self.hidden.add(body)
        actor = self.actors.get(body)
        if actor is not None:
            actor.SetVisibility(visible)
        self._update_legend()
        self.plotter.render()

    def _update_legend(self) -> None:
        from sphinxsim.visualization.preview import _draw_legend, _legend_entries_for_config

        entries = _legend_entries_for_config(self.config, self.bodies)
        self.plotter.subplot(0, 0)
        self.legend = _draw_legend(self.plotter, entries)
        if self.legend is not None:
            for index, body in enumerate(sorted(self.bodies)):
                if body in self.hidden:
                    self.legend.SetEntryString(index, f"{body} Particles (hidden)")

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
    from qtpy import QtCore, QtWidgets

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
            self.menu.popup(position)

    event_filter = ParticleMenuFilter()
    widget.installEventFilter(event_filter)
    return event_filter
