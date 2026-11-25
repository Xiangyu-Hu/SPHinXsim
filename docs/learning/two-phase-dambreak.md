# Simulate two-phase dambreak flow with SPHinXsim

The two-phase dambreak problem is very similar to the (single surface) dambreak flow problem,
except air phase is included here.

## Geometry of air body

The geometry of air body is defined in the same way as the water body,
and is the entire inner box region excluding the part occupied by water body. We use `multipolygon` with binary operation to define it.

```json
{
  "name": "AirBody",
  "type": "multipolygon",
  "polygons": [
    {
      "operation": "union",
      "type": "bounding_box",
      "primitive": "ContainerBox"
    },
    {
      "operation": "subtraction",
      "type": "bounding_box",
      "primitive": "WaterBox"
    }
  ]
}
```

Note that we do not define its geometry directly but referring
to `primitive` entries which can be reused for different purposes.
The usage of `primitive` is very useful, as one may modify it once
then all entries referring it will be modified automatically.

## Fluid bodies

In this problem we assume both water and air are viscous,
so their viscosity are defined as in the following:

```json
"fluid_bodies": [
    {
      "name": "WaterBody",
      "material": {
        "type": "weakly_compressible_fluid",
        "density": 1.0,
        "viscosity": {
          "Reynolds_number": 1000.0
        }
      }
    },
    {
      "name": "AirBody",
      "surface_type": "confined",
      "material": {
        "type": "weakly_compressible_fluid",
        "density": 0.001,
        "viscosity": {
          "Reynolds_number": 100000.0
        }
      }
    }
  ]
```

Note that the viscosity is defined by Reynolds number which is relies on
the characteristic length, velocity and density.
Also note that, here, the `surface_type` of air body is defined as `confined`
as it is confined by water body and wall boundary.
The water body is still defined as `free_surface` by default
because of the great density difference between water and air. The air basically still applies a free surface
condition to water.

## Air particle bounding

The two-phase dambreak problem is very violent, and
many small air bubbles will be generated.
Some of them can be as small as a single air particle
and lead to the issue of numerical instability,
in which the non-physical penetration such air particles into
the wall boundary may occur. To avoid this, air particle bounding is introduced:

```json
  "fluid_boundary_conditions": [
    {
      "body_name": "AirBody",
      "type": "wall_bounding"
    }
  ],
```

In the simulation, we can find the change of flow behavior due to the introduction of viscosity and air phase.
