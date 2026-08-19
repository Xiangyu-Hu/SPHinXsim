"""Versioned local knowledge and deterministic retrieval for Agent planning."""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import tempfile
from collections import Counter
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from .capabilities import CapabilityRegistry
from .tools import ToolRegistry


_TOKEN_PATTERN = re.compile(r"[A-Za-z0-9_]+")


class KnowledgeError(ValueError):
    """Raised when local knowledge cannot be indexed or retrieved."""


class KnowledgeDocument(BaseModel):
    model_config = ConfigDict(extra="forbid")

    document_id: str = Field(..., min_length=1, pattern=r"^[A-Za-z0-9_.-]+$")
    title: str = Field(..., min_length=1)
    text: str = Field(..., min_length=1)
    source: str = Field(..., min_length=1)
    tags: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class RetrievedKnowledge(BaseModel):
    model_config = ConfigDict(extra="forbid")

    document: KnowledgeDocument
    score: float = Field(..., gt=0.0)
    matched_terms: list[str]


class KnowledgeManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: str
    document_count: int = Field(..., ge=0)
    sources: list[str]
    fingerprint: str = Field(..., min_length=64, max_length=64)


class KnowledgeSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: str
    documents: list[KnowledgeDocument]


class LocalKnowledgeBase:
    """Small dependency-free lexical index suitable for planner grounding."""

    def __init__(self, documents: list[KnowledgeDocument], version: str = "1.0"):
        if not version.strip():
            raise KnowledgeError("knowledge version must not be empty")
        identifiers = [document.document_id for document in documents]
        if len(identifiers) != len(set(identifiers)):
            raise KnowledgeError("knowledge document identifiers must be unique")
        self.version = version
        self.documents = sorted(documents, key=lambda item: item.document_id)
        self._document_tokens = [self._weighted_tokens(item) for item in self.documents]

    @property
    def manifest(self) -> KnowledgeManifest:
        serialized = self._canonical_payload().encode("utf-8")
        return KnowledgeManifest(
            version=self.version,
            document_count=len(self.documents),
            sources=sorted({document.source for document in self.documents}),
            fingerprint=hashlib.sha256(serialized).hexdigest(),
        )

    def retrieve(
        self,
        query: str,
        *,
        limit: int = 5,
        required_tags: set[str] | None = None,
    ) -> list[RetrievedKnowledge]:
        query_terms = sorted(set(self._tokenize(query)))
        if not query_terms:
            raise KnowledgeError("retrieval query must contain searchable terms")
        if limit < 1:
            raise KnowledgeError("retrieval limit must be at least one")

        candidates = [
            index
            for index, document in enumerate(self.documents)
            if not required_tags or required_tags.issubset(set(document.tags))
        ]
        document_frequency = {
            term: sum(term in self._document_tokens[index] for index in candidates)
            for term in query_terms
        }
        scored: list[RetrievedKnowledge] = []
        count = len(candidates)
        for index in candidates:
            tokens = self._document_tokens[index]
            matched = [term for term in query_terms if term in tokens]
            if not matched:
                continue
            score = sum(
                tokens[term]
                * (math.log((count + 1) / (document_frequency[term] + 1)) + 1.0)
                for term in matched
            )
            scored.append(
                RetrievedKnowledge(
                    document=self.documents[index],
                    score=score,
                    matched_terms=matched,
                )
            )
        scored.sort(key=lambda item: (-item.score, item.document.document_id))
        return scored[:limit]

    def save(self, path: str | Path) -> Path:
        destination = Path(path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        payload = self._canonical_payload() + "\n"
        handle, temporary_name = tempfile.mkstemp(
            dir=destination.parent,
            prefix=f".{destination.name}.",
            suffix=".tmp",
            text=True,
        )
        try:
            with os.fdopen(handle, "w", encoding="utf-8") as stream:
                stream.write(payload)
            os.replace(temporary_name, destination)
        except Exception:
            Path(temporary_name).unlink(missing_ok=True)
            raise
        return destination.resolve()

    @classmethod
    def load(cls, path: str | Path) -> "LocalKnowledgeBase":
        source = Path(path)
        try:
            snapshot = KnowledgeSnapshot.model_validate_json(
                source.read_text(encoding="utf-8")
            )
        except (OSError, ValueError) as exc:
            raise KnowledgeError(f"failed to load knowledge snapshot {source}: {exc}") from exc
        return cls(snapshot.documents, snapshot.version)

    def _canonical_payload(self) -> str:
        snapshot = KnowledgeSnapshot(version=self.version, documents=self.documents)
        return json.dumps(
            snapshot.model_dump(mode="json"),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )

    @classmethod
    def _weighted_tokens(cls, document: KnowledgeDocument) -> Counter[str]:
        tokens: Counter[str] = Counter(cls._tokenize(document.text))
        tokens.update({term: count * 3 for term, count in Counter(cls._tokenize(document.title)).items()})
        tokens.update({term: count * 2 for term, count in Counter(cls._tokenize(" ".join(document.tags))).items()})
        return tokens

    @staticmethod
    def _tokenize(value: str) -> list[str]:
        return [match.group(0).lower() for match in _TOKEN_PATTERN.finditer(value)]


def build_default_knowledge_base(
    project_root: str | Path,
    tool_registry: ToolRegistry,
    capabilities: CapabilityRegistry,
) -> LocalKnowledgeBase:
    """Build planner knowledge from registered facts and selected project docs."""
    documents: list[KnowledgeDocument] = []
    for definition in tool_registry.definitions():
        capability = definition.as_capability()
        documents.append(
            KnowledgeDocument(
                document_id=f"tool.{definition.name}",
                title=f"Agent tool: {definition.name}",
                text=(
                    f"{definition.description} Action: {definition.action.value}. "
                    f"Cost: {definition.cost.value}. Side effect: "
                    f"{definition.side_effect.value}. Deterministic: "
                    f"{definition.deterministic}. Input schema: "
                    f"{json.dumps(capability['input_schema'], sort_keys=True)}"
                ),
                source=f"registry://tools/{definition.name}",
                tags=["tool", definition.action.value, *definition.tags],
            )
        )

    friction = capabilities.parameters["friction_angle"]
    documents.append(
        KnowledgeDocument(
            document_id="domain.friction_angle",
            title="Friction angle parameter contract",
            text=(
                "The material friction angle is edited through set_friction_angle. "
                f"Planner input uses {friction['input_units']}; JSON storage uses "
                f"{friction['storage_unit']}. Valid degrees are "
                f"{friction['input_range']}. The current setter applies "
                f"to material types {friction['supported_materials']}."
            ),
            source="registry://capabilities/parameters/friction_angle",
            tags=["domain", "parameter", "friction_angle", "units"],
        )
    )

    runout = capabilities.metrics["runout"]
    documents.append(
        KnowledgeDocument(
            document_id="domain.runout",
            title="Runout and final-front analysis contract",
            text=(
                "Runout analysis streams ASCII VTP Position values and does not load the "
                "complete particle file into memory. final_front is the selected final "
                "particle x-coordinate front. runout_extension equals final_front minus "
                "initial_front. The current initial front comes from configuration "
                "geometry. Current front methods: "
                f"{runout['front_methods']}. Current initial-front sources: "
                f"{runout['initial_front_sources']}."
            ),
            source="registry://capabilities/metrics/runout",
            tags=["domain", "analysis", "runout", "vtp", "metric"],
        )
    )

    documents.append(
        KnowledgeDocument(
            document_id="workflow.calibration",
            title="Bounded calibration workflow",
            text=(
                "CalibrationSpec connects one registered parameter tool to one registered "
                "analysis metric, target, tolerance, bounds, units, iteration limit, and "
                "expected monotonicity. BoundedSearchPolicy first samples both bounds and "
                "then selects interval midpoints. It rejects unbracketed targets and "
                "responses that violate expected monotonicity. The policy selects values; "
                "an orchestrator must execute simulations and append observations."
            ),
            source="code://sphinxsim.agent.calibration",
            tags=["workflow", "calibration", "search_policy"],
        )
    )

    cli_path = Path(project_root) / "docs" / "cli-usage.md"
    if cli_path.is_file():
        documents.extend(_markdown_sections(cli_path, maximum_sections=20))
    return LocalKnowledgeBase(documents, version="1.1")


def _markdown_sections(path: Path, maximum_sections: int) -> list[KnowledgeDocument]:
    text = path.read_text(encoding="utf-8")
    sections = re.split(r"(?m)^##+\s+", text)
    documents: list[KnowledgeDocument] = []
    for index, section in enumerate(sections[:maximum_sections]):
        content = section.strip()
        if not content:
            continue
        lines = content.splitlines()
        title = lines[0].strip("# ")
        body = "\n".join(lines[1:]).strip() or title
        documents.append(
            KnowledgeDocument(
                document_id=f"docs.cli_usage.{index}",
                title=title,
                text=body,
                source="docs/cli-usage.md",
                tags=["documentation", "cli"],
            )
        )
    return documents
