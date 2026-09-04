"""Pydantic schemas for the builder-centric SPHSimulation JSON configuration."""

from __future__ import annotations

from enum import Enum
import math
import warnings
from typing import Any, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class PhysicalCorrectionWarning(UserWarning):
    """A physically unambiguous input correction applied during validation."""


class SimulationType(str, Enum):
    FLUID_DYNAMICS = "fluid_dynamics"
    CONTINUUM_DYNAMICS = "continuum_dynamics"


class LogLevel(str, Enum):
    TRACE = "trace"
    DEBUG = "debug"
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"
    OFF = "off"


class CharacteristicDimensionName(str, Enum):
    LENGTH = "Length"
    MASS = "Mass"
    TIME = "Time"
    TEMPERATURE = "Temperature"
    ELECTRIC_CURRENT = "ElectricCurrent"
    AMOUNT_OF_SUBSTANCE = "AmountOfSubstance"
    LUMINOUS_INTENSITY = "LuminousIntensity"
    DENSITY = "Density"
    PRESSURE = "Pressure"
    STRESS = "Stress"
    VISCOSITY = "Viscosity"
    VELOCITY = "Velocity"
    SPEED = "Speed"
    ANGULAR_VELOCITY = "AngularVelocity"
    GRAVITY = "Gravity"
    ACCELERATION = "Acceleration"
    DIMENSIONLESS = "Dimensionless"
    NORMAL_DIRECTION = "NormalDirection"


class GeometricOperationType(str, Enum):
    UNION = "union"
    INTERSECTION = "intersection"
    SUBTRACTION = "subtraction"


class BodyShapeType(str, Enum):
    BOX = "box"
    BOUNDING_BOX = "bounding_box"
    EXPANDED_BOX = "expanded_box"
    COMPLEX_SHAPE = "complex_shape"
    MULTIPOLYGON = "multipolygon"
    CYLINDER = "cylinder"
    TRIANGLE_MESH = "triangle_mesh"


class MultiPolygonPrimitiveType(str, Enum):
    BOX = "box"
    BOUNDING_BOX = "bounding_box"
    CONTAINER_BOX = "container_box"
    CIRCLE = "circle"
    TRIANGLE = "triangle"
    CLOCKWISE_POINTS = "clockwise_points"
    DATA_FILE = "data_file"


class OrientedBoxType(str, Enum):
    BOUNDARY = "boundary"
    REGION = "region"


class MaterialType(str, Enum):
    WEAKLY_COMPRESSIBLE_FLUID = "weakly_compressible_fluid"
    WEAKLY_COMPRESSIBLE_MIXTURE = "weakly_compressible_mixture"
    WEAKLY_COMPRESSIBLE_MULTI_SPECIES = "weakly_compressible_multi_species"
    WEAKLY_COMPRESSIBLE_MULTI_PHASE = "weakly_compressible_multi_phase"
    RIGID_BODY = "rigid_body"
    J2_PLASTICITY = "j2_plasticity"
    PLASTIC_CONTINUUM = "plastic_continuum"
    GENERAL_CONTINUUM = "general_continuum"
    COMPOSITE_SOLID = "composite_solid"


class FluidBoundaryConditionType(str, Enum):
    EMITTER = "emitter"
    BI_DIRECTIONAL = "bi_directional"
    FREE_STREAM = "free_stream"


class BodyConstraintType(str, Enum):
    FIXED = "fixed"
    SIMBODY = "simbody"


class CharacteristicDimensionConfig(BaseModel):
    value: float
    name: CharacteristicDimensionName
    hint: str = Field(..., min_length=1)


class DomainConfig(BaseModel):
    lower_bound: List[float] = Field(..., min_length=2, max_length=3)
    upper_bound: List[float] = Field(..., min_length=2, max_length=3)

    @model_validator(mode="after")
    def _valid_bounds(self) -> "DomainConfig":
        if len(self.lower_bound) != len(self.upper_bound):
            raise ValueError("system_domain lower_bound and upper_bound dimensionality must match")
        for lo, hi in zip(self.lower_bound, self.upper_bound):
            if hi <= lo:
                raise ValueError("system_domain upper_bound must be greater than lower_bound")
        return self


class GlobalResolutionConfig(BaseModel):
    particle_spacing: Optional[float] = Field(default=None, gt=0)
    characteristic_length_particles: Optional[int] = Field(default=None, gt=0)

    @model_validator(mode="after")
    def _requires_one_mode(self) -> "GlobalResolutionConfig":
        if self.particle_spacing is None and self.characteristic_length_particles is None:
            raise ValueError("global_resolution requires particle_spacing or characteristic_length_particles")
        return self


class TransformConfig(BaseModel):
    translation: List[float] = Field(..., min_length=2, max_length=3)
    rotation_angle: float
    rotation_axis: Optional[List[float]] = Field(default=None, min_length=3, max_length=3)

    @model_validator(mode="after")
    def _validate_rotation_axis_requirements(self) -> "TransformConfig":
        # 3D transforms require a rotation axis in C++ jsonToTransform().
        if len(self.translation) == 3 and self.rotation_axis is None:
            raise ValueError("3D transform requires rotation_axis")
        return self


class PrimitiveConfig(BaseModel):
    name: str = Field(..., min_length=1)
    type: Literal["box"]
    half_size: List[float] = Field(..., min_length=2, max_length=3)
    transform: TransformConfig


