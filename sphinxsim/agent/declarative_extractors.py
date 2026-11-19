"""Safe declarative compilation of simple VTP Position extractors."""

from __future__ import annotations

import hashlib
import json
from enum import Enum
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .analysis import AnalysisError, TimeSelection, VTPPositionReader
from .extractors import ExtractorDefinition, ExtractorRegistry
from .store import AgentTaskStore


class PositionReduction(str, Enum):
    MINIMUM = "minimum"
    MAXIMUM = "maximum"


class PositionOutputSpec(BaseModel):
    """One allow-listed reduction over one Position component."""

    model_config = ConfigDict(extra="forbid")

    output_name: str = Field(..., pattern=r"^[A-Za-z][A-Za-z0-9_]*$")
    component_index: int = Field(..., ge=0, le=2)
    reduction: PositionReduction


class DeclarativeExtractorSpec(BaseModel):
    """Auditable data-only definition; it contains no executable expression."""

    model_config = ConfigDict(extra="forbid")

    extractor_id: str = Field(..., pattern=r"^[A-Za-z][A-Za-z0-9_]*$")
    description: str = Field(..., min_length=1, max_length=500)
    body_name: str = Field(..., min_length=1, max_length=200)
    source: str = Field(default="ascii_vtp_position", pattern=r"^ascii_vtp_position$")
    time_selection: TimeSelection = TimeSelection.FINAL
    outputs: list[PositionOutputSpec] = Field(..., min_length=1, max_length=8)
    unit: str = Field(default="m", pattern=r"^m$")

    @model_validator(mode="after")
    def validate_outputs(self) -> "DeclarativeExtractorSpec":
        names = [item.output_name for item in self.outputs]
        if len(names) != len(set(names)):
            raise ValueError("declarative extractor output names must be unique")
        return self

    @property
    def fingerprint(self) -> str:
        payload = json.dumps(
            self.model_dump(mode="json"),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class DeclarativeExtractorInvocation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    metric: str = Field(..., pattern=r"^[A-Za-z][A-Za-z0-9_]*$")


class DeclarativeExtractorResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    metric: str
    extractor_fingerprint: str = Field(..., min_length=64, max_length=64)
    body_name: str
    state_file: str
    simulation_time: float
    particle_count: int = Field(..., ge=1)
    values: dict[str, float]
    unit: str = "m"


class CompiledPositionExtractor:
    """Deterministic runtime object produced from one validated data spec."""

    def __init__(
        self,
        spec: DeclarativeExtractorSpec,
        position_reader: VTPPositionReader | None = None,
    ) -> None:
        self.spec = spec.model_copy(deep=True)
        self.position_reader = position_reader or VTPPositionReader()

    def analyze(
        self,
        config_path,
        output_dir,
        invocation: BaseModel,
    ) -> DeclarativeExtractorResult:
        if invocation.metric != self.spec.extractor_id:
            raise AnalysisError("extractor invocation metric does not match its definition")
        summary = self.position_reader.read_final_summary(
            output_dir,
            self.spec.body_name,
        )
        values: dict[str, float] = {}
        for output in self.spec.outputs:
            if output.component_index >= summary.component_count:
                raise AnalysisError(
                    f"component_index {output.component_index} exceeds the VTP dimension"
                )
            source = (
                summary.minimum
                if output.reduction == PositionReduction.MINIMUM
                else summary.maximum
            )
            values[output.output_name] = source[output.component_index]
        return DeclarativeExtractorResult(
            metric=self.spec.extractor_id,
            extractor_fingerprint=self.spec.fingerprint,
            body_name=self.spec.body_name,
            state_file=str(summary.state_file),
            simulation_time=summary.time,
            particle_count=summary.particle_count,
            values=values,
            unit=self.spec.unit,
        )


class DeclarativeExtractorCompiler:
    """Compile validated JSON data into a registered object without eval/exec."""

    def compile(self, spec: DeclarativeExtractorSpec) -> ExtractorDefinition:
        validated = DeclarativeExtractorSpec.model_validate(spec)
        return ExtractorDefinition(
            name=validated.extractor_id,
            description=validated.description,
            spec_model=DeclarativeExtractorInvocation,
            result_model=DeclarativeExtractorResult,
            extractor=CompiledPositionExtractor(validated),
        )

    def register(
        self,
        registry: ExtractorRegistry,
        spec: DeclarativeExtractorSpec,
    ) -> None:
        registry.register(self.compile(spec))


class DeclarativeExtractorCatalog(BaseModel):
    """Versioned set of specs that can be persisted and recompiled."""

    model_config = ConfigDict(extra="forbid")

    version: str = Field(default="1.0", min_length=1)
    extractors: list[DeclarativeExtractorSpec] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_identifiers(self) -> "DeclarativeExtractorCatalog":
        identifiers = [item.extractor_id for item in self.extractors]
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("declarative extractor identifiers must be unique")
        return self

    def save(self, path: str | Path) -> Path:
        destination = Path(path).resolve()
        AgentTaskStore._atomic_write(destination, self.model_dump_json(indent=2))
        return destination

    @classmethod
    def load(cls, path: str | Path) -> "DeclarativeExtractorCatalog":
        return cls.model_validate_json(Path(path).read_text(encoding="utf-8"))

    def build_registry(self, base: ExtractorRegistry | None = None) -> ExtractorRegistry:
        registry = base or ExtractorRegistry()
        compiler = DeclarativeExtractorCompiler()
        for spec in self.extractors:
            compiler.register(registry, spec)
        return registry
