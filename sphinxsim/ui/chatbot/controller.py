"""Provider-neutral general conversation controller for the chatbot UI."""

from __future__ import annotations

from typing import Any

from sphinxsim.llm import get_llm, normalize_llm_provider


class ChatController:
    """Keep chat history and delegate responses to the existing LLM provider."""

    SYSTEM_PROMPT = (
        "You are a helpful conversational assistant in the SPHinXsim desktop app. "
        "Have a natural general conversation. Do not execute tools or simulations."
    )

    def __init__(self, provider: str | None = None) -> None:
        self.provider = normalize_llm_provider(provider)
        self.llm = get_llm(self.provider)
        self.messages: list[dict[str, str]] = [
            {"role": "system", "content": self.SYSTEM_PROMPT}
        ]

    def respond(self, text: str) -> str:
        """Append one user message, call the configured provider, and save the reply."""
        cleaned = text.strip()
        if not cleaned:
            raise ValueError("message must not be empty")
        self.messages.append({"role": "user", "content": cleaned})
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
