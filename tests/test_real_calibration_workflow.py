"""Opt-in acceptance test for the complete real C++ calibration workflow."""

from __future__ import annotations

import json
import math
import os
from pathlib import Path
from uuid import uuid4

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from sphinxsim.agent import (
    AgentTaskStore,
    CalibrationWorkflowStatus,
    DeterministicCalibrationWorkflow,
    MockColumnCollapseCalibrationPlanner,
    RunStatus,
    build_calibration_graph,
)
from sphinxsim.services import SPHinXsimCLIAdapter


pytestmark = pytest.mark.integration


def real_calibration_enabled() -> bool:
    return os.getenv("SPHINXSIM_RUN_REAL_CALIBRATION") == "1"


def real_langgraph_enabled() -> bool:
    return os.getenv("SPHINXSIM_RUN_REAL_LANGGRAPH") == "1"


@pytest.mark.skipif(
    not real_calibration_enabled(),
    reason="set SPHINXSIM_RUN_REAL_CALIBRATION=1 to run the real C++ workflow",
)
def test_mock_llm_real_cpp_calibration_converges(build_temp_path: Path) -> None:
    project_root = Path(__file__).resolve().parents[1]
    store = AgentTaskStore(build_temp_path / f"real_calibration_{uuid4().hex}")
    adapter = SPHinXsimCLIAdapter(
        cwd=project_root,
        environment={"SPHINXSIM_LLM_PROVIDER": "mock"},
        default_timeout=300,
    )
    result = DeterministicCalibrationWorkflow(
        store,
        MockColumnCollapseCalibrationPlanner(
            target_final_front=0.30,
            tolerance=0.005,
            lower_friction_angle=20.0,
            upper_friction_angle=40.0,
            maximum_iterations=6,
        ),
        adapter,
        command_timeout=300,
    ).execute(
        "Calibrate a 2D column collapse final front by adjusting friction angle.",
        workflow_id="real_cpp_acceptance",
    )

    assert result.status == CalibrationWorkflowStatus.CONVERGED
    assert result.succeeded is True
    parameter_values = [item.parameter_value for item in result.iterations]
    assert parameter_values[:2] == [20.0, 40.0]
    assert len(parameter_values) <= 6
    assert len(parameter_values) == len(set(parameter_values))
    assert all(20.0 <= value <= 40.0 for value in parameter_values)
    assert result.final_decision is not None
    assert result.final_decision.best_parameter_value in parameter_values
    assert result.final_decision.best_absolute_error <= 0.005

    generated = json.loads(
        Path(result.baseline_case_path).read_text(encoding="utf-8")
    )
    baseline_angle = generated["continuum_bodies"][0]["material"]["friction_angle"]
    assert baseline_angle == pytest.approx(0.3)

    for iteration in result.iterations:
        case = json.loads(Path(iteration.case_path).read_text(encoding="utf-8"))
        stored_angle = case["continuum_bodies"][0]["material"]["friction_angle"]
        assert math.degrees(stored_angle) == pytest.approx(iteration.parameter_value)
        run = store.load_run(iteration.task_id, iteration.run_id)
        analysis = store.load_analysis(
            iteration.task_id,
            iteration.run_id,
            iteration.analysis_id,
        )
        assert run.status == RunStatus.COMPLETED
        assert Path(run.output_dir, "output").is_dir()
        assert analysis.metrics["particle_count"] == 5000
        assert Path(analysis.metrics["state_file"]).is_file()
        assert iteration.metric_value == pytest.approx(analysis.metrics["final_front"])


@pytest.mark.skipif(
    not real_langgraph_enabled(),
    reason="set SPHINXSIM_RUN_REAL_LANGGRAPH=1 to run the real C++ graph",
)
def test_approved_langgraph_real_cpp_calibration_converges(
    build_temp_path: Path,
) -> None:
    project_root = Path(__file__).resolve().parents[1]
    store = AgentTaskStore(build_temp_path / f"real_graph_{uuid4().hex}")
    adapter = SPHinXsimCLIAdapter(
        cwd=project_root,
        environment={"SPHINXSIM_LLM_PROVIDER": "mock"},
        default_timeout=300,
    )
    workflow = DeterministicCalibrationWorkflow(
        store,
        MockColumnCollapseCalibrationPlanner(tolerance=0.005),
        adapter,
        command_timeout=300,
    )
    graph = build_calibration_graph(
        workflow,
        checkpointer=InMemorySaver(),
        require_approval=True,
    )
    config = {"configurable": {"thread_id": "real_graph_thread"}}

    state = graph.invoke(
        {
            "user_prompt": "Calibrate a real column collapse with approval.",
            "workflow_id": "real_approved_graph",
        },
        config,
    )
    approvals = 0
    while state.get("__interrupt__"):
        approvals += 1
        state = graph.invoke(Command(resume={"action": "approve"}), config)

    result = workflow.load("real_approved_graph")
    assert result.status == CalibrationWorkflowStatus.CONVERGED
    assert approvals == len(result.iterations)
    assert result.final_decision is not None
    assert result.final_decision.best_absolute_error <= 0.005
    assert len(list(graph.get_state_history(config))) >= 10
    for iteration in result.iterations:
        analysis = store.load_analysis(
            iteration.task_id,
            iteration.run_id,
            iteration.analysis_id,
        )
        assert analysis.metrics["particle_count"] == 5000
