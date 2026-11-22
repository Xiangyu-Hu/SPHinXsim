"""Particle legend controls and visibility across actor updates."""

import json
import os
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from sphinxsim.config.schemas import SimulationConfig
from sphinxsim.visualization.particle_visibility import ParticleVisibility, install_particle_menu


CASE = Path(__file__).parent / "test_simulation/test_2d_simulation/data/column_collapse.json"

pytestmark = pytest.mark.skipif(
    os.environ.get("SPHINXSIM_DESKTOP_TESTS") != "1",
    reason="Real Qt/VTK rendering requires a display; Linux CI runs this under Xvfb",
)


def test_hidden_particle_does_not_change_other_legend_text_pixels():
    np = pytest.importorskip("numpy")
    pv = pytest.importorskip("pyvista")
    config = SimulationConfig.model_validate_json(CASE.read_text(encoding="utf-8"))
    plotter = pv.Plotter(off_screen=True, window_size=(800, 600))
    controller = ParticleVisibility(plotter)
    controller.set_scene("run", config)
    try:
        controller.add_actor("GranularBody", MagicMock())
        controller.add_actor("WallBoundary", MagicMock())
        plotter.show(auto_close=False, interactive_update=True)
        before = plotter.screenshot(return_img=True)
        controller.set_visible("GranularBody", False)
        after = plotter.screenshot(return_img=True)
        renderer = plotter.renderers[0]
        left, bottom = controller.legend.GetPositionCoordinate().GetComputedDisplayValue(renderer)
        right, top = controller.legend.GetPosition2Coordinate().GetComputedDisplayValue(renderer)
        height = before.shape[0]
        second_row = np.s_[height - (top + bottom) // 2:height - bottom, left:right]
        assert np.array_equal(before[second_row], after[second_row])
    finally:
        plotter.close()


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
        legend = controller.legend
        initial_colour = legend.GetEntryColor(0)
        text = [legend.GetEntryString(i) for i in range(2)]
        action.trigger()
        assert not first.GetVisibility()
        assert second.GetVisibility()
        assert controller.legend is legend
        assert [legend.GetEntryString(i) for i in range(2)] == text
        assert legend.GetEntryColor(0) == pytest.approx(tuple(c * 0.75 for c in initial_colour))
        menu.close()
        QtTest.QTest.mouseClick(plotter, QtCore.Qt.MouseButton.RightButton, pos=pos)
        QtTest.QTest.qWait(50)
        action = event_filter.menu.actions()[0]
        assert not action.isChecked()
        action.trigger()
        assert first.GetVisibility()
        assert legend.GetEntryColor(0) == pytest.approx(initial_colour)
        event_filter.menu.close()
        mesh = pv.Sphere()
        mesh.point_data["Pressure"] = [0.2] * mesh.n_points
        mesh.field_data["ScalingReference_Pressure"] = [3.0]
        replacement = plotter.add_mesh(mesh, name="particle-preview-GranularBody", color="red")
        controller.add_actor("GranularBody", replacement, mesh, initialized=True)
        QtTest.QTest.mouseClick(plotter, QtCore.Qt.MouseButton.RightButton, pos=pos)
        QtTest.QTest.qWait(50)
        submenu = event_filter.menu.findChild(QtWidgets.QMenu, "sphinxsim-particle-colouring")
        assert submenu is not None
        actions = {action.text(): action for action in submenu.actions()}
        assert actions["Body color"].isChecked()
        actions["Pressure"].trigger()
        assert controller.colouring["GranularBody"] == "Pressure"
        assert replacement.GetMapper().GetScalarRange() == pytest.approx((-3, 3))
        assert controller.scalar_bars["GranularBody"].GetTitle() == "GranularBody: Pressure"
        event_filter.menu.close()
        QtTest.QTest.mouseClick(plotter, QtCore.Qt.MouseButton.RightButton, pos=pos)
        QtTest.QTest.qWait(50)
        representation = event_filter.menu.findChild(QtWidgets.QMenu, "sphinxsim-particle-representation")
        choices = {action.text(): action for action in representation.actions()}
        assert choices["Points"].isChecked()
        choices["Sphere splats"].trigger()
        assert controller.sphere_splats == {"GranularBody"}
        assert replacement.GetMapper().GetClassName() == "vtkOpenGLPointGaussianMapper"
        assert replacement.GetMapper().GetScalarRange() == pytest.approx((-3, 3))
        event_filter.menu.close()
        QtTest.QTest.mouseClick(plotter, QtCore.Qt.MouseButton.RightButton, pos=pos)
        QtTest.QTest.qWait(50)
        representation = event_filter.menu.findChild(QtWidgets.QMenu, "sphinxsim-particle-representation")
        choices = {action.text(): action for action in representation.actions()}
        assert choices["Sphere splats"].isChecked()
        choices["Points"].trigger()
        assert not controller.sphere_splats
        assert replacement.GetMapper().GetClassName() != "vtkOpenGLPointGaussianMapper"
        assert replacement.GetMapper().GetScalarRange() == pytest.approx((-3, 3))
        event_filter.menu.close()
        QtTest.QTest.mouseClick(plotter, QtCore.Qt.MouseButton.RightButton, pos=pos)
        QtTest.QTest.qWait(50)
        representation = event_filter.menu.findChild(QtWidgets.QMenu, "sphinxsim-particle-representation")
        next(action for action in representation.actions() if action.text() == "Sphere splats").trigger()
        event_filter.menu.close()
        controller.set_visible("GranularBody", False)

        replacement = plotter.add_mesh(mesh, name="particle-preview-GranularBody", color="red")
        controller.add_actor("GranularBody", replacement, mesh, initialized=True)
        assert not replacement.GetVisibility()
        assert replacement.GetMapper().GetClassName() == "vtkOpenGLPointGaussianMapper"
        assert replacement.GetMapper().GetScalarVisibility()
        assert not controller.scalar_bars["GranularBody"].GetVisibility()
        controller.set_scene("first-run", config)
        controller.add_actor("GranularBody", replacement, mesh, initialized=True)
        assert not replacement.GetVisibility()
        assert replacement.GetMapper().GetScalarVisibility()
        controller.set_visible("GranularBody", True)
        assert replacement.GetVisibility()
        controller.set_visible("GranularBody", False)
        controller.set_scene("second-run", config)
        controller.add_actor("GranularBody", replacement)
        assert replacement.GetVisibility()
        assert replacement.GetMapper().GetClassName() != "vtkOpenGLPointGaussianMapper"
        assert not controller.colouring
        assert not controller.sphere_splats
    finally:
        if event_filter.menu is not None:
            event_filter.menu.close()
        plotter.close()
        app.processEvents()


@pytest.mark.parametrize("window_size", [(800, 600), (1400, 800)])
def test_time_and_first_colour_legend_are_horizontally_aligned(window_size):
    np = pytest.importorskip("numpy")
    pv = pytest.importorskip("pyvista")
    from sphinxsim.visualization.particle_time import ParticleTimeDisplay

    config = SimulationConfig.model_validate_json(CASE.read_text(encoding="utf-8"))
    plotter = pv.Plotter(off_screen=True, window_size=window_size)
    controller = ParticleVisibility(plotter)
    controller.set_scene("run", config)
    time_display = ParticleTimeDisplay(plotter)
    mesh = pv.PolyData(np.array([[0, 0, 0], [1, 0, 0]], dtype=float))
    mesh.point_data["Pressure"] = [0.0, 1.0]
    mesh.field_data["TimeValue"] = [0.125]
    try:
        for body in ("GranularBody", "WallBoundary"):
            actor = plotter.add_mesh(mesh.copy(), name=f"particle-preview-{body}", color="red")
            controller.add_actor(body, actor, actor.mapper.dataset, initialized=True)
            controller.set_colour(body, "Pressure")
            time_display.update(body, mesh)
        plotter.show(auto_close=False, interactive_update=True)
        renderer = plotter.renderers[0]
        text = renderer.actors["particle-frame-time"]
        x, y = text.GetActualPositionCoordinate().GetComputedDoubleDisplayValue(renderer)
        bar = controller.scalar_bars["GranularBody"]
        left, bottom = bar.GetPositionCoordinate().GetComputedDoubleDisplayValue(renderer)
        _, top = bar.GetPosition2Coordinate().GetComputedDoubleDisplayValue(renderer)
        bounds = [0.0] * 4
        text.GetBoundingBox(renderer, bounds)
        assert y == pytest.approx((bottom + top) / 2, abs=1)
        assert x + bounds[1] < left
        assert controller.scalar_bars["WallBoundary"].GetPosition()[1] > bar.GetPosition()[1]
        controller.set_visible("GranularBody", False)
        bar = controller.scalar_bars["WallBoundary"]
        bottom = bar.GetPositionCoordinate().GetComputedDoubleDisplayValue(renderer)[1]
        top = bar.GetPosition2Coordinate().GetComputedDoubleDisplayValue(renderer)[1]
        assert y == pytest.approx((bottom + top) / 2, abs=1)
    finally:
        plotter.close()


def test_particle_colour_ranges_and_missing_fields(caplog):
    np = pytest.importorskip("numpy")
    pv = pytest.importorskip("pyvista")
    config = SimulationConfig.model_validate_json(CASE.read_text(encoding="utf-8"))
    plotter = pv.Plotter(off_screen=True)
    controller = ParticleVisibility(plotter)
    controller.set_scene("run", config)
    mesh = pv.PolyData(np.array([[0, 0, 0], [1, 0, 0], [2, 0, 0]], dtype=float))
    mesh.point_data["Pressure"] = [-4.0, 0.0, 9.0]
    mesh.point_data["Velocity"] = [[3.0, 4.0, 0.0], [0, 0, 0], [6, 8, 0]]
    mesh.point_data["OriginalID"] = [0, 1, 2]
    mesh.point_data["Matrix"] = np.zeros((3, 9))
    mesh.field_data["ScalingReference_Pressure"] = [2.0]
    mesh.field_data["ScalingReference_Velocity"] = [8.0]
    try:
        actor = plotter.add_mesh(mesh, color="red", name="particle-preview-GranularBody")
        controller.add_actor("GranularBody", actor, mesh, initialized=False)
        assert not controller.colourable_fields("GranularBody")
        controller.add_actor("GranularBody", actor, mesh, initialized=True)
        assert controller.colourable_fields("GranularBody") == {
            "OriginalID": False, "Pressure": False, "Velocity": True,
        }
        controller.set_colour("GranularBody", "Pressure")
        assert actor.GetMapper().GetScalarRange() == pytest.approx((-2, 2))
        assert plotter.renderer.GetViewProps().IsItemPresent(controller.scalar_bars["GranularBody"])
        lut = actor.GetMapper().GetLookupTable()
        assert lut.GetColor(-4) == pytest.approx(lut.GetColor(-2))
        assert lut.GetColor(9) == pytest.approx(lut.GetColor(2))
        controller.set_colour("GranularBody", "Velocity")
        assert actor.GetMapper().GetScalarRange() == pytest.approx((0, 8))
        assert mesh.point_data["_sphinxsim_colour_Velocity"] == pytest.approx([5, 0, 10])
        assert "Velocity magnitude" in controller.scalar_bars["GranularBody"].GetTitle()
        controller.set_colour("GranularBody", "OriginalID")
        assert actor.GetMapper().GetScalarRange() == pytest.approx((0, 2))

        later = pv.PolyData(np.array([[0, 0, 0], [1, 0, 0]], dtype=float))
        later.point_data["Pressure"] = [0.0, 1.0]
        replacement = plotter.add_mesh(later, color="red", name="particle-preview-GranularBody")
        controller.add_actor("GranularBody", replacement, later, initialized=True)
        assert not replacement.GetMapper().GetScalarVisibility()
        assert "unavailable" in caplog.text
        assert controller.colouring["GranularBody"] == "OriginalID"
        assert "GranularBody" not in controller.scalar_bars
        later.point_data["OriginalID"] = [5, 6]
        replacement = plotter.add_mesh(later, color="red", name="particle-preview-GranularBody")
        controller.add_actor("GranularBody", replacement, later, initialized=True)
        assert replacement.GetMapper().GetScalarRange() == pytest.approx((5, 6))
        controller.set_colour("GranularBody", "Pressure")
        assert replacement.GetMapper().GetScalarRange() == pytest.approx((0, 1))
        later.field_data["ScalingReference_Pressure"] = [0.0]
        controller.set_colour("GranularBody", "Pressure")
        assert replacement.GetMapper().GetScalarRange() == pytest.approx((0, 1))
        assert "Invalid scaling reference" in caplog.text
        later.point_data["Pressure"] = [4.0, 4.0]
        controller.set_colour("GranularBody", "Pressure")
        low, high = replacement.GetMapper().GetScalarRange()
        assert low < 4 < high
        later.point_data["Pressure"] = [np.nan, np.nan]
        controller.set_colour("GranularBody", "Pressure")
        assert not replacement.GetMapper().GetScalarVisibility()
        assert "no finite values" in caplog.text
        controller.set_colour("GranularBody", None)
        assert not replacement.GetMapper().GetScalarVisibility()
        assert not controller.scalar_bars
        controller.set_scene("another-run", config)
        assert not controller.colouring
    finally:
        plotter.close()


def test_particle_colouring_survives_live_and_replay_frame_updates(tmp_path):
    np = pytest.importorskip("numpy")
    pv = pytest.importorskip("pyvista")
    from sphinxsim.results import ResultCatalog
    from sphinxsim.visualization.live_particles import refresh_particle_actors
    from sphinxsim.visualization.preview_runtime import _ShellPreviewRuntime

    config = SimulationConfig.model_validate_json(CASE.read_text(encoding="utf-8"))
    plotter = pv.Plotter(off_screen=True)
    controller = ParticleVisibility(plotter)
    controller.set_scene("run", config)
    mesh = pv.PolyData(np.array([[0, 0, 0], [1, 0, 0]], dtype=float))
    mesh.point_data["Pressure"] = [0.1, 0.2]
    mesh.field_data["ScalingReference_Pressure"] = [2.0]
    mesh.field_data["TimeValue"] = [0.0]
    generation = tmp_path / "GranularBody_ite_0000.vtp"
    initial = tmp_path / "GranularBody_0000.vtp"
    later = tmp_path / "GranularBody_0001.vtp"
    mesh.save(generation, binary=False)
    colours = {"GranularBody": "red"}
    versions = {}
    catalog = ResultCatalog()
    try:
        refresh_particle_actors(plotter, tmp_path, colours, versions, catalog, controller)
        assert not controller.colourable_fields("GranularBody")
        mesh.save(initial, binary=False)
        refresh_particle_actors(plotter, tmp_path, colours, versions, catalog, controller)
        assert "Pressure" in controller.colourable_fields("GranularBody")
        controller.set_colour("GranularBody", "Pressure")
        mesh.point_data["Pressure"] = [5.0, 6.0]
        mesh.field_data["TimeValue"] = [0.1]
        mesh.save(later, binary=False)
        refresh_particle_actors(plotter, tmp_path, colours, versions, catalog, controller)
        actor = controller.actors["GranularBody"]
        assert actor.GetMapper().GetScalarRange() == pytest.approx((-2, 2))
        assert controller.meshes["GranularBody"].point_data["Pressure"] == pytest.approx([5, 6])

        runtime = _ShellPreviewRuntime()
        runtime.plotter = plotter
        runtime._particle_visibility = controller
        runtime._live_output_dir = tmp_path
        runtime._live_particle_colours = colours
        runtime._live_replay_mode = True
        runtime._live_playback_queue = [(0, {"GranularBody": initial}), (1, {"GranularBody": later})]
        runtime._refresh_live_particles()
        assert controller.actors["GranularBody"].GetMapper().GetScalarRange() == pytest.approx((-2, 2))
        assert controller.meshes["GranularBody"].point_data["Pressure"] == pytest.approx([0.1, 0.2])
        assert plotter.renderer.actors["particle-frame-time"].GetInput() == "Time: 0 s"
        runtime._refresh_live_particles()
        assert controller.actors["GranularBody"].GetMapper().GetScalarRange() == pytest.approx((-2, 2))
        assert controller.meshes["GranularBody"].point_data["Pressure"] == pytest.approx([5, 6])
        assert plotter.renderer.actors["particle-frame-time"].GetInput() == "Time: 0.1 s"
    finally:
        plotter.close()


def test_native_initialized_particle_colouring_persists_into_replay(tmp_path, monkeypatch):
    pv = pytest.importorskip("pyvista")
    from sphinxsim.bindings.loader import load_sphinxsys_core_nd
    from sphinxsim.results import ResultCatalog
    from sphinxsim.visualization.preview_runtime import _ShellPreviewRuntime

    try:
        core = load_sphinxsys_core_nd(2)
    except ImportError as exc:
        pytest.skip(f"Native 2D binding unavailable: {exc}")
    source = CASE.parent / "dambreak.json"
    data = json.loads(source.read_text())
    data["geometries"]["global_resolution"]["particle_spacing"] = 0.1
    data["particle_generation"]["relaxation_parameters"]["total_iterations"] = 1
    data["solver_parameters"].update(end_time=0.05, output_interval=0.01)
    path = tmp_path / "config.json"
    path.write_text(json.dumps(data))
    monkeypatch.chdir(tmp_path)
    output = tmp_path / "run"
    output.mkdir()
    simulation = core.SPHSimulation(str(path))
    simulation.resetOutputRoot(str(output), True)
    simulation.buildGeometries()
    simulation.generateParticles()
    config = SimulationConfig.model_validate(data)
    plotter = pv.Plotter(off_screen=True)
    controller = ParticleVisibility(plotter)
    controller.set_scene("run", config)
    runtime = _ShellPreviewRuntime()
    runtime.plotter = plotter
    runtime._particle_visibility = controller
    runtime._pending_simulation = simulation
    runtime._live_output_dir = output / "output"
    runtime._live_particle_body_names = {"WaterBody", "WallBoundary"}
    runtime._live_particle_colours = {"WaterBody": "blue", "WallBoundary": "grey"}
    try:
        assert runtime.initialize_simulation() == 0
        assert plotter.renderer.actors["particle-frame-time"].GetInput() == "Time: 0 s"
        assert "Pressure" in controller.colourable_fields("WaterBody")
        reference = controller.meshes["WaterBody"].field_data["ScalingReference_Pressure"][0]
        controller.set_colour("WaterBody", "Pressure")
        assert controller.actors["WaterBody"].GetMapper().GetScalarRange() == pytest.approx(
            (-reference, reference)
        )
        assert runtime.continue_to_run() == 0
        runtime._live_run_thread.join(timeout=60)
        assert runtime._animation_run_completed
        final = ResultCatalog().latest(runtime._live_output_dir, ["WaterBody"])["WaterBody"]
        assert final.time > 0
        runtime._live_replay_mode = True
        runtime._live_playback_queue = [(final.time, {"WaterBody": final.path})]
        runtime._refresh_live_particles()
        assert plotter.renderer.actors["particle-frame-time"].GetInput() == (
            f"Time: WallBoundary: 0 s, WaterBody: {final.time:.6g} s"
        )
        assert controller.colouring["WaterBody"] == "Pressure"
        assert controller.actors["WaterBody"].GetMapper().GetScalarRange() == pytest.approx(
            (-reference, reference)
        )
    finally:
        plotter.close()
