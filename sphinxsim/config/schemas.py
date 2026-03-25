"""Pydantic schemas for SPHSimulation JSON configuration."""

from __future__ import annotations

from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field, field_validator, model_serializer, model_validator


class PhysicsType(str, Enum):
    """Heuristic physics category used by the NLP mock layer."""

    FLUID = "fluid"
    SOLID = "solid"
    FSI = "fsi"


class DomainConfig(BaseModel):
    """Spatial domain geometry used by SPHSimulation."""

    lower_bound: List[float] = Field(..., min_length=2, max_length=3)
    upper_bound: List[float] = Field(..., min_length=2, max_length=3)

    @model_validator(mode="before")
    @classmethod
    def _upgrade_legacy_dimensions(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        if "dimensions" in data and ("lower_bound" not in data or "upper_bound" not in data):
            dims = data["dimensions"]
            data = dict(data)
            data["lower_bound"] = [0.0] * len(dims)
            data["upper_bound"] = dims
        return data

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

    @model_serializer(mode="plain")
    def _serialize_domain(self) -> Dict[str, Any]:
        return {
            "lower_bound": self.lower_bound,
            "upper_bound": self.upper_bound,
            "dimensions": self.dimensions,
        }


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
    fluid_bodies: List[Dict[str, Any]] = Field(..., min_length=1)
    solid_bodies: List[Dict[str, Any]] = Field(..., min_length=1)
    gravity: Optional[List[float]] = Field(default=None, min_length=2, max_length=3)
    observers: List[ObserverConfig] = Field(default_factory=list)
    solver: SolverConfig = Field(default_factory=SolverConfig)
    end_time: Optional[float] = Field(default=None, gt=0)

    @model_validator(mode="before")
    @classmethod
    def _upgrade_legacy_layout(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data

        upgraded = dict(data)

        if "fluid_bodies" not in upgraded and "fluid_blocks" in upgraded:
            upgraded["fluid_bodies"] = [
                {
                    "name": block["name"],
                    "geometry": {
                        "type": "bounding_box",
                        "lower_bound": [0.0] * len(block["dimensions"]),
                        "upper_bound": block["dimensions"],
                    },
                    "material": {
                        "type": "weakly_compressible_fluid",
                        "density": block.get("density", 1.0),
                        "sound_speed": block.get("sound_speed", 10.0),
                    },
                }
                for block in upgraded["fluid_blocks"]
            ]

        if "solid_bodies" not in upgraded and "walls" in upgraded:
            upgraded["solid_bodies"] = [
                {
                    "name": wall["name"],
                    "geometry": {
                        "type": "container_box",
                        "inner_lower_bound": [0.0] * len(wall["dimensions"]),
                        "inner_upper_bound": wall["dimensions"],
                        "thickness": wall["boundary_width"],
                    },
                    "material": {"type": "rigid_body"},
                }
                for wall in upgraded["walls"]
            ]

        return upgraded

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
                raise ValueError("Fluid block dimensionality must match domain dimensionality")

        for solid_body in self.solid_bodies:
            geom = GeometryConfig.model_validate(solid_body["geometry"])
            mat = MaterialConfig.model_validate(solid_body["material"])
            if mat.type != MaterialType.RIGID_BODY:
                raise ValueError("Solid body material type must be rigid_body")
            if geom.type != ShapeType.CONTAINER_BOX:
                raise ValueError("Solid body geometry type must be container_box")
            assert geom.inner_lower_bound is not None and geom.inner_upper_bound is not None
            if len(geom.inner_lower_bound) != dim:
                raise ValueError("Wall dimensionality must match domain dimensionality")

        if self.gravity is not None and len(self.gravity) != dim:
            raise ValueError("Gravity dimensionality must match domain dimensionality")

        for observer in self.observers:
            if observer.position is not None and len(observer.position) != dim:
                raise ValueError("Observer dimensionality must match domain dimensionality")
            if observer.positions is not None and any(len(pos) != dim for pos in observer.positions):
                raise ValueError("Observer dimensionality must match domain dimensionality")
        return self

    @property
    def fluid_blocks(self) -> List[FluidBlockConfig]:
        blocks: List[FluidBlockConfig] = []
        for fluid_body in self.fluid_bodies:
            geom = GeometryConfig.model_validate(fluid_body["geometry"])
            mat = MaterialConfig.model_validate(fluid_body["material"])
            if geom.lower_bound is None or geom.upper_bound is None:
                continue
            dims = [hi - lo for lo, hi in zip(geom.lower_bound, geom.upper_bound)]
            blocks.append(
                FluidBlockConfig(
                    name=fluid_body["name"],
                    dimensions=dims,
                    density=mat.density if mat.density is not None else 1.0,
                    sound_speed=mat.sound_speed if mat.sound_speed is not None else 10.0,
                )
            )
        return blocks

    @property
    def walls(self) -> List[WallConfig]:
        wall_list: List[WallConfig] = []
        for solid_body in self.solid_bodies:
            geom = GeometryConfig.model_validate(solid_body["geometry"])
            if geom.inner_lower_bound is None or geom.inner_upper_bound is None or geom.thickness is None:
                continue
            dims = [hi - lo for lo, hi in zip(geom.inner_lower_bound, geom.inner_upper_bound)]
            wall_list.append(
                WallConfig(
                    name=solid_body["name"],
                    dimensions=dims,
                    boundary_width=geom.thickness,
                )
            )
        return wall_list

    @model_serializer(mode="plain")
    def _serialize_simulation(self) -> Dict[str, Any]:
        return {
            "domain": self.domain.model_dump(),
            "particle_spacing": self.particle_spacing,
            "particle_boundary_buffer": self.particle_boundary_buffer,
            "fluid_bodies": self.fluid_bodies,
            "solid_bodies": self.solid_bodies,
            "gravity": self.gravity,
            "observers": [observer.model_dump() for observer in self.observers],
            "solver": self.solver.model_dump(),
            "end_time": self.end_time,
            # Keep legacy aliases for current CLI/LLM compatibility.
            "fluid_blocks": [block.model_dump() for block in self.fluid_blocks],
            "walls": [wall.model_dump() for wall in self.walls],
        }
