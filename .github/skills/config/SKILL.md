---
name: config
description: SPHinXsim config schema and update-patch guidance for Pydantic models, validation patterns, patch operations, and testing conventions. Invoke by default for any task that edits or debugs files under sphinxsim/config.
---

# Config Schema & Update Patch Skill

## Purpose

This skill provides authoritative guidance for working with the SPHinXsim configuration system:
- Pydantic v2 schema models that define the JSON config payload for SPH simulations
- Operation-based update patches for modifying existing configs
- Cross-field validation conventions and enum-driven type safety
- Testing patterns for schema validation and patch execution

## Invocation Triggers

Invoke this skill when:
- Editing or debugging files under `sphinxsim/config/`
- Adding or modifying schema fields, enums, or validation rules in `schemas.py`
- Adding or modifying patch operations in `update_patch.py`
- Writing or updating tests in `tests/test_schemas.py` or `tests/test_update_patch.py`
- Debugging `ValidationError` messages from config parsing
- Working on LLM modules that consume `SimulationConfig` or `UpdatePatch` (e.g. `sphinxsim/llm/`)

## Scope

### Covers
- `sphinxsim/config/schemas.py` — Pydantic schema hierarchy
- `sphinxsim/config/update_patch.py` — Operation-based patch executor
- `sphinxsim/config/__init__.py` — Public exports
- `tests/test_schemas.py` — Schema validation tests
- `tests/test_update_patch.py` — Patch executor tests
- Schema-related code in `sphinxsim/llm/` that imports from config

### Does not cover
- `sphinxsim/sph_simulation/` — Simulation execution layer
- `sphinxsim/sphinxsys/` — C++ binding layer
- `sphinxsim/visualization/` — Rendering (see visualization skill)
- `sphinxsim/cli.py` — CLI entry point

## Architecture

### Schema Hierarchy

`SimulationConfig` is the top-level model consumed by `SPHSimulation` methods (`buildGeometries()`, `generateParticles()`, `buildSimulation()`). It composes:

```
SimulationConfig
├── characteristic_dimensions: List[CharacteristicDimensionConfig]
├── simulation_type: SimulationType (enum)
├── geometries: GeometriesConfig
│   ├── system_domain: DomainConfig
│   ├── global_resolution: GlobalResolutionConfig
│   ├── primitives: List[PrimitiveConfig]
│   ├── shapes: List[ShapeConfig]
│   └── oriented_boxes: List[OrientedBoxConfig]
├── particle_generation: ParticleGenerationConfig
│   └── settings: ParticleGenerationSettingsConfig
│       ├── bodies: List[ParticleBodyConfig]
│       └── relaxation_constraints: List[RelaxationConstraintConfig]
├── fluid_bodies: List[FluidBodyConfig]
├── continuum_bodies: List[ContinuumBodyConfig]
├── solid_bodies: List[SolidBodyConfig]
├── observers: List[ObserverConfig]
├── fluid_boundary_conditions: List[FluidBoundaryConditionConfig]
├── body_constraints: List[BodyConstraintConfig]
├── initial_conditions: List[InitialConditionConfig]
├── extra_state_recording: List[ExtraStateRecordingConfig]
├── energy_recording: List[EnergyRecordingConfig]
├── restart: RestartConfig
└── solver_parameters: SolverParametersConfig
    ├── fluid_dynamics: FluidDynamicsSolverConfig
    └── continuum_dynamics: ContinuumDynamicsSolverConfig
```

### Material & Body Type Constraints

Each body type restricts its `material.type` to specific `MaterialType` enum values:
- `FluidBodyConfig` → `WEAKLY_COMPRESSIBLE_FLUID`, `WEAKLY_COMPRESSIBLE_MULTI_SPECIES`, or `WEAKLY_COMPRESSIBLE_MULTI_PHASE`
- `SolidBodyConfig` → `RIGID_BODY` or `COMPOSITE_SOLID`
- `ContinuumBodyConfig` → `J2_PLASTICITY`, `PLASTIC_CONTINUUM`, or `GENERAL_CONTINUUM`

