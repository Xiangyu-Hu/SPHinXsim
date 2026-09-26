"""Chainlit lifecycle callbacks for the standalone general chatbot."""

from __future__ import annotations

import asyncio
import mimetypes
import os
from pathlib import Path

import chainlit as cl

from sphinxsim.llm.chat_attachments import AttachmentError, ChatAttachment
from sphinxsim.ui.chatbot.controller import AgentChatSession, ChatController


CONTROLLER_SESSION_KEY = "sphinxsim_chat_controller"
AGENT_SESSION_KEY = "sphinxsim_agent_chat_session"
AGENT_TASK_KEY = "sphinxsim_agent_execution_task"


async def _new_controller() -> ChatController:
    """Initialize provider clients away from Chainlit's event loop."""
    return await asyncio.to_thread(ChatController)


def _message_attachments(message: cl.Message) -> tuple[ChatAttachment, ...]:
    """Convert Chainlit upload elements into provider-neutral local files."""
    converted: list[ChatAttachment] = []
    for element in getattr(message, "elements", None) or []:
        path = getattr(element, "path", None)
        name = getattr(element, "name", "") or "attachment"
        media_type = getattr(element, "mime", None)
        if not media_type:
            media_type = mimetypes.guess_type(name)[0] or "application/octet-stream"
        if not path:
            raise AttachmentError(
                f"Uploaded attachment {name} does not have a readable local file."
            )
        converted.append(
            ChatAttachment(path=path, name=name, media_type=media_type)
        )
    return tuple(converted)


def _agent_context_text() -> str:
    root = Path(os.environ.get("SPHINXSIM_AGENT_PROJECT_ROOT", Path.cwd())).resolve()
    workspace = Path(
        os.environ.get(
            "SPHINXSIM_AGENT_WORKSPACE",
            str(root / ".build-temp" / "chatbot_agent"),
        )
    )
    if not workspace.is_absolute():
        workspace = (root / workspace).resolve()
    return f"Agent project root: {root}\nAgent workspace: {workspace}"


def _is_agent_approve(text: str) -> bool:
    return text.strip().lower() in {
        "agent approve",
        "/agent approve",
        "approve agent",
    }


def _is_agent_start(text: str) -> bool:
    lowered = text.strip().lower()
    return any(
        lowered.startswith(prefix)
        for prefix in ("agent start ", "/agent start ", "start agent: ")
    )


def _progress_bar(completed: int, total: int, width: int = 10) -> str:
    fraction = 0.0 if total <= 0 else min(1.0, max(0.0, completed / total))
    filled = round(fraction * width)
    return "🟩" * filled + "⬜" * (width - filled)


def _progress_signature(status) -> tuple[object, ...]:
    return (
        status.status.value,
        status.phase,
        status.current_node,
        status.current_candidate_value,
        status.iteration_count,
        status.best_parameter_value,
        status.best_metric_value,
        status.best_absolute_error,
        status.error_message,
    )


def _progress_message(status) -> str:
    node = status.next_nodes[0] if status.next_nodes else "workflow_complete"
    activities = {
        "prepare_case": "🧱 Preparing the isolated simulation case.",
        "decide": "🧠 Selecting the next calibration candidate.",
        "execute_candidate": "🚀 Running SPHinXsim C++ and analyzing the VTP result.",
        "replan": "🔄 Evaluating recovery after a failed candidate.",
        "workflow_complete": "🏁 The workflow has reached a terminal state.",
    }
    maximum = status.plan_summary.maximum_simulations
    lines = [
        "## 🚦 Agent Progress",
        "",
        f"**{_progress_bar(status.iteration_count, maximum)} "
        f"{status.iteration_count}/{maximum} simulations**",
        "",
        f"- 🧭 **Phase:** `{status.phase}`",
        f"- ⚙️ **Activity:** {activities.get(node, node)}",
    ]
    if status.current_candidate_value is not None:
        lines.append(
            f"- 🎛️ **Current candidate:** {status.current_candidate_value:g} "
            f"{status.plan_summary.parameter_unit}"
        )
    if status.best_metric_value is not None:
        lines.extend([
            f"- ⭐ **Best parameter:** {status.best_parameter_value:g} {status.plan_summary.parameter_unit}",
            f"- 📏 **Best metric:** {status.best_metric_value:.9g} {status.plan_summary.target_unit}",
            f"- 🎯 **Best error:** {status.best_absolute_error:.9g} {status.plan_summary.target_unit}",
        ])
    if status.error_message:
        lines.append(f"- ⚠️ **Message:** {status.error_message}")
    return "\n".join(lines)


async def _send_agent_update(content: str, image_path=None) -> None:
    elements = []
    image_type = getattr(cl, "Image", None)
    if image_path is not None and image_type is not None:
        elements.append(
            image_type(
                path=str(image_path),
                name=image_path.name,
                display="inline",
            )
        )
    message_kwargs = {
        "content": content,
        "author": "SPHinXsim AgentCAE",
    }
    if elements:
        message_kwargs["elements"] = elements
    await cl.Message(**message_kwargs).send()