class MultiPolygonEntryConfig(BaseModel):
    operation: GeometricOperationType
    type: MultiPolygonPrimitiveType
    primitive: Optional[str] = None
    half_size: Optional[List[float]] = Field(default=None, min_length=2, max_length=3)
    transform: Optional[TransformConfig] = None
    center: Optional[List[float]] = Field(default=None, min_length=2, max_length=3)
    radius: Optional[float] = Field(default=None, gt=0)
    resolution: Optional[int] = Field(default=None, gt=0)
    points: Optional[List[List[float]]] = None
    lower_bound: Optional[List[float]] = Field(default=None, min_length=2, max_length=3)
    upper_bound: Optional[List[float]] = Field(default=None, min_length=2, max_length=3)
    inner_lower_bound: Optional[List[float]] = Field(default=None, min_length=2, max_length=3)
    inner_upper_bound: Optional[List[float]] = Field(default=None, min_length=2, max_length=3)
    thickness: Optional[float] = Field(default=None, gt=0)
    file_name: Optional[str] = None

    @model_validator(mode="after")
    def _validate_shape_payload(self) -> "MultiPolygonEntryConfig":
        if self.type == MultiPolygonPrimitiveType.BOX:
            if not self.primitive and (self.half_size is None or self.transform is None):
                raise ValueError("multipolygon box requires primitive or half_size and transform")
        elif self.type == MultiPolygonPrimitiveType.BOUNDING_BOX:
            if self.lower_bound is None or self.upper_bound is None:
                raise ValueError("multipolygon bounding_box requires lower_bound and upper_bound")
            if len(self.lower_bound) != len(self.upper_bound):
                raise ValueError("multipolygon bounding_box dimensionality must match")
        elif self.type == MultiPolygonPrimitiveType.CONTAINER_BOX:
            if self.inner_lower_bound is None or self.inner_upper_bound is None or self.thickness is None:
                raise ValueError(
                    "multipolygon container_box requires inner_lower_bound, inner_upper_bound and thickness"
                )
            if len(self.inner_lower_bound) != len(self.inner_upper_bound):
                raise ValueError("multipolygon container_box dimensionality must match")
        elif self.type == MultiPolygonPrimitiveType.CIRCLE:
            if self.center is None or self.radius is None or self.resolution is None:
                raise ValueError("multipolygon circle requires center, radius and resolution")
        elif self.type == MultiPolygonPrimitiveType.TRIANGLE:
            if self.half_size is None or self.transform is None:
                raise ValueError("multipolygon triangle requires half_size and transform")
        elif self.type == MultiPolygonPrimitiveType.CLOCKWISE_POINTS:
            if not self.points:
                raise ValueError("multipolygon clockwise_points requires points")
            if self.points[0] != self.points[-1]:
                raise ValueError("multipolygon clockwise_points must repeat the first point as the last point")
        elif self.type == MultiPolygonPrimitiveType.DATA_FILE:
            if not self.file_name:
                raise ValueError("multipolygon data_file requires file_name")
        return self


class ShapeConfig(BaseModel):
    name: str = Field(..., min_length=1)
    type: BodyShapeType
    primitive: Optional[str] = None

    lower_bound: Optional[List[float]] = Field(default=None, min_length=2, max_length=3)
    upper_bound: Optional[List[float]] = Field(default=None, min_length=2, max_length=3)

    half_size: Optional[List[float]] = Field(default=None, min_length=2, max_length=3)
    transform: Optional[TransformConfig] = None

    original: Optional[str] = None
    expansion: Optional[float] = Field(default=None, gt=0)

    sub_shapes: Optional[List[str]] = None
    operations: Optional[List[GeometricOperationType]] = None

    polygons: Optional[List[MultiPolygonEntryConfig]] = None

    radius: Optional[float] = Field(default=None, gt=0)
    half_height: Optional[float] = Field(default=None, gt=0)

    file_name: Optional[str] = None
    translation: Optional[List[float]] = Field(default=None, min_length=3, max_length=3)
    scale: Optional[float] = Field(default=None, gt=0)

    @model_validator(mode="after")
    def _validate_type_fields(self) -> "ShapeConfig":
        if self.type == BodyShapeType.BOX:
            if not self.primitive and (self.half_size is None or self.transform is None):
                raise ValueError("box shape requires primitive or half_size and transform")
            return self

        if self.type == BodyShapeType.BOUNDING_BOX:
            if self.lower_bound is None or self.upper_bound is None:
                raise ValueError("bounding_box shape requires lower_bound and upper_bound")
            if len(self.lower_bound) != len(self.upper_bound):
                raise ValueError("bounding_box dimensionality must match")
            return self

        if self.type == BodyShapeType.EXPANDED_BOX:
            if not self.original or self.expansion is None:
                raise ValueError("expanded_box shape requires original and expansion")
            return self

        if self.type == BodyShapeType.COMPLEX_SHAPE:
            if not self.sub_shapes or not self.operations:
                raise ValueError("complex_shape requires sub_shapes and operations")
            if len(self.sub_shapes) != len(self.operations):
                raise ValueError("complex_shape sub_shapes and operations must have same length")
            if any(op == GeometricOperationType.INTERSECTION for op in self.operations):
                raise ValueError("complex_shape operations only support union and subtraction")
            return self

        if self.type == BodyShapeType.MULTIPOLYGON:
            if not self.polygons:
                raise ValueError("multipolygon shape requires non-empty polygons")
            return self

        if self.type == BodyShapeType.CYLINDER:
            if self.radius is None or self.half_height is None or self.transform is None:
                raise ValueError("cylinder shape requires radius, half_height and transform")
            return self

        if self.type == BodyShapeType.TRIANGLE_MESH:
            if not self.file_name:
                raise ValueError("triangle_mesh shape requires file_name")
            return self

        return self


class OrientedBoxConfig(BaseModel):
    name: str = Field(..., min_length=1)
    type: OrientedBoxType
    primitive: Optional[str] = None

    center: Optional[List[float]] = Field(default=None, min_length=2, max_length=3)
    normal: Optional[List[float]] = Field(default=None, min_length=2, max_length=3)
    radius: Optional[float] = Field(default=None, gt=0)

    half_size: Optional[List[float]] = Field(default=None, min_length=2, max_length=3)
    transform: Optional[TransformConfig] = None

    @model_validator(mode="after")
    def _validate_oriented_box(self) -> "OrientedBoxConfig":
        if self.type == OrientedBoxType.BOUNDARY:
            if self.center is None or self.normal is None or self.radius is None:
                raise ValueError("boundary oriented_box requires center, normal and radius")
        elif self.type == OrientedBoxType.REGION:
            if not self.primitive and (self.half_size is None or self.transform is None):
                raise ValueError("region oriented_box requires primitive or half_size and transform")
        return self


