# Simulating dambreak flows with SPHinXsim

This is the first example use to learn how to setting up a flow simulation using SPHinXsim.
Note that, dambreak flow is a classical benchmark case for free-surface flow simulation using SPH method,
and it has been widely used in the literature to validate the accuracy of SPH solvers.

The 2d and 3d dambreak examples demonstrate the basic usage of SPHinXsim for simulating free-surface flows. The canonical configurations are [the 2d case](../../tests/test_simulation/test_2d_simulation/data/dambreak.json) and [the 3d case](../../tests/test_simulation/test_3d_simulation/data/dambreak.json).

Here, I show (2d) simulation setting json file to illustrate the basic usage.

## 2d dambreak simulation setting JSON file

```json
{
  "characteristic_dimensions": [
    {
      "value": 1.0,
      "name": "Length",
      "hint": "geometries.shapes[name=WaterBody].upper_bound"
    },
    {
      "value": 1.0,
      "name": "Gravity",
      "hint": "gravity"
    },
    {
      "value": 1.0,
      "name": "Density",
      "hint": "fluid_bodies[name=WaterBody].material.density"
    }
  ],
  "simulation_type": "fluid_dynamics",
  "geometries": {
    "global_resolution": {
      "particle_spacing": 0.025
    },
    "shapes": [
      {
        "name": "WaterBody",
        "type": "bounding_box",
        "lower_bound": [0.0, 0.0],
        "upper_bound": [2.0, 1.0]
      },
      {
        "name": "WallBoundary",
        "type": "multipolygon",
        "polygons": [
          {
            "operation": "union",
            "type": "container_box",
            "inner_box": {
              "lower_bound": [0.0, 0.0],
              "upper_bound": [5.366, 5.366]
            },
            "thickness": 0.1
          }
        ]
      }
    ]
  },
  "particle_generation": {
    "bodies": [
      {
        "name": "WaterBody"
      },
      {
        "name": "WallBoundary",
        "solid_body": {}
      }
    ],
    "relaxation_parameters": {
      "total_iterations": 1000
    }
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
  "gravity": [0.0, -1.0],
  "observers": [
    {
      "name": "FluidObserver",
      "observed_body": "WaterBody",
      "variable": {
        "real_type": "Pressure"
      },
      "positions": [[5.366, 0.2]]
    }
  ],
  "extra_state_recording": [
    {
      "name": "WaterBody",
      "variables": [
        {
          "real_type": ["Pressure"]
        }
      ]
    },
    {
      "name": "WallBoundary",
      "variables": [
        {
          "vector_type": ["NormalDirection"]
        }
      ]
    }
  ],
  "solver_parameters": {
    "end_time": 20.0,
    "output_interval": 0.1,
    "screen_interval": 100,
    "fluid_dynamics": {
      "acoustic_cfl": 0.6,
      "advection_cfl": 0.25,
      "particle_sort_frequency": 100
    }
  }
}
```

## Explanation of the JSON entries

### 1. Characteristic_dimensions

The `characteristic_dimensions` section defines scaling references for the simulation.
If provided, it must include `Length` and at least one other characteristic dimension.
In this example, `Length`, `Gravity`, and `Density` are provided.

Each entry requires a `hint` identifying the corresponding configuration value or stating
that the reference is externally defined.

### 2. Simulation type

The `simulation_type` field specifies the type of simulation being performed.
In this case, it is set to `"fluid_dynamics"`, indicating that the simulation is characterized by fluid flow,
or the dynamics of the simulation is centered around the behavior of fluids.
Other dynamics, such as solid dynamics, are considered as secondary dynamics in the simulation
where the fluid-structure interaction is involved.

### 3. Geometries

This section defines the all geometries used in the simulation.
Each geometry is defined by its type, name, and relevant parameters.
The name of the geometry is used to reference it in other parts of the simulation settings,
such as in the `fluid_bodies`, `solid_bodies`, and `observers`.

We first define the `global_resolution` for the simulation,
which specifies the particle spacing used to discretize the geometries.

Then we define the main geometric components of the simulation, which include the `shapes` and the `oriented_boxes`.
In dambreak case, we only define the `shapes` for the simulation, which include the `WaterBody` and the `WallBoundary`.
We will learn more about the `oriented_boxes` in other cases.

As this is a 2d simulation, the `WaterBody` is defined as a bounding box with lower and upper bounds,
while the `WallBoundary` is defined as a multipolygon.
For multipolygon, the components are added one-by-one with a polygon and a binary operation.
In this case, we define the `WallBoundary` as a union of a container box with inner lower and upper bounds and a thickness.

Note that for 3d simulations, the definition of the `WaterBody` and `WallBoundary` will be similar,
but using 3d geometrical building blocks.

### 4. Particle generation

