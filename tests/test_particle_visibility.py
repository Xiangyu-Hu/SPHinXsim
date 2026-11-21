"""Particle legend controls and visibility across actor updates."""

import os
import sys
from pathlib import Path

import pytest

from sphinxsim.config.schemas import SimulationConfig
from sphinxsim.visualization.particle_visibility import ParticleVisibility, install_particle_menu


CASE = Path(__file__).parent / "test_simulation/test_2d_simulation/data/column_collapse.json"


@pytest.mark.skipif(
    sys.platform.startswith("linux") and not os.environ.get("DISPLAY"),
    reason="Native Qt interaction requires a display",
)
@pytest.mark.parametrize("surface", ["workbench", "shell"])
def test_particle_legend_menu_and_actor_replacement(surface):
    pv = pytest.importorskip("pyvista")
    pytest.importorskip("pyvistaqt")
    pytest.importorskip("PySide6")
    from PySide6 import QtCore, QtTest, QtWidgets
    from pyvistaqt import BackgroundPlotter
    from sphinxsim.ui.workbench.viewport import PreviewViewport

    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    if surface == "workbench":
        plotter = PreviewViewport()
        plotter.resize(800, 600)
        plotter.show()
    else:
        plotter = BackgroundPlotter(show=True, window_size=(800, 600), auto_update=False)
    controller = ParticleVisibility(plotter)
    event_filter = install_particle_menu(plotter, controller)
    config = SimulationConfig.model_validate_json(CASE.read_text(encoding="utf-8"))
    controller.set_scene("first-run", config)
    try:
        first = plotter.add_mesh(pv.Sphere(), name="particle-preview-GranularBody")
        second = plotter.add_mesh(pv.Sphere(center=(2, 0, 0)), name="particle-preview-WallBoundary")
        controller.add_actor("GranularBody", first)
        controller.add_actor("WallBoundary", second)
        plotter.render()
        app.processEvents()

        renderer = plotter.renderers[0]
        left, bottom = controller.legend.GetPositionCoordinate().GetComputedDoubleDisplayValue(renderer)
        right, top = controller.legend.GetPosition2Coordinate().GetComputedDoubleDisplayValue(renderer)
        x = (left + right) / 2
        y = top - (top - bottom) / 4
        assert controller.body_at(x, y) == "GranularBody"
        assert controller.body_at(x, bottom + (top - bottom) / 4) == "WallBoundary"
        assert controller.body_at(left - 1, y) is None
        width, height = plotter.render_window.GetSize()
        pos = QtCore.QPoint(round(x * plotter.width() / width),
                            round((height - y) * plotter.height() / height))
        QtTest.QTest.mousePress(plotter, QtCore.Qt.MouseButton.RightButton, pos=pos)
        drag_end = pos + QtCore.QPoint(30, 30)
        QtTest.QTest.mouseMove(plotter, drag_end)
        QtTest.QTest.mouseRelease(plotter, QtCore.Qt.MouseButton.RightButton, pos=drag_end)
        QtTest.QTest.qWait(20)
        assert event_filter.menu is None
        QtTest.QTest.mouseClick(plotter, QtCore.Qt.MouseButton.RightButton, pos=pos)
        QtTest.QTest.qWait(50)
        menu = event_filter.menu
        assert menu is not None and menu.isVisible()
        action = menu.actions()[0]
        assert action.text() == "Show particles" and action.isChecked()
        action.trigger()
        assert not first.GetVisibility()
        assert second.GetVisibility()
        assert controller.legend.GetEntryString(0) == "GranularBody Particles (hidden)"
        menu.close()
        QtTest.QTest.mouseClick(plotter, QtCore.Qt.MouseButton.RightButton, pos=pos)
        QtTest.QTest.qWait(50)
        action = event_filter.menu.actions()[0]
        assert not action.isChecked()
        action.trigger()
        assert first.GetVisibility()
        event_filter.menu.close()
        controller.set_visible("GranularBody", False)

        replacement = plotter.add_mesh(pv.Sphere(), name="particle-preview-GranularBody")
        controller.add_actor("GranularBody", replacement)
        assert not replacement.GetVisibility()
        controller.set_scene("first-run", config)
        controller.add_actor("GranularBody", replacement)
        assert not replacement.GetVisibility()
        controller.set_visible("GranularBody", True)
        assert replacement.GetVisibility()
        controller.set_visible("GranularBody", False)
        controller.set_scene("second-run", config)
        controller.add_actor("GranularBody", replacement)
        assert replacement.GetVisibility()
    finally:
        if event_filter.menu is not None:
            event_filter.menu.close()
        plotter.close()
        app.processEvents()
