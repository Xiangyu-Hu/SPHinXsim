"""Mock LLM that converts natural language descriptions into SimulationConfig objects.

In production, this module would call a real LLM API (e.g. OpenAI, Anthropic).
For local testing the ``MockLLM`` class uses keyword matching to produce
deterministic, schema-validated configs without any network access.
"""

from __future__ import annotations

import re
import json
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List

from sphinxsim.config.schemas import SimulationConfig
from sphinxsim.config.update_patch import UpdatePatch


class PhysicsType(str, Enum):
    """Heuristic physics category used by the mock NLP layer."""

    FLUID = "fluid"
    SOLID = "solid"
    PLASTIC_CONTINUUM = "plastic_continuum"
    FSI = "fsi"


# ---------------------------------------------------------------------------
# Template configs keyed by physics type
# ---------------------------------------------------------------------------

_FLUID_TEMPLATE: Dict[str, Any] = {
    "simulation_type": "fluid_dynamics",
    "geometries": {
        "system_domain": {"lower_bound": [0.0, 0.0], "upper_bound": [5.366, 5.366]},
        "global_resolution": {"particle_spacing": 0.025},
        "shapes": [
            {
                "name": "WaterBody",
                "type": "bounding_box",
                "lower_bound": [0.0, 0.0],
                "upper_bound": [2.0, 1.0],
            },
            {
                "name": "WallBoundary",
                "type": "bounding_box",
                "lower_bound": [0.0, 0.0],
                "upper_bound": [5.366, 5.366],
            },
        ],
    },
    "particle_generation": {
        "build_and_run": True,
        "settings": {
            "bodies": [
                {"name": "WaterBody"},
                {"name": "WallBoundary", "solid_body": {}},
            ],
            "relaxation_parameters": {"total_iterations": 1},
        },
    },
    "fluid_bodies": [
        {
            "name": "WaterBody",
            "material": {
                "type": "weakly_compressible_fluid",
                "density": 1.0
            },
        }
    ],
    "solid_bodies": [
        {
            "name": "WallBoundary",
            "material": {"type": "rigid_body"},
        }
    ],
    "gravity": [0.0, -1.0],
    "observers": [
        {
            "name": "FluidObserver",
            "observed_body": "WaterBody",
            "variable": {"real_type": "Pressure"},
            "positions": [[5.0, 0.2]],
        }
    ],
    "solver_parameters": {
        "end_time": 1.0,
        "output_interval": 0.01,
        "screen_interval": 100,
        "fluid_dynamics": {
            "acoustic_cfl": 0.6,
            "advection_cfl": 0.25,
            "surface_type": "free_surface",
            "particle_sort_frequency": 100,
        },
    },
}

_SOLID_TEMPLATE: Dict[str, Any] = {
    "simulation_type": "continuum_dynamics",
    "geometries": {
        "system_domain": {"lower_bound": [0.0, 0.0], "upper_bound": [1.0, 0.2]},
        "global_resolution": {"particle_spacing": 0.01},
        "shapes": [
            {
                "name": "ProcessingPiece",
                "type": "bounding_box",
                "lower_bound": [0.0, 0.0],
                "upper_bound": [0.8, 0.18],
            },
            {
                "name": "WallBoundary",
                "type": "bounding_box",
                "lower_bound": [0.0, 0.0],
                "upper_bound": [1.0, 0.2],
            },
        ],
    },
    "particle_generation": {
        "build_and_run": True,
        "settings": {
            "bodies": [
                {"name": "ProcessingPiece"},
                {"name": "WallBoundary", "solid_body": {}},
            ],
            "relaxation_parameters": {"total_iterations": 1},
        },
    },
    "continuum_bodies": [
        {
            "name": "ProcessingPiece",
            "material": {
                "type": "general_continuum",
                "density": 7850.0,
                "sound_speed": 50.0,
                "youngs_modulus": 2.0e5,
                "poisson_ratio": 0.3,
            },
        }
    ],
    "solid_bodies": [
        {
            "name": "WallBoundary",
            "material": {"type": "rigid_body"},
        }
    ],
    "gravity": [0.0, -1.0],
    "observers": [],
    "solver_parameters": {
        "end_time": 0.5,
        "output_interval": 0.01,
        "screen_interval": 100,
        "continuum_dynamics": {
            "acoustic_cfl": 0.4,
            "advection_cfl": 0.2,
            "linear_correction_matrix_coeff": 0.5,
            "contact_numerical_damping": 0.5,
            "shear_stress_damping": 0.0,
            "hourglass_factor": 2.0,
        },
    },
    "restart": {
    "restore_step": 0,
    "save_interval": 400,
    "summary_enabled": True
  }
}