class GeometriesConfig(BaseModel):
    system_domain: Optional[DomainConfig] = None
    global_resolution: GlobalResolutionConfig
    primitives: List[PrimitiveConfig] = Field(default_factory=list)
    shapes: List[ShapeConfig] = Field(..., min_length=1)
    oriented_boxes: List[OrientedBoxConfig] = Field(default_factory=list)

    @model_validator(mode="after")
    def _validate_shape_references(self) -> "GeometriesConfig":
        """Ensure shape definitions are unique and only reference earlier shapes."""
        defined_shape_names: set[str] = set()
        defined_primitive_names: set[str] = set()

        for primitive in self.primitives:
            if primitive.name in defined_primitive_names:
                raise ValueError(
                    f"geometries.primitives contains duplicate primitive name '{primitive.name}'"
                )
            defined_primitive_names.add(primitive.name)

        for shape in self.shapes:
            if shape.name in defined_shape_names:
                raise ValueError(
                    f"geometries.shapes contains duplicate shape name '{shape.name}'"
                )

            if shape.primitive is not None and shape.primitive not in defined_primitive_names:
                raise ValueError(
                    f"shape '{shape.name}' references unknown primitive '{shape.primitive}'"
                )

            if shape.type == BodyShapeType.EXPANDED_BOX:
                if shape.original not in defined_shape_names:
                    raise ValueError(
                        f"expanded_box shape '{shape.name}' must reference a previously defined shape in original"
                    )

            if shape.type == BodyShapeType.COMPLEX_SHAPE:
                for sub_shape in shape.sub_shapes or []:
                    if sub_shape not in defined_shape_names:
                        raise ValueError(
                            f"complex_shape '{shape.name}' has sub_shape '{sub_shape}' that is not previously defined"
                        )

            if shape.type == BodyShapeType.MULTIPOLYGON:
                for polygon in shape.polygons or []:
                    if polygon.primitive is not None and polygon.primitive not in defined_primitive_names:
                        raise ValueError(
                            f"multipolygon shape '{shape.name}' references unknown primitive '{polygon.primitive}'"
                        )

            defined_shape_names.add(shape.name)

        for oriented_box in self.oriented_boxes:
            if oriented_box.primitive is not None and oriented_box.primitive not in defined_primitive_names:
                raise ValueError(
                    f"oriented_box '{oriented_box.name}' references unknown primitive '{oriented_box.primitive}'"
                )

        return self


class RelaxationBodyConfig(BaseModel):
    model_config = ConfigDict(extra="allow")

    level_set: Optional[dict] = None
    dependent_bodies: List[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _warn_unknown_fields(self) -> "RelaxationBodyConfig":
        extra = getattr(self, "__pydantic_extra__", None)
        if extra:
            warnings.warn(
                "particle_generation.relaxation contains unknown keys that are preserved: "
                + ", ".join(sorted(extra.keys())),
                UserWarning,
                stacklevel=2,
            )
        return self


class ParticleGenerationBodyConfig(BaseModel):
    model_config = ConfigDict(extra="allow")

    name: str = Field(..., min_length=1)
    blockers: List[str] = Field(default_factory=list)
    box_shape_inserts: List[str] = Field(default_factory=list)
    cylinder_shape_inserts: List[str] = Field(default_factory=list)
    solid_body: Optional[dict] = None
    relaxation: Optional[RelaxationBodyConfig] = None

    @model_validator(mode="after")
    def _warn_unknown_fields(self) -> "ParticleGenerationBodyConfig":
        extra = getattr(self, "__pydantic_extra__", None)
        if extra:
            warnings.warn(
                "particle_generation.settings.bodies contains unknown keys that are preserved: "
                + ", ".join(sorted(extra.keys())),
                UserWarning,
                stacklevel=2,
            )
        return self


class RelaxationParametersConfig(BaseModel):
    total_iterations: int = Field(default=1000, gt=0)


class RelaxationConstraintConfig(BaseModel):
    body_name: str = Field(..., min_length=1)
    oriented_box: str = Field(..., min_length=1)
    type: str = Field(..., min_length=1)


class ParticleGenerationSettingsConfig(BaseModel):
    model_config = ConfigDict(extra="allow")

    bodies: List[ParticleGenerationBodyConfig] = Field(..., min_length=1)
    relaxation_constraints: List[RelaxationConstraintConfig] = Field(default_factory=list)
    relaxation_parameters: RelaxationParametersConfig = Field(default_factory=RelaxationParametersConfig)

    @model_validator(mode="after")
    def _warn_unknown_fields(self) -> "ParticleGenerationSettingsConfig":
        extra = getattr(self, "__pydantic_extra__", None)
        if extra:
            warnings.warn(
                "particle_generation.settings contains unknown keys that are preserved: "
                + ", ".join(sorted(extra.keys())),
                UserWarning,
                stacklevel=2,
            )
        return self


class ParticleGenerationConfig(BaseModel):
    build_and_run: bool
    settings: Optional[ParticleGenerationSettingsConfig] = None

    @model_validator(mode="after")
    def _validate_settings(self) -> "ParticleGenerationConfig":
        if self.build_and_run and self.settings is None:
            raise ValueError("particle_generation.settings is required when build_and_run is true")
        return self


class VariableConfig(BaseModel):
    real_type: Optional[str] = None
    vector_type: Optional[str] = None

    @model_validator(mode="after")
    def _exactly_one(self) -> "VariableConfig":
        if (self.real_type is None) == (self.vector_type is None):
            raise ValueError("observer variable requires exactly one of real_type or vector_type")
        return self


class ObserverConfig(BaseModel):
    name: str = Field(..., min_length=1)
    observed_body: str = Field(..., min_length=1)
    variable: VariableConfig
    positions: List[List[float]] = Field(default_factory=list)


class StateRecordingVariableConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    int_type: Optional[List[str]] = None
    real_type: Optional[List[str]] = None
    vector_type: Optional[List[str]] = None

    @model_validator(mode="after")
    def _at_least_one(self) -> "StateRecordingVariableConfig":
        if self.int_type is None and self.real_type is None and self.vector_type is None:
            raise ValueError(
                "extra_state_recording variables require at least one of int_type, real_type, vector_type"
            )
        return self


class ExtraStateRecordingConfig(BaseModel):
    name: str = Field(..., min_length=1)
    variables: List[StateRecordingVariableConfig] = Field(..., min_length=1)


class EnergyRecordingConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., min_length=1)
    body: str = Field(..., min_length=1)
    quantity: Literal["TotalMechanicalEnergy"] = "TotalMechanicalEnergy"
    gravity: Optional[List[float]] = Field(default=None, min_length=2, max_length=3)


