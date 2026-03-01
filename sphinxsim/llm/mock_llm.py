"""Mock LLM that converts natural language descriptions into SimulationConfig objects.

In production, this module would call a real LLM API (e.g. OpenAI, Anthropic).
For local testing the ``MockLLM`` class uses keyword matching to produce
deterministic, schema-validated configs without any network access.
"""

from __future__ import annotations

import re
from typing import Any, Dict

from sphinxsim.config.schemas import (
    BoundaryCondition,
    BoundaryType,
    DomainConfig,
    MaterialConfig,
    OutputConfig,
    PhysicsType,
    SimulationConfig,
    TimeSteppingConfig,
)


# ---------------------------------------------------------------------------
# Template configs keyed by physics type
# ---------------------------------------------------------------------------

_FLUID_TEMPLATE: Dict[str, Any] = {
    "physics": PhysicsType.FLUID,
    "domain": {"bounds_min": [0.0, 0.0], "bounds_max": [1.0, 1.0], "resolution": 0.02},
    "materials": [
        {"name": "water", "density": 1000.0, "dynamic_viscosity": 1e-3}
    ],
    "boundary_conditions": [
        {"name": "inlet", "type": BoundaryType.INLET, "velocity": [1.0, 0.0]},
        {"name": "outlet", "type": BoundaryType.OUTLET, "pressure": 0.0},
        {"name": "walls", "type": BoundaryType.WALL},
    ],
    "time_stepping": {"end_time": 1.0, "output_interval": 0.1},
    "output": {"directory": "./output", "format": "vtp"},
}

_SOLID_TEMPLATE: Dict[str, Any] = {
    "physics": PhysicsType.SOLID,
    "domain": {"bounds_min": [0.0, 0.0], "bounds_max": [1.0, 0.2], "resolution": 0.01},
    "materials": [
        {"name": "steel", "density": 7850.0, "youngs_modulus": 2e11, "poisson_ratio": 0.3}
    ],
    "boundary_conditions": [
        {"name": "fixed_end", "type": BoundaryType.WALL},
    ],
    "time_stepping": {"end_time": 0.5, "output_interval": 0.05},
    "output": {"directory": "./output", "format": "vtu"},
}

_FSI_TEMPLATE: Dict[str, Any] = {
    "physics": PhysicsType.FSI,
    "domain": {"bounds_min": [0.0, 0.0], "bounds_max": [2.0, 1.0], "resolution": 0.02},
    "materials": [
        {"name": "water", "density": 1000.0, "dynamic_viscosity": 1e-3},
        {"name": "elastic_plate", "density": 1100.0, "youngs_modulus": 1e6, "poisson_ratio": 0.4},
    ],
    "boundary_conditions": [
        {"name": "inlet", "type": BoundaryType.INLET, "velocity": [0.5, 0.0]},
        {"name": "outlet", "type": BoundaryType.OUTLET, "pressure": 0.0},
        {"name": "walls", "type": BoundaryType.WALL},
    ],
    "time_stepping": {"end_time": 2.0, "output_interval": 0.1},
    "output": {"directory": "./output", "format": "vtp"},
}

_TEMPLATES = {
    PhysicsType.FLUID: _FLUID_TEMPLATE,
    PhysicsType.SOLID: _SOLID_TEMPLATE,
    PhysicsType.FSI: _FSI_TEMPLATE,
}

# ---------------------------------------------------------------------------
# Keyword rules for physics-type detection
# ---------------------------------------------------------------------------

_FLUID_KEYWORDS = re.compile(
    r"\b(fluid|flow|water|liquid|viscous|navier[- ]stokes|incompressible|pipe|channel|dam)\b",
    re.IGNORECASE,
)
_SOLID_KEYWORDS = re.compile(
    r"\b(solid|elastic|deform|beam|plate|shell|impact|fracture|structure(?!s?\s+interact))\b",
    re.IGNORECASE,
)
_FSI_KEYWORDS = re.compile(
    r"\b(fsi|fluid[- ]structure|coupled|interaction|flexible|hydroelastic)\b",
    re.IGNORECASE,
)