_FSI_TEMPLATE: Dict[str, Any] = {
    "simulation_type": "fluid_dynamics",
    "geometries": {
        "system_domain": {"lower_bound": [0.0, 0.0], "upper_bound": [2.0, 1.0]},
        "global_resolution": {"particle_spacing": 0.02},
        "shapes": [
            {
                "name": "WaterBody",
                "type": "bounding_box",
                "lower_bound": [0.0, 0.0],
                "upper_bound": [0.8, 0.4],
            },
            {
                "name": "WallBoundary",
                "type": "bounding_box",
                "lower_bound": [0.0, 0.0],
                "upper_bound": [2.0, 1.0],
            },
        ],
    },
    "particle_generation": {
        "build_and_run": True,
        "settings": {
            "bodies": [
                {"name": "WaterBody"},
                {"name": "WallBoundary", "solid_body": {}},
            ],
            "relaxation_parameters": {"total_iterations": 1},
        },
    },
    "fluid_bodies": [
        {
            "name": "WaterBody",
            "material": {
                "type": "weakly_compressible_fluid",
                "density": 1000.0
            },
        }
    ],
    "solid_bodies": [
        {
            "name": "WallBoundary",
            "material": {"type": "rigid_body"},
        }
    ],
    "gravity": [0.0, -1.0],
    "observers": [
        {
            "name": "FluidObserver",
            "observed_body": "WaterBody",
            "variable": {"real_type": "Pressure"},
            "positions": [[2.0, 0.2]],
        }
    ],
    "solver_parameters": {
        "end_time": 2.0,
        "output_interval": 0.01,
        "screen_interval": 100,
        "fluid_dynamics": {
            "acoustic_cfl": 0.6,
            "advection_cfl": 0.25,
            "surface_type": "free_surface",
            "particle_sort_frequency": 100,
        },
    },
}

