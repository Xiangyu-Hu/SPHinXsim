"""Display the physical times of the particle frames currently on screen."""

from __future__ import annotations

import logging
import math
from typing import Any

import numpy as np

logger = logging.getLogger(__name__)
_TIME_ACTOR = "particle-frame-time"


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

        values = set(self.times.values())
        if len(values) == 1:
            text = f"Time: {next(iter(values)):.6g} s"
        else:
            text = "Time: " + ", ".join(
                f"{name}: {value:.6g} s" for name, value in sorted(self.times.items())
            )
        self.plotter.subplot(0, 0)
        self.plotter.add_text(
            text, name=_TIME_ACTOR, position="lower_edge", color="white", font_size=11,
        )
        self.shown = True
