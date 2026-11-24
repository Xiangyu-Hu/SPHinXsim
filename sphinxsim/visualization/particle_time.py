"""Display the physical times of the particle frames currently on screen."""

from __future__ import annotations

import logging
import math
from typing import Any

import numpy as np

logger = logging.getLogger(__name__)
_TIME_ACTOR = "particle-frame-time"
PARTICLE_OVERLAY_CENTER_Y = 0.10


class ParticleTimeDisplay:
    def __init__(self, plotter: Any) -> None:
        self.plotter = plotter
        self.times: dict[str, float] = {}
        self.shown = False

    def reset(self) -> None:
        self.times.clear()
        if self.shown:
            self.plotter.remove_actor(_TIME_ACTOR, reset_camera=False, render=False)
            self.shown = False

    def update(self, body: str, mesh: Any, *, generation: bool = False) -> None:
        time = None
        if not generation:
            field_data = getattr(mesh, "field_data", {})
            value = field_data.get("TimeValue")
            if value is not None:
                values = np.asarray(value).ravel()
                if values.size == 1 and np.issubdtype(values.dtype, np.number):
                    candidate = float(values[0])
                    if math.isfinite(candidate):
                        time = candidate
            if time is None:
                logger.warning("Particle snapshot for %s has no valid TimeValue", body)
        if time is None:
            self.times.pop(body, None)
        else:
            self.times[body] = time
        if not self.times:
            if self.shown:
                self.plotter.remove_actor(_TIME_ACTOR, reset_camera=False, render=False)
                self.shown = False
            return

        current_time = max(self.times.values())
        text = f"Time: {current_time:.6g} s"
        self.plotter.subplot(0, 0)
        actor = self.plotter.add_text(
            text, name=_TIME_ACTOR, position=(0.50, PARTICLE_OVERLAY_CENTER_Y),
            viewport=True, color="white", font_size=11,
        )
        actor.GetTextProperty().SetJustificationToCentered()
        actor.GetTextProperty().SetVerticalJustificationToCentered()
        self.shown = True
