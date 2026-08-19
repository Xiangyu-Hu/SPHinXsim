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


def create_friction_angle_column_collapse_task(
    store: AgentTaskStore,
    *,
    friction_angle_degrees: float = 30.0,
    config_path: str | Path = DEFAULT_COLUMN_COLLAPSE_CONFIG,
    task_id: str | None = None,
) -> tuple[AgentTask, AgentPlan]:
    """Create a fixed copy-update-validate-run column-collapse task."""
    task = create_task(
        (
            "Create a working copy of the two-dimensional column-collapse "
            f"benchmark, set the GranularBody friction angle to "
            f"{friction_angle_degrees:g} degrees, validate it, and run it."
        ),
        objective=(
            "Run an isolated column-collapse case with a deterministic "
            f"friction angle of {friction_angle_degrees:g} degrees."
        ),
        task_id=task_id,
    )
    output_path = (
        store.root / task.task_id / "cases" / "column_collapse_friction_v1.json"
    ).resolve()
    source_path = str(Path(config_path))
    working_path = str(output_path)
    plan = AgentPlan(
        plan_id=f"{task.task_id}_plan_v1",
        version=1,
        objective=task.objective,
        assumptions=[
            "The JSON friction_angle field stores radians.",
            "The requested friction angle is provided in degrees.",
        ],
        acceptance_criteria=[
            "The source column-collapse JSON remains unchanged.",
            "The working JSON stores the requested friction angle in radians.",
            "The working JSON configuration passes validation.",
            "The SPHinXsim simulation completes successfully.",
        ],
        steps=[
            PlanStep(
                step_id="prepare_case",
                action=PlanAction.PREPARE_CASE,
                description="Copy the benchmark JSON into the task workspace.",
                parameters={
                    "source_config_path": source_path,
                    "output_config_path": working_path,
                },
            ),
            PlanStep(
                step_id="update_friction_angle",
                action=PlanAction.UPDATE_CASE,
                description="Set the GranularBody internal friction angle.",
                parameters={
                    "config_path": working_path,
                    "body_name": "GranularBody",
                    "friction_angle_degrees": friction_angle_degrees,
                },
                depends_on=["prepare_case"],
            ),
            PlanStep(
                step_id="validate_case",
                action=PlanAction.VALIDATE_CASE,
                description="Validate the modified working configuration.",
                parameters={"config_path": working_path},
                depends_on=["update_friction_angle"],
            ),
            PlanStep(
                step_id="run_case",
                action=PlanAction.RUN_CASE,
                description="Run the modified column-collapse simulation.",
                parameters={"config_path": working_path},
                depends_on=["validate_case"],
            ),
        ],
    )
    store.create_task(task)
    persisted_task = store.add_plan(task.task_id, plan)
    return persisted_task, plan
