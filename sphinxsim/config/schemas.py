"""Pydantic schemas for the builder-centric SPHSimulation JSON configuration."""

from __future__ import annotations

from enum import Enum
from typing import List, Optional

from pydantic import BaseModel, Field, model_validator


class PhysicsType(str, Enum):
    """Heuristic physics category used by the NLP mock layer."""

    FLUID = "fluid"
    SOLID = "solid"
    FSI = "fsi"


class SimulationType(str, Enum):
    FLUID_DYNAMICS = "fluid_dynamics"
    CONTINUUM_DYNAMICS = "continuum_dynamics"


class CharacteristicDimensionName(str, Enum):
    LENGTH = "Length"
    MASS = "Mass"
    TIME = "Time"
    DENSITY = "Density"
    PRESSURE = "Pressure"
    STRESS = "Stress"
    VISCOSITY = "Viscosity"
    VELOCITY = "Velocity"
    SPEED = "Speed"
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
    TRIANGLE_MESH = "triangle_mesh"


class MultiPolygonPrimitiveType(str, Enum):
    BOUNDING_BOX = "bounding_box"
    CONTAINER_BOX = "container_box"
    DATA_FILE = "data_file"


class AlignedBoxType(str, Enum):
    IN_OUTLET = "in_outlet"
    REGION = "region"


class MaterialType(str, Enum):
    WEAKLY_COMPRESSIBLE_FLUID = "weakly_compressible_fluid"
    RIGID_BODY = "rigid_body"
    J2_PLASTICITY = "j2_plasticity"
    GENERAL_CONTINUUM = "general_continuum"


class FluidBoundaryConditionType(str, Enum):
    EMITTER = "emitter"
    BI_DIRECTIONAL = "bi_directional"


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
    min_dimension_particles: Optional[int] = Field(default=None, gt=0)

    @model_validator(mode="after")
    def _requires_one_mode(self) -> "GlobalResolutionConfig":
        if self.particle_spacing is None and self.min_dimension_particles is None:
            raise ValueError("global_resolution requires particle_spacing or min_dimension_particles")
        return self


class TransformConfig(BaseModel):
    translation: List[float] = Field(..., min_length=2, max_length=3)
    rotation_angle: float
    rotation_axis: Optional[List[float]] = Field(default=None, min_length=3, max_length=3)


class MultiPolygonEntryConfig(BaseModel):
    operation: GeometricOperationType
    type: MultiPolygonPrimitiveType
    lower_bound: Optional[List[float]] = Field(default=None, min_length=2, max_length=3)
    upper_bound: Optional[List[float]] = Field(default=None, min_length=2, max_length=3)
    inner_lower_bound: Optional[List[float]] = Field(default=None, min_length=2, max_length=3)
    inner_upper_bound: Optional[List[float]] = Field(default=None, min_length=2, max_length=3)
    thickness: Optional[float] = Field(default=None, gt=0)
    file_path: Optional[str] = None

    @model_validator(mode="after")
    def _validate_shape_payload(self) -> "MultiPolygonEntryConfig":
        if self.type == MultiPolygonPrimitiveType.BOUNDING_BOX:
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
        elif self.type == MultiPolygonPrimitiveType.DATA_FILE:
            if not self.file_path:
                raise ValueError("multipolygon data_file requires file_path")
        return self


