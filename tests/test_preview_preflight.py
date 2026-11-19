from __future__ import annotations

import json
from pathlib import Path

from sphinxsim.agent import (
    AgentTaskStore,
    DeterministicCalibrationWorkflow,
    MockColumnCollapseCalibrationPlanner,
    PreviewDecision,
    PreviewReview,
    ProviderPreviewReviewer,
)
from sphinxsim.services import CLICommandResult


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SOURCE_CASE = (
    PROJECT_ROOT
    / "tests/test_simulation/test_2d_simulation/data/column_collapse.json"
)


def success(name: str) -> CLICommandResult:
    return CLICommandResult((name,), 0, "ok", "", 0.01)


class PreviewAdapter:
    cwd = PROJECT_ROOT

    def __init__(self) -> None:
        self.preview_calls = 0
        self.update_calls: list[str] = []

    def generate(self, description, output_path, *, timeout=None):
        target = Path(output_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(SOURCE_CASE.read_text(encoding="utf-8"), encoding="utf-8")
        return success("generate")

    def validate(self, config_path, *, timeout=None):
        json.loads(Path(config_path).read_text(encoding="utf-8"))
        return success("validate")

    def preview(
        self,
        config_path,
        *,
        with_particles=False,
        off_screen=False,
        screenshot_path=None,
        timeout=None,
    ):
        self.preview_calls += 1
        target = Path(screenshot_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"\x89PNG\r\n\x1a\nmock")
        assert with_particles and off_screen
        return success("preview")

    def update(self, config_path, instruction, *, output_path=None, timeout=None, **kwargs):
        self.update_calls.append(instruction)
        return success("update")


class RevisingReviewer:
    def review(self, screenshot_path, config_path, *, attempt, request=""):
        if attempt == 1:
            return PreviewReview(
                decision=PreviewDecision.REVISE,
                summary="The body is outside the expected view.",
                issues=["body placement"],
                revision_instruction="Move GranularBody into the domain.",
            )
        return PreviewReview(
            decision=PreviewDecision.ACCEPT,
            summary="The revised setup is visually coherent.",
        )


def test_visual_preflight_revises_and_accepts_without_user_interaction(tmp_path):
    adapter = PreviewAdapter()
    workflow = DeterministicCalibrationWorkflow(
        AgentTaskStore(tmp_path / "store"),
        MockColumnCollapseCalibrationPlanner(),
        adapter,
        preview_reviewer=RevisingReviewer(),
    )
    result = workflow.draft_plan("calibrate", workflow_id="preview_revision")
    workflow.prepare_case(result)
    workflow.preview_case(result)

    assert result.preview_status == "accepted"
    assert result.preview_fingerprint
    assert adapter.preview_calls == 2
    assert adapter.update_calls == ["Move GranularBody into the domain."]
    assert [item["review"]["decision"] for item in result.preview_attempts] == [
        "revise",
        "accept",
    ]


class FakeVisionClient:
    def __init__(self) -> None:
        self.messages = None

    def chat(self, messages):
        self.messages = messages
        return """```json
        {"decision":"accept","summary":"Bodies are visible and aligned.",
         "issues":[],"revision_instruction":null}
        ```"""


def test_provider_preview_reviewer_sends_png_and_parses_strict_json(tmp_path):
    screenshot = tmp_path / "preview.png"
    screenshot.write_bytes(b"\x89PNG\r\n\x1a\nmock")
    config = tmp_path / "case.json"
    config.write_text("{}", encoding="utf-8")
    client = FakeVisionClient()

    review = ProviderPreviewReviewer(client).review(
        screenshot, config, attempt=1, request="Build a column collapse."
    )

    assert review.decision == PreviewDecision.ACCEPT
    attachment = client.messages[1]["attachments"][0]
    assert attachment.path == screenshot.resolve()
    assert attachment.media_type == "image/png"
    assert "Build a column collapse" in client.messages[1]["content"]
