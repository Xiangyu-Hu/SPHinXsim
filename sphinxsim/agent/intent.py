"""Natural-language intent extraction for the internal Agent planner."""

from __future__ import annotations

from enum import Enum
import re

from pydantic import BaseModel, ConfigDict, Field


class TaskIntentType(str, Enum):
    CREATE_CASE = "create_case"
    UPDATE_CASE = "update_case"
    CLARIFY = "clarify"


class PlanningIntent(BaseModel):
    """Small, validated intent surface consumed by :class:`TaskPlanner`."""

    model_config = ConfigDict(extra="forbid")

    intent_type: TaskIntentType
    should_run: bool = False
    should_analyze: bool = False
    should_report: bool = False
    existing_config_path: str | None = None
    requested_metrics: list[str] = Field(default_factory=list)
    clarification_questions: list[str] = Field(default_factory=list)


class RuleBasedIntentResolver:
    """Resolve common English CAE requests without an external LLM call.

    The resolver intentionally handles only high-confidence workflow intent.
    Detailed physics interpretation remains delegated to the existing
    ``sphinxsim generate`` command, which already owns natural-language case
    generation and schema validation.
    """

    _JSON_PATH = re.compile(
        r"(?P<path>(?:[A-Za-z]:[\\/])?[^\s\"']+\.json)",
        flags=re.IGNORECASE,
    )
    _UPDATE_WORDS = ("update", "modify", "change", "revise", "adjust", "edit")
    _CREATE_WORDS = ("generate", "create", "build", "simulate", "model")
    _RUN_WORDS = ("run", "rerun", "execute", "simulate", "calculate", "compute")
    _NO_RUN_PHRASES = (
        "do not run",
        "don't run",
        "without running",
        "generate only",
        "create only",
        "only generate",
        "only create",
        "just generate",
        "just create",
    )
    _ANALYZE_WORDS = (
        "analyze",
        "analyse",
        "inspect results",
        "evaluate results",
        "calculate result",
        "compute result",
        "compare",
    )
    _REPORT_WORDS = ("report", "summary", "document the results")
    _METRIC_PATTERNS = {
        "runout_distance": ("runout distance", "run-out distance", "travel distance"),
        "deposit_height": ("deposit height", "deposition height", "final height"),
        "maximum_velocity": ("maximum velocity", "max velocity", "peak velocity"),
        "final_maximum_velocity": ("final maximum velocity", "final max velocity"),
        "repose_angle": ("repose angle", "angle of repose"),
        "energy": ("energy", "mechanical energy"),
    }

    @staticmethod
    def _contains_any(text: str, values: tuple[str, ...]) -> bool:
        return any(value in text for value in values)

    def resolve(self, user_prompt: str) -> PlanningIntent:
        """Convert an English user prompt into constrained workflow intent."""
        prompt = user_prompt.strip()
        if not prompt:
            raise ValueError("user_prompt must not be empty")
        lowered = prompt.lower()

        config_match = self._JSON_PATH.search(prompt)
        config_path = config_match.group("path") if config_match else None
        is_update = self._contains_any(lowered, self._UPDATE_WORDS)
        is_create = self._contains_any(lowered, self._CREATE_WORDS)

        if is_update and config_path is None:
            return PlanningIntent(
                intent_type=TaskIntentType.CLARIFY,
                clarification_questions=[
                    "Which existing JSON configuration should be updated?"
                ],
            )

        if not is_update and not is_create and config_path is None:
            return PlanningIntent(
                intent_type=TaskIntentType.CLARIFY,
                clarification_questions=[
                    "Should SPHinXsim create a new case or update an existing JSON configuration?"
                ],
            )

        explicit_no_run = self._contains_any(lowered, self._NO_RUN_PHRASES)
        should_analyze = self._contains_any(lowered, self._ANALYZE_WORDS)
        requested_metrics = [
            metric
            for metric, patterns in self._METRIC_PATTERNS.items()
            if self._contains_any(lowered, patterns)
        ]
        should_analyze = should_analyze or bool(requested_metrics)
        should_report = self._contains_any(lowered, self._REPORT_WORDS)
        explicit_run = self._contains_any(lowered, self._RUN_WORDS)
        should_run = not explicit_no_run and (explicit_run or should_analyze or should_report)

        return PlanningIntent(
            intent_type=(
                TaskIntentType.UPDATE_CASE if is_update else TaskIntentType.CREATE_CASE
            ),
            should_run=should_run,
            should_analyze=should_analyze,
            should_report=should_report,
            existing_config_path=config_path,
            requested_metrics=requested_metrics,
        )
