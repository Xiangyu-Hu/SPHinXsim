"""Preset plans used to validate Agent execution before LLM planning."""

from __future__ import annotations

from pathlib import Path

from .models import AgentPlan, AgentTask, PlanAction, PlanStep, create_task
from .store import AgentTaskStore


DEFAULT_COLUMN_COLLAPSE_CONFIG = Path(
    "tests/test_simulation/test_2d_simulation/data/column_collapse.json"
)


def create_column_collapse_task(
    store: AgentTaskStore,
    *,
    config_path: str | Path = DEFAULT_COLUMN_COLLAPSE_CONFIG,
    task_id: str | None = None,
) -> tuple[AgentTask, AgentPlan]:
    """Create and persist a fixed validate-and-run column-collapse task."""
    task = create_task(
        "Validate and run the existing two-dimensional column-collapse benchmark.",
        objective="Execute the existing column-collapse benchmark successfully.",
        task_id=task_id,
    )
    normalized_config_path = str(Path(config_path))
    plan = AgentPlan(
        plan_id=f"{task.task_id}_plan_v1",
        version=1,
        objective=task.objective,
        acceptance_criteria=[
            "The existing column-collapse JSON configuration passes validation.",
            "The SPHinXsim column-collapse simulation completes successfully.",
        ],
        steps=[
            PlanStep(
                step_id="validate_case",
                action=PlanAction.VALIDATE_CASE,
                description="Validate the existing column-collapse JSON configuration.",
                parameters={"config_path": normalized_config_path},
            ),
            PlanStep(
                step_id="run_case",
                action=PlanAction.RUN_CASE,
                description="Run the complete column-collapse simulation.",
                parameters={"config_path": normalized_config_path},
                depends_on=["validate_case"],
            ),
        ],
    )
    store.create_task(task)
    persisted_task = store.add_plan(task.task_id, plan)
    return persisted_task, plan
