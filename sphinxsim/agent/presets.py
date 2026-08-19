"""Preset plans used to validate Agent execution before LLM planning."""

from __future__ import annotations

import math
from pathlib import Path
from typing import Sequence

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
    analyze_runout: bool = False,
) -> tuple[AgentTask, AgentPlan]:
    """Create a fixed parameterized column-collapse task."""
    final_action = (
        "run it, and analyze its final runout."
        if analyze_runout
        else "run it."
    )
    task = create_task(
        (
            "Create a working copy of the two-dimensional column-collapse "
            f"benchmark, set the GranularBody friction angle to "
            f"{friction_angle_degrees:g} degrees, validate it, {final_action}"
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
    run_id = "run_001"
    run_output_dir = str(store.run_directory(task.task_id, run_id))
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
                parameters={
                    "config_path": working_path,
                    "run_id": run_id,
                    "output_dir": run_output_dir,
                },
                depends_on=["validate_case"],
            ),
        ],
    )
    if analyze_runout:
        plan.acceptance_criteria.append(
            "The final GranularBody front and runout extension are persisted."
        )
        plan.steps.append(
            PlanStep(
                step_id="analyze_result",
                action=PlanAction.ANALYZE_RESULT,
                description="Calculate the final GranularBody runout metrics.",
                parameters={
                    "analysis_id": "analysis_001",
                    "run_id": run_id,
                    "config_path": working_path,
                    "analysis_spec": {
                        "metric": "runout",
                        "body_name": "GranularBody",
                        "time_selection": "final",
                        "direction_vector": [1.0, 0.0],
                        "initial_front_source": "geometry",
                        "front_method": "maximum",
                        "unit": "m",
                    },
                },
                depends_on=["run_case"],
            )
        )
    store.create_task(task)
    persisted_task = store.add_plan(task.task_id, plan)
    return persisted_task, plan


def create_runout_analysis_column_collapse_task(
    store: AgentTaskStore,
    *,
    friction_angle_degrees: float = 30.0,
    config_path: str | Path = DEFAULT_COLUMN_COLLAPSE_CONFIG,
    task_id: str | None = None,
) -> tuple[AgentTask, AgentPlan]:
    """Create the five-step preset including deterministic runout analysis."""
    return create_friction_angle_column_collapse_task(
        store,
        friction_angle_degrees=friction_angle_degrees,
        config_path=config_path,
        task_id=task_id,
        analyze_runout=True,
    )


