# JSON Configuration Reference

The JSON file is the authoritative simulation input in SPHinXsim. Python validates it with `SimulationConfig` in `sphinxsim/config/schemas.py`, and the C++ runtime consumes the same structure through `SPHSimulation::buildGeometries()`, `generateParticles()`, and `buildSimulation()`.

This page documents the entries accepted by the current schema and explains how the C++ builders interpret them.

## Lifecycle Mapping

At runtime, the top-level sections are consumed in three stages:

| JSON section | Primary consumer | Purpose |
| --- | --- | --- |
| `characteristic_dimensions` | `ScalingConfig` | Optional unit scaling for all dimensional values |
| `simulation_type` | `SPHSimulation::buildSimulation()` | Selects the fluid or continuum builder |
| `geometries` | `GeometryBuilder` | Creates shapes, domain bounds, resolution, and oriented boxes |
| `particle_generation` | `ParticleGeneration` | Builds reload particles and optional relaxation passes |
| `fluid_bodies`, `continuum_bodies`, `solid_bodies` | `SimulationBuilder` + `MaterialBuilder` | Creates SPH bodies and assigns materials |
| `gravity` | base simulation builder | Adds external gravity to real bodies |
| `fluid_boundary_conditions` | `FluidSimulationBuilder` | Adds emitters or bidirectional boundaries |
| `observers`, `extra_state_recording` | `RecordingBuilder` | Creates observer bodies and extra output variables |
| `body_constraints` | `ConstraintBuilder` | Adds fixed or Simbody constraints |
| `solver_parameters` | simulation builders | Sets common and physics-specific solver settings |

## Top-Level Structure

Minimal fluid case:

```json
{
  "simulation_type": "fluid_dynamics",
  "geometries": {
    "global_resolution": { "particle_spacing": 0.025 },
    "shapes": [
      {
        "name": "WaterBody",
        "type": "bounding_box",
        "lower_bound": [0.0, 0.0],
        "upper_bound": [1.0, 1.0]
      },
      {
        "name": "WallBoundary",
        "type": "bounding_box",
        "lower_bound": [-0.1, -0.1],
        "upper_bound": [1.1, 1.1]
      }
    ]
  },
  "particle_generation": {
    "build_and_run": false
  },
  "fluid_bodies": [
    {
      "name": "WaterBody",
      "material": {
        "type": "weakly_compressible_fluid",
        "density": 1.0
      }
    }
  ],
  "solid_bodies": [
    {
      "name": "WallBoundary",
      "material": {
        "type": "rigid_body"
      }
    }
  ],
  "solver_parameters": {
    "end_time": 1.0,
    "fluid_dynamics": {}
  }
}
```

Cross-reference rules enforced by the schema:

- Every body name must match a shape name from `geometries.shapes`.
- `fluid_boundary_conditions[].oriented_box` and particle-relaxation constraints must match names from `geometries.oriented_boxes`.
- `observers[].observed_body` must refer to an existing fluid or continuum body.
- `body_constraints[].body_name` must refer to an existing continuum or solid body.

## `characteristic_dimensions`

Optional list used to activate unit scaling. If omitted, the runtime uses raw values as-is.

Each entry has:

| Field | Type | Meaning |
| --- | --- | --- |
| `name` | enum | Physical dimension, such as `Length`, `Time`, `Density`, `Pressure`, `Velocity` |
| `value` | float | Characteristic physical value for that dimension |
| `hint` | string | JSON path-like hint used by the C++ scaling logic to verify the chosen magnitude |

Rules and behavior:

- `Length` is mandatory if `characteristic_dimensions` is present.
- The C++ `ScalingConfig` computes scaling factors by solving a least-squares system from the provided dimensions.
- Every dimensional quantity parsed later by C++ is divided by the derived scaling reference for its unit.

Use this section when the JSON values should stay in physical units while the solver operates on internally scaled values.

## `simulation_type`

Allowed values:

- `fluid_dynamics`
- `continuum_dynamics`

This field selects the builder used by `SPHSimulation::buildSimulation()`:

- `fluid_dynamics` requires `fluid_bodies` and `solver_parameters.fluid_dynamics`.
- `continuum_dynamics` requires `continuum_bodies` and `solver_parameters.continuum_dynamics`.

