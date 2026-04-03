"""Pydantic schemas for SPHSimulation JSON configuration."""

from __future__ import annotations

from enum import Enum
from typing import List, Optional

from pydantic import BaseModel, Field, field_validator, model_validator


class IndexableModel(BaseModel):
    """Backward-compatible dict-style access for existing callers."""

    def __getitem__(self, key: str):
        return getattr(self, key)

    def get(self, key: str, default=None):
        return getattr(self, key, default)


class PhysicsType(str, Enum):
    """Heuristic physics category used by the NLP mock layer."""

    FLUID = "fluid"
    SOLID = "solid"
    FSI = "fsi"


class DomainConfig(IndexableModel):
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
    MULTIPOLYGON = "multipolygon"


class GeometricOperationType(str, Enum):
    UNION = "union"
    INTERSECTION = "intersection"
    SUBTRACTION = "subtraction"


class PrimitiveGeometryConfig(IndexableModel):
    """Primitive geometry blocks used directly or inside multipolygon."""

    type: ShapeType
    lower_bound: Optional[List[float]] = Field(default=None, min_length=2, max_length=3)
    upper_bound: Optional[List[float]] = Field(default=None, min_length=2, max_length=3)
    inner_lower_bound: Optional[List[float]] = Field(default=None, min_length=2, max_length=3)
    inner_upper_bound: Optional[List[float]] = Field(default=None, min_length=2, max_length=3)
    thickness: Optional[float] = Field(default=None, gt=0)

    @model_validator(mode="after")
    def _shape_fields_match_type(self) -> "PrimitiveGeometryConfig":
        if self.type == ShapeType.MULTIPOLYGON:
            raise ValueError("PrimitiveGeometryConfig does not support multipolygon type")

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


class MultiPolygonEntryConfig(PrimitiveGeometryConfig):
    """Single polygon entry within a multipolygon geometry."""

    operation: GeometricOperationType


class GeometryConfig(IndexableModel):
    """Body geometry accepted by SPHSimulation::addShape()."""

    type: ShapeType
    lower_bound: Optional[List[float]] = Field(default=None, min_length=2, max_length=3)
    upper_bound: Optional[List[float]] = Field(default=None, min_length=2, max_length=3)
    inner_lower_bound: Optional[List[float]] = Field(default=None, min_length=2, max_length=3)
    inner_upper_bound: Optional[List[float]] = Field(default=None, min_length=2, max_length=3)
    thickness: Optional[float] = Field(default=None, gt=0)
    polygons: Optional[List[MultiPolygonEntryConfig]] = Field(default=None, min_length=1)

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

        if self.type == ShapeType.MULTIPOLYGON:
            if self.polygons is None or len(self.polygons) == 0:
                raise ValueError("multipolygon geometry requires non-empty polygons")
        return self


class MaterialType(str, Enum):
    WEAKLY_COMPRESSIBLE_FLUID = "weakly_compressible_fluid"
    RIGID_BODY = "rigid_body"


class MaterialConfig(IndexableModel):
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


class FluidBodyConfig(IndexableModel):
    """Fluid body payload accepted by SPHSimulation::addFluidBody()."""

    name: str = Field(..., min_length=1)
    geometry: GeometryConfig
    material: MaterialConfig
    particle_reserve_factor: Optional[float] = Field(default=None, gt=0)

    @model_validator(mode="after")
    def _fluid_body_constraints(self) -> "FluidBodyConfig":
        if self.material.type != MaterialType.WEAKLY_COMPRESSIBLE_FLUID:
            raise ValueError("Fluid body material type must be weakly_compressible_fluid")
        return self


class SolidBodyConfig(IndexableModel):
    """Solid body payload accepted by SPHSimulation::addSolidBody()."""

    name: str = Field(..., min_length=1)
    geometry: GeometryConfig
    material: MaterialConfig

    @model_validator(mode="after")
    def _solid_body_constraints(self) -> "SolidBodyConfig":
        if self.material.type != MaterialType.RIGID_BODY:
            raise ValueError("Solid body material type must be rigid_body")
        return self


class ObserverConfig(IndexableModel):
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


class SolverConfig(IndexableModel):
    """Numerical solver toggles accepted by SPHSimulation."""

    dual_time_stepping: bool = False
    free_surface_correction: bool = False


class FluidBoundaryConditionType(str, Enum):
    EMITTER = "emitter"


