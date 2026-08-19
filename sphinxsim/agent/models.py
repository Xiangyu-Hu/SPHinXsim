"""Persistent state models for AgentCAE tasks and revisable plans."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator


def utc_now() -> datetime:
    """Return a timezone-aware timestamp suitable for persisted state."""
    return datetime.now(timezone.utc)


class TaskStatus(str, Enum):
    DRAFT = "draft"
    PLANNED = "planned"
    APPROVED = "approved"
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class PlanStatus(str, Enum):
    DRAFT = "draft"
    APPROVED = "approved"
    ACTIVE = "active"
    COMPLETED = "completed"
    SUPERSEDED = "superseded"


class StepStatus(str, Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"
    CANCELLED = "cancelled"


class PlanAction(str, Enum):
    """Allow-listed actions that a future executor may dispatch."""

    CLARIFY_REQUIREMENTS = "clarify_requirements"
    GENERATE_CASE = "generate_case"
    PREPARE_CASE = "prepare_case"
    UPDATE_CASE = "update_case"
    VALIDATE_CASE = "validate_case"
    PREVIEW_CASE = "preview_case"
    RUN_CASE = "run_case"
    ANALYZE_RESULT = "analyze_result"
    COMPARE_RESULTS = "compare_results"
    GENERATE_REPORT = "generate_report"
    ASK_USER = "ask_user"


class PlanStep(BaseModel):
    """One auditable action in an Agent plan."""

    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    step_id: str = Field(..., min_length=1, pattern=r"^[A-Za-z0-9_-]+$")
    action: PlanAction
    description: str = Field(..., min_length=1)
    status: StepStatus = StepStatus.PENDING
    parameters: dict[str, Any] = Field(default_factory=dict)
    depends_on: list[str] = Field(default_factory=list)
    requires_approval: bool = False
    artifacts: list[str] = Field(default_factory=list)
    result_summary: str | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None

    @model_validator(mode="after")
    def _validate_step_state(self) -> "PlanStep":
        if self.step_id in self.depends_on:
            raise ValueError("plan step cannot depend on itself")
        if self.status == StepStatus.IN_PROGRESS and self.started_at is None:
            raise ValueError("in_progress plan step requires started_at")
        if self.status in {
            StepStatus.COMPLETED,
            StepStatus.FAILED,
            StepStatus.SKIPPED,
            StepStatus.CANCELLED,
        } and self.finished_at is None:
            raise ValueError(f"{self.status.value} plan step requires finished_at")
        return self

    def transition_to(self, new_status: StepStatus, *, summary: str | None = None) -> None:
        """Apply a legal step-state transition and update timestamps."""
        allowed = {
            StepStatus.PENDING: {
                StepStatus.IN_PROGRESS,
                StepStatus.SKIPPED,
                StepStatus.CANCELLED,
            },
            StepStatus.IN_PROGRESS: {
                StepStatus.COMPLETED,
                StepStatus.FAILED,
                StepStatus.CANCELLED,
            },
            StepStatus.FAILED: {StepStatus.IN_PROGRESS, StepStatus.CANCELLED},
            StepStatus.COMPLETED: set(),
            StepStatus.SKIPPED: set(),
            StepStatus.CANCELLED: set(),
        }
        if new_status not in allowed[self.status]:
            raise ValueError(
                f"illegal plan step transition: {self.status.value} -> {new_status.value}"
            )

        now = utc_now()
        update: dict[str, Any] = {"status": new_status}
        if new_status == StepStatus.IN_PROGRESS:
            update["started_at"] = now
            update["finished_at"] = None
        elif new_status in {
            StepStatus.COMPLETED,
            StepStatus.FAILED,
            StepStatus.SKIPPED,
            StepStatus.CANCELLED,
        }:
            update["finished_at"] = now
        if summary is not None:
            update["result_summary"] = summary

        updated = self.model_copy(update=update)
        self.__dict__.update(updated.__dict__)


class AgentPlan(BaseModel):
    """A versioned, user-visible plan for completing an Agent task."""

    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    plan_id: str = Field(..., min_length=1, pattern=r"^[A-Za-z0-9_-]+$")
    version: int = Field(..., ge=1)
    objective: str = Field(..., min_length=1)
    status: PlanStatus = PlanStatus.DRAFT
    assumptions: list[str] = Field(default_factory=list)
    acceptance_criteria: list[str] = Field(default_factory=list)
    steps: list[PlanStep] = Field(..., min_length=1)
    revision_reason: str | None = None
    created_at: datetime = Field(default_factory=utc_now)

    @model_validator(mode="after")
    def _validate_steps(self) -> "AgentPlan":
        step_ids = [step.step_id for step in self.steps]
        if len(step_ids) != len(set(step_ids)):
            raise ValueError("plan step_id values must be unique")

        known: set[str] = set()
        for step in self.steps:
            unknown = set(step.depends_on) - known
            if unknown:
                raise ValueError(
                    f"plan step '{step.step_id}' depends on unknown or later steps: "
                    f"{sorted(unknown)}"
                )
            known.add(step.step_id)
        return self


class AgentTask(BaseModel):
    """Top-level user request managed across one or more plan revisions."""

    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    task_id: str = Field(..., min_length=1, pattern=r"^[A-Za-z0-9_-]+$")
    user_prompt: str = Field(..., min_length=1)
    objective: str = Field(..., min_length=1)
    status: TaskStatus = TaskStatus.DRAFT
    active_plan_version: int | None = Field(default=None, ge=1)
    plan_versions: list[int] = Field(default_factory=list)
    case_versions: list[str] = Field(default_factory=list)
    run_ids: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)

    @model_validator(mode="after")
    def _validate_plan_references(self) -> "AgentTask":
        if len(self.plan_versions) != len(set(self.plan_versions)):
            raise ValueError("task plan_versions values must be unique")
        if self.plan_versions != sorted(self.plan_versions):
            raise ValueError("task plan_versions values must be sorted")
        if (
            self.active_plan_version is not None
            and self.active_plan_version not in self.plan_versions
        ):
            raise ValueError("active_plan_version must reference task plan_versions")
        return self

    def transition_to(self, new_status: TaskStatus) -> None:
        """Apply a legal task-state transition."""
        allowed = {
            TaskStatus.DRAFT: {TaskStatus.PLANNED, TaskStatus.CANCELLED},
            TaskStatus.PLANNED: {
                TaskStatus.APPROVED,
                TaskStatus.CANCELLED,
            },
            TaskStatus.APPROVED: {
                TaskStatus.RUNNING,
                TaskStatus.CANCELLED,
            },
            TaskStatus.RUNNING: {
                TaskStatus.PAUSED,
                TaskStatus.COMPLETED,
                TaskStatus.FAILED,
                TaskStatus.CANCELLED,
            },
            TaskStatus.PAUSED: {
                TaskStatus.RUNNING,
                TaskStatus.CANCELLED,
            },
            TaskStatus.FAILED: {
                TaskStatus.PLANNED,
                TaskStatus.CANCELLED,
            },
            TaskStatus.COMPLETED: set(),
            TaskStatus.CANCELLED: set(),
        }
        if new_status not in allowed[self.status]:
            raise ValueError(
                f"illegal agent task transition: {self.status.value} -> {new_status.value}"
            )
        updated = self.model_copy(update={"status": new_status, "updated_at": utc_now()})
        self.__dict__.update(updated.__dict__)


def create_task(
    user_prompt: str,
    *,
    objective: str | None = None,
    task_id: str | None = None,
) -> AgentTask:
    """Create a new draft task without planning or executing it."""
    cleaned_prompt = user_prompt.strip()
    if not cleaned_prompt:
        raise ValueError("user_prompt must not be empty")
    return AgentTask(
        task_id=task_id or f"task_{uuid4().hex[:12]}",
        user_prompt=cleaned_prompt,
        objective=(objective or cleaned_prompt).strip(),
    )
