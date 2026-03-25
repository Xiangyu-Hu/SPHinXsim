"""Pydantic schemas for SPHSimulation JSON configuration."""

from __future__ import annotations

from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field, field_validator, model_validator


class PhysicsType(str, Enum):
    """Heuristic physics category used by the NLP mock layer."""

    FLUID = "fluid"
    SOLID = "solid"
    FSI = "fsi"


class DomainConfig(BaseModel):
    """Spatial domain geometry used by SPHSimulation."""

    lower_bound: List[float] = Field(..., min_length=2, max_length=3)
    upper_bound: List[float] = Field(..., min_length=2, max_length=3)

    @field_validator("lower_bound", "upper_bound")
    @classmethod
    def _finite_vectors(cls, vec: List[float]) -> List[float]:
        if any(not isinstance(v, (int, float)) for v in vec):
            raise ValueError("Domain bounds must be numeric")
        return vec

    @model_validator(mode="after")
    def _valid_domain_box(self) -> "DomainConfig":
        if len(self.lower_bound) != len(self.upper_bound):
            raise ValueError("Domain lower_bound and upper_bound dimensionality must match")
        for lo, hi in zip(self.lower_bound, self.upper_bound):
            if hi <= lo:
                raise ValueError("Domain upper_bound must be greater than lower_bound in every axis")
        return self

    @property
    def dimensions(self) -> List[float]:
        return [hi - lo for lo, hi in zip(self.lower_bound, self.upper_bound)]


class ShapeType(str, Enum):
    BOUNDING_BOX = "bounding_box"
    CONTAINER_BOX = "container_box"


class GeometryConfig(BaseModel):
    """Body geometry accepted by SPHSimulation::addShape()."""

    type: ShapeType
    lower_bound: Optional[List[float]] = Field(default=None, min_length=2, max_length=3)
    upper_bound: Optional[List[float]] = Field(default=None, min_length=2, max_length=3)
    inner_lower_bound: Optional[List[float]] = Field(default=None, min_length=2, max_length=3)
    inner_upper_bound: Optional[List[float]] = Field(default=None, min_length=2, max_length=3)
    thickness: Optional[float] = Field(default=None, gt=0)

    @model_validator(mode="after")
    def _shape_fields_match_type(self) -> "GeometryConfig":
        if self.type == ShapeType.BOUNDING_BOX:
            if self.lower_bound is None or self.upper_bound is None:
                raise ValueError("bounding_box geometry requires lower_bound and upper_bound")
            if len(self.lower_bound) != len(self.upper_bound):
                raise ValueError("bounding_box lower_bound and upper_bound dimensionality must match")
            for lo, hi in zip(self.lower_bound, self.upper_bound):
                if hi <= lo:
                    raise ValueError("bounding_box upper_bound must be greater than lower_bound")
            return self

        if self.type == ShapeType.CONTAINER_BOX:
            if self.inner_lower_bound is None or self.inner_upper_bound is None:
                raise ValueError("container_box geometry requires inner_lower_bound and inner_upper_bound")
            if self.thickness is None:
                raise ValueError("container_box geometry requires thickness")
            if len(self.inner_lower_bound) != len(self.inner_upper_bound):
                raise ValueError("container_box inner bounds dimensionality must match")
            for lo, hi in zip(self.inner_lower_bound, self.inner_upper_bound):
                if hi <= lo:
                    raise ValueError("container_box inner_upper_bound must be greater than inner_lower_bound")
        return self


class MaterialType(str, Enum):
    WEAKLY_COMPRESSIBLE_FLUID = "weakly_compressible_fluid"
    RIGID_BODY = "rigid_body"


class MaterialConfig(BaseModel):
    """Body material accepted by SPHSimulation::addMaterial()."""

    type: MaterialType
    density: Optional[float] = Field(default=None, gt=0)
    sound_speed: Optional[float] = Field(default=None, gt=0)

    @model_validator(mode="after")
    def _material_fields_match_type(self) -> "MaterialConfig":
        if self.type == MaterialType.WEAKLY_COMPRESSIBLE_FLUID:
            if self.density is None or self.sound_speed is None:
                raise ValueError("weakly_compressible_fluid material requires density and sound_speed")
        return self


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
    fluid_bodies: List[Dict[str, Any]] = Field(..., min_length=1)
    solid_bodies: List[Dict[str, Any]] = Field(..., min_length=1)
    gravity: Optional[List[float]] = Field(default=None, min_length=2, max_length=3)
    observers: List[ObserverConfig] = Field(default_factory=list)
    solver: SolverConfig = Field(default_factory=SolverConfig)
    end_time: Optional[float] = Field(default=None, gt=0)

    @field_validator("fluid_bodies", "solid_bodies")
    @classmethod
    def _body_entries_have_required_keys(cls, entries: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        for entry in entries:
            if "name" not in entry:
                raise ValueError("Each body requires a name")
            if "geometry" not in entry:
                raise ValueError("Each body requires a geometry block")
            if "material" not in entry:
                raise ValueError("Each body requires a material block")
            GeometryConfig.model_validate(entry["geometry"])
            MaterialConfig.model_validate(entry["material"])
        return entries

    @model_validator(mode="after")
    def _dimensionality_consistent(self) -> "SimulationConfig":
        dim = len(self.domain.lower_bound)

        for fluid_body in self.fluid_bodies:
            geom = GeometryConfig.model_validate(fluid_body["geometry"])
            mat = MaterialConfig.model_validate(fluid_body["material"])
            if mat.type != MaterialType.WEAKLY_COMPRESSIBLE_FLUID:
                raise ValueError("Fluid body material type must be weakly_compressible_fluid")
            if geom.type != ShapeType.BOUNDING_BOX:
                raise ValueError("Fluid body geometry type must be bounding_box")
            assert geom.lower_bound is not None and geom.upper_bound is not None
            if len(geom.lower_bound) != dim:
                raise ValueError("Fluid body dimensionality must match domain dimensionality")

        for solid_body in self.solid_bodies:
            geom = GeometryConfig.model_validate(solid_body["geometry"])
            mat = MaterialConfig.model_validate(solid_body["material"])
            if mat.type != MaterialType.RIGID_BODY:
                raise ValueError("Solid body material type must be rigid_body")
            if geom.type != ShapeType.CONTAINER_BOX:
                raise ValueError("Solid body geometry type must be container_box")
            assert geom.inner_lower_bound is not None and geom.inner_upper_bound is not None
            if len(geom.inner_lower_bound) != dim:
                raise ValueError("Solid body dimensionality must match domain dimensionality")

        if self.gravity is not None and len(self.gravity) != dim:
            raise ValueError("Gravity dimensionality must match domain dimensionality")

        for observer in self.observers:
            if observer.position is not None and len(observer.position) != dim:
                raise ValueError("Observer dimensionality must match domain dimensionality")
            if observer.positions is not None and any(len(pos) != dim for pos in observer.positions):
                raise ValueError("Observer dimensionality must match domain dimensionality")
        return self