class ShapeConfig(BaseModel):
    name: str = Field(..., min_length=1)
    type: BodyShapeType

    lower_bound: Optional[List[float]] = Field(default=None, min_length=2, max_length=3)
    upper_bound: Optional[List[float]] = Field(default=None, min_length=2, max_length=3)

    half_size: Optional[List[float]] = Field(default=None, min_length=2, max_length=3)
    transform: Optional[TransformConfig] = None

    original: Optional[str] = None
    expansion: Optional[float] = Field(default=None, gt=0)

    sub_shapes: Optional[List[str]] = None
    operations: Optional[List[GeometricOperationType]] = None

    polygons: Optional[List[MultiPolygonEntryConfig]] = None

    file_path: Optional[str] = None
    translation: Optional[List[float]] = Field(default=None, min_length=3, max_length=3)
    scale: Optional[float] = Field(default=None, gt=0)

    @model_validator(mode="after")
    def _validate_type_fields(self) -> "ShapeConfig":
        if self.type == BodyShapeType.BOX:
            if self.half_size is None or self.transform is None:
                raise ValueError("box shape requires half_size and transform")
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
            return self

        if self.type == BodyShapeType.MULTIPOLYGON:
            if not self.polygons:
                raise ValueError("multipolygon shape requires non-empty polygons")
            return self

        if self.type == BodyShapeType.TRIANGLE_MESH:
            if not self.file_path:
                raise ValueError("triangle_mesh shape requires file_path")
            return self

        return self


class AlignedBoxConfig(BaseModel):
    name: str = Field(..., min_length=1)
    type: AlignedBoxType

    center: Optional[List[float]] = Field(default=None, min_length=2, max_length=3)
    normal: Optional[List[float]] = Field(default=None, min_length=2, max_length=3)
    radius: Optional[float] = Field(default=None, gt=0)

    half_size: Optional[List[float]] = Field(default=None, min_length=2, max_length=3)
    transform: Optional[TransformConfig] = None

    @model_validator(mode="after")
    def _validate_aligned_box(self) -> "AlignedBoxConfig":
        if self.type == AlignedBoxType.IN_OUTLET:
            if self.center is None or self.normal is None or self.radius is None:
                raise ValueError("in_outlet aligned_box requires center, normal and radius")
        elif self.type == AlignedBoxType.REGION:
            if self.half_size is None or self.transform is None:
                raise ValueError("region aligned_box requires half_size and transform")
        return self


class GeometriesConfig(BaseModel):
    system_domain: Optional[DomainConfig] = None
    global_resolution: Optional[GlobalResolutionConfig] = None
    shapes: List[ShapeConfig] = Field(..., min_length=1)
    aligned_boxes: List[AlignedBoxConfig] = Field(default_factory=list)


class RelaxationBodyConfig(BaseModel):
    level_set: Optional[dict] = None
    dependent_bodies: List[str] = Field(default_factory=list)


class ParticleGenerationBodyConfig(BaseModel):
    name: str = Field(..., min_length=1)
    solid_body: Optional[dict] = None
    relaxation: Optional[RelaxationBodyConfig] = None


class RelaxationParametersConfig(BaseModel):
    total_iterations: int = Field(default=1000, gt=0)


class RelaxationConstraintConfig(BaseModel):
    body_name: str = Field(..., min_length=1)
    aligned_box: str = Field(..., min_length=1)
    type: str = Field(..., min_length=1)


class ParticleGenerationSettingsConfig(BaseModel):
    bodies: List[ParticleGenerationBodyConfig] = Field(..., min_length=1)
    relaxation_constraints: List[RelaxationConstraintConfig] = Field(default_factory=list)
    relaxation_parameters: RelaxationParametersConfig = Field(default_factory=RelaxationParametersConfig)


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
    real_type: Optional[List[str]] = None
    vector_type: Optional[List[str]] = None

    @model_validator(mode="after")
    def _at_least_one(self) -> "StateRecordingVariableConfig":
        if self.real_type is None and self.vector_type is None:
            raise ValueError("extra_state_recording variables require real_type or vector_type")
        return self


class ExtraStateRecordingConfig(BaseModel):
    name: str = Field(..., min_length=1)
    variables: List[StateRecordingVariableConfig] = Field(..., min_length=1)


