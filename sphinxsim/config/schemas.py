"""Pydantic schemas for SPHSimulation JSON configuration."""

from __future__ import annotations

from enum import Enum
from typing import List, Optional

from pydantic import BaseModel, Field, field_validator, model_validator


class PhysicsType(str, Enum):
    """Heuristic physics category used by the NLP mock layer."""

    FLUID = "fluid"
    SOLID = "solid"
    FSI = "fsi"


class DomainConfig(BaseModel):
    """Spatial domain geometry used by SPHSimulation."""

    dimensions: List[float] = Field(
        ..., min_length=2, max_length=3, description="Domain size [Lx, Ly] or [Lx, Ly, Lz]"
    )

    @field_validator("dimensions")
    @classmethod
    def _positive_dimensions(cls, dims: List[float]) -> List[float]:
        if any(v <= 0.0 for v in dims):
            raise ValueError("All domain dimensions must be positive")
        return dims


class FluidBlockConfig(BaseModel):
    """Fluid block definition accepted by SPHSimulation."""

    name: str = Field(..., min_length=1)
    dimensions: List[float] = Field(..., min_length=2, max_length=3)
    density: float = Field(default=1.0, gt=0)
    sound_speed: float = Field(default=10.0, gt=0)

    @field_validator("dimensions")
    @classmethod
    def _positive_dimensions(cls, dims: List[float]) -> List[float]:
        if any(v <= 0.0 for v in dims):
            raise ValueError("All fluid block dimensions must be positive")
        return dims


class WallConfig(BaseModel):
    """Wall boundary definition accepted by SPHSimulation."""

    name: str = Field(..., min_length=1)
    dimensions: List[float] = Field(..., min_length=2, max_length=3)
    boundary_width: float = Field(..., gt=0)

    @field_validator("dimensions")
    @classmethod
    def _positive_dimensions(cls, dims: List[float]) -> List[float]:
        if any(v <= 0.0 for v in dims):
            raise ValueError("All wall dimensions must be positive")
        return dims


class ObserverConfig(BaseModel):
    """Observer points used for sampling flow quantities."""

    name: str = Field(..., min_length=1)
    position: Optional[List[float]] = Field(default=None, min_length=2, max_length=3)
    positions: Optional[List[List[float]]] = Field(default=None, min_length=1)

    @field_validator("position")
    @classmethod
    def _position_has_valid_dimension(cls, position: Optional[List[float]]) -> Optional[List[float]]:
        if position is not None and (len(position) < 2 or len(position) > 3):
            raise ValueError("Observer position must have 2 or 3 components")
        return position

    @field_validator("positions")
    @classmethod
    def _positions_non_empty_vectors(cls, positions: Optional[List[List[float]]]) -> Optional[List[List[float]]]:
        if positions is None:
            return positions
        for pos in positions:
            if len(pos) < 2 or len(pos) > 3:
                raise ValueError("Each observer position must have 2 or 3 components")
        return positions

    @model_validator(mode="after")
    def _require_exactly_one_position_source(self) -> "ObserverConfig":
        if self.position is None and self.positions is None:
            raise ValueError("Observer must define either position or positions")
        if self.position is not None and self.positions is not None:
            raise ValueError("Observer must define either position or positions, not both")
        return self


class SolverConfig(BaseModel):
    """Numerical solver toggles accepted by SPHSimulation."""

    dual_time_stepping: bool = False
    free_surface_correction: bool = False


class SimulationConfig(BaseModel):
    """Top-level JSON payload accepted by SPHSimulation.loadConfig()."""

    domain: DomainConfig
    particle_spacing: float = Field(..., gt=0, description="Reference particle spacing")
    particle_boundary_buffer: int = Field(
        ..., gt=0, description="Number of particle spacings used for boundary padding"
    )
    fluid_blocks: List[FluidBlockConfig] = Field(..., min_length=1)
    walls: List[WallConfig] = Field(..., min_length=1)
    gravity: Optional[List[float]] = Field(default=None, min_length=2, max_length=3)
    observers: List[ObserverConfig] = Field(default_factory=list)
    solver: SolverConfig = Field(default_factory=SolverConfig)
    end_time: Optional[float] = Field(default=None, gt=0)

    @model_validator(mode="after")
    def _dimensionality_consistent(self) -> "SimulationConfig":
        dim = len(self.domain.dimensions)
        for fb in self.fluid_blocks:
            if len(fb.dimensions) != dim:
                raise ValueError("Fluid block dimensionality must match domain dimensionality")
        if self.gravity is not None and len(self.gravity) != dim:
            raise ValueError("Gravity dimensionality must match domain dimensionality")
        for wall in self.walls:
            if len(wall.dimensions) != dim:
                raise ValueError("Wall dimensionality must match domain dimensionality")
        for observer in self.observers:
            if observer.position is not None and len(observer.position) != dim:
                raise ValueError("Observer dimensionality must match domain dimensionality")
            if observer.positions is not None and any(len(pos) != dim for pos in observer.positions):
                    raise ValueError("Observer dimensionality must match domain dimensionality")
        return self
