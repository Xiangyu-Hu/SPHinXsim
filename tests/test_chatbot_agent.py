"""Tests for the Chatbot adapter over the existing AgentCAE facade."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from sphinxsim.ui.chatbot.controller import AgentChatSession


def _status(workflow_id: str = "chat_test"):
    plan = SimpleNamespace(
        case_name="column_collapse",
        generation_description="Generate a 2D granular column collapse case.",
        body_name="GranularBody",
        parameter_name="friction_angle_degrees",
        parameter_bounds=(0.0, 40.0),
        parameter_unit="degree",
        target_metric="runout_extension",
        target_value=0.2,
        target_tolerance=0.005,
        target_unit="m",
        maximum_simulations=8,
    )
    return SimpleNamespace(
        workflow_id=workflow_id,
        status=SimpleNamespace(value="running"),
        phase="awaiting_plan_approval",
        plan_summary=plan,
        iteration_count=0,
        target_metric="runout_extension",
        target_value=0.2,
        best_parameter_value=None,
        best_metric_value=None,
        best_absolute_error=None,
        error_message=None,
    )


class _FakeAgent:
    last: "_FakeAgent | None" = None

    def __init__(self, project_root, workspace) -> None:
        self.project_root = Path(project_root)
        self.workspace = Path(workspace)
        self.runtime = SimpleNamespace(
            workspace=self.workspace,
            project_root=self.project_root,
            store=SimpleNamespace(
                run_directory=lambda task_id, run_id: self.workspace / task_id / run_id,
            ),
        )
        self.calls: list[tuple[str, str]] = []
        _FakeAgent.last = self

    def start(self, prompt, *, workflow_id):
        self.calls.append(("start", prompt))
        return _status(workflow_id)

    def approve(self, workflow_id):
        self.calls.append(("approve", workflow_id))
        result = _status(workflow_id)
        result.phase = "calibrating"
        return result

    def status(self, workflow_id):
        self.calls.append(("status", workflow_id))
        return _status(workflow_id)

    def cancel(self, workflow_id):
        self.calls.append(("cancel", workflow_id))
        result = _status(workflow_id)
        result.status = SimpleNamespace(value="cancelled")
        result.phase = "cancelled"
        return result

    def report(self, workflow_id, *, save=True):
        self.calls.append(("report", workflow_id))
        return SimpleNamespace(
            workflow_id=workflow_id,
            status=SimpleNamespace(value="converged"),
            target_metric="runout_extension",
            target_value=0.2,
            target_unit="m",
            parameter_name="friction_angle_degrees",
            parameter_unit="degree",
            plan_summary=SimpleNamespace(
                maximum_simulations=8,
                body_name="GranularBody",
                target_unit="m",
            ),
            iterations=[
                SimpleNamespace(
                    iteration=1,
                    parameter_value=0.0,
                    parameter_unit="degree",
                    metric_name="runout_extension",
                    metric_value=0.299,
                    absolute_error=0.099,
                    task_id="task_1",
                    run_id="run_001",
                ),
                SimpleNamespace(
                    iteration=2,
                    parameter_value=20.0,
                    parameter_unit="degree",
                    metric_name="runout_extension",
                    metric_value=0.201,
                    absolute_error=0.001,
                    task_id="task_2",
                    run_id="run_001",
                ),
            ],
            best_result=SimpleNamespace(
                parameter_value=14.375,
                metric_value=0.1968,
                absolute_error=0.0032,
            ),
        )


def test_chat_agent_adapter_maps_current_lifecycle(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr("sphinxsim.agent.AgentCAE", _FakeAgent)
    session = AgentChatSession(project_root=tmp_path, workspace=tmp_path / "agent")

    plan = session.handle(
        "agent start Calibrate a 2D column-collapse runout to 0.2 m."
    )
    assert plan is not None
    assert "Agent Plan:" in plan
    assert "agent approve" in plan

    approved = session.handle("agent approve")
    assert approved is not None
    assert "execution is in progress" in approved

    status = session.handle("agent status")
    assert status is not None
    assert "Workflow:" in status

    report = session.handle("agent report")
    assert report is not None
    assert "Best parameter: 14.375 degree" in report
    assert "Best metric: 0.1968 m" in report
    assert [call[0] for call in _FakeAgent.last.calls] == [
        "start",
        "approve",
        "status",
        "report",
    ]


def test_non_agent_message_is_left_for_general_chat(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr("sphinxsim.agent.AgentCAE", _FakeAgent)
    session = AgentChatSession(project_root=tmp_path, workspace=tmp_path / "agent")

    assert session.handle("Tell me about SPH.") is None


def test_latest_iteration_update_reports_metric_and_snapshot(
    monkeypatch,
    tmp_path,
) -> None:
    monkeypatch.setattr("sphinxsim.agent.AgentCAE", _FakeAgent)
    session = AgentChatSession(project_root=tmp_path, workspace=tmp_path / "agent")
    session.workflow_id = "chat_test"

    monkeypatch.setattr(
        "sphinxsim.agent.analysis.VTPPositionReader.find_final_state",
        lambda _reader, _output, _body: (tmp_path / "final.vtp", 1.0),
    )
    monkeypatch.setattr(
        "sphinxsim.ui.chatbot.visuals.render_vtp_snapshot",
        lambda _source, destination, **_kwargs: destination,
    )
    update, image = session.latest_iteration_update()

    assert "Simulation 2/8 completed" in update
    assert "runout_extension | 0.201 m" in update
    assert image is not None
    assert image.name == "iteration_002.png"
