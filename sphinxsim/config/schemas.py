"""Pydantic schemas for SPHinXsys simulation configuration."""

from __future__ import annotations

from enum import Enum
from typing import List, Optional

from pydantic import BaseModel, Field, field_validator, model_validator


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------


class PhysicsType(str, Enum):
    """High-level physics category for the simulation."""

    FLUID = "fluid"
    SOLID = "solid"
    FSI = "fsi"  # Fluid-Structure Interaction


class BoundaryType(str, Enum):
    """Type of a boundary condition."""

    INLET = "inlet"
    OUTLET = "outlet"
    WALL = "wall"
    SYMMETRY = "symmetry"


class OutputFormat(str, Enum):
    """File format for simulation output."""

    VTP = "vtp"
    VTU = "vtu"
    PLT = "plt"


# ---------------------------------------------------------------------------
# Sub-models
# ---------------------------------------------------------------------------


class DomainConfig(BaseModel):
    """Spatial domain and particle resolution."""

    bounds_min: List[float] = Field(
        ..., min_length=2, max_length=3, description="Minimum corner [x, y] or [x, y, z]"
    )
    bounds_max: List[float] = Field(
        ..., min_length=2, max_length=3, description="Maximum corner [x, y] or [x, y, z]"
    )
    resolution: float = Field(..., gt=0, description="Particle spacing (m)")

    @model_validator(mode="after")
    def _bounds_consistent(self) -> "DomainConfig":
        if len(self.bounds_min) != len(self.bounds_max):
            raise ValueError("bounds_min and bounds_max must have the same dimensionality")
        for lo, hi in zip(self.bounds_min, self.bounds_max):
            if lo >= hi:
                raise ValueError("Each component of bounds_min must be less than bounds_max")
        return self


class MaterialConfig(BaseModel):
    """Material properties for a body in the simulation."""

    name: str = Field(..., min_length=1, description="Unique material/body name")
    density: float = Field(..., gt=0, description="Reference density (kg/m³)")
    # Fluid properties
    dynamic_viscosity: Optional[float] = Field(
        None, gt=0, description="Dynamic viscosity (Pa·s) – fluids only"
    )
    # Solid properties
    youngs_modulus: Optional[float] = Field(
        None, gt=0, description="Young's modulus (Pa) – solids only"
    )
    poisson_ratio: Optional[float] = Field(
        None, ge=0.0, lt=0.5, description="Poisson ratio – solids only"
    )


class BoundaryCondition(BaseModel):
    """A single boundary condition applied to the domain."""

    name: str = Field(..., min_length=1, description="Boundary name / identifier")
    type: BoundaryType
    velocity: Optional[List[float]] = Field(
        None, description="Prescribed velocity vector (m/s)"
    )
    pressure: Optional[float] = Field(None, description="Prescribed pressure (Pa)")

    @field_validator("velocity")
    @classmethod
    def _velocity_nonzero_length(cls, v: Optional[List[float]]) -> Optional[List[float]]:
        if v is not None and len(v) == 0:
            raise ValueError("velocity must have at least one component")
        return v


class TimeSteppingConfig(BaseModel):
    """Time-integration parameters."""

    end_time: float = Field(..., gt=0, description="Physical end time of simulation (s)")
    dt: Optional[float] = Field(
        None, gt=0, description="Fixed time-step (s); auto-computed when None"
    )
    output_interval: float = Field(
        ..., gt=0, description="Interval between output snapshots (s)"
    )

    @model_validator(mode="after")
    def _interval_not_larger_than_end(self) -> "TimeSteppingConfig":
        if self.output_interval > self.end_time:
            raise ValueError("output_interval must not exceed end_time")
        return self


class OutputConfig(BaseModel):
    """Output settings."""

    directory: str = Field("./output", description="Directory for output files")
    format: OutputFormat = Field(OutputFormat.VTP, description="Output file format")


# ---------------------------------------------------------------------------
# Top-level config
# ---------------------------------------------------------------------------


class SimulationConfig(BaseModel):
    """Complete configuration for a SPHinXsys simulation."""

    name: str = Field(..., min_length=1, description="Human-readable simulation name")
    physics: PhysicsType
    domain: DomainConfig
    materials: List[MaterialConfig] = Field(..., min_length=1)
    boundary_conditions: List[BoundaryCondition] = Field(default_factory=list)
    time_stepping: TimeSteppingConfig
    output: OutputConfig = Field(default_factory=OutputConfig)
