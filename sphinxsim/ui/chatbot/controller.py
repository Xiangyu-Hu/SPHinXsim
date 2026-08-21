"""Provider-neutral general conversation controller for the chatbot UI."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from sphinxsim.llm import get_llm, normalize_llm_provider
from sphinxsim.llm.chat_attachments import ChatAttachment


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
