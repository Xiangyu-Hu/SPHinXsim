"""Provider-neutral general conversation controller for the chatbot UI."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any
import os
from pathlib import Path
from uuid import uuid4

from sphinxsim.llm import get_llm, normalize_llm_provider
from sphinxsim.llm.chat_attachments import ChatAttachment


class AgentChatSession:
    """Chat-facing adapter for the existing AgentCAE lifecycle API.

    The adapter deliberately contains no simulation logic.  It translates a
    small, explicit set of chat commands into the same facade calls used by
    the Agent CLI, so the Chatbot and CLI observe identical workflow state.
    """

    def __init__(
        self,
        *,
        project_root: str | Path | None = None,
        workspace: str | Path | None = None,
    ) -> None:
        from sphinxsim.agent import AgentCAE

        root = Path(project_root or os.environ.get(
            "SPHINXSIM_AGENT_PROJECT_ROOT", Path.cwd()
        )).resolve()
        selected_workspace = workspace or os.environ.get(
            "SPHINXSIM_AGENT_WORKSPACE", ".build-temp/chatbot_agent"
        )
        self.agent = AgentCAE(root, selected_workspace)
        self.workflow_id: str | None = None

    @property
    def has_workflow(self) -> bool:
        return self.workflow_id is not None

    def start(self, prompt: str) -> str:
        if not prompt.strip():
            raise ValueError("agent start requires a simulation request")
        if self.workflow_id is not None:
            raise ValueError(
                f"an Agent workflow is already active: {self.workflow_id}"
            )
        identifier = f"chat_{uuid4().hex[:12]}"
        status = self.agent.start(prompt, workflow_id=identifier)
        self.workflow_id = status.workflow_id
        runtime = self.agent.runtime
        project_root = getattr(runtime, "project_root", Path.cwd())
        return (
            f"📁 **Agent project root:** `{project_root}`\n"
            f"💾 **Agent workspace:** `{runtime.workspace}`\n\n"
            + self._format_status(status, include_plan=True)
        )

    def approve(self) -> str:
        return self._format_status(self._require_workflow("approve"), include_plan=False, action="approve")

    def submit(self, action="approve"):
        return self.agent.submit(self._require_id(), action=action)

    def execution_status(self):
        return self.agent.execution_status(self._require_id())

    def open_preview(self):
        from sphinxsim.visualization.agent_preview import launch_agent_preview
        return launch_agent_preview(self.agent.runtime.project_root,
                                    self.agent.runtime.workspace, self._require_id())

    def attach(self, workflow_id):
        status = self.agent.status(workflow_id.strip())
        self.workflow_id = status.workflow_id
        return self._format_status(status, include_plan=status.awaiting_approval)

    def live_snapshot(self):
        from sphinxsim.ui.chatbot.visuals import render_vtp_snapshot
        frames = self.agent.live_results(self._require_id())
        if not frames:
            return None
        frame = frames[0]
        signature = (frame["path"], tuple(frame["version"]))
        if signature == getattr(self, "_last_frame", None):
            return None
        destination = self.agent.runtime.workspace / "artifacts" / self.workflow_id / "live" / (Path(frame["path"]).stem + ".png")
        image = render_vtp_snapshot(frame["path"], destination, title=f"Simulation time: {frame['time']:.6g} s")
        if image is None:
            return None
        self._last_frame = signature
        return (f"Live particle state — simulation time {frame['time']:.6g} s", image)

    def status(self) -> str:
        return self.format_status(self._require_workflow("status"), include_plan=False)

    def status_snapshot(self):
        """Return the provider-neutral status object for progress monitors."""
        return self.agent.status(self._require_id())

    @staticmethod
    def format_status(status, *, include_plan: bool, action: str | None = None) -> str:
        return AgentChatSession._format_status(
            status,
            include_plan=include_plan,
            action=action,
        )

    def cancel(self) -> str:
        return self._format_status(self.agent.cancel(self._require_id()), include_plan=False, action="cancel")

    def report(self) -> str:
        report = self.agent.report(self._require_id())
        result = report.best_result
        lines = [
            f"🏁 Agent report: {report.workflow_id}",
            f"📌 Status: {report.status.value}",
            f"🎯 Target: {report.target_metric} = {report.target_value:g} {getattr(report, 'target_unit', 'm')}",
            f"🔁 Iterations: {len(report.iterations)}",
        ]
        if result is not None:
            lines.extend([
                f"🎛️ Best parameter: {result.parameter_value:g} {report.parameter_unit}",
                f"📏 Best metric: {result.metric_value:.9g} m",
                f"✅ Absolute error: {result.absolute_error:.9g} m",
            ])
        lines.append(
            "Report JSON: "
            f"{self.agent.runtime.workspace / 'reports' / (report.workflow_id + '.json')}"
        )
        return "\n".join(lines)

    def latest_iteration_update(self) -> tuple[str, Path | None]:
        """Build a human-readable update and optional PNG for the latest run."""
        from sphinxsim.agent.analysis import VTPPositionReader
        from sphinxsim.ui.chatbot.visuals import render_vtp_snapshot

        report = self.agent.report(self._require_id(), save=False)
        if not report.iterations:
            return ("⏳ No completed simulation iteration is available yet.", None)
        item = report.iterations[-1]
        lines = [
            f"## ✅ Simulation {item.iteration}/{report.plan_summary.maximum_simulations} completed",
            "",
            "| Result | Value |",
            "|---|---:|",
            f"| 🎛️ {report.parameter_name} | {item.parameter_value:g} {report.parameter_unit} |",
            f"| 📏 {item.metric_name} | {item.metric_value:.9g} {report.plan_summary.target_unit} |",
            f"| 🎯 Absolute error | {item.absolute_error:.9g} {report.plan_summary.target_unit} |",
        ]
        output_dir = self.agent.runtime.store.run_directory(item.task_id, item.run_id)
        image_path: Path | None = None
        try:
            vtp_path, _time_value = VTPPositionReader().find_final_state(
                output_dir,
                report.plan_summary.body_name,
            )
            image_path = render_vtp_snapshot(
                vtp_path,
                self.agent.runtime.workspace
                / "artifacts"
                / report.workflow_id
                / f"iteration_{item.iteration:03d}.png",
                title=(
                    f"AgentCAE iteration {item.iteration}: "
                    f"{item.parameter_value:g} {report.parameter_unit}"
                ),
            )
            lines.append(f"🗂️ VTP: {vtp_path}")
            if image_path is not None:
                lines.append(f"🖼️ Snapshot: {image_path}")
        except (OSError, ValueError):
            lines.append("🗂️ VTP snapshot is not available for this iteration.")
        return ("\n".join(lines), image_path)

    def handle(self, text: str) -> str | None:
        """Handle an explicit ``agent`` command, or return ``None`` for chat."""
        cleaned = text.strip()
        lowered = cleaned.lower()
        if lowered == "agent preview":
            self.open_preview()
            return "Preview window opened on the Agent computer. It follows the current run."
        if lowered.startswith("agent attach "):
            return self.attach(cleaned[len("agent attach "):])
        if lowered == "agent resume":
            self.submit("resume")
            return "Workflow recovery started. Use agent status to inspect progress."
        if lowered in {"agent help", "/agent help"}:
            return (
                "Agent commands:\n"
                "agent start <simulation request>\n"
                "agent approve\n"
                "agent status\n"
                "agent preview\n"
                "agent attach <workflow_id>\n"
                "agent resume\n"
                "agent report\n"
                "agent cancel"
            )
        prefixes = ("agent start ", "/agent start ", "start agent: ")
        for prefix in prefixes:
            if lowered.startswith(prefix):
                return self.start(cleaned[len(prefix):])
        if lowered in {"agent approve", "/agent approve", "approve agent"}:
            return self.approve()
        if lowered in {"agent status", "/agent status", "status agent"}:
            return self.status()
        if lowered in {"agent report", "/agent report", "report agent"}:
            return self.report()
        if lowered in {"agent cancel", "/agent cancel", "cancel agent"}:
            return self.cancel()
        return None

    def _require_id(self) -> str:
        if self.workflow_id is None:
            raise ValueError("no Agent workflow is active; use agent start first")
        return self.workflow_id

    def _require_workflow(self, action: str):
        workflow_id = self._require_id()
        if action == "approve":
            return self.agent.approve(workflow_id)
        return self.agent.status(workflow_id)

    @staticmethod
    def _format_status(status, *, include_plan: bool, action: str | None = None) -> str:
        lines = [
            "## 🤖 AgentCAE Workflow",
            "",
            f"- 🆔 **Workflow:** `{status.workflow_id}`",
            f"- 📌 **Status:** `{status.status.value}`",
            f"- 🧭 **Phase:** `{status.phase}`",
            f"- 🔁 **Simulations:** {status.iteration_count}/{status.plan_summary.maximum_simulations}",
            f"- 🎯 **Target:** {status.target_metric} = {status.target_value:g} {status.plan_summary.target_unit}",
        ]
        if include_plan:
            plan = status.plan_summary
            lines.extend([
                "",
                "## 📋 Agent Plan:",
                "",
                "| Item | Plan |",
                "|---|---|",
                f"| 🧪 Case | `{plan.case_name}` |",
                f"| 🧱 Body | `{plan.body_name}` |",
                f"| 🎛️ Parameter | `{plan.parameter_name}`: {plan.parameter_bounds[0]:g}–{plan.parameter_bounds[1]:g} {plan.parameter_unit} |",
                f"| 🎯 Objective | `{plan.target_metric}` = {plan.target_value:g} ± {plan.target_tolerance:g} {plan.target_unit} |",
                f"| 🔁 Budget | Maximum {plan.maximum_simulations} simulations |",
                "",
                f"> 📝 {plan.generation_description}",
                "",
                "### ⏸️ Approval required",
                "Type `agent approve` to execute this Plan or `agent cancel` to reject it.",
            ])
        if action == "approve":
            if status.status.value == "running":
                lines.insert(1, "✅ **Plan approved — execution is in progress.**")
            else:
                lines.insert(
                    1,
                    "🏁 **Plan approved — this execution cycle has completed.**",
                )
        elif action == "cancel":
            lines.insert(1, "🛑 **Cancellation requested; stopping the solver.**" if status.phase == "cancelling" else "🛑 **The Agent workflow was cancelled.**")
        if status.best_metric_value is not None:
            lines.extend([
                "",
                "### ⭐ Current best",
                f"- 🎛️ **Parameter:** {status.best_parameter_value:g} {status.plan_summary.parameter_unit}",
                f"- 📏 **Metric:** {status.best_metric_value:.9g} {status.plan_summary.target_unit}",
                f"- 🎯 **Error:** {status.best_absolute_error:.9g} {status.plan_summary.target_unit}",
            ])
        if status.error_message:
            lines.append(f"\n⚠️ **Message:** {status.error_message}")
        return "\n".join(lines)


class ChatController:
    """Keep chat history and delegate responses to the existing LLM provider."""

    SYSTEM_PROMPT = (
        "You are a helpful general-purpose conversational assistant in the "
        "SPHinXsim chatbot. Have a natural conversation. You are not connected "
        "to the local SPHinXsim repository, project knowledge, tools, or simulation "
        "runtime. Do not claim that you have read them, and do not execute tools "
        "or simulations."
    )

    def __init__(self, provider: str | None = None) -> None:
        self.provider = normalize_llm_provider(provider)
        self.llm = get_llm(self.provider)
        self.messages: list[dict[str, Any]] = [
            {"role": "system", "content": self.SYSTEM_PROMPT}
        ]

    def respond(
        self,
        text: str,
        attachments: Sequence[ChatAttachment] = (),
    ) -> str:
        """Append one user message, call the configured provider, and save the reply."""
        cleaned = text.strip()
        if not cleaned and not attachments:
            raise ValueError("message must contain text or an attachment")
        user_message: dict[str, Any] = {"role": "user", "content": cleaned}
        if attachments:
            user_message["attachments"] = list(attachments)
        self.messages.append(user_message)
        try:
            response = self.llm.chat(self.messages)
        except Exception:
            self.messages.pop()
            raise
        if not isinstance(response, str) or not response.strip():
            self.messages.pop()
            raise ValueError("the configured LLM returned an empty chat response")
        answer = response.strip()
        self.messages.append({"role": "assistant", "content": answer})
        return answer

    def context(self) -> list[dict[str, Any]]:
        """Return a copy of the current provider-neutral conversation history."""
        return [dict(message) for message in self.messages]
