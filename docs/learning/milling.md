# Simulating machine tooling with SPHinXsim

In this tutorial, we will demonstrate how to simulate a milling process using SPHinXsim. The milling process involves the removal of material from a workpiece using a rotating cutting tool. This simulation will help us understand the dynamics of the cutting process and the interaction between the tool and the workpiece.

## Externally defined characteristic velocity

Compared to previous tutorials, the characteristic dimension uses a different way to define the characteristic velocity. In this case, the characteristic velocity is defined as the cutting speed of the milling tool, which is calculated based on the rotational speed and the diameter of the tool. However, there is such velocity directly defined in the simulation setting file, so the `hint` entry here is `externally_defined`.

## Geometry of the milling tool

Another difference is that the we need read a data file for the geometry of the milling tool, which is defined in the `geometries` section of the JSON file, under the subsection `shapes`. The parameters of the mesh file are defined as shown below:

```json
{
  "name": "MillingTool",
  "type": "multipolygon",
  "polygons": [
    {
      "operation": "union",
      "type": "data_file",
      "file_name": "milling_tool.dat"
    }
  ]
}
```

## Particle relaxation

Since the milling tool is a complex geometry, it is necessary to perform particle relaxation to ensure that the particles are properly distributed around the tool. This can be done by running a separate simulation with the milling tool geometry and allowing the particles to settle into a stable configuration before starting the actual milling simulation.

For this, we can use the `relaxation` entry in the simulation setting file to specify the relaxation approach. In this case, we will use `level_set`, which indicate that the relaxation will be performed using a level set method to ensure that the particles conform to the geometry of the milling tool. Note that the empty brackets `{}` indicate that no additional parameters are needed for this relaxation approach.

To achieve full relaxation, we can set the `total_iterations` entry to a large value, such as 1000, to ensure that the particles have enough time to settle into a stable configuration. The `total_iterations` entry is defined in the `relaxation_parameters` section of the JSON file.

## Simulation type

Milling simulation is not fluid simulation, so the `simulation_type` entry is set to `continuum_dynamics`. This indicates that the simulation will be performed using a solid mechanics (with failure and fracture) approach, which is appropriate for simulating the behavior of the milling tool and the workpiece during the cutting process.

## Continuum bodies

Similar to the fluid bodies, we need to define the continuum bodies in the `continuum_bodies` section of the JSON file. In this case, we will define two continuum bodies: one for the milling tool and one for the workpiece. The parameters of the continuum bodies are defined as shown below:

```json
"continuum_bodies": [
    {
      "name": "ProcessingPiece",
      "material": {
        "type": "j2_plasticity",
        "density": 2.7,
        "sound_speed": 50.0,
        "youngs_modulus": 78.2e2,
        "poisson_ratio": 0.3,
        "yield_stress": 2.9e1,
        "hardening_modulus": 1.0,

        "ductile_damage": {
          "enabled": true,
          "threshold_equivalent_plastic_strain": 0.15,
          "critical_equivalent_plastic_strain": 0.5,
          "critical_damage": 1.0
        }
      }
    }
  ]
```

You will see that the material model used for the workpiece is `j2_plasticity`, which is a common material model for simulating plastic deformation in metals. The parameters of the material model, such as density, sound speed, Young's modulus, Poisson's ratio, yield stress, and hardening modulus, are defined in the `material` section of the JSON file.

There is also a `ductile_damage` entry, which indicates that the simulation will include a ductile damage model to simulate the failure and fracture of the workpiece during the milling process. The parameters of the ductile damage model, such as threshold equivalent plastic strain, critical equivalent plastic strain, and critical damage, are defined in the `ductile_damage` section of the JSON file.

## Moving the milling tool

In this simulation, there are two solid (rigid) bodies: the milling tool and container of the tool and working piece. The milling tool is a moving body, which means that it will be moving during the simulation. The movement of the milling tool is indicated by the `is_moving` entry.

## Body constraints

The prescribed movement of the milling tool and constraints on the working piece are defined in the `body_constraints` section of the JSON file. The parameters of the body constraints are defined as shown below:

```json
  "body_constraints": [
    {
      "body_name": "MillingTool",
      "type": "simbody",
      "mobilized_body": "planar",
      "velocity": [0.0, -0.03],
      "angular_velocity": 2.0
    },

    {
      "body_name": "ProcessingPiece",
      "type": "fixed",
      "region": "ClampingRegion"
    }
  ]
```

Note that the milling tool is defined as a `simbody` type, which indicates that the rigid body dynamics of the milling tool will be simulated using the Simbody library. The `mobilized_body` entry is set to `planar`, which indicates that the milling tool will be constrained to move in a plane. The `velocity` and `angular_velocity` entries define the linear and angular velocities of the milling tool, respectively.

For the workpiece, the `type` entry is set to `fixed`, which indicates that the `region` entry is set to `ClampingRegion`, which indicates that the workpiece will be clamped in place using a specified region of the workpiece geometry.

## Restarting the simulation

In this simulation, we will also demonstrate how to restart the simulation from a previous state. This can be useful for continuing a simulation that was interrupted or for running a simulation with different parameters starting from a previously computed state. Another usage is that one can use this approach to locate an issue (for example a blowup) which may occur during the simulation, by restarting the simulation from a previous state, one can check when, where and how this issue happens.

```json
"restart": {
    "save_interval": 1000,
    "restore_step": 0,
    "summary_enabled": true
  }
```
You can see that the `save_interval` entry is set to 1000, which indicates that the simulation state will be saved every 1000 time steps. The `restore_step` entry is set to 0, which indicates that the simulation runs directly without restart. The `summary_enabled` entry is set to true, which indicates that a summary of the simulation state will be printed to the console when the restart file is written.

With non-zero `restore_step`, the simulation will be restarted from the specified time step, and the simulation state will be restored from the saved state at that time step. 
Note that, with restart, the particle generation will be disabled, which means that you should have valid simulation before (also restart file written at given `restore_step`). Otherwise, the simulation will not be able to run properly. 
