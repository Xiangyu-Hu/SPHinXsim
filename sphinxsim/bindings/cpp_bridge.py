"""Thin Python bindings for the SPHinXsys C++ library.

In a full installation, this module wraps the compiled pybind11 extension
``_sphinxsys_core``.  When that extension is not available (e.g. during
pure-Python testing or CI without a C++ build), the bridge falls back to a
lightweight Python stub so that the rest of the package remains usable.

The public interface is intentionally kept minimal ("thin bindings"):
Python is responsible for configuration and pre/post-processing; C++ handles
the heavy numerical computation.
"""

from __future__ import annotations

import json
from typing import Any

from sphinxsim.config.schemas import SimulationConfig


# ---------------------------------------------------------------------------
# Try to import the compiled C++ extension
# ---------------------------------------------------------------------------

try:
    import _sphinxsys_core as _core  # type: ignore[import]

    _CPP_AVAILABLE = True
except ImportError:
    _core = None
    _CPP_AVAILABLE = False


# ---------------------------------------------------------------------------
# Python stub used when C++ extension is absent
# ---------------------------------------------------------------------------


class _StubSimulation:
    """Lightweight Python stub that mimics the C++ Simulation object."""

    def __init__(self, config_json: str) -> None:
        self._config = json.loads(config_json)
        self._step = 0
        self._time = 0.0

    def initialize(self) -> None:
        pass

    def run(self) -> None:
        end_time: float = self._config["time_stepping"]["end_time"]
        self._time = end_time
        self._step += 1

    @property
    def current_time(self) -> float:
        return self._time

    @property
    def step_count(self) -> int:
        return self._step

    def __repr__(self) -> str:
        return (
            f"<StubSimulation name={self._config.get('name', '?')!r} "
            f"time={self._time:.4g}>"
        )


# ---------------------------------------------------------------------------
# Public bridge class
# ---------------------------------------------------------------------------


class SPHinXsysBridge:
    """Bridge between Python configuration and the SPHinXsys C++ backend.

    Usage::

        bridge = SPHinXsysBridge(config)
        bridge.initialize()
        bridge.run()
        print(bridge.current_time)

    When the C++ extension ``_sphinxsys_core`` is installed the calls are
    forwarded directly to the compiled simulation object.  Otherwise the
    Python stub is used transparently, which is sufficient for unit tests
    and configuration validation without a C++ build.
    """

    def __init__(self, config: SimulationConfig) -> None:
        """Create a bridge for the given *config*.

        Parameters
        ----------
        config:
            A validated :class:`~sphinxsim.config.schemas.SimulationConfig`.
        """
        config_json = config.model_dump_json()
        if _CPP_AVAILABLE:
            self._sim: Any = _core.Simulation(config_json)
        else:
            self._sim = _StubSimulation(config_json)

    @property
    def cpp_available(self) -> bool:
        """``True`` if the compiled C++ extension is loaded."""
        return _CPP_AVAILABLE

    def initialize(self) -> None:
        """Initialise the simulation (allocate particles, build neighbour lists)."""
        self._sim.initialize()

    def run(self) -> None:
        """Advance the simulation to ``config.time_stepping.end_time``."""
        self._sim.run()

    @property
    def current_time(self) -> float:
        """Current physical time of the simulation (seconds)."""
        return self._sim.current_time

    @property
    def step_count(self) -> int:
        """Number of time steps completed so far."""
        return self._sim.step_count

    def __repr__(self) -> str:
        backend = "C++" if _CPP_AVAILABLE else "Python stub"
        return f"<SPHinXsysBridge backend={backend!r} time={self.current_time:.4g}>"