def _detect_physics(description: str) -> PhysicsType:
    """Infer physics type from *description* using keyword matching."""
    has_fsi = bool(_FSI_KEYWORDS.search(description))
    has_fluid = bool(_FLUID_KEYWORDS.search(description))
    has_solid = bool(_SOLID_KEYWORDS.search(description))

    if has_fsi or (has_fluid and has_solid):
        return PhysicsType.FSI
    if has_fluid:
        return PhysicsType.FLUID
    if has_solid:
        return PhysicsType.SOLID
    # Default to fluid simulation when no keywords match
    return PhysicsType.FLUID


def _extract_name(description: str) -> str:
    """Derive a short simulation name from *description*."""
    # Take the first sentence or up to 60 chars
    sentence = description.split(".")[0].strip()
    if len(sentence) > 60:
        sentence = sentence[:57] + "..."
    return sentence or "unnamed simulation"


def _apply_overrides(template: Dict[str, Any], description: str) -> Dict[str, Any]:
    """Apply simple numeric overrides extracted from *description* to *template*.

    Recognised patterns:
    - ``<N> m/s``  → sets inlet velocity magnitude
    - ``<N> s``    → sets end_time
    - ``<N> m``    → sets domain size (square/cubic domain)
    - ``<N> mm``   → sets resolution
    """
    import copy

    cfg = copy.deepcopy(template)

    # Velocity override
    vel_match = re.search(r"(\d+(?:\.\d+)?)\s*m/s", description, re.IGNORECASE)
    if vel_match:
        speed = float(vel_match.group(1))
        for bc in cfg.get("boundary_conditions", []):
            if bc.get("type") == BoundaryType.INLET and bc.get("velocity") is not None:
                dim = len(bc["velocity"])
                bc["velocity"] = [speed] + [0.0] * (dim - 1)
                break

    # End-time override
    time_match = re.search(r"(\d+(?:\.\d+)?)\s*s\b", description, re.IGNORECASE)
    if time_match:
        cfg["time_stepping"]["end_time"] = float(time_match.group(1))
        # Ensure output_interval stays ≤ end_time
        et = cfg["time_stepping"]["end_time"]
        if cfg["time_stepping"]["output_interval"] > et:
            cfg["time_stepping"]["output_interval"] = et / 10.0

    # Domain size override (e.g. "2 m domain")
    domain_match = re.search(r"(\d+(?:\.\d+)?)\s*m\s+domain", description, re.IGNORECASE)
    if domain_match:
        size = float(domain_match.group(1))
        dim = len(cfg["domain"]["bounds_min"])
        cfg["domain"]["bounds_max"] = [size] * dim

    # Resolution override (e.g. "5 mm resolution")
    res_match = re.search(r"(\d+(?:\.\d+)?)\s*mm\s+resolution", description, re.IGNORECASE)
    if res_match:
        cfg["domain"]["resolution"] = float(res_match.group(1)) / 1000.0

    return cfg


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


class MockLLM:
    """Mocked LLM for local testing.

    ``MockLLM.generate(description)`` converts a free-text simulation
    description into a validated :class:`~sphinxsim.config.schemas.SimulationConfig`.

    No network access is required – physics type and numeric parameters
    are inferred from the description via keyword/regex matching.
    """

    def generate(self, description: str) -> SimulationConfig:
        """Convert *description* (natural language) into a ``SimulationConfig``.

        Parameters
        ----------
        description:
            Free-text description of the desired simulation, e.g.
            ``"water flowing through a pipe at 2 m/s"``.

        Returns
        -------
        SimulationConfig
            A fully validated configuration object.

        Raises
        ------
        pydantic.ValidationError
            If the generated configuration fails schema validation
            (should not happen with well-formed templates, but protects
            against bad numeric overrides).
        """
        if not description or not description.strip():
            raise ValueError("description must not be empty")

        physics = _detect_physics(description)
        template = _apply_overrides(_TEMPLATES[physics], description)
        template["name"] = _extract_name(description)

        return SimulationConfig(**template)
