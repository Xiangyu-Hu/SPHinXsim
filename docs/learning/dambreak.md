# Simulating dambreak flows with SPHinXsim

The 2d and 3d dambreak examples demonstrate the basic usage of SPHinXsim for simulating free-surface flows. The examples are located in the `tests/test_simulation/test_2d_simulation/dambreak.json` for the 2d case and `tests/test_simulation/test_3d_simulation/dambreak.json` for the 3d case.

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
        "upper_bound": [2.0, 2.0]
      },
      {
        "name": "WallBoundary",
        "type": "multipolygon",
        "polygons": [
          {
            "operation": "union",
            "type": "container_box",
            "inner_lower_bound": [0.0, 0.0],
            "inner_upper_bound": [5.366, 5.366],
            "thickness": 0.1
          }
        ]
      }
    ]
  },
  "particle_generation": {
    "build_and_run": true,
    "settings": {
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
      "surface_type": "free_surface",
      "particle_sort_frequency": 100
    }
  }
}
```

## Explanation of the JSON entries

### 1. Characteristic_dimensions

The `characteristic_dimensions` section defines the characteristic length, gravity, and density for the simulation. 
These values are used to non-dimensionalize the governing equations and set the scale of the simulation.
If the `characteristic_dimensions` section is not provided, the unity scaling reference is set.
If this section is provided, the `Length` reference is set as the first characteristic dimension in the list,
and one must provide at least two characteristic dimensions. 
In the dambreak case, three scaling reference, i.e.  `Length` and `Gravity` and `Density` are provided.

Note that the `hint` field, pointing to a specific JSON entry of the setting,
is obligated and is used to provide additional information about the characteristic dimension.

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

This section defines the particle generation settings for the simulation.
The `build_and_run` field is set to `true`, indicating that the particle generation process should
be executed before the simulation starts. 
It can be set to `false` if the particle generation is already done and the generated particles are saved in a particle reload file.
A typical case for the latter is when the simulation is restarted from a previous simulation.

The `settings` field contains the parameters for particle generation,
including the bodies to be generated and the relaxation parameters.
Note that the `bodies` field references the geometries defined in the `geometries` section,
and the `relaxation_parameters` field specifies the number of iterations for particle relaxation.
Also, if the body is a solid body, the `solid_body` field 
should be provided to indicate that the body is a solid.

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
including the `acoustic_cfl`, `advection_cfl`, `surface_type`, and `particle_sort_frequency`.
The `acoustic_cfl` and `advection_cfl` fields specify the CFL conditions for the acoustic and advection time steps, respectively.
The `surface_type` field specifies the type of free surface treatment used in the simulation, 
and the `particle_sort_frequency` field specifies how often the particles are sorted to improve computational efficiency.

Note except for the `end_time` and `output_interval` have physical units
and scaled by the characteristic dimensions defined in the `characteristic_dimensions` section,
the other parameters are dimensionless.

## Notes on 3d dambreak simulation setting JSON file

The 3d dambreak simulation setting JSON file is similar to the 2d case,
with the main difference being the definition of the geometries and the particle spacing.
In the 3d case, the `WaterBody` is defined as a bounding box with lower and upper bounds in 3d space, 
and the `WallBoundary` is defined as a multipolygon with 3d boxes.
The particle spacing is also adjusted to account for the additional dimension, and the simulation parameters are scaled accordingly.

One more things in the 3d case, we have included an cylindrical emitter in the `geometries` section, 
which is used to generate particles for the fluid body.
To make emitter work, we define an oriented box in the `oriented_boxes` section, 
which specifies the position and orientation of the emitting behavior.
The `oriented_boxes` is defined so that its size can cover the emitter and its `x` 
direction is aligned with axis direction of the cylinder and facing the fluid domain.

Note that we need generate the particles for the emitter too.
The emitter is defined as an entry in `cylinder_shape_inserts` in the settings of the fluid body.

Still, one need define a fluid boundary condition for the emitter, which is done by defining 
the `fluid_boundary_condition` section.
Here, we define a `fluid_boundary_condition` 
for the `WaterBody` with a `Emitter` that references the name of the oriented box.
In the boundary condition, we specify the `inflow_velocity` for the emitter 
and scheduled emitting with `switch_on_time` at 1.0 and  `duration` of 5.

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