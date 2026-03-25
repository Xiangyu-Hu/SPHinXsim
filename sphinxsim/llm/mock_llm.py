"""Mock LLM that converts natural language descriptions into SimulationConfig objects.

In production, this module would call a real LLM API (e.g. OpenAI, Anthropic).
For local testing the ``MockLLM`` class uses keyword matching to produce
deterministic, schema-validated configs without any network access.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List

from sphinxsim.config.schemas import (
    PhysicsType,
    SimulationConfig,
)


# ---------------------------------------------------------------------------
# Template configs keyed by physics type
# ---------------------------------------------------------------------------

_FLUID_TEMPLATE: Dict[str, Any] = {
    "domain": {"lower_bound": [0.0, 0.0], "upper_bound": [5.366, 5.366]},
    "particle_spacing": 0.025,
    "particle_boundary_buffer": 4,
    "fluid_bodies": [
        {
            "name": "WaterBody",
            "geometry": {
                "type": "bounding_box",
                "lower_bound": [0.0, 0.0],
                "upper_bound": [2.0, 1.0],
            },
            "material": {
                "type": "weakly_compressible_fluid",
                "density": 1.0,
                "sound_speed": 20.0,
            },
        }
    ],
    "solid_bodies": [
        {
            "name": "WallBoundary",
            "geometry": {
                "type": "container_box",
                "inner_lower_bound": [0.0, 0.0],
                "inner_upper_bound": [5.366, 5.366],
                "thickness": 0.1,
            },
            "material": {"type": "rigid_body"},
        }
    ],
    "gravity": [0.0, -1.0],
    "observers": [
        {"name": "FluidObserver", "positions": [[5.366, 0.2]]}
    ],
    "solver": {"dual_time_stepping": True, "free_surface_correction": True},
    "end_time": 1.0,
}

_SOLID_TEMPLATE: Dict[str, Any] = {
    "domain": {"lower_bound": [0.0, 0.0], "upper_bound": [1.0, 0.2]},
    "particle_spacing": 0.01,
    "particle_boundary_buffer": 4,
    "fluid_bodies": [
        {
            "name": "ReferenceBody",
            "geometry": {
                "type": "bounding_box",
                "lower_bound": [0.0, 0.0],
                "upper_bound": [0.5, 0.1],
            },
            "material": {
                "type": "weakly_compressible_fluid",
                "density": 7850.0,
                "sound_speed": 50.0,
            },
        }
    ],
    "solid_bodies": [
        {
            "name": "WallBoundary",
            "geometry": {
                "type": "container_box",
                "inner_lower_bound": [0.0, 0.0],
                "inner_upper_bound": [5.366, 5.366],
                "thickness": 0.1,
            },
            "material": {"type": "rigid_body"},
        }
    ],
    "gravity": [0.0, -1.0],
    "observers": [],
    "solver": {"dual_time_stepping": True, "free_surface_correction": True},
    "end_time": 0.5,
}

_FSI_TEMPLATE: Dict[str, Any] = {
    "domain": {"lower_bound": [0.0, 0.0], "upper_bound": [2.0, 1.0]},
    "particle_spacing": 0.02,
    "particle_boundary_buffer": 4,
    "fluid_bodies": [
        {
            "name": "WaterBody",
            "geometry": {
                "type": "bounding_box",
                "lower_bound": [0.0, 0.0],
                "upper_bound": [0.8, 0.4],
            },
            "material": {
                "type": "weakly_compressible_fluid",
                "density": 1000.0,
                "sound_speed": 20.0,
            },
        }
    ],
    "solid_bodies": [
        {
            "name": "WallBoundary",
            "geometry": {
                "type": "container_box",
                "inner_lower_bound": [0.0, 0.0],
                "inner_upper_bound": [5.366, 5.366],
                "thickness": 0.1,
            },
            "material": {"type": "rigid_body"},
        }
    ],
    "gravity": [0.0, -1.0],
    "observers": [
        {"name": "FluidObserver", "positions": [[2.0, 0.2]]}
    ],
    "solver": {"dual_time_stepping": True, "free_surface_correction": True},
    "end_time": 2.0,
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


def _sync_geometry(cfg: Dict[str, Any]) -> None:
    dims = cfg["domain"]["upper_bound"]
    domain_x = dims[0]
    domain_y = dims[1] if len(dims) > 1 else dims[0]

    if cfg.get("fluid_bodies"):
        cfg["fluid_bodies"][0]["geometry"]["upper_bound"] = [0.4 * domain_x, 0.2 * domain_y]

    if cfg.get("solid_bodies"):
        cfg["solid_bodies"][0]["geometry"]["inner_upper_bound"] = [domain_x, domain_y]

    if cfg.get("observers") and cfg["observers"][0].get("positions"):
        cfg["observers"][0]["positions"] = [[domain_x, min(0.2, domain_y)]]


def _apply_overrides(template: Dict[str, Any], description: str) -> Dict[str, Any]:
    """Apply simple numeric overrides extracted from *description* to *template*.

    Recognised patterns:
    - ``<N> m/s``          → sets inlet velocity magnitude (e.g. ``"2 m/s"``)
    - ``<N> s``            → sets end_time (e.g. ``"5 s"``)
    - ``<N> m domain``     → sets domain size (e.g. ``"2 m domain"``)
    - ``<N> mm resolution``→ sets particle spacing (e.g. ``"5 mm resolution"``)
    """
    import copy

    cfg = copy.deepcopy(template)

    # Velocity override (mapped to sound speed as c ~= 10U)
    vel_match = re.search(r"(\d+(?:\.\d+)?)\s*m/s", description, re.IGNORECASE)
    if vel_match:
        speed = float(vel_match.group(1))
        if cfg.get("fluid_bodies"):
            cfg["fluid_bodies"][0]["material"]["sound_speed"] = max(20.0, 10.0 * speed)

    # End-time override (e.g. "5 s", "5 sec", "5 second", "5 seconds")
    time_match = re.search(
        r"(\d+(?:\.\d+)?)\s*(?:s|sec|secs|second|seconds)\b",
        description,
        re.IGNORECASE,
    )
    if time_match:
        cfg["end_time"] = float(time_match.group(1))

    # Domain size override (e.g. "2 m domain")
    domain_match = re.search(r"(\d+(?:\.\d+)?)\s*m\s+domain", description, re.IGNORECASE)
    if domain_match:
        size = float(domain_match.group(1))
        dim = len(cfg["domain"]["upper_bound"])
        cfg["domain"]["upper_bound"] = [size] * dim

    # Resolution override (e.g. "5 mm resolution")
    res_match = re.search(r"(\d+(?:\.\d+)?)\s*mm\s+resolution", description, re.IGNORECASE)
    if res_match:
        cfg["particle_spacing"] = float(res_match.group(1)) / 1000.0

    _sync_geometry(cfg)

    return cfg


def _extract_float_list(text: str) -> List[float]:
    return [float(x) for x in re.findall(r"[-+]?\d*\.?\d+", text)]


def _apply_additions(cfg: Dict[str, Any], description: str) -> None:
    lower = description.lower()

    if "add observer" in lower:
        obs_name_match = re.search(
            r"add\s+observer(?:\s+named\s+([\w\- ]+?)(?=\s+(?:at|position|positions)\b|$))?",
            description,
            re.IGNORECASE,
        )
        obs_name = (obs_name_match.group(1).strip() if obs_name_match and obs_name_match.group(1) else "Observer")
        at_match = re.search(r"(?:at|position(?:s)?)\s*[:=]?\s*\(?([^\)]*)\)?", description, re.IGNORECASE)
        if at_match:
            coords = _extract_float_list(at_match.group(1))
            dim = len(cfg.get("domain", {}).get("upper_bound", [0.0, 0.0]))
            if len(coords) == dim:
                cfg.setdefault("observers", []).append({"name": obs_name, "positions": [coords]})

    if "add fluid block" in lower:
        name_match = re.search(r"add\s+fluid\s+block(?:\s+named\s+([\w\- ]+))?", description, re.IGNORECASE)
        block_name = (name_match.group(1).strip() if name_match and name_match.group(1) else "FluidBlock")
        dims_match = re.search(r"dimensions?\s*[:=]?\s*([^,;]+)", description, re.IGNORECASE)
        dims = _extract_float_list(dims_match.group(1)) if dims_match else []
        dim = len(cfg.get("domain", {}).get("upper_bound", [0.0, 0.0]))
        if len(dims) == dim:
            density_match = re.search(r"density\s*[:=]?\s*(\d+(?:\.\d+)?)", description, re.IGNORECASE)
            sound_speed_match = re.search(r"sound\s*speed\s*[:=]?\s*(\d+(?:\.\d+)?)", description, re.IGNORECASE)
            density = float(density_match.group(1)) if density_match else 1.0
            sound_speed = float(sound_speed_match.group(1)) if sound_speed_match else 20.0
            cfg.setdefault("fluid_bodies", []).append(
                {
                    "name": block_name,
                    "geometry": {
                        "type": "bounding_box",
                        "lower_bound": [0.0] * dim,
                        "upper_bound": dims,
                    },
                    "material": {
                        "type": "weakly_compressible_fluid",
                        "density": density,
                        "sound_speed": sound_speed,
                    },
                }
            )


def _apply_updates(existing: Dict[str, Any], description: str) -> Dict[str, Any]:
    import copy

    cfg = copy.deepcopy(existing)
    cfg = _apply_overrides(cfg, description)
    _apply_additions(cfg, description)
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
        ValueError
            If *description* is empty or contains only whitespace.
        pydantic.ValidationError
            If the generated configuration fails schema validation
            (should not happen with well-formed templates, but protects
            against bad numeric overrides).
        """
        if not description or not description.strip():
            raise ValueError("description must not be empty")

        physics = _detect_physics(description)
        template = _apply_overrides(_TEMPLATES[physics], description)
        if template.get("fluid_bodies"):
            template["fluid_bodies"][0]["name"] = _extract_name(description)

        return SimulationConfig(**template)

    def update(self, existing: SimulationConfig, description: str) -> SimulationConfig:
        """Apply a natural-language update to an existing config."""
        if not description or not description.strip():
            raise ValueError("description must not be empty")

        updated = _apply_updates(existing.model_dump(), description)
        return SimulationConfig(**updated)
