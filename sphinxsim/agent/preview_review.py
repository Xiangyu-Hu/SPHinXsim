"""Autonomous visual preflight for generated simulation cases."""

from __future__ import annotations

from enum import Enum
import json
from pathlib import Path
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field, model_validator

from sphinxsim.llm.chat_attachments import ChatAttachment
from sphinxsim.llm.common import strip_code_fences


class PreviewDecision(str, Enum):
    ACCEPT = "accept"
    REVISE = "revise"
    REJECT = "reject"


class PreviewReview(BaseModel):
    """Strict response returned by a visual reviewer."""

    model_config = ConfigDict(extra="forbid")

    decision: PreviewDecision
    summary: str = Field(..., min_length=1)
    issues: list[str] = Field(default_factory=list)
    revision_instruction: str | None = None

    @model_validator(mode="after")
    def validate_revision(self) -> "PreviewReview":
        if self.decision == PreviewDecision.REVISE and not (
            self.revision_instruction and self.revision_instruction.strip()
        ):
            raise ValueError("revise requires revision_instruction")
        if self.decision != PreviewDecision.REVISE:
            self.revision_instruction = None
        return self


class PreviewReviewer(Protocol):
    def review(
        self,
        screenshot_path: str | Path,
        config_path: str | Path,
        *,
        attempt: int,
        request: str = "",
    ) -> PreviewReview: ...


class MockPreviewReviewer:
    """Deterministic visual reviewer used by offline tests and demos."""

    def review(
        self, screenshot_path, config_path, *, attempt: int, request: str = ""
    ) -> PreviewReview:
        return PreviewReview(
            decision=PreviewDecision.ACCEPT,
            summary="Mock visual preflight accepted the generated case.",
        )


class ProviderPreviewReviewer:
    """Use any configured multimodal chat provider for screenshot review."""

    SYSTEM_PROMPT = """You are the visual preflight reviewer for an SPH simulation.
Inspect the preview image for obvious setup defects: missing bodies, geometry outside
the domain, severe overlap, implausible scale, empty particle regions, or a layout
that contradicts the requested case. Do not estimate numerical simulation results
from pixels. Return exactly one JSON object with keys decision, summary, issues,
revision_instruction. decision must be accept, revise, or reject. Use revise only
when a concrete configuration edit can fix the image; otherwise reject.
"""

    def __init__(self, client: Any) -> None:
        self.client = client

    def review(
        self, screenshot_path, config_path, *, attempt: int, request: str = ""
    ) -> PreviewReview:
        screenshot = Path(screenshot_path).resolve()
        config = Path(config_path).resolve()
        config_text = config.read_text(encoding="utf-8")
        if len(config_text) > 12000:
            config_text = config_text[:12000] + "\n...[truncated]"
        response = self.client.chat(
            [
                {"role": "system", "content": self.SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": (
                        f"Review preview attempt {attempt}. Requested objective:\n"
                        f"{request}\n\nValidated config {config.name}:\n"
                        f"{config_text}\n\nReturn JSON only."
                    ),
                    "attachments": (
                        ChatAttachment(screenshot, screenshot.name, "image/png"),
                    ),
                },
            ]
        )
        text = strip_code_fences(str(response)).strip()
        start, end = text.find("{"), text.rfind("}")
        if start < 0 or end <= start:
            raise ValueError("preview reviewer did not return a JSON object")
        try:
            payload = json.loads(text[start : end + 1])
        except json.JSONDecodeError as exc:
            raise ValueError(f"preview reviewer returned invalid JSON: {exc}") from exc
        return PreviewReview.model_validate(payload)
