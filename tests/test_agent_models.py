"""Tests for Agent task and plan state models."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from sphinxsim.agent import (
    AgentPlan,
    PlanAction,
    PlanStep,
    StepStatus,
    TaskStatus,
    create_task,
)


def _plan() -> AgentPlan:
    return AgentPlan(
        plan_id="plan_demo_v1",
        version=1,
        objective="Generate and run a landslide case",
        acceptance_criteria=["simulation completes"],
        steps=[
            PlanStep(
                step_id="generate",
                action=PlanAction.GENERATE_CASE,
                description="Generate a validated JSON case",
            ),
            PlanStep(
                step_id="run",
                action=PlanAction.RUN_CASE,
                description="Run the generated case",
                depends_on=["generate"],
            ),
        ],
    )


def test_create_task_is_draft_and_does_not_execute() -> None:
    task = create_task("simulate a 3D landslide", task_id="task_demo")
    assert task.status == TaskStatus.DRAFT
    assert task.plan_versions == []
    assert task.run_ids == []


def test_task_and_plan_round_trip_json() -> None:
    task = create_task("simulate a landslide", task_id="task_roundtrip")
    plan = _plan()
    restored_task = type(task).model_validate_json(task.model_dump_json())
    restored_plan = AgentPlan.model_validate_json(plan.model_dump_json())
    assert restored_task == task
    assert restored_plan == plan


def test_plan_rejects_unknown_action() -> None:
    with pytest.raises(ValidationError, match="action"):
        PlanStep(step_id="bad", action="delete_everything", description="bad")


def test_plan_rejects_duplicate_step_ids() -> None:
    step = PlanStep(
        step_id="same",
        action=PlanAction.GENERATE_CASE,
        description="generate",
    )
    with pytest.raises(ValidationError, match="unique"):
        AgentPlan(
            plan_id="plan_bad",
            version=1,
            objective="bad plan",
            steps=[step, step.model_copy()],
        )


def test_plan_rejects_dependency_on_later_step() -> None:
    with pytest.raises(ValidationError, match="unknown or later"):
        AgentPlan(
            plan_id="plan_bad_dependency",
            version=1,
            objective="bad plan",
            steps=[
                PlanStep(
                    step_id="run",
                    action=PlanAction.RUN_CASE,
                    description="run",
                    depends_on=["generate"],
                ),
                PlanStep(
                    step_id="generate",
                    action=PlanAction.GENERATE_CASE,
                    description="generate",
                ),
            ],
        )


def test_step_transition_updates_timestamps() -> None:
    step = _plan().steps[0]
    step.transition_to(StepStatus.IN_PROGRESS)
    assert step.started_at is not None
    step.transition_to(StepStatus.COMPLETED, summary="case.json generated")
    assert step.finished_at is not None
    assert step.result_summary == "case.json generated"


def test_completed_step_cannot_be_restarted() -> None:
    step = _plan().steps[0]
    step.transition_to(StepStatus.IN_PROGRESS)
    step.transition_to(StepStatus.COMPLETED)
    with pytest.raises(ValueError, match="illegal plan step transition"):
        step.transition_to(StepStatus.IN_PROGRESS)


def test_task_transition_rejects_running_without_plan_approval() -> None:
    task = create_task("simulate a landslide", task_id="task_transition")
    with pytest.raises(ValueError, match="illegal agent task transition"):
        task.transition_to(TaskStatus.RUNNING)