Current parser-aligned constraint:
- `WEAKLY_COMPRESSIBLE_MIXTURE` is rejected by schema because the current C++ material parser does not support it.

### Boundary Condition Types

`FluidBoundaryConditionConfig` validates type-specific requirements:
- `EMITTER` requires `inflow_speed`
- `BI_DIRECTIONAL` requires `pressure`
- `FREE_STREAM` requires `buffer_box`, `disposer_box`, `target_speed`, and `t_ref`
- `mass_fractions` are only valid for `BI_DIRECTIONAL`, must sum to 1.0, and values must be in [0, 1]
- `on_schedule.duration` is optional (if omitted, the schedule switches on and stays on)

### Body Constraints

`BodyConstraintConfig`:
- `FIXED` type: no additional fields required
- `SIMBODY` type: requires `mobilized_body`, `velocity`, and `angular_velocity`
- Simbody constraints also require `config.restart` to exist (cross-validated at top level)

### Cross-Field Validation

`SimulationConfig._cross_validate` (using `@model_validator(mode="after")`) checks:
1. `characteristic_dimensions` must include a `Length` entry if provided
2. Simulation type requirements: `fluid_dynamics` needs `fluid_bodies` + `solver_parameters.fluid_dynamics`; `continuum_dynamics` needs `continuum_bodies` + `solver_parameters.continuum_dynamics`
3. Current builder limitations are enforced: fluid dynamics supports exactly one fluid body; continuum dynamics supports exactly one continuum body
4. At least one `solid_body` is required unless a `FREE_STREAM` boundary condition is configured
5. All body names (fluid/continuum/solid) must match shape names in `geometries.shapes`
6. `particle_generation` body names and relaxation constraint references must exist as shapes/oriented boxes
7. `fluid_boundary_conditions.body_name` must reference an existing fluid body; `oriented_box` must exist
8. `mass_fractions` require the boundary body's material to be `WEAKLY_COMPRESSIBLE_MULTI_SPECIES` with matching species count
9. `observers.observed_body` must reference an existing fluid/continuum body
10. `body_constraints.body_name` must reference an existing continuum/solid body; `region` must reference an oriented box
11. `initial_conditions.body_name` must reference an existing body; assignment `region` must reference an oriented box
12. Simbody constraints require `config.restart`
13. `solid_bodies[*].material.material_id_regions.regions[*].shape` must reference existing shapes
14. Dimensional consistency across vectors (including gravity/observers, primitives, transforms, and active_strain center)
15. 2D/3D compatibility checks: `multipolygon` is 2D-only; `triangle_mesh` is 3D-only

## Update Patch System

### Overview

`update_patch.py` provides an operation-based patch executor for modifying existing config dicts. The main entry point is `apply_update_patch(config: dict, patch: UpdatePatch) -> PatchApplyResult`.

### Operation Types

Five operations are supported:

| Operation | Description | Key Fields |
|-----------|-------------|------------|
| `set_value` | Set a scalar/object at a path | `path`, `value` |
| `merge_object` | Deep-merge an object into a path | `path`, `value` |
| `append_item` | Append an item to an array at a path | `path`, `value` |
| `upsert_item` | Update or insert an array item by match | `path`, `match`, `value`, `on_match`, `on_missing` |
| `rename_item_key` | Rename a key in a matched array item | `path`, `match`, `key`, `new_value` |

### Path Syntax

Paths use dot notation: `solver_parameters.end_time`, `geometries.global_resolution.particle_spacing`. Empty string `""` refers to the root object.

### Preconditions

Each operation can specify `preconditions` (list of `PatchPrecondition`) checked before execution:
- `path_exists` — target path must exist
- `path_absent` — target path must not exist
- `array_unique_key` — array item with matching key must not exist
- `type_is` — value at path must be of a specific type

### Strict Mode

- `strict=True` (default): precondition failures or operation errors raise exceptions, halting execution
- `strict=False`: errors are collected as warnings in `PatchApplyResult.warnings`; execution continues

### Result

`PatchApplyResult` contains:
- `applied: bool` — whether the patch was applied
- `changed: bool` — whether the config was actually modified
- `updated: dict` — the resulting config
- `warnings: List[str]` — collected warnings (non-strict mode)

