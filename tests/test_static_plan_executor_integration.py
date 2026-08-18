"""Lightweight integration test using the real existing validate command."""

from pathlib import Path
from uuid import uuid4

from sphinxsim.agent import (
    AgentPlan,
    AgentTaskStore,
    PlanAction,
    PlanStep,
    StaticPlanExecutor,
    create_task,
)
from sphinxsim.services import SPHinXsimCLIAdapter


def test_real_column_collapse_validation_without_simulation(build_temp_path: Path) -> None:
    project_root = Path(__file__).resolve().parents[1]
    store = AgentTaskStore(build_temp_path / f"validate_executor_{uuid4().hex}")
    task = create_task(
        "Validate the existing column-collapse benchmark.",
        task_id=f"task_validate_{uuid4().hex[:8]}",
    )
    store.create_task(task)
    config_path = "tests/test_simulation/test_2d_simulation/data/column_collapse.json"
    plan = AgentPlan(
        plan_id=f"{task.task_id}_plan_v1",
        version=1,
        objective=task.objective,
        steps=[
            PlanStep(
                step_id="validate_case",
                action=PlanAction.VALIDATE_CASE,
                description="Validate the existing column-collapse configuration.",
                parameters={"config_path": config_path},
            )
        ],
    )
    store.add_plan(task.task_id, plan)
    adapter = SPHinXsimCLIAdapter(cwd=project_root, default_timeout=30)

    result = StaticPlanExecutor(store, adapter).execute(task.task_id)

    assert result.succeeded is True
    assert result.completed_step_ids == ("validate_case",)