class ViscosityConfig(BaseModel):
    Reynolds_number: float = Field(..., gt=0)


class MixtureSpeciesConfig(BaseModel):
    name: str = Field(..., min_length=1)
    density: float = Field(..., gt=0)


class ThermalBoundaryType(str, Enum):
    DIRICHLET = "Dirichlet"
    NEUMANN = "Neumann"
    ROBIN = "Robin"


class ThermalPropertiesConfig(BaseModel):
    thermal_conductivity: Optional[float] = Field(default=None, gt=0)
    volumetric_heat_capacity: Optional[float] = Field(default=None, gt=0)
    thermal_boundary: Optional[ThermalBoundaryType] = None

    @model_validator(mode="after")
    def _validate_thermal_mode(self) -> "ThermalPropertiesConfig":
        if self.thermal_boundary is not None:
            return self

        if self.thermal_conductivity is None or self.volumetric_heat_capacity is None:
            raise ValueError(
                "thermal_properties requires thermal_boundary or both thermal_conductivity and volumetric_heat_capacity"
            )
        return self


class MultiSpeciesPhaseMaterialConfig(BaseModel):
    name: str = Field(..., min_length=1)
    species: List[MixtureSpeciesConfig] = Field(..., min_length=1)


class MaterialIdRegionEntryConfig(BaseModel):
    shape: str = Field(..., min_length=1)
    id: int


class MaterialIdRegionsConfig(BaseModel):
    regions: List[MaterialIdRegionEntryConfig] = Field(..., min_length=1)
    default_id: int


class ActiveStrainConfig(BaseModel):
    center: List[float] = Field(..., min_length=2, max_length=3)
    region_span: float = Field(..., gt=0)
    core_thickness: float = Field(..., ge=0)
    amplitude: float
    frequency: float = Field(..., gt=0)
    wavelength_factor: float = Field(..., gt=0)
    start_time: float = Field(..., gt=0)


class MaterialConfig(BaseModel):
    type: MaterialType

    density: Optional[float] = Field(default=None, gt=0)
    species: List[MixtureSpeciesConfig] = Field(default_factory=list)
    pure_phases: List[MixtureSpeciesConfig] = Field(default_factory=list)
    multi_species_phases: List[MultiSpeciesPhaseMaterialConfig] = Field(default_factory=list)
    sound_speed: Optional[float] = Field(default=None, gt=0)
    viscosity: Optional[float | ViscosityConfig] = None
    thermal_properties: Optional[ThermalPropertiesConfig] = None

    youngs_modulus: Optional[float] = Field(default=None, gt=0)
    poisson_ratio: Optional[float] = None
    yield_stress: Optional[float] = Field(default=None, gt=0)
    hardening_modulus: Optional[float] = Field(default=None, gt=0)
    friction_angle: Optional[float] = Field(default=None, ge=0)
    cohesion: Optional[float] = Field(default=None, ge=0)
    dilatancy_angle: Optional[float] = Field(default=None, ge=0)
    youngs_modulus_active: Optional[float] = Field(default=None, gt=0)
    youngs_modulus_1: Optional[float] = Field(default=None, gt=0)
    youngs_modulus_2: Optional[float] = Field(default=None, gt=0)
    material_id_regions: Optional[MaterialIdRegionsConfig] = None
    active_strain: Optional[ActiveStrainConfig] = None

    @field_validator("friction_angle", "dilatancy_angle", mode="before")
    @classmethod
    def _normalize_angle_to_radians(cls, value: object, info: object) -> object:
        if value is None or isinstance(value, bool):
            return value
        try:
            numeric = float(value)
        except (TypeError, ValueError):
            return value

        if math.pi / 2 < numeric <= 90.0:
            corrected = math.radians(numeric)
            field_name = getattr(info, "field_name", "material_angle")
            warnings.warn(
                f"Corrected {field_name} from {numeric:g} degrees to "
                f"{corrected:.12g} radians; SPHinXsim JSON stores material angles in radians.",
                PhysicalCorrectionWarning,
                stacklevel=2,
            )
            return corrected
        return value

    @model_validator(mode="after")
    def _validate_material_by_type(self) -> "MaterialConfig":
        if self.type == MaterialType.WEAKLY_COMPRESSIBLE_FLUID:
            if self.density is None:
                raise ValueError("weakly_compressible_fluid requires density")
            if self.sound_speed is not None:
                raise ValueError(
                    "weakly_compressible_fluid does not support sound_speed; "
                    "use solver_parameters.fluid_dynamics.max_velocity_factor"
                )
        elif self.type == MaterialType.WEAKLY_COMPRESSIBLE_MIXTURE:
            raise ValueError(
                "weakly_compressible_mixture is not supported by the current C++ parser; "
                "use weakly_compressible_multi_species"
            )
        elif self.type in (
            MaterialType.WEAKLY_COMPRESSIBLE_MULTI_SPECIES,
        ):
            if not self.species:
                raise ValueError(f"{self.type.value} requires species")
            if self.sound_speed is not None:
                raise ValueError(
                    f"{self.type.value} does not support sound_speed; "
                    "use solver_parameters.fluid_dynamics.max_velocity_factor"
                )
        elif self.type == MaterialType.WEAKLY_COMPRESSIBLE_MULTI_PHASE:
            if not self.pure_phases:
                raise ValueError("weakly_compressible_multi_phase requires pure_phases")
            if self.sound_speed is not None:
                raise ValueError(
                    "weakly_compressible_multi_phase does not support sound_speed; "
                    "use solver_parameters.fluid_dynamics.max_velocity_factor"
                )
        elif self.type == MaterialType.RIGID_BODY:
            if self.sound_speed is not None:
                raise ValueError("rigid_body does not support sound_speed")
        elif self.type == MaterialType.J2_PLASTICITY:
            required = (
                self.density,
                self.sound_speed,
                self.youngs_modulus,
                self.poisson_ratio,
                self.yield_stress,
                self.hardening_modulus,
            )
            if any(v is None for v in required):
                raise ValueError(
                    "j2_plasticity requires density, sound_speed, youngs_modulus, "
                    "poisson_ratio, yield_stress and hardening_modulus"
                )
        elif self.type == MaterialType.PLASTIC_CONTINUUM:
            required = (
                self.density,
                self.youngs_modulus,
                self.poisson_ratio,
                self.friction_angle,
            )
            if any(v is None for v in required):
                raise ValueError(
                    "plastic_continuum requires density, youngs_modulus, poisson_ratio "
                    "and friction_angle"
                )
            assert self.poisson_ratio is not None
            assert self.friction_angle is not None
            if not 0.0 <= self.poisson_ratio < 0.5:
                raise ValueError("plastic_continuum requires 0 <= poisson_ratio < 0.5")
            if not 0.0 <= self.friction_angle < math.pi / 2:
                raise ValueError(
                    "plastic_continuum requires friction_angle in radians with "
                    "0 <= friction_angle < pi/2"
                )
            if self.dilatancy_angle is not None:
                if not 0.0 <= self.dilatancy_angle < math.pi / 2:
                    raise ValueError(
                        "plastic_continuum requires dilatancy_angle in radians with "
                        "0 <= dilatancy_angle < pi/2"
                    )
                if self.dilatancy_angle > self.friction_angle:
                    raise ValueError(
                        "plastic_continuum requires dilatancy_angle <= friction_angle"
                    )
        elif self.type == MaterialType.GENERAL_CONTINUUM:
            required = (self.density, self.sound_speed, self.youngs_modulus, self.poisson_ratio)
            if any(v is None for v in required):
                raise ValueError(
                    "general_continuum requires density, sound_speed, youngs_modulus and poisson_ratio"
                )
        elif self.type == MaterialType.COMPOSITE_SOLID:
            required = (
                self.density,
                self.poisson_ratio,
                self.youngs_modulus_active,
                self.youngs_modulus_1,
                self.youngs_modulus_2,
            )
            if any(v is None for v in required):
                raise ValueError(
                    "composite_solid requires density, poisson_ratio, youngs_modulus_active, "
                    "youngs_modulus_1 and youngs_modulus_2"
                )
            assert self.poisson_ratio is not None
            if not 0.0 <= self.poisson_ratio < 0.5:
                raise ValueError("composite_solid requires 0 <= poisson_ratio < 0.5")
        return self