async def _run_agent_execution(agent_session: AgentChatSession, action="approve") -> None:
    """Submit a durable worker and observe it without owning its lifetime."""
    try:
        await asyncio.to_thread(agent_session.submit, action)
    except Exception as exc:
        await _send_agent_update(f"Agent execution could not start: {exc}")
        return
    last_signature = None
    last_iteration = 0
    last_live = 0.0
    import time
    while True:
        try:
            execution = await asyncio.to_thread(agent_session.execution_status)
            status = await asyncio.to_thread(agent_session.status_snapshot)
            signature = _progress_signature(status)
            if signature != last_signature:
                await _send_agent_update(_progress_message(status))
                last_signature = signature
            if status.iteration_count > last_iteration:
                update, image_path = await asyncio.to_thread(agent_session.latest_iteration_update)
                await _send_agent_update(update, image_path)
                last_iteration = status.iteration_count
            if time.monotonic() - last_live >= 5:
                live = await asyncio.to_thread(agent_session.live_snapshot)
                if live is not None:
                    await _send_agent_update(*live)
                last_live = time.monotonic()
            if execution.get("state") not in {"starting", "running", "cancelling"}:
                if execution.get("state") == "interrupted":
                    await _send_agent_update("Worker interrupted. Use `agent resume` to recover. " + execution.get("error_message", ""))
                else:
                    await _send_agent_update(await asyncio.to_thread(agent_session.report))
                break
        except Exception as exc:
            await _send_agent_update(f"Progress temporarily unavailable: {exc}")
            # A visualization failure must not hide a terminal worker forever.
            execution = await asyncio.to_thread(agent_session.execution_status)
            if execution.get("state") not in {"starting", "running", "cancelling"}:
                break
        await asyncio.sleep(2.0)


@cl.on_chat_start
async def on_chat_start() -> None:
    """Create an isolated LLM conversation for this browser session."""
    try:
        controller = await _new_controller()
    except Exception as exc:
        await cl.Message(
            content=f"Unable to initialize the configured LLM provider: {exc}",
            author="SPHinXsim",
        ).send()
        return

    cl.user_session.set(CONTROLLER_SESSION_KEY, controller)
    await cl.Message(
        content=(
            "# 👋 SPHinXsim Chatbot\n\n"
            "I can have a normal conversation and run the current AgentCAE "
            "column-collapse workflow.\n\n"
            "### 🤖 Agent commands\n\n"
            "- `agent start <request>` — draft a simulation Plan\n"
            "- `agent approve` — approve and run the Plan\n"
            "- `agent status` — inspect current progress\n"
            "- `agent report` — show the final report\n"
            "- `agent cancel` — cancel the workflow\n\n"
            f"{_agent_context_text()}"
        ),
        author="SPHinXsim",
    ).send()


@cl.on_message
async def on_message(message: cl.Message) -> None:
    """Send one user turn through the shared provider-neutral LLM interface."""
    controller = cl.user_session.get(CONTROLLER_SESSION_KEY)
    if controller is None:
        try:
            controller = await _new_controller()
            cl.user_session.set(CONTROLLER_SESSION_KEY, controller)
        except Exception as exc:
            await cl.Message(
                content=f"Unable to initialize the configured LLM provider: {exc}",
                author="SPHinXsim",
            ).send()
            return

    try:
        agent_session = cl.user_session.get(AGENT_SESSION_KEY)
        if agent_session is None:
            agent_session = AgentChatSession()
            cl.user_session.set(AGENT_SESSION_KEY, agent_session)
        if _is_agent_start(message.content):
            await _send_agent_update(
                "## 🧠 Drafting the Agent Plan\n\n"
                "🔍 Interpreting the request, retrieving capability context, and "
                "validating a structured calibration Plan..."
            )
        if _is_agent_approve(message.content) or message.content.strip().lower() == "agent resume":
            active_task = cl.user_session.get(AGENT_TASK_KEY)
            if active_task is not None and not active_task.done():
                await cl.Message(
                    content=(
                        "⏳ **Agent execution is already running.** "
                        "Use `agent status` for the latest state."
                    ),
                    author="SPHinXsim AgentCAE",
                ).send()
                return
            task = asyncio.create_task(_run_agent_execution(agent_session, "resume" if message.content.strip().lower() == "agent resume" else "approve"))
            cl.user_session.set(AGENT_TASK_KEY, task)
            await cl.Message(
                content=(
                    "## ✅ Plan approved\n\n"
                    "🚀 Agent execution is running in the background. Progress "
                    "updates and result snapshots will appear here automatically.\n\n"
                    "> You can also use `agent status` at any time."
                ),
                author="SPHinXsim AgentCAE",
            ).send()
            return
        try:
            agent_answer = await asyncio.to_thread(agent_session.handle, message.content)
        except Exception as exc:
            await cl.Message(
                content=f"❌ **AgentCAE command failed:** {exc}",
                author="SPHinXsim AgentCAE",
            ).send()
            return
        if agent_answer is not None:
            await cl.Message(content=agent_answer, author="SPHinXsim AgentCAE").send()
            return
        attachments = _message_attachments(message)
        answer = await asyncio.to_thread(
            controller.respond,
            message.content,
            attachments,
        )
    except AttachmentError as exc:
        await cl.Message(content=str(exc), author="SPHinXsim").send()
        return
    except Exception as exc:
        await cl.Message(
            content=f"The configured LLM could not answer: {exc}",
            author="SPHinXsim",
        ).send()
        return

    await cl.Message(content=answer, author="SPHinXsim").send()
