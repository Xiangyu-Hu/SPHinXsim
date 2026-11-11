# Simulating the filling tank flow with SPHinXsim

Compared to the dambreak flow, 
the filling tank flow is a more complex case, which involves a new boundary condition, i.e. emitter.
Emitter is a boundary condition that can inject fluid particles into the simulation domain.

To facilitate emitter boundary condition, several JSON entries are added to the simulation setting file.

First, one need  to define an oriented box as the geometry of the emitter, 
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

Note that the `transform` entry defines the position and orientation of the emitter in the simulation domain. and the `half_size` entry defines the half size of the emitter box, which indicate the amount of particles contained in the box.

Since there are particles generated in the emitter box, one need to define the amount of reserved particles in the `particle_reserve_factor` section of the fluid body, as shown below: 

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

Note that the value is set to 350.0, which means that the number of particles in the emitter box is 350 times the initial number of particles in the fluid body.

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