class FluidBodyConfig(BaseModel):
    name: str = Field(..., min_length=1)
    material: MaterialConfig
    particle_reserve_factor: Optional[float] = Field(default=None, gt=0)

    @model_validator(mode="after")
    def _material_type(self) -> "FluidBodyConfig":
        if self.material.type not in (
            MaterialType.WEAKLY_COMPRESSIBLE_FLUID,
            MaterialType.WEAKLY_COMPRESSIBLE_MULTI_SPECIES,
            MaterialType.WEAKLY_COMPRESSIBLE_MULTI_PHASE,
        ):
            raise ValueError(
                "fluid body material type must be weakly_compressible_fluid, "
                "weakly_compressible_multi_species or weakly_compressible_multi_phase"
            )
        return self


class SolidBodyConfig(BaseModel):
    name: str = Field(..., min_length=1)
    material: MaterialConfig

    @model_validator(mode="after")
    def _material_type(self) -> "SolidBodyConfig":
        if self.material.type not in (MaterialType.RIGID_BODY, MaterialType.COMPOSITE_SOLID):
            raise ValueError("solid body material type must be rigid_body or composite_solid")
        return self


class ContinuumBodyConfig(BaseModel):
    name: str = Field(..., min_length=1)
    material: MaterialConfig

    @model_validator(mode="after")
    def _material_type(self) -> "ContinuumBodyConfig":
        if self.material.type not in (
            MaterialType.J2_PLASTICITY,
            MaterialType.PLASTIC_CONTINUUM,
            MaterialType.GENERAL_CONTINUUM,
        ):
            raise ValueError(
                "continuum body material type must be j2_plasticity, plastic_continuum or general_continuum"
            )
        return self


class FluidBoundaryConditionScheduleConfig(BaseModel):
    switch_on_time: float = Field(..., ge=0)
    duration: Optional[float] = Field(default=None, gt=0)


class MultiSpeciesPhaseBoundaryConfig(BaseModel):
    phase_name: str = Field(..., min_length=1)
    mass_fractions: List[float] = Field(..., min_length=1)

    @model_validator(mode="after")
    def _validate_mass_fractions(self) -> "MultiSpeciesPhaseBoundaryConfig":
        if any(fraction < 0.0 or fraction > 1.0 for fraction in self.mass_fractions):
            raise ValueError("multi_species_phases mass_fractions values must be in [0, 1]")
        if abs(sum(self.mass_fractions) - 1.0) > 1.0e-6:
            raise ValueError("multi_species_phases mass_fractions must sum to 1.0")
        return self


class FluidBoundaryConditionConfig(BaseModel):
    body_name: str = Field(..., min_length=1)
    oriented_box: str = Field(..., min_length=1)
    type: FluidBoundaryConditionType
    inflow_speed: Optional[float] = Field(default=None, gt=0)
    pressure: Optional[float] = None
    mass_fractions: Optional[List[float]] = None
    multi_species_phases: Optional[List[MultiSpeciesPhaseBoundaryConfig]] = None
    volume_fractions: Optional[List[float]] = None
    on_schedule: Optional[FluidBoundaryConditionScheduleConfig] = None
    buffer_box: Optional[str] = Field(default=None, min_length=1)
    disposer_box: Optional[str] = Field(default=None, min_length=1)
    target_speed: Optional[float] = Field(default=None, ge=0)
    t_ref: Optional[float] = Field(default=None, gt=0)

    @model_validator(mode="after")
    def _type_specific_requirements(self) -> "FluidBoundaryConditionConfig":
        if self.type == FluidBoundaryConditionType.FREE_STREAM and (
            self.buffer_box is None
            or self.disposer_box is None
            or self.target_speed is None
            or self.t_ref is None
        ):
            raise ValueError("free_stream boundary condition requires buffer_box, disposer_box, target_speed and t_ref")
        if self.type == FluidBoundaryConditionType.EMITTER and self.inflow_speed is None:
            raise ValueError("emitter boundary condition requires inflow_speed")
        if self.type == FluidBoundaryConditionType.BI_DIRECTIONAL and self.pressure is None:
            raise ValueError("bi_directional boundary condition requires pressure")
        if self.mass_fractions is not None:
            if self.type != FluidBoundaryConditionType.BI_DIRECTIONAL:
                raise ValueError("mass_fractions are only supported for bi_directional boundary conditions")
            if not self.mass_fractions:
                raise ValueError("mass_fractions must be non-empty when provided")
            if any(fraction < 0.0 or fraction > 1.0 for fraction in self.mass_fractions):
                raise ValueError("mass_fractions values must be in [0, 1]")
            if abs(sum(self.mass_fractions) - 1.0) > 1.0e-6:
                raise ValueError("mass_fractions must sum to 1.0")
        if self.multi_species_phases is not None:
            if self.type != FluidBoundaryConditionType.EMITTER:
                raise ValueError("multi_species_phases are only supported for emitter boundary conditions")
            if not self.multi_species_phases:
                raise ValueError("multi_species_phases must be non-empty when provided")
        if self.volume_fractions is not None:
            if self.type != FluidBoundaryConditionType.EMITTER:
                raise ValueError("volume_fractions are only supported for emitter boundary conditions")
            if not self.volume_fractions:
                raise ValueError("volume_fractions must be non-empty when provided")
            if any(fraction < 0.0 or fraction > 1.0 for fraction in self.volume_fractions):
                raise ValueError("volume_fractions values must be in [0, 1]")
            if abs(sum(self.volume_fractions) - 1.0) > 1.0e-6:
                raise ValueError("volume_fractions must sum to 1.0")
        return self


