# Simulating the filling tank flow with SPHinXsim

Compared to the dambreak flow,
the filling tank flow is a more complex case, which involves a new boundary condition, i.e. emitter.
Emitter is a boundary condition that can inject fluid particles into the simulation domain.

To facilitate emitter boundary condition, several JSON entries are added to the simulation setting file.

First, one need to define an oriented box as the geometry of the emitter,
which is used to generate fluid particles.
The geometry of the emitter can be defined in the `geometries` section of the JSON file,
under the subsection `oriented_boxes`. The parameters of the oriented box are defined as shown below:

```json
"geometries": {
    ...
    "oriented_boxes": [
      {
        "name": "Emitter",
        "type": "region",
        "half_size": [0.15, 0.0625],
        "transform": {
          "translation": [0.05, 1.0625],
          "rotation_angle": 0.0
        }
      }
    ]
}
```

The `transform` entry defines the position and orientation of the emitter region
in the simulation domain. `half_size` gives its half-widths; together they define
the region used to select particles for emission.

The fluid body's `particle_reserve_factor` reserves capacity for particles added
by the emitter. It is a positive dimensionless capacity factor, not a count or a
ratio describing the number of particles inside the emitter region:

```json
"fluid_bodies": [
    {
      "name": "WaterBody",
      "material": {
        "type": "weakly_compressible_fluid",
        "density": 1.0
      },
      "particle_reserve_factor": 350.0
    }
  ]
```

The value shown is specific to this example. Choose a factor large enough for
the expected particle growth during inflow.

Finally, one need to define the emitter boundary condition in the `fluid_boundary_conditions` section of the JSON file, as shown below:

```json
"fluid_boundary_conditions": [
    {
      "body_name": "WaterBody",
      "oriented_box": "Emitter",
      "type": "emitter",
      "inflow_speed": 2.0,
      "on_schedule": {
        "switch_on_time": 1.0,
        "duration": 25.0
      }
    }
  ]
```

The `inflow_speed` entry defines the speed of the fluid particles injected into the simulation domain, and the optional `on_schedule` entry defines the time schedule for the emitter boundary condition. In this case, the emitter will be switched on at 1.0 and will last for 25.0.