class MaterialConfig(BaseModel):
    type: MaterialType

    density: Optional[float] = Field(default=None, gt=0)
    sound_speed: Optional[float] = Field(default=None, gt=0)
    viscosity: Optional[float] = Field(default=None, gt=0)

    youngs_modulus: Optional[float] = Field(default=None, gt=0)
    poisson_ratio: Optional[float] = None
    yield_stress: Optional[float] = Field(default=None, gt=0)
    hardening_modulus: Optional[float] = Field(default=None, gt=0)

    @model_validator(mode="after")
    def _validate_material_by_type(self) -> "MaterialConfig":
        if self.type == MaterialType.WEAKLY_COMPRESSIBLE_FLUID:
            if self.density is None or self.sound_speed is None:
                raise ValueError("weakly_compressible_fluid requires density and sound_speed")
        elif self.type == MaterialType.RIGID_BODY:
            pass
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
        elif self.type == MaterialType.GENERAL_CONTINUUM:
            required = (self.density, self.sound_speed, self.youngs_modulus, self.poisson_ratio)
            if any(v is None for v in required):
                raise ValueError(
                    "general_continuum requires density, sound_speed, youngs_modulus and poisson_ratio"
                )
        return self


class FluidBodyConfig(BaseModel):
    name: str = Field(..., min_length=1)
    material: MaterialConfig
    particle_reserve_factor: Optional[float] = Field(default=None, gt=0)

    @model_validator(mode="after")
    def _material_type(self) -> "FluidBodyConfig":
        if self.material.type != MaterialType.WEAKLY_COMPRESSIBLE_FLUID:
            raise ValueError("fluid body material type must be weakly_compressible_fluid")
        return self


class SolidBodyConfig(BaseModel):
    name: str = Field(..., min_length=1)
    material: MaterialConfig

    @model_validator(mode="after")
    def _material_type(self) -> "SolidBodyConfig":
        if self.material.type != MaterialType.RIGID_BODY:
            raise ValueError("solid body material type must be rigid_body")
        return self


class ContinuumBodyConfig(BaseModel):
    name: str = Field(..., min_length=1)
    material: MaterialConfig

    @model_validator(mode="after")
    def _material_type(self) -> "ContinuumBodyConfig":
        if self.material.type not in (MaterialType.J2_PLASTICITY, MaterialType.GENERAL_CONTINUUM):
            raise ValueError("continuum body material type must be j2_plasticity or general_continuum")
        return self


class FluidBoundaryConditionConfig(BaseModel):
    body_name: str = Field(..., min_length=1)
    aligned_box: str = Field(..., min_length=1)
    type: FluidBoundaryConditionType
    inflow_speed: Optional[float] = Field(default=None, gt=0)
    pressure: Optional[float] = None

    @model_validator(mode="after")
    def _type_specific_requirements(self) -> "FluidBoundaryConditionConfig":
        if self.type == FluidBoundaryConditionType.EMITTER and self.inflow_speed is None:
            raise ValueError("emitter boundary condition requires inflow_speed")
        if self.type == FluidBoundaryConditionType.BI_DIRECTIONAL and self.pressure is None:
            raise ValueError("bi_directional boundary condition requires pressure")
        return self


class RestartConfig(BaseModel):
    enabled: bool
    restore_step: int = Field(..., ge=0)
    save_interval: int = Field(default=1000, gt=0)
    summary_enabled: bool = False


class FluidDynamicsSolverConfig(BaseModel):
    acoustic_cfl: float = Field(default=0.6, gt=0)
    advection_cfl: float = Field(default=0.25, gt=0)
    flow_type: str = "free_surface"
    particle_sort_frequency: Optional[int] = Field(default=None, gt=0)


class ContinuumDynamicsSolverConfig(BaseModel):
    acoustic_cfl: float = Field(default=0.4, gt=0)
    advection_cfl: float = Field(default=0.2, gt=0)
    linear_correction_matrix_coeff: float = 0.5
    contact_numerical_damping: float = 0.5
    shear_stress_damping: float = 0.0
    hourglass_factor: float = 2.0