class InitialConditionAssignmentConfig(BaseModel):
    region: Optional[str] = None
    variable: VariableConfig
    value: float | List[float]

    @model_validator(mode="after")
    def _validate_value_shape(self) -> "InitialConditionAssignmentConfig":
        # C++ parses scalar values for real_type and vectors for vector_type.
        if self.variable.real_type is not None and isinstance(self.value, list):
            raise ValueError("initial_conditions real_type assignment requires a scalar value")
        if self.variable.vector_type is not None and not isinstance(self.value, list):
            raise ValueError("initial_conditions vector_type assignment requires a vector value")
        return self


class InitialConditionConfig(BaseModel):
    body_name: str = Field(..., min_length=1)
    assignments: List[InitialConditionAssignmentConfig] = Field(..., min_length=1)


class RestartConfig(BaseModel):
    restore_step: int = Field(..., ge=0)
    save_interval: int = Field(default=1000, gt=0)
    summary_enabled: bool = False


class FluidDynamicsSolverConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    acoustic_cfl: float = Field(default=0.6, gt=0)
    advection_cfl: float = Field(default=0.25, gt=0)
    max_velocity_factor: float = Field(default=1.0, gt=0)
    surface_type: Literal["free_surface", "confined", "open_boundary", "free_stream"] = "free_surface"
    kernel_correction: Literal["linear", "none"] = "linear"
    particle_sort_frequency: Optional[int] = Field(default=None, gt=0)


class ContinuumDynamicsSolverConfig(BaseModel):
    acoustic_cfl: float = Field(default=0.4, gt=0)
    advection_cfl: float = Field(default=0.2, gt=0)
    # These controls apply to J2Plasticity. PlasticContinuum
    # removes them during cross-validation because it does not build the
    # corresponding correction, repulsion, shear, or hourglass dynamics.
    linear_correction_matrix_coeff: Optional[float] = 0.5
    contact_numerical_damping: Optional[float] = 0.5
    shear_stress_damping: Optional[float] = 0.0
    hourglass_factor: Optional[float] = 2.0
    plastic_riemann_dissipation_factor: Optional[float] = Field(default=None, gt=0)
    surface_type: Literal["free_surface", "confined", "open_boundary"] = "free_surface"


class SolverParametersConfig(BaseModel):
    end_time: Optional[float] = Field(default=None, gt=0)
    output_interval: Optional[float] = Field(default=None, gt=0)
    screen_interval: Optional[int] = Field(default=None, gt=0)
    observation_interval: int = Field(default=200, gt=0)
    fluid_dynamics: Optional[FluidDynamicsSolverConfig] = None
    continuum_dynamics: Optional[ContinuumDynamicsSolverConfig] = None


class BodyConstraintConfig(BaseModel):
    body_name: str = Field(..., min_length=1)
    type: BodyConstraintType

    region: Optional[str] = None

    mobilized_body: Optional[str] = None
    velocity: Optional[List[float]] = Field(default=None, min_length=2, max_length=3)
    angular_velocity: Optional[float] = None

    @model_validator(mode="after")
    def _validate_constraint_type(self) -> "BodyConstraintConfig":
        if self.type == BodyConstraintType.FIXED:
            return self
        if self.mobilized_body is None or self.velocity is None or self.angular_velocity is None:
            raise ValueError("simbody constraint requires mobilized_body, velocity and angular_velocity")
        return self