_TEMPLATES = {
    PhysicsType.FLUID: _FLUID_TEMPLATE,
    PhysicsType.SOLID: _SOLID_TEMPLATE,
    PhysicsType.PLASTIC_CONTINUUM: _SOLID_TEMPLATE,
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
_PLASTIC_CONTINUUM_KEYWORDS = re.compile(
    r"\b("
    r"plastic(?:\s+continuum)?|granular|soil|landslide|slope|column[- ]collapse|"
    r"drucker[- ]prager|cohesion|friction(?:\s+angle)?|dilatancy"
    r")\b",
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
    has_plastic_continuum = bool(_PLASTIC_CONTINUUM_KEYWORDS.search(description))

    if has_fsi or (has_fluid and has_solid):
        return PhysicsType.FSI
    if has_plastic_continuum:
        return PhysicsType.PLASTIC_CONTINUUM
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
    geometries = cfg.setdefault("geometries", {})
    system_domain = geometries.setdefault("system_domain", {})
    dims = system_domain.get("upper_bound")

    if not isinstance(dims, list) or not dims:
        inferred_dims = None
        for shape in geometries.get("shapes", []):
            upper = shape.get("upper_bound")
            if isinstance(upper, list) and upper:
                inferred_dims = upper
                break
        dims = inferred_dims or [1.0, 1.0]
        system_domain["upper_bound"] = dims

    if "lower_bound" not in system_domain or not isinstance(system_domain["lower_bound"], list):
        system_domain["lower_bound"] = [0.0] * len(dims)

    domain_x = dims[0]
    domain_y = dims[1] if len(dims) > 1 else dims[0]

    shape_by_name = {s["name"]: s for s in geometries.get("shapes", [])}
    if "WaterBody" in shape_by_name:
        shape_by_name["WaterBody"]["upper_bound"] = [0.4 * domain_x, 0.2 * domain_y]
    if "WallBoundary" in shape_by_name:
        shape_by_name["WallBoundary"]["upper_bound"] = [domain_x, domain_y]

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
    geometry_changed = False

    # Velocity override
    vel_match = re.search(r"(\d+(?:\.\d+)?)\s*m/s", description, re.IGNORECASE)
    if vel_match:
        speed = float(vel_match.group(1))
        cfg.setdefault("solver_parameters", {}).setdefault("fluid_dynamics", {})["max_velocity_factor"] = max(2.0, speed)

    # End-time override (e.g. "5 s", "5 sec", "5 second", "5 seconds")
    time_match = re.search(
        r"(\d+(?:\.\d+)?)\s*(?:s|sec|secs|second|seconds)\b",
        description,
        re.IGNORECASE,
    )
    if time_match:
        cfg.setdefault("solver_parameters", {})["end_time"] = float(time_match.group(1))

    # Domain size override (e.g. "2 m domain")
    domain_match = re.search(r"(\d+(?:\.\d+)?)\s*m\s+domain", description, re.IGNORECASE)
    if domain_match:
        size = float(domain_match.group(1))
        geometries = cfg.setdefault("geometries", {})
        system_domain = geometries.setdefault("system_domain", {})
        upper_bound = system_domain.get("upper_bound")
        if isinstance(upper_bound, list) and upper_bound:
            dim = len(upper_bound)
        else:
            lower_bound = system_domain.get("lower_bound")
            dim = len(lower_bound) if isinstance(lower_bound, list) and lower_bound else 2
        system_domain.setdefault("lower_bound", [0.0] * dim)
        system_domain["upper_bound"] = [size] * dim
        geometry_changed = True

    # Resolution override (e.g. "5 mm resolution")
    res_match = re.search(r"(\d+(?:\.\d+)?)\s*mm\s+resolution", description, re.IGNORECASE)
    if res_match:
        cfg.setdefault("geometries", {}).setdefault("global_resolution", {})["particle_spacing"] = (
            float(res_match.group(1)) / 1000.0
        )

    if geometry_changed:
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
            dim = len(
                cfg.get("geometries", {}).get("system_domain", {}).get("upper_bound", [0.0, 0.0])
            )
            if len(coords) == dim:
                observed_body = "ProcessingPiece"
                if cfg.get("fluid_bodies"):
                    observed_body = cfg["fluid_bodies"][0].get("name", "WaterBody")
                cfg.setdefault("observers", []).append(
                    {
                        "name": obs_name,
                        "observed_body": observed_body,
                        "variable": {"real_type": "Pressure"},
                        "positions": [coords],
                    }
                )

    if "add fluid block" in lower:
        name_match = re.search(r"add\s+fluid\s+block(?:\s+named\s+([\w\- ]+))?", description, re.IGNORECASE)
        block_name = (name_match.group(1).strip() if name_match and name_match.group(1) else "FluidBlock")
        dims_match = re.search(r"dimensions?\s*[:=]?\s*([^,;]+)", description, re.IGNORECASE)
        dims = _extract_float_list(dims_match.group(1)) if dims_match else []
        dim = len(
            cfg.get("geometries", {}).get("system_domain", {}).get("upper_bound", [0.0, 0.0])
        )
        if len(dims) == dim:
            density_match = re.search(r"density\s*[:=]?\s*(\d+(?:\.\d+)?)", description, re.IGNORECASE)
            sound_speed_match = re.search(r"sound\s*speed\s*[:=]?\s*(\d+(?:\.\d+)?)", description, re.IGNORECASE)
            density = float(density_match.group(1)) if density_match else 1.0
            max_velocity_factor = float(sound_speed_match.group(1)) / 10.0 if sound_speed_match else 2.0
            block_shape_name = f"{block_name}Shape"
            cfg.setdefault("geometries", {}).setdefault("shapes", []).append(
                {
                    "name": block_shape_name,
                    "type": "bounding_box",
                    "lower_bound": [0.0] * dim,
                    "upper_bound": dims,
                }
            )
            cfg.setdefault("fluid_bodies", []).append(
                {
                    "name": block_shape_name,
                    "material": {
                        "type": "weakly_compressible_fluid",
                        "density": density,
                    },
                }
            )
            cfg.setdefault("solver_parameters", {}).setdefault("fluid_dynamics", {})["max_velocity_factor"] = max_velocity_factor


def _apply_updates(existing: Dict[str, Any], description: str) -> Dict[str, Any]:
    import copy

    cfg = copy.deepcopy(existing)
    cfg = _apply_overrides(cfg, description)
    _apply_additions(cfg, description)
    return cfg


def _dict_diff(base: Any, updated: Any) -> Any:
    if isinstance(base, dict) and isinstance(updated, dict):
        changed: Dict[str, Any] = {}
        for key in updated.keys():
            if key not in base:
                changed[key] = updated[key]
                continue
            child = _dict_diff(base[key], updated[key])
            if child is not None:
                changed[key] = child
        return changed if changed else None

    if isinstance(base, list) and isinstance(updated, list):
        if base != updated:
            return updated
        return None

    if base != updated:
        return updated
    return None


def _fixture_template_for_physics(
    physics: PhysicsType, description: str = ""
) -> Dict[str, Any] | None:
    """Load a validated fixture template for the given physics type when available."""
    root = Path(__file__).resolve().parents[2]
    is_3d = bool(re.search(r"\b3[\s-]*d(?:imensional)?\b", description, re.IGNORECASE))
    fixture_rel = {
        PhysicsType.FLUID: Path("tests/test_simulation/test_2d_simulation/data/dambreak.json"),
        PhysicsType.SOLID: Path("tests/test_simulation/test_2d_simulation/data/milling.json"),
        PhysicsType.PLASTIC_CONTINUUM: Path(
            "tests/test_simulation/test_3d_simulation/data/repose_angle.json"
            if is_3d
            else "tests/test_simulation/test_2d_simulation/data/column_collapse.json"
        ),
    }.get(physics)

    if fixture_rel is None:
        return None

    fixture_path = root / fixture_rel
    try:
        payload = json.loads(fixture_path.read_text())
        validated = SimulationConfig.model_validate(payload)
        return validated.model_dump(exclude_none=True)
    except Exception:
        return None


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
        base_template = _fixture_template_for_physics(physics, description) or _TEMPLATES[physics]
        template = _apply_overrides(base_template, description)
        from sphinxsim.llm.common import (
            apply_explicit_instruction_overrides,
            apply_stl_geometry_overrides,
            suppress_implicit_plastic_observers,
        )

        template = apply_stl_geometry_overrides(template, description)
        template = apply_explicit_instruction_overrides(template, description)
        template = suppress_implicit_plastic_observers(template, description)

        return SimulationConfig(**template)

    def update(self, existing: SimulationConfig, description: str) -> SimulationConfig:
        """Apply a natural-language update to an existing config."""
        if not description or not description.strip():
            raise ValueError("description must not be empty")

        updated = _apply_updates(existing.model_dump(), description)
        return SimulationConfig(**updated)

    def update_patch(self, existing: SimulationConfig, description: str, strict: bool = True) -> Dict[str, Any]:
        """Return an operation patch for updating an existing config."""
        if not description or not description.strip():
            raise ValueError("description must not be empty")

        operations: List[Dict[str, Any]] = []

        time_match = re.search(
            r"(\d+(?:\.\d+)?)\s*(?:s|sec|secs|second|seconds)\b",
            description,
            re.IGNORECASE,
        )
        if time_match:
            operations.append(
                {
                    "op": "set_value",
                    "path": "solver_parameters.end_time",
                    "value": float(time_match.group(1)),
                }
            )

        res_match = re.search(r"(\d+(?:\.\d+)?)\s*mm\s+resolution", description, re.IGNORECASE)
        if res_match:
            operations.append(
                {
                    "op": "set_value",
                    "path": "geometries.global_resolution.particle_spacing",
                    "value": float(res_match.group(1)) / 1000.0,
                }
            )

        lower = description.lower()
        if "add observer" in lower:
            updated = self.update(existing, description)
            before = existing.model_dump(exclude_none=True).get("observers", [])
            after = updated.model_dump(exclude_none=True).get("observers", [])
            if len(after) > len(before):
                operations.append(
                    {
                        "op": "append_item",
                        "path": "observers",
                        "value": after[-1],
                    }
                )

        if not operations:
            updated = self.update(existing, description)
            base = existing.model_dump(exclude_none=True)
            target = updated.model_dump(exclude_none=True)
            delta = _dict_diff(base, target) or {}
            operations.append(
                {
                    "op": "merge_object",
                    "path": "",
                    "value": delta,
                }
            )

        patch = UpdatePatch(
            strict=strict,
            operations=operations,
        )
        return patch.model_dump(exclude_none=True)

    def explore(self, question: str, context: str | None = None) -> str:
        """Return a deterministic schema/functionality explanation for local usage."""
        if not question or not question.strip():
            raise ValueError("question must not be empty")

        top_level_fields = sorted(SimulationConfig.model_json_schema().get("properties", {}).keys())
        return (
            "Mock exploration mode (deterministic). "
            "SPHinXsim supports generate, update, validate, run, and shell workflows. "
            f"Top-level SimulationConfig fields: {', '.join(top_level_fields)}. "
            f"Question received: {question.strip()}"
        )