`particle_generation` directly contains the `bodies` to generate and the
`relaxation_parameters`. Each body name refers to a shape in `geometries.shapes`;
include `solid_body` for a solid boundary. For a restart, set `restart.restore_step`
to a saved step; particle generation and relaxation are skipped for nonzero
`restore_step`.

In 2D, a circle can be defined once in `geometries.primitives` with `name`,
`type: "circle"`, `center`, `radius`, and `resolution`. Multipolygon entries
can reuse it with `type: "circle"` and `primitive: "<name>"` in place of the
inline circle fields. The primitive must be defined before the shapes and
cannot be used in a 3D configuration.

### 5. Fluid bodies

Here, we define the fluid bodies in the simulation.
In this case, we have one fluid body named `WaterBody`,
which is associated with a material of type `weakly_compressible_fluid` and a density of 1.0.
Note that `weakly_compressible_fluid` is the default fluid type in SPHinXsim,
and it is used to simulate incompressible fluids with a small compressibility.

### 6. Solid bodies

Similarly, we define the solid bodies in the simulation.
In this case, we have one solid body named `WallBoundary`,
which is associated with a material of type `rigid_body`,
which indicates that the body takes the role of wall boundary conditions.

### 7. Gravity

Here, we define the gravity vector for the simulation.
In this case, the gravity is set to an constant value of [0.0, -1.0].

### 8. Observers

Observers are used to monitor specific variables at certain positions in the simulation domain.
In this case, we have one observer named `FluidObserver`,
which observes the `WaterBody` and records the `Pressure` variable at the position [5.366, 0.2].
The observer's data can be used for analysis and visualization of the simulation results.

### 9. Extra state recording

Besides the observers, SPHinXsim also records state variables for all bodies.
The `extra_state_recording` section specifies, beyond default variables setting,
the extra variables to be recorded for each body.
In this case, we record the `Pressure` variable for the `WaterBody`
and the `NormalDirection` variable for the `WallBoundary`.
Note that we need to specify the `real_type`
or `vector_type` for the variables to be recorded.

### 10. Solver parameters

Here, we define the solver parameters for the simulation.
The `end_time` field specifies the total simulation time,
the `output_interval` field specifies the time interval for outputting simulation results,
and the `screen_interval` field specifies the number of time steps between screen outputs.
The `fluid_dynamics` field contains parameters specific to the fluid dynamics solver,
including the `acoustic_cfl`, `advection_cfl`, and `particle_sort_frequency` values shown here.
The `acoustic_cfl` and `advection_cfl` fields specify the CFL conditions for the acoustic and advection time steps, respectively.
The `particle_sort_frequency` field specifies how often the particles are sorted to improve computational efficiency.

Note except for the `end_time` and `output_interval` have physical units
and scaled by the characteristic dimensions defined in the `characteristic_dimensions` section,
the other parameters are dimensionless.

## Notes on 3d dambreak simulation setting JSON file

The 3d dambreak simulation setting JSON file is similar to the 2d case,
with the main difference being the definition of the geometries and the particle spacing.
In the 3d case, the `WaterBody` is defined as a bounding box with lower and upper bounds in 3d space.
The `WallBoundary` is a `complex_shape` formed by taking the union of an expanded box and subtracting the inner box.
The particle spacing is also adjusted to account for the additional dimension, and the simulation parameters are scaled accordingly.

The 3d example also includes a cylindrical emitter shape. It is inserted into the
fluid body's generated particles through
`particle_generation.bodies[].cylinder_shape_inserts`. A matching region in
`geometries.oriented_boxes` defines the emitter boundary condition's active
region. The `fluid_boundary_conditions` entry uses type `emitter`, references
that region by name, and sets `inflow_speed` and an optional `on_schedule`.

## Try to modify the JSON file for your own simulation

There are many parameters in the simulation setting JSON file that can be modified to create different simulation scenarios.
For example, you can change the `particle_spacing` in the `global_resolution` section to adjust the resolution of the simulation,
or modify the `end_time` and `output_interval` in the `solver_parameters` section to change the duration and frequency of the simulation output.
You can also add or remove observers in the `observers` section to monitor different variables or positions in the simulation domain.

Another example is to modify the `WaterBody` geometry to change the initial shape of the fluid body,
or adjust the `WallBoundary` geometry to create different boundary conditions for the simulation.
You can also add solid bodies to the simulation by defining them in the `solid_bodies` section.

For 3d simulations, you can modify the `geometries` section
to change the position and orientation of the emitter.

## Feedback and suggestions

Please feel free to provide feedback and suggestions (in Issue of the SPHinXsim repository )
to improve the simulation setting JSON file and the documentation.
Especially, if you feel something which may be confusing or unclear,
please let us know so that we can make the necessary improvements.