class SimulationConfig(BaseModel):
    """Top-level JSON payload consumed by SPHSimulation methods:
    buildGeometries(), generateParticles(), buildSimulation()."""

    characteristic_dimensions: Optional[List[CharacteristicDimensionConfig]] = None
    simulation_type: SimulationType
    log_level: LogLevel = LogLevel.INFO
    geometries: GeometriesConfig
    particle_generation: ParticleGenerationConfig

    fluid_bodies: List[FluidBodyConfig] = Field(default_factory=list)
    continuum_bodies: List[ContinuumBodyConfig] = Field(default_factory=list)
    solid_bodies: List[SolidBodyConfig] = Field(default_factory=list)

    gravity: Optional[List[float]] = Field(default=None, min_length=2, max_length=3)
    observers: List[ObserverConfig] = Field(default_factory=list)
    fluid_boundary_conditions: List[FluidBoundaryConditionConfig] = Field(default_factory=list)
    body_constraints: List[BodyConstraintConfig] = Field(default_factory=list)
    initial_conditions: List[InitialConditionConfig] = Field(default_factory=list)
    extra_state_recording: List[ExtraStateRecordingConfig] = Field(default_factory=list)
    energy_recording: List[EnergyRecordingConfig] = Field(default_factory=list)

    solver_parameters: SolverParametersConfig
    restart: Optional[RestartConfig] = None

    def _infer_spatial_dim(self) -> int | None:
        """Infer spatial dimension from available vector-valued config fields."""
        if self.geometries.system_domain is not None:
            return len(self.geometries.system_domain.lower_bound)

        dims: set[int] = set()
        if self.gravity is not None:
            dims.add(len(self.gravity))

        for shape in self.geometries.shapes:
            for vec in (shape.lower_bound, shape.upper_bound, shape.half_size, shape.translation):
                if vec is not None:
                    dims.add(len(vec))
            if shape.transform is not None:
                dims.add(len(shape.transform.translation))

        for primitive in self.geometries.primitives:
            dims.add(len(primitive.half_size))
            dims.add(len(primitive.transform.translation))

        for oriented_box in self.geometries.oriented_boxes:
            for vec in (oriented_box.center, oriented_box.normal, oriented_box.half_size):
                if vec is not None:
                    dims.add(len(vec))
            if oriented_box.transform is not None:
                dims.add(len(oriented_box.transform.translation))

        for observer in self.observers:
            for position in observer.positions:
                dims.add(len(position))

        for constraint in self.body_constraints:
            if constraint.velocity is not None:
                dims.add(len(constraint.velocity))

        for solid_body in self.solid_bodies:
            if solid_body.material.active_strain is not None:
                dims.add(len(solid_body.material.active_strain.center))

        if len(dims) > 1:
            raise ValueError("configuration vector dimensionality must be consistent")
        return next(iter(dims), None)

    @model_validator(mode="after")
    def _cross_validate(self) -> "SimulationConfig":
        def _find_non_finite(value: object, path: str) -> str | None:
            if isinstance(value, float):
                return path if not math.isfinite(value) else None
            if isinstance(value, dict):
                for key, child in value.items():
                    found = _find_non_finite(child, f"{path}.{key}")
                    if found is not None:
                        return found
            elif isinstance(value, (list, tuple)):
                for index, child in enumerate(value):
                    found = _find_non_finite(child, f"{path}[{index}]")
                    if found is not None:
                        return found
            return None

        non_finite_path = _find_non_finite(self.model_dump(mode="python"), "config")
        if non_finite_path is not None:
            raise ValueError(
                "all numeric configuration values must be finite; "
                f"found non-finite value at {non_finite_path}"
            )

        shape_names = {shape.name for shape in self.geometries.shapes}
        shape_types_by_name = {shape.name: shape.type for shape in self.geometries.shapes}
        oriented_box_names = {ab.name for ab in self.geometries.oriented_boxes}

        for section_name, bodies in (
            ("fluid_bodies", self.fluid_bodies),
            ("continuum_bodies", self.continuum_bodies),
            ("solid_bodies", self.solid_bodies),
        ):
            body_names = [body.name for body in bodies]
            if len(body_names) != len(set(body_names)):
                raise ValueError(f"{section_name} must use unique body names")

        if self.particle_generation.settings is not None:
            particle_body_names = [
                body.name for body in self.particle_generation.settings.bodies
            ]
            if len(particle_body_names) != len(set(particle_body_names)):
                raise ValueError(
                    "particle_generation.settings.bodies must use unique body names"
                )

        # Scaling: if characteristic_dimensions provided, Length must be among them
        if self.characteristic_dimensions is not None:
            names = {cd.name for cd in self.characteristic_dimensions}
            if CharacteristicDimensionName.LENGTH not in names:
                raise ValueError("characteristic_dimensions must include a 'Length' entry")

        # Simulation type specific requirements
        if self.simulation_type == SimulationType.FLUID_DYNAMICS:
            if not self.fluid_bodies:
                raise ValueError("fluid_dynamics simulation requires fluid_bodies")
            if len(self.fluid_bodies) > 1:
                raise ValueError("fluid_dynamics currently supports exactly one fluid body")
            if self.solver_parameters.fluid_dynamics is None:
                raise ValueError("fluid_dynamics simulation requires solver_parameters.fluid_dynamics")
        elif self.simulation_type == SimulationType.CONTINUUM_DYNAMICS:
            if not self.continuum_bodies:
                raise ValueError("continuum_dynamics simulation requires continuum_bodies")
            if len(self.continuum_bodies) > 1:
                raise ValueError("continuum_dynamics currently supports exactly one continuum body")
            if self.solver_parameters.continuum_dynamics is None:
                raise ValueError("continuum_dynamics simulation requires solver_parameters.continuum_dynamics")

        free_stream = any(
            bc.type == FluidBoundaryConditionType.FREE_STREAM
            for bc in self.fluid_boundary_conditions
        )
        if not self.solid_bodies and not free_stream:
            raise ValueError("simulation requires at least one solid body")

        # Bodies must reference existing geometry names
        for body in self.fluid_bodies:
            if body.name not in shape_names:
                raise ValueError(f"fluid body '{body.name}' must match a shape name in geometries.shapes")
        for body in self.continuum_bodies:
            if body.name not in shape_names:
                raise ValueError(f"continuum body '{body.name}' must match a shape name in geometries.shapes")
        for body in self.solid_bodies:
            if body.name not in shape_names:
                raise ValueError(f"solid body '{body.name}' must match a shape name in geometries.shapes")
            if body.material.material_id_regions is not None:
                for region in body.material.material_id_regions.regions:
                    if region.shape not in shape_names:
                        raise ValueError(
                            "material_id_regions region.shape must reference an existing shape"
                        )

        # Particle generation body names must exist as shapes
        if self.particle_generation.settings is not None:
            for body in self.particle_generation.settings.bodies:
                if body.name not in shape_names:
                    raise ValueError(
                        f"particle_generation body '{body.name}' must match a shape name in geometries.shapes"
                    )
                for blocker_name in body.blockers:
                    if blocker_name not in oriented_box_names:
                        raise ValueError(
                            "particle_generation body blockers entries must reference existing "
                            "geometries.oriented_boxes names"
                        )
                for insert_name in body.box_shape_inserts:
                    if insert_name not in shape_names:
                        raise ValueError(
                            "particle_generation body box_shape_inserts entries must reference existing "
                            "geometries.shapes names"
                        )
                    if shape_types_by_name[insert_name] not in (
                        BodyShapeType.BOX,
                        BodyShapeType.BOUNDING_BOX,
                        BodyShapeType.EXPANDED_BOX,
                    ):
                        raise ValueError(
                            "particle_generation body box_shape_inserts entries must reference "
                            "box-compatible shapes (box, bounding_box, expanded_box)"
                        )
                for insert_name in body.cylinder_shape_inserts:
                    if insert_name not in shape_names:
                        raise ValueError(
                            "particle_generation body cylinder_shape_inserts entries must reference existing "
                            "geometries.shapes names"
                        )
                    if shape_types_by_name[insert_name] != BodyShapeType.CYLINDER:
                        raise ValueError(
                            "particle_generation body cylinder_shape_inserts entries must reference "
                            "cylinder shapes"
                        )
            for c in self.particle_generation.settings.relaxation_constraints:
                if c.body_name not in shape_names:
                    raise ValueError(
                        f"relaxation constraint body '{c.body_name}' must match a shape name in geometries.shapes"
                    )
                if c.oriented_box not in oriented_box_names:
                    raise ValueError(
                        f"relaxation constraint oriented_box '{c.oriented_box}' must exist in geometries.oriented_boxes"
                    )

        # Boundary condition references
        fluid_names = {body.name for body in self.fluid_bodies}
        fluid_body_map = {body.name: body for body in self.fluid_bodies}
        for bc in self.fluid_boundary_conditions:
            if bc.body_name not in fluid_names:
                raise ValueError("fluid_boundary_conditions body_name must reference an existing fluid body")
            if bc.oriented_box not in oriented_box_names:
                raise ValueError("fluid_boundary_conditions oriented_box must exist in geometries.oriented_boxes")
            if bc.mass_fractions is not None:
                fluid_body = fluid_body_map[bc.body_name]
                if fluid_body.material.type not in (
                    MaterialType.WEAKLY_COMPRESSIBLE_MULTI_SPECIES,
                ):
                    raise ValueError(
                        "mass_fractions require boundary-condition body material type "
                        "weakly_compressible_multi_species"
                    )
                species_count = len(fluid_body.material.species)
                if species_count != len(bc.mass_fractions):
                    raise ValueError(
                        "mass_fractions length must match number of material species"
                    )
            if bc.multi_species_phases is not None or bc.volume_fractions is not None:
                fluid_body = fluid_body_map[bc.body_name]
                if fluid_body.material.type != MaterialType.WEAKLY_COMPRESSIBLE_MULTI_PHASE:
                    raise ValueError(
                        "multi_species_phases/volume_fractions require boundary-condition body "
                        "material type weakly_compressible_multi_phase"
                    )
                phase_map = {
                    phase.name: phase for phase in fluid_body.material.multi_species_phases
                }
                if bc.multi_species_phases is not None:
                    for phase_cfg in bc.multi_species_phases:
                        if phase_cfg.phase_name not in phase_map:
                            raise ValueError(
                                "fluid_boundary_conditions multi_species_phases phase_name must reference "
                                "an existing material multi_species_phases name"
                            )
                        if len(phase_cfg.mass_fractions) != len(phase_map[phase_cfg.phase_name].species):
                            raise ValueError(
                                "multi_species_phases mass_fractions length must match number of species "
                                "for the referenced material phase"
                            )
                if bc.volume_fractions is not None:
                    expected_phase_count = (
                        len(fluid_body.material.pure_phases)
                        + len(fluid_body.material.multi_species_phases)
                    )
                    if expected_phase_count != len(bc.volume_fractions):
                        raise ValueError(
                            "volume_fractions length must match number of material pure_phases plus "
                            "multi_species_phases"
                        )

        # Observer references
        observed_names = fluid_names | {body.name for body in self.continuum_bodies}
        for observer in self.observers:
            if observer.observed_body not in observed_names:
                raise ValueError("observer observed_body must reference an existing fluid/continuum body")

        # Body-constraint references
        real_body_names = {body.name for body in self.continuum_bodies} | {body.name for body in self.solid_bodies}
        for constraint in self.body_constraints:
            if constraint.body_name not in real_body_names:
                raise ValueError("body_constraints body_name must reference an existing continuum/solid body")
            if constraint.region is not None and constraint.region not in oriented_box_names:
                raise ValueError("body_constraints region must reference an existing oriented box name")

        # Initial-condition references
        initial_condition_body_names = fluid_names | real_body_names
        for initial_condition in self.initial_conditions:
            if initial_condition.body_name not in initial_condition_body_names:
                raise ValueError("initial_conditions body_name must reference an existing body")
            for assignment in initial_condition.assignments:
                if assignment.region is not None and assignment.region not in oriented_box_names:
                    raise ValueError(
                        "initial_conditions assignment region must reference an existing oriented box name"
                    )

        # Simbody constraints require restart section to exist at runtime.
        if any(constraint.type == BodyConstraintType.SIMBODY for constraint in self.body_constraints):
            if self.restart is None:
                raise ValueError("simbody body_constraints require config.restart")

        # Dimensional consistency if system_domain is present
        if self.geometries.system_domain is not None:
            dim = len(self.geometries.system_domain.lower_bound)
            if self.gravity is not None and len(self.gravity) != dim:
                raise ValueError("gravity dimensionality must match geometries.system_domain")
            for observer in self.observers:
                for p in observer.positions:
                    if len(p) != dim:
                        raise ValueError("observer positions dimensionality must match geometries.system_domain")

        dim = self._infer_spatial_dim()
        if dim == 3:
            if any(shape.type == BodyShapeType.MULTIPOLYGON for shape in self.geometries.shapes):
                raise ValueError(
                    "multipolygon shapes are 2D-only; use bounding_box, box, "
                    "complex_shape, or triangle_mesh for 3D configurations"
                )
        elif dim == 2:
            if any(shape.type == BodyShapeType.TRIANGLE_MESH for shape in self.geometries.shapes):
                raise ValueError("triangle_mesh shapes are 3D-only; use 2D shapes for 2D configurations")
            if any(shape.type == BodyShapeType.CYLINDER for shape in self.geometries.shapes):
                raise ValueError("cylinder shapes are 3D-only; use 2D shapes for 2D configurations")

        all_plastic_continuum = bool(self.continuum_bodies) and all(
            body.material.type == MaterialType.PLASTIC_CONTINUUM
            for body in self.continuum_bodies
        )
        continuum_solver = self.solver_parameters.continuum_dynamics
        if all_plastic_continuum and continuum_solver is not None:
            continuum_solver.linear_correction_matrix_coeff = None
            continuum_solver.contact_numerical_damping = None
            continuum_solver.shear_stress_damping = None
            continuum_solver.hourglass_factor = None

        return self