class FluidBoundaryConditionConfig(IndexableModel):
    """Fluid boundary condition payload currently supported by SPHSimulation."""

    body_name: str = Field(..., min_length=1)
    type: FluidBoundaryConditionType
    alignment_axis: int = Field(..., ge=0)
    half_size: List[float] = Field(..., min_length=2, max_length=3)
    translation: List[float] = Field(..., min_length=2, max_length=3)
    rotation_angle: float
    rotation_axis: Optional[List[float]] = Field(default=None, min_length=3, max_length=3)
    inflow_speed: float = Field(..., gt=0)


class SimulationConfig(BaseModel):
    """Top-level JSON payload accepted by SPHSimulation.loadConfig()."""

    domain: DomainConfig
    particle_spacing: float = Field(..., gt=0, description="Reference particle spacing")
    particle_boundary_buffer: int = Field(
        ..., gt=0, description="Number of particle spacings used for boundary padding"
    )
    particle_sort_frequency: Optional[int] = Field(default=None, gt=0)
    fluid_bodies: List[FluidBodyConfig] = Field(..., min_length=1)
    solid_bodies: List[SolidBodyConfig] = Field(..., min_length=1)
    fluid_boundary_conditions: List[FluidBoundaryConditionConfig] = Field(default_factory=list)
    gravity: Optional[List[float]] = Field(default=None, min_length=2, max_length=3)
    observers: List[ObserverConfig] = Field(default_factory=list)
    solver: SolverConfig = Field(default_factory=SolverConfig)
    end_time: Optional[float] = Field(default=None, gt=0)

    @staticmethod
    def _validate_geometry_dimension(geometry: GeometryConfig, dim: int) -> None:
        if geometry.type == ShapeType.BOUNDING_BOX:
            assert geometry.lower_bound is not None
            if len(geometry.lower_bound) != dim:
                raise ValueError("Body geometry dimensionality must match domain dimensionality")
            return

        if geometry.type == ShapeType.CONTAINER_BOX:
            assert geometry.inner_lower_bound is not None
            if len(geometry.inner_lower_bound) != dim:
                raise ValueError("Body geometry dimensionality must match domain dimensionality")
            return

        if geometry.type == ShapeType.MULTIPOLYGON:
            if dim != 2:
                raise ValueError("multipolygon geometry is only supported for 2D domain")
            assert geometry.polygons is not None
            for polygon in geometry.polygons:
                if polygon.type == ShapeType.BOUNDING_BOX:
                    assert polygon.lower_bound is not None
                    if len(polygon.lower_bound) != dim:
                        raise ValueError("multipolygon entry dimensionality must match domain dimensionality")
                elif polygon.type == ShapeType.CONTAINER_BOX:
                    assert polygon.inner_lower_bound is not None
                    if len(polygon.inner_lower_bound) != dim:
                        raise ValueError("multipolygon entry dimensionality must match domain dimensionality")
            return

    @model_validator(mode="after")
    def _dimensionality_consistent(self) -> "SimulationConfig":
        dim = len(self.domain.lower_bound)

        for fluid_body in self.fluid_bodies:
            self._validate_geometry_dimension(fluid_body.geometry, dim)

        for solid_body in self.solid_bodies:
            self._validate_geometry_dimension(solid_body.geometry, dim)

        fluid_names = {body.name for body in self.fluid_bodies}
        for condition in self.fluid_boundary_conditions:
            if condition.body_name not in fluid_names:
                raise ValueError("fluid_boundary_conditions body_name must reference an existing fluid body")
            if len(condition.half_size) != dim or len(condition.translation) != dim:
                raise ValueError("Fluid boundary condition dimensionality must match domain dimensionality")
            if condition.alignment_axis >= dim:
                raise ValueError("Fluid boundary condition alignment_axis out of range for domain dimensionality")
            if dim == 3:
                if condition.rotation_axis is None:
                    raise ValueError("3D fluid boundary condition requires rotation_axis")
            elif condition.rotation_axis is not None:
                if len(condition.rotation_axis) != 3:
                    raise ValueError("rotation_axis must have 3 components when provided")

        if self.gravity is not None and len(self.gravity) != dim:
            raise ValueError("Gravity dimensionality must match domain dimensionality")

        for observer in self.observers:
            if observer.position is not None and len(observer.position) != dim:
                raise ValueError("Observer dimensionality must match domain dimensionality")
            if observer.positions is not None and any(len(pos) != dim for pos in observer.positions):
                raise ValueError("Observer dimensionality must match domain dimensionality")
        return self
