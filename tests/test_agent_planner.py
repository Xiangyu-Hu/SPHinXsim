"""Tests for internal natural-language Task planning."""

from pathlib import Path
from uuid import uuid4

import pytest

from sphinxsim.agent import (
    AgentTaskStore,
    PlanAction,
    TaskPlanner,
    TaskStatus,
)


@pytest.fixture
def planner(build_temp_path: Path) -> TaskPlanner:
    store = AgentTaskStore(build_temp_path / f"planner_{uuid4().hex}")
    return TaskPlanner(store)


def _actions(result) -> list[PlanAction]:
    return [step.action for step in result.plan.steps]


def test_planner_creates_generate_then_run_plan(planner: TaskPlanner) -> None:
    result = planner.create_plan(
        "Generate and run a 2D water dam break simulation.",
        task_id="task_generate_run",
    )
    assert _actions(result) == [PlanAction.GENERATE_CASE, PlanAction.RUN_CASE]
    assert result.plan.steps[1].depends_on == ["generate_case"]
    assert result.task.status == TaskStatus.PLANNED


def test_planner_does_not_run_generate_only_request(planner: TaskPlanner) -> None:
    result = planner.create_plan(
        "Generate a 3D landslide JSON file, but do not run it.",
        task_id="task_generate_only",
    )
    assert _actions(result) == [PlanAction.GENERATE_CASE]


def test_planner_updates_existing_case_without_regeneration(planner: TaskPlanner) -> None:
    result = planner.create_plan(
        "Update cases/base.json to use an end time of 5 seconds, then run it.",
        task_id="task_update_run",
    )
    assert _actions(result) == [PlanAction.UPDATE_CASE, PlanAction.RUN_CASE]
    update_parameters = result.plan.steps[0].parameters
    assert update_parameters["config_path"] == "cases/base.json"
    assert update_parameters["output_path"].endswith("case_v1.json")


def test_planner_adds_analysis_for_requested_metrics(planner: TaskPlanner) -> None:
    result = planner.create_plan(
        "Simulate a 3D landslide and calculate runout distance and deposit height.",
        task_id="task_analyze",
    )
    assert _actions(result) == [
        PlanAction.GENERATE_CASE,
        PlanAction.RUN_CASE,
        PlanAction.ANALYZE_RESULT,
    ]
    assert result.plan.steps[-1].parameters["metrics"] == [
        "runout_distance",
        "deposit_height",
    ]


def test_planner_creates_hidden_clarification_step(planner: TaskPlanner) -> None:
    result = planner.create_plan(
        "Change the simulation end time to 5 seconds.",
        task_id="task_clarify",
    )
    assert _actions(result) == [PlanAction.ASK_USER]
    assert result.plan.steps[0].requires_approval is True


def test_planner_persists_task_and_plan(planner: TaskPlanner) -> None:
    result = planner.create_plan(
        "Generate and run a 2D water dam break simulation.",
        task_id="task_persisted",
    )
    assert planner.store.load_task(result.task.task_id) == result.task
    assert planner.store.load_active_plan(result.task.task_id) == result.plan


def test_planner_does_not_print_or_execute_commands(planner: TaskPlanner, capsys) -> None:
    result = planner.create_plan(
        "Generate and run a 2D water dam break simulation.",
        task_id="task_hidden",
    )
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""
    assert result.task.run_ids == []
    assert result.task.case_versions == []