The schema also requires at least one `solid_bodies` entry for both modes.

## `geometries`

This section defines all named geometric objects referenced elsewhere in the JSON.

### `system_domain`

Optional explicit simulation bounds:

| Field | Type | Meaning |
| --- | --- | --- |
| `lower_bound` | float array | Domain lower corner |
| `upper_bound` | float array | Domain upper corner |

If present, `lower_bound` and `upper_bound` must have the same dimension, and each upper value must be greater than its lower value.

Implementation detail: after all shapes are created, the C++ geometry builder expands the stored system bounds to include the bounds of every shape. In practice, `system_domain` seeds the domain, and shapes can enlarge it.

### `global_resolution`

Required. Provide one of:

| Field | Type | Meaning |
| --- | --- | --- |
| `particle_spacing` | float | Direct particle spacing |
| `characteristic_length_particles` | int | Alternative resolution mode |

Implementation detail: in C++, `characteristic_length_particles` is currently converted to particle spacing as `1.0 / characteristic_length_particles`. If you want direct control, prefer `particle_spacing`.

### `shapes`

Required non-empty array. Every entry defines a named shape that later bodies and constraints refer to.

Common fields:

| Field | Type | Meaning |
| --- | --- | --- |
| `name` | string | Unique shape identifier |
| `type` | enum | Shape construction mode |

Supported shape types:

#### `bounding_box`

Fields:

- `lower_bound`
- `upper_bound`

Creates a box-aligned shape and also stores its bounding box explicitly in the config manager.

#### `box`

Fields:

- `half_size`
- `transform.translation`
- `transform.rotation_angle`
- `transform.rotation_axis` in 3D only

Creates a transformed box.

#### `expanded_box`

Fields:

- `original`
- `expansion`

Creates a box by expanding a previously defined box-like shape. The schema requires `original` to point to an earlier shape entry.

#### `complex_shape`

Fields:

- `sub_shapes`
- `operations`

Builds a shape by combining previously defined shapes. Only `union` and `subtraction` are accepted here. The schema rejects `intersection` for `complex_shape` even though the lower-level geometric-op enum includes it.

#### `multipolygon`

Fields:

- `polygons`

This is the 2D composite-polygon path. Each polygon entry contains:

| Field | Meaning |
| --- | --- |
| `operation` | `union`, `intersection`, or `subtraction` when combining the polygon into the overall multipolygon |
| `type` | `bounding_box`, `container_box`, or `data_file` |
| `lower_bound` / `upper_bound` | Required for `bounding_box` |
| `inner_lower_bound` / `inner_upper_bound` / `thickness` | Required for `container_box` |
| `file_path` | Required for `data_file` |

Implementation detail: `multipolygon` exists only in 2D builds.

#### `triangle_mesh`

Fields:

- `file_path`
- optional `translation`
- optional `scale`

Loads an STL-backed triangle mesh. Implementation detail: this path exists only in 3D builds.

### `oriented_boxes`

Optional named boxes used by inflow, outflow, and relaxation constraints.

Supported types:

#### `in_outlet`

Fields:

- `center`
- `normal`
- `radius`

The C++ builder expands this into an oriented box aligned to the provided normal. Its thickness is derived from particle spacing, so the JSON only specifies center, normal, and radius.

#### `region`

Fields:

- `half_size`
- `transform`

This is a generic transformed oriented box used for emitters, observation regions, or constraints.

## `particle_generation`

Controls pre-simulation particle reload generation and optional relaxation.

| Field | Type | Meaning |
| --- | --- | --- |
| `build_and_run` | bool | Whether particle generation and relaxation should run |
| `settings` | object | Required when `build_and_run` is `true` |

### `particle_generation.settings.bodies`

Each entry names a shape that should produce reload particles:

| Field | Type | Meaning |
| --- | --- | --- |
| `name` | string | Shape name |
| `solid_body` | object | Marker enabling normal-direction generation for this body |
| `relaxation` | object | Enables particle relaxation for this body |

`relaxation` supports:

| Field | Type | Meaning |
| --- | --- | --- |
| `level_set` | object | Presence enables level-set bounded relaxation |
| `dependent_bodies` | string array | Bodies used for contact relations during relaxation |

Implementation detail: the runtime currently treats `solid_body` as a marker. When present, particle generation writes the `NormalDirection` evolving variable for that body's particles.

### `particle_generation.settings.relaxation_constraints`

Each entry has:

- `body_name`
- `oriented_box`
- `type`

These constraints are applied during the relaxation stage. The schema validates references to existing shapes and oriented boxes.

### `particle_generation.settings.relaxation_parameters`

Currently supported:

| Field | Type | Default |
| --- | --- | --- |
| `total_iterations` | int | `1000` |

## Body Sections

Exactly one of `fluid_bodies` or `continuum_bodies` is required by the selected `simulation_type`, and `solid_bodies` is always required.

### `fluid_bodies`

Each fluid body has:

| Field | Type | Meaning |
| --- | --- | --- |
| `name` | string | Must match a shape name |
| `material` | object | Must have `type: "weakly_compressible_fluid"` |
| `particle_reserve_factor` | float | Optional extra particle reserve for inflow/open-boundary use |

Implementation detail: if `particle_reserve_factor` is present, the fluid body is created with a reserve buffer for particle injection.

### `continuum_bodies`

Each entry has:

- `name`
- `material`

The material type must be `j2_plasticity` or `general_continuum`.

### `solid_bodies`

Each entry has:

- `name`
- `material`

The material type must be `rigid_body`.

### `material`

Supported `material.type` values:

- `weakly_compressible_fluid`
- `rigid_body`
- `j2_plasticity`
- `general_continuum`

Per-type fields:

| Material type | Required fields |
| --- | --- |
| `weakly_compressible_fluid` | `density` |
| `rigid_body` | none |
| `j2_plasticity` | `density`, `sound_speed`, `youngs_modulus`, `poisson_ratio`, `yield_stress`, `hardening_modulus` |
| `general_continuum` | `density`, `sound_speed`, `youngs_modulus`, `poisson_ratio` |

Optional material properties:

| Field | Meaning |
| --- | --- |
| `viscosity` | Either a numeric viscosity value or `{ "Reynolds_number": ... }` |
| `thermal_properties` | Either thermal coefficients or a thermal boundary mode |

`thermal_properties` supports two modes:

- Material-property mode: `thermal_conductivity` and `volumetric_heat_capacity`
- Boundary mode: `thermal_boundary` with `Dirichlet`, `Neumann`, or `Robin`

Implementation detail: for `weakly_compressible_fluid`, the current C++ builder derives sound speed from `10 * max_velocity_factor` in `solver_parameters.fluid_dynamics`. A `sound_speed` field is not consumed for fluid bodies.

## `fluid_boundary_conditions`

Fluid-only section for inflow and open-boundary behavior.

Each entry includes:

| Field | Type | Meaning |
| --- | --- | --- |
| `body_name` | string | Existing fluid body |
| `oriented_box` | string | Existing oriented box |
| `type` | `emitter` or `bi_directional` | Boundary mode |
| `inflow_speed` | float | Required for `emitter` |
| `pressure` | float | Required for `bi_directional` |

Runtime behavior:

- `emitter` applies a constant inflow speed and injects particles through an `OrientedBoxByParticle` region.
- `bi_directional` creates a pressure-prescribed bidirectional boundary on an `OrientedBoxByCell` region and also enables outflow particle deletion.

## `gravity`

Optional acceleration vector. Its dimensionality must match the simulation dimension when `system_domain` is present.

If provided, the base simulation builder adds a gravity force to real bodies.

## `observers`

Optional point-sampling observers.

Each entry has:

| Field | Type | Meaning |
| --- | --- | --- |
| `name` | string | Observer body name |
| `observed_body` | string | Fluid or continuum body to sample |
| `variable` | object | Exactly one of `real_type` or `vector_type` |
| `positions` | array of points | Observer sample locations |

The C++ runtime creates an `ObserverBody` and fills it with observer particles at the supplied positions.

## `extra_state_recording`

Optional extra variables written with state output.

Each entry has:

| Field | Type | Meaning |
| --- | --- | --- |
| `name` | string | Body name to record from |
| `variables` | array | Variable groups to add to the recorder |

Each variable group can contain one or more of:

- `int_type`: list of integer-valued variable names
- `real_type`: list of scalar variable names
- `vector_type`: list of vector variable names

The schema forbids unknown keys in each variable group.

## `body_constraints`

Continuum/solid constraint section.

Each entry has:

| Field | Type | Meaning |
| --- | --- | --- |
| `body_name` | string | Continuum or solid body to constrain |
| `type` | `fixed` or `simbody` | Constraint mode |
| `region` | string | Optional region-limited constraint |
| `mobilized_body` | string | Required for `simbody` |
| `velocity` | float array | Required for `simbody` |
| `angular_velocity` | float | Required for `simbody` |

Runtime behavior:

- `fixed` constrains `Velocity` to zero, either on the whole body or only inside the named region.
- `simbody` currently supports `mobilized_body: "planar"` and integrates the body through Simbody.

Important schema rule: if any constraint uses `simbody`, `solver_parameters.restart` must also exist.

Current implementation note: the Python schema currently validates `region` against shape names, but the C++ fixed-constraint builder looks up an `OrientedBox` by that same name. Until those two sides are aligned, treat `region` as an advanced field that may require matching schema and runtime expectations manually.

## `solver_parameters`

Common solver settings plus a physics-specific subsection.

### Common fields

| Field | Type | Meaning |
| --- | --- | --- |
| `end_time` | float | Total simulated time |
| `output_interval` | float | Interval between output writes |
| `screen_interval` | int | Interval for console reporting |
| `restart` | object | Restart and state-save settings |

`restart` supports:

| Field | Type | Default |
| --- | --- | --- |
| `restore_step` | int | required |
| `save_interval` | int | `1000` |
| `summary_enabled` | bool | `false` |

Implementation details:

- If `output_interval` is omitted, C++ defaults it to `end_time / 100`.
- The schema accepts `screen_interval`, but the current C++ `parseSolverCommonConfig()` does not read it and the runtime keeps the default value `100`.

### `solver_parameters.fluid_dynamics`

Fluid-only subsection:

| Field | Type | Default | Meaning |
| --- | --- | --- | --- |
| `acoustic_cfl` | float | `0.6` | Acoustic time-step CFL |
| `advection_cfl` | float | `0.25` | Advection time-step CFL |
| `max_velocity_factor` | float | `1.0` | Used by the fluid material builder to derive sound speed |
| `surface_type` | string | `free_surface` | One of `free_surface`, `confined`, `open_boundary` |
| `particle_sort_frequency` | int | unset | Enables periodic particle sorting |

Runtime notes:

- `surface_type: "open_boundary"` enables free-surface indication logic at open boundaries.
- Providing `particle_sort_frequency` switches on particle sorting in the fluid solver.

### `solver_parameters.continuum_dynamics`

Continuum-only subsection:

| Field | Type | Default |
| --- | --- | --- |
| `acoustic_cfl` | float | `0.4` |
| `advection_cfl` | float | `0.2` |
| `linear_correction_matrix_coeff` | float | `0.5` |
| `contact_numerical_damping` | float | `0.5` |
| `shear_stress_damping` | float | `0.0` |
| `hourglass_factor` | float | `2.0` |

## Practical Notes

- Prefer names that match the physical role of a shape or body. Most later sections refer to earlier entries by name.
- Define referenced shapes before any `expanded_box` or `complex_shape` that depends on them.
- For fluid cases with emitters or open boundaries, pair `fluid_boundary_conditions` with `particle_reserve_factor` on the fluid body.
- For 2D wall-like domains, `multipolygon` is usually the most expressive way to combine container and subtraction geometry.
- For 3D imported geometry, use `triangle_mesh` and give translations in physical units.

## Current Example

The repository file `tests/examples/full_updated_simulation_config.json` is a representative fluid-dynamics case showing:

- a `multipolygon` wall boundary,
- a `region` oriented box used as an emitter,
- fluid and solid body sections,
- a point observer, and
- fluid solver parameters.

It is a good template for authoring new fluid cases.