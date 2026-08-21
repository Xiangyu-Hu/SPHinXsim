"""Chainlit lifecycle callbacks for the standalone general chatbot."""

from __future__ import annotations

import asyncio
import mimetypes

import chainlit as cl

from sphinxsim.llm.chat_attachments import AttachmentError, ChatAttachment
from sphinxsim.ui.chatbot.controller import ChatController


CONTROLLER_SESSION_KEY = "sphinxsim_chat_controller"


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
            "Hello. I am the SPHinXsim general chatbot. I can have a normal "
            "conversation, but this version is not connected to SPHinXsim project "
            "knowledge, AgentCAE, or simulation tools."
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