## Core Files

| File | Purpose | Key Exports |
|------|---------|-------------|
| `sphinxsim/config/schemas.py` | Pydantic schema models | `SimulationConfig`, `GeometriesConfig`, `ShapeConfig`, `MaterialConfig`, `FluidBodyConfig`, `SolidBodyConfig`, `ContinuumBodyConfig`, `FluidBoundaryConditionConfig`, `BodyConstraintConfig`, `SolverParametersConfig`, all enums |
| `sphinxsim/config/update_patch.py` | Patch executor | `UpdatePatch`, `apply_update_patch`, `PatchApplyResult`, `PatchPrecondition` |
| `sphinxsim/config/__init__.py` | Public API exports | `BodyConstraintConfig`, `DomainConfig`, `FluidBodyConfig`, `FluidBoundaryConditionConfig`, `GeometriesConfig`, `MaterialConfig`, `ObserverConfig`, `ParticleGenerationConfig`, `ShapeConfig`, `SimulationConfig`, `SimulationType`, `SolverParametersConfig` |

## Key Conventions

### 1. Pydantic v2 Patterns
- Use `BaseModel` with `ConfigDict(extra="forbid")` where strict field sets are needed (e.g. `FluidDynamicsSolverConfig`)
- Use `Field(..., min_length=1)` for non-empty string fields, `Field(..., ge=0)` / `gt=0` for numeric bounds
- Use `@model_validator(mode="after")` for all cross-field validation — never use `mode="before"` for cross-field checks
- Validator methods are named with a leading underscore: `_material_type`, `_type_specific_requirements`, `_cross_validate`, `_validate_constraint_type`

### 2. Enum-Driven Type Safety
- All type discriminators are `Enum` classes: `SimulationType`, `MaterialType`, `BodyShapeType`, `MultiPolygonPrimitiveType`, `OrientedBoxType`, `FluidBoundaryConditionType`, `BodyConstraintType`, `ThermalBoundaryType`, `CharacteristicDimensionName`, `GeometricOperationType`
- Enums use string values matching JSON keys (e.g. `WEAKLY_COMPRESSIBLE_FLUID = "weakly_compressible_fluid"`)
- Body/condition types are validated against allowed enum subsets in model validators

### 3. Error Messages
- All `ValueError` messages are descriptive and lowercase, referencing the field name and the constraint
- Example: `"fluid body 'WaterBody' must match a shape name in geometries.shapes"`
- Messages guide the user to the fix, not just describe the problem

### 4. Shape Type Validation
`ShapeConfig` uses `@model_validator(mode="after")` to enforce type-specific required fields:
- `bounding_box` → requires `lower_bound`, `upper_bound`
- `box` / `region` / multipolygon `box` entries → support either inline geometry (`half_size` + `transform`) or primitive references (`primitive`)
- `expanded_box` → requires `original` and `expansion`
- `complex_shape` → requires `sub_shapes` and `operations` (union/subtraction only)
- `multipolygon` → requires non-empty `polygons` with per-entry type validation
- `triangle_mesh` → requires `file_name`
- `TransformConfig` requires `rotation_axis` for 3D translations
- Other shape types have their own field requirements

### 5. Material Type Validation
`MaterialConfig` validates type-specific required fields:
- `WEAKLY_COMPRESSIBLE_FLUID` → `density`
- `WEAKLY_COMPRESSIBLE_MIXTURE` → explicitly rejected (not supported by current C++ parser)
- `WEAKLY_COMPRESSIBLE_MULTI_SPECIES` → non-empty `species`
- `WEAKLY_COMPRESSIBLE_MULTI_PHASE` → non-empty `pure_phases`
- `RIGID_BODY` → no extra fields
- `J2_PLASTICITY` → `density`, `sound_speed`, `youngs_modulus`, `poisson_ratio`, `yield_stress`, `hardening_modulus`
- `PLASTIC_CONTINUUM` / `GENERAL_CONTINUUM` → `density`, `sound_speed`, `youngs_modulus`, `poisson_ratio`
- `COMPOSITE_SOLID` → `density`, `poisson_ratio`, `youngs_modulus_active`, `youngs_modulus_1`, `youngs_modulus_2`