def create_friction_angle_sweep_task(
    store: AgentTaskStore,
    *,
    friction_angles_degrees: Sequence[float] = (20.0, 25.0, 30.0, 35.0, 40.0),
    target_final_front: float = 0.4,
    config_path: str | Path = DEFAULT_COLUMN_COLLAPSE_CONFIG,
    task_id: str | None = None,
) -> tuple[AgentTask, AgentPlan]:
    """Create a fixed multi-run friction-angle sweep and comparison Plan."""
    angles: list[float] = []
    for value in friction_angles_degrees:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError("friction_angles_degrees must contain only numbers")
        angle = float(value)
        if not math.isfinite(angle) or not 0.0 <= angle < 90.0:
            raise ValueError("friction angles must satisfy 0 <= angle < 90")
        angles.append(angle)
    if not angles:
        raise ValueError("friction_angles_degrees must not be empty")
    if len(angles) != len(set(angles)):
        raise ValueError("friction_angles_degrees must not contain duplicates")
    if isinstance(target_final_front, bool) or not isinstance(
        target_final_front,
        (int, float),
    ):
        raise ValueError("target_final_front must be numeric")
    target = float(target_final_front)
    if not math.isfinite(target):
        raise ValueError("target_final_front must be finite")

    angle_text = ", ".join(f"{angle:g}" for angle in angles)
    task = create_task(
        (
            "Run a two-dimensional column-collapse friction-angle sweep at "
            f"{angle_text} degrees and compare final fronts against "
            f"{target:g} m."
        ),
        objective=(
            "Compare a fixed friction-angle sweep and identify the sample "
            f"closest to a final front of {target:g} m."
        ),
        task_id=task_id,
    )
    source_path = str(Path(config_path))
    steps: list[PlanStep] = []
    comparison_samples: list[dict[str, object]] = []
    analysis_step_ids: list[str] = []

    for index, angle in enumerate(angles, start=1):
        suffix = f"{index:03d}"
        case_path = str(
            (
                store.root
                / task.task_id
                / "cases"
                / f"column_collapse_friction_{suffix}.json"
            ).resolve()
        )
        run_id = f"run_{suffix}"
        analysis_id = f"analysis_{suffix}"
        prepare_id = f"prepare_case_{suffix}"
        update_id = f"update_friction_{suffix}"
        validate_id = f"validate_case_{suffix}"
        run_step_id = f"run_case_{suffix}"
        analysis_step_id = f"analyze_result_{suffix}"
        steps.extend(
            [
                PlanStep(
                    step_id=prepare_id,
                    action=PlanAction.PREPARE_CASE,
                    description=f"Prepare the case for the {angle:g}-degree sample.",
                    parameters={
                        "source_config_path": source_path,
                        "output_config_path": case_path,
                    },
                ),
                PlanStep(
                    step_id=update_id,
                    action=PlanAction.UPDATE_CASE,
                    description=f"Set the friction angle to {angle:g} degrees.",
                    parameters={
                        "config_path": case_path,
                        "body_name": "GranularBody",
                        "friction_angle_degrees": angle,
                    },
                    depends_on=[prepare_id],
                ),
                PlanStep(
                    step_id=validate_id,
                    action=PlanAction.VALIDATE_CASE,
                    description=f"Validate the {angle:g}-degree case.",
                    parameters={"config_path": case_path},
                    depends_on=[update_id],
                ),
                PlanStep(
                    step_id=run_step_id,
                    action=PlanAction.RUN_CASE,
                    description=f"Run the {angle:g}-degree case.",
                    parameters={
                        "config_path": case_path,
                        "run_id": run_id,
                        "output_dir": str(store.run_directory(task.task_id, run_id)),
                    },
                    depends_on=[validate_id],
                ),
                PlanStep(
                    step_id=analysis_step_id,
                    action=PlanAction.ANALYZE_RESULT,
                    description=f"Analyze runout for the {angle:g}-degree case.",
                    parameters={
                        "analysis_id": analysis_id,
                        "run_id": run_id,
                        "config_path": case_path,
                        "analysis_spec": {
                            "metric": "runout",
                            "body_name": "GranularBody",
                            "time_selection": "final",
                            "direction_vector": [1.0, 0.0],
                            "initial_front_source": "geometry",
                            "front_method": "maximum",
                            "unit": "m",
                        },
                    },
                    depends_on=[run_step_id],
                ),
            ]
        )
        analysis_step_ids.append(analysis_step_id)
        comparison_samples.append(
            {
                "friction_angle_degrees": angle,
                "run_id": run_id,
                "analysis_id": analysis_id,
            }
        )

    steps.append(
        PlanStep(
            step_id="compare_results",
            action=PlanAction.COMPARE_RESULTS,
            description="Compare all final fronts with the target value.",
            parameters={
                "sweep_id": "sweep_001",
                "target_value": target,
                "samples": comparison_samples,
            },
            depends_on=analysis_step_ids,
        )
    )
    plan = AgentPlan(
        plan_id=f"{task.task_id}_plan_v1",
        version=1,
        objective=task.objective,
        assumptions=[
            "Each friction-angle sample uses an isolated case and run directory.",
            "Final front is the maximum particle projection along positive x.",
        ],
        acceptance_criteria=[
            "Every requested friction-angle sample is validated, run, and analyzed.",
            "The sweep summary reports each final front and absolute target error.",
            "The sample closest to the target final front is identified.",
        ],
        steps=steps,
    )
    store.create_task(task)
    persisted_task = store.add_plan(task.task_id, plan)
    return persisted_task, plan
