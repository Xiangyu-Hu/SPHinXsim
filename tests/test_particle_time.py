"""Time labels follow displayed particle snapshots, not filenames."""

from types import SimpleNamespace
from unittest.mock import MagicMock

import numpy as np

from sphinxsim.visualization.particle_time import ParticleTimeDisplay


def mesh(time=None):
    return SimpleNamespace(field_data={} if time is None else {"TimeValue": np.array([time])})


def text(plotter):
    actor = plotter.add_text.return_value
    if actor.SetInput.called:
        return actor.SetInput.call_args.args[0]
    return plotter.add_text.call_args.args[0]


def test_time_tracks_per_body_generation_missing_and_scene_reset(caplog):
    plotter = MagicMock()
    display = ParticleTimeDisplay(plotter)
    display.update("Water", mesh(0))
    assert text(plotter) == "Time: 0 s"
    display.update("Wall", mesh(0))
    assert text(plotter) == "Time: 0 s"
    display.update("Water", mesh(0.125))
    assert text(plotter) == "Time: 0.125 s"
    display.update("Wall", mesh(0.125))
    assert text(plotter) == "Time: 0.125 s"
    display.update("Water", mesh(200), generation=True)
    assert text(plotter) == "Time: 0.125 s"
    assert "Water" not in display.times
    display.update("Wall", mesh(float("nan")))
    assert "no valid TimeValue" in caplog.text
    assert not display.times
    plotter.remove_actor.assert_called_once_with("particle-frame-time", reset_camera=False, render=False)
    display.update("Water", mesh(1.5))
    display.reset()
    assert not display.times
    assert plotter.remove_actor.call_count == 2


def test_invalid_or_missing_time_does_not_invent_a_time(caplog):
    plotter = MagicMock()
    display = ParticleTimeDisplay(plotter)
    display.update("Water", mesh())
    display.update("Water", SimpleNamespace(field_data={"TimeValue": [1, 2]}))
    display.update("Water", SimpleNamespace(field_data={"TimeValue": ["invalid"]}))
    plotter.add_text.assert_not_called()
    assert "no valid TimeValue" in caplog.text


def test_time_overlay_actor_is_reused_and_updated_in_place():
    plotter = MagicMock()
    display = ParticleTimeDisplay(plotter)

    display.update("Water", mesh(0))
    display.update("Water", mesh(0.125))

    plotter.add_text.assert_called_once()
    plotter.add_text.return_value.SetInput.assert_called_once_with("Time: 0.125 s")
