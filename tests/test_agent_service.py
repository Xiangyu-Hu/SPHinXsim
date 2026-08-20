"""Tests for the durable AgentCAE application service."""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest

from sphinxsim.agent import (
    AgentCAEAgentService,
    AgentServiceError,
    AgentTaskStore,
    CalibrationWorkflowStatus,
    MockColumnCollapseCalibrationPlanner,
)
from tests.test_deterministic_calibration_workflow import DeterministicArtifactAdapter


def make_service(
    build_temp_path: Path,
    adapter: DeterministicArtifactAdapter,
    *,
    interrupt_after: list[str] | None = None,
) -> AgentCAEAgentService:
    root = build_temp_path / f"agent_service_{uuid4().hex}"
    return AgentCAEAgentService(
        AgentTaskStore(root / "store"),
        root / "checkpoints.sqlite",
        MockColumnCollapseCalibrationPlanner(),
        adapter,
        interrupt_after=interrupt_after,
    )


def test_service_runs_approval_lifecycle_to_convergence(build_temp_path: Path) -> None:
    adapter = DeterministicArtifactAdapter()
    service = make_service(build_temp_path, adapter)

    status = service.start(
        "Calibrate a 2D column-collapse runout extension to 0.2 m.",
        workflow_id="service_complete",
    )
    assert status.awaiting_approval is True
    assert adapter.generate_calls == 0
    assert adapter.run_calls == 0

    status = service.approve(status.workflow_id)

    assert status.status == CalibrationWorkflowStatus.CONVERGED
    assert status.iteration_count == 4
    assert status.target_metric == "runout_extension"
    assert status.target_value == pytest.approx(0.2)
    assert status.best_absolute_error == pytest.approx(0.0)
    assert status.awaiting_approval is False
    assert adapter.run_calls == 4


def test_new_service_instance_reads_same_sqlite_approval_state(
    build_temp_path: Path,
) -> None:
    adapter = DeterministicArtifactAdapter()
    first = make_service(build_temp_path, adapter)
    status = first.start(
        "Reach a runout extension of 0.2 m.",
        workflow_id="service_reopen",
    )
    reopened = AgentCAEAgentService(
        first.store,
        first.database_path,
        MockColumnCollapseCalibrationPlanner(),
        adapter,
    )

    persisted = reopened.status(status.workflow_id)
    advanced = reopened.approve(status.workflow_id)

    assert persisted.awaiting_approval is True
    assert persisted.approval_request["action"] == "approve_calibration_plan"
    assert persisted.approval_request["target_value"] == pytest.approx(0.2)
    assert advanced.iteration_count == 4
    assert advanced.awaiting_approval is False
    assert advanced.status == CalibrationWorkflowStatus.CONVERGED
    assert adapter.run_calls == 4


def test_service_cancel_rejects_pending_solver(build_temp_path: Path) -> None:
    adapter = DeterministicArtifactAdapter()
    service = make_service(build_temp_path, adapter)
    started = service.start(
        "Reach a runout extension of 0.2 m.",
        workflow_id="service_cancel",
    )

    cancelled = service.cancel(started.workflow_id)

    assert cancelled.status == CalibrationWorkflowStatus.CANCELLED
    assert cancelled.awaiting_approval is False
    assert adapter.run_calls == 0


def test_resume_rejects_approval_checkpoint(build_temp_path: Path) -> None:
    service = make_service(build_temp_path, DeterministicArtifactAdapter())
    started = service.start(
        "Reach a runout extension of 0.2 m.",
        workflow_id="service_resume_guard",
    )

    with pytest.raises(AgentServiceError, match="awaiting approval"):
        service.resume(started.workflow_id)
