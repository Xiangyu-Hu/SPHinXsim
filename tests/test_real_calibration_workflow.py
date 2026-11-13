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
            target_runout_extension=0.20,
            tolerance=0.005,
            lower_friction_angle=0.0,
            upper_friction_angle=40.0,
            maximum_iterations=8,
        ),
        adapter,
        command_timeout=300,
    ).execute(
        "Calibrate a 2D column-collapse runout extension to 0.2 m by adjusting "
        "the friction angle.",
        workflow_id="real_cpp_acceptance",
    )

    assert result.status == CalibrationWorkflowStatus.CONVERGED
    assert result.succeeded is True
    parameter_values = [item.parameter_value for item in result.iterations]
    assert parameter_values[:2] == [0.0, 40.0]
    assert len(parameter_values) <= 8
    assert len(parameter_values) == len(set(parameter_values))
    assert all(0.0 <= value <= 40.0 for value in parameter_values)
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
        assert iteration.metric_value == pytest.approx(
            analysis.metrics["runout_extension"]
        )


@pytest.mark.skipif(
    not real_langgraph_enabled(),
    reason="set SPHINXSIM_RUN_REAL_LANGGRAPH=1 to run the real C++ graph",
)
def test_single_approved_langgraph_real_cpp_calibration_converges(
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
            "user_prompt": (
                "Calibrate a real column-collapse runout extension to 0.2 m "
                "with approval."
            ),
            "workflow_id": "real_approved_graph",
        },
        config,
    )
    assert state.get("__interrupt__")
    assert state["__interrupt__"][0].value["action"] == (
        "approve_calibration_plan"
    )
    assert not Path(workflow.load("real_approved_graph").baseline_case_path).exists()

    state = graph.invoke(Command(resume={"action": "approve"}), config)

    result = workflow.load("real_approved_graph")
    assert result.status == CalibrationWorkflowStatus.CONVERGED
    assert not state.get("__interrupt__")
    assert result.plan_approved is True
    assert result.approved_at is not None
    assert result.case_prepared is True
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
