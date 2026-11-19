"""Persistable analysis selection used by the main workflow and standalone tools."""
from __future__ import annotations

from typing import Literal
from pydantic import BaseModel, ConfigDict, model_validator
from .analysis import AnalysisSpec
from .declarative_extractors import DeclarativeExtractorSpec, DeclarativeExtractorCompiler
from .extractors import build_builtin_extractor_registry


class AnalysisPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["builtin", "declarative"] = "builtin"
    metric_name: str = "runout_extension"
    builtin: AnalysisSpec | None = None
    declarative: DeclarativeExtractorSpec | None = None

    @model_validator(mode="after")
    def validate_selection(self):
        if self.kind == "builtin":
            if self.builtin is None or self.declarative is not None:
                raise ValueError("builtin analysis requires only a builtin spec")
            if self.metric_name not in {"runout_extension", "initial_front", "final_front"}:
                raise ValueError("unknown builtin output metric")
        else:
            if self.declarative is None or self.builtin is not None:
                raise ValueError("declarative analysis requires only a declarative spec")
            if self.metric_name not in {item.output_name for item in self.declarative.outputs}:
                raise ValueError("objective metric is not an extractor output")
        return self

    @property
    def body_name(self):
        return (self.builtin if self.kind == "builtin" else self.declarative).body_name

    @property
    def unit(self):
        return (self.builtin if self.kind == "builtin" else self.declarative).unit

    @property
    def fingerprint(self):
        return self.declarative.fingerprint if self.declarative else None

    def registry_and_spec(self):
        registry = build_builtin_extractor_registry()
        if self.kind == "builtin":
            return registry, self.builtin.model_dump(mode="json")
        DeclarativeExtractorCompiler().register(registry, self.declarative)
        return registry, {"metric": self.declarative.extractor_id}
