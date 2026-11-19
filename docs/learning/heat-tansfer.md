# Simulating thermo-fluid coupling with SPHinXsim

In this tutorial, we will demonstrate how to simulate thermo-fluid coupling using SPHinXsys.
The simulation involves a viscous fluid flow that interacts with a solid boundary,
where heat transfer occurs between the fluid and the solid.

## Viscous fluid flow

There are two ways to define the viscous fluid flow in SPHinXsim.
The first directly set the value of `viscosity` when define a fluid body.
The second is to define it by using `Reynolds number`,
which is a dimensionless quantity that characterizes the flow regime of the fluid.
Therefore, in the second case, the `viscosity` is not directly defined by
by computing the characteristic velocity, characteristic length and fluid density.

## Thermal properties

In SPHinXsim, the thermal properties of the fluid and solid are defined in the `thermal_properties` section of the JSON file.
If a body is usual thermal body, the `thermal_properties` entry is usually:

```json
"thermal_properties": {
  "thermal_conductivity": 1.0e-2,
  "volumetric_heat_capacity": 1.0,
  "contact_body": [
    {
        "body_name": "InsertBody",
        "heat_transfer_coefficient": 0.01
    }
  ]
}
```

If the body is a boundary body, which it represent an external environment,
the `thermal_properties` entry is usually:

```json
"thermal_properties": {
    "thermal_boundary": "Dirichlet"
}
```

Note that the `thermal_boundary` entry can be set to either `Dirichlet` or `Neumann`, depending on the type of boundary condition you want to apply.