class SolverParametersConfig(BaseModel):
    end_time: Optional[float] = Field(default=None, gt=0)
    output_interval: Optional[float] = Field(default=None, gt=0)
    screen_interval: Optional[int] = Field(default=None, gt=0)
    restart: Optional[RestartConfig] = None
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
    """Top-level JSON payload consumed directly by SPHSimulation::loadConfig."""

    characteristic_dimensions: Optional[List[CharacteristicDimensionConfig]] = None
    simulation_type: SimulationType
    geometries: GeometriesConfig
    particle_generation: ParticleGenerationConfig

    fluid_bodies: List[FluidBodyConfig] = Field(default_factory=list)
    continuum_bodies: List[ContinuumBodyConfig] = Field(default_factory=list)
    solid_bodies: List[SolidBodyConfig] = Field(default_factory=list)

    gravity: Optional[List[float]] = Field(default=None, min_length=2, max_length=3)
    observers: List[ObserverConfig] = Field(default_factory=list)
    fluid_boundary_conditions: List[FluidBoundaryConditionConfig] = Field(default_factory=list)
    body_constraints: List[BodyConstraintConfig] = Field(default_factory=list)
    extra_state_recording: List[ExtraStateRecordingConfig] = Field(default_factory=list)

    solver_parameters: SolverParametersConfig

    @model_validator(mode="after")
    def _cross_validate(self) -> "SimulationConfig":
        shape_names = {shape.name for shape in self.geometries.shapes}
        aligned_box_names = {ab.name for ab in self.geometries.aligned_boxes}

        # Scaling: if characteristic_dimensions provided, Length must be among them
        if self.characteristic_dimensions is not None:
            names = {cd.name for cd in self.characteristic_dimensions}
            if CharacteristicDimensionName.LENGTH not in names:
                raise ValueError("characteristic_dimensions must include a 'Length' entry")

        # Simulation type specific requirements
        if self.simulation_type == SimulationType.FLUID_DYNAMICS:
            if not self.fluid_bodies:
                raise ValueError("fluid_dynamics simulation requires fluid_bodies")
            if self.solver_parameters.fluid_dynamics is None:
                raise ValueError("fluid_dynamics simulation requires solver_parameters.fluid_dynamics")
        elif self.simulation_type == SimulationType.CONTINUUM_DYNAMICS:
            if not self.continuum_bodies:
                raise ValueError("continuum_dynamics simulation requires continuum_bodies")
            if self.solver_parameters.continuum_dynamics is None:
                raise ValueError("continuum_dynamics simulation requires solver_parameters.continuum_dynamics")

        if not self.solid_bodies:
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

        # Particle generation body names must exist as shapes
        if self.particle_generation.settings is not None:
            for body in self.particle_generation.settings.bodies:
                if body.name not in shape_names:
                    raise ValueError(
                        f"particle_generation body '{body.name}' must match a shape name in geometries.shapes"
                    )
            for c in self.particle_generation.settings.relaxation_constraints:
                if c.body_name not in shape_names:
                    raise ValueError(
                        f"relaxation constraint body '{c.body_name}' must match a shape name in geometries.shapes"
                    )
                if c.aligned_box not in aligned_box_names:
                    raise ValueError(
                        f"relaxation constraint aligned_box '{c.aligned_box}' must exist in geometries.aligned_boxes"
                    )

        # Boundary condition references
        fluid_names = {body.name for body in self.fluid_bodies}
        for bc in self.fluid_boundary_conditions:
            if bc.body_name not in fluid_names:
                raise ValueError("fluid_boundary_conditions body_name must reference an existing fluid body")
            if bc.aligned_box not in aligned_box_names:
                raise ValueError("fluid_boundary_conditions aligned_box must exist in geometries.aligned_boxes")

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
            if constraint.region is not None and constraint.region not in shape_names:
                raise ValueError("body_constraints region must reference an existing shape name")

        # Dimensional consistency if system_domain is present
        if self.geometries.system_domain is not None:
            dim = len(self.geometries.system_domain.lower_bound)
            if self.gravity is not None and len(self.gravity) != dim:
                raise ValueError("gravity dimensionality must match geometries.system_domain")
            for observer in self.observers:
                for p in observer.positions:
                    if len(p) != dim:
                        raise ValueError("observer positions dimensionality must match geometries.system_domain")

        return self