### 6. LLM Integration
- `sphinxsim/llm/common.py` provides `sanitize_config_dict()` and `apply_explicit_instruction_overrides()` for cleaning LLM-generated configs before validation
- `SimulationConfig.model_validate(payload)` is the standard validation entry point for LLM output
- `SimulationConfig.model_json_schema()` is used to enumerate top-level fields for LLM prompts
- `BODY_TYPE_RULES` in `common.py` defines strict constraints communicated to LLM providers

## Testing Patterns

### Schema Tests (`tests/test_schemas.py`)

Use helper functions to build complete valid configs, then apply overrides to test specific validation:

```python
def _make_minimal_fluid_config(**overrides) -> SimulationConfig:
    data = { ... full valid fluid_dynamics config ... }
    data.update(overrides)
    return SimulationConfig(**data)

def _make_minimal_continuum_config(**overrides) -> SimulationConfig:
    data = { ... full valid continuum_dynamics config ... }
    data.update(overrides)
    return SimulationConfig(**data)
```

Test patterns:
- **Valid config**: construct via helper, assert field values
- **Invalid field**: construct via helper with override, expect `ValidationError` with `match=` regex
- **Cross-field validation**: override a reference field (e.g. body name) to break a link, expect `ValidationError`
- **Enum rejection**: override with invalid enum string, expect `ValidationError`
- **Extra field rejection**: add unknown key to a `extra="forbid"` model, expect `ValidationError` with `"Extra inputs are not permitted"`

### Patch Tests (`tests/test_update_patch.py`)

Use `_base_config()` helper returning a minimal dict, then construct `UpdatePatch` with operations:

```python
def _base_config() -> dict:
    return {
        "solver_parameters": {"end_time": 1.0},
        "geometries": {"global_resolution": {"particle_spacing": 0.01}},
        "observers": [ ... ],
    }

def test_apply_set_value_changes_scalar() -> None:
    patch = UpdatePatch(operations=[
        {"op": "set_value", "path": "solver_parameters.end_time", "value": 2.5},
    ])
    result = apply_update_patch(_base_config(), patch)
    assert result.applied is True
    assert result.changed is True
    assert result.updated["solver_parameters"]["end_time"] == 2.5
```

Test each operation type: `set_value`, `merge_object`, `append_item`, `upsert_item` (with `on_match`/`on_missing`), `rename_item_key`. Test strict mode failures and precondition checks.

### Running Tests

```bash
# Schema tests only
python3 -m pytest tests/test_schemas.py -v

# Patch tests only
python3 -m pytest tests/test_update_patch.py -v

# Both
python3 -m pytest tests/test_schemas.py tests/test_update_patch.py -v
```

## Common Pitfalls

1. **Forgetting a required cross-reference**: When adding a new body, ensure its `name` matches a shape in `geometries.shapes` — the top-level `_cross_validate` will reject mismatches.
2. **Material type / body type mismatch**: `FluidBodyConfig` with `MaterialType.RIGID_BODY` will fail validation. Each body type has an allowed material type subset.
3. **Missing solver section**: `fluid_dynamics` simulations require `solver_parameters.fluid_dynamics`; `continuum_dynamics` requires `solver_parameters.continuum_dynamics`.
4. **At least one solid body**: Required unless `FREE_STREAM` boundary condition is used.
5. **Dimensional mismatch**: `gravity` and `observer.positions` must match `system_domain` dimensionality (2D vs 3D).
6. **Patch path errors**: `append_item` requires the path to resolve to a list; applying it to a scalar will fail (strict mode) or warn (non-strict).
7. **mass_fractions constraints**: Only valid for `BI_DIRECTIONAL` boundary conditions on `WEAKLY_COMPRESSIBLE_MULTI_SPECIES` fluid bodies, must sum to 1.0, and length must match material species count.
8. **Single-body builder assumptions**: Current C++ builders assume one fluid body for fluid dynamics and one continuum body for continuum dynamics.
