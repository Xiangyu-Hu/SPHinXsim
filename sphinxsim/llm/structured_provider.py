"""Provider-neutral structured JSON transport for Agent LLM roles."""

from __future__ import annotations

import json
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict

from . import LLMProviderName, get_llm, normalize_llm_provider
from .common import strip_code_fences


class StructuredProviderError(ValueError):
    """Raised when an LLM provider does not return one JSON object."""


class StructuredJSONBackend(Protocol):
    """Transport boundary shared by every structured Agent role."""

    provider_name: str

    def complete_json(
        self,
        *,
        system_prompt: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]: ...


class StructuredProviderCall(BaseModel):
    """Auditable provider-independent representation of one LLM call."""

    model_config = ConfigDict(extra="forbid")

    system_prompt: str
    payload: dict[str, Any]


class ExistingLLMStructuredBackend:
    """Adapt the existing JSON-generation providers to structured Agent calls."""

    def __init__(
        self,
        provider_name: str | None = None,
        *,
        llm: Any | None = None,
    ) -> None:
        self.provider_name: LLMProviderName = normalize_llm_provider(provider_name)
        if self.provider_name == "mock":
            raise StructuredProviderError(
                "mock structured calls require an explicit deterministic response"
            )
        self.llm = llm if llm is not None else get_llm(self.provider_name)
        self.calls: list[StructuredProviderCall] = []

    def complete_json(
        self,
        *,
        system_prompt: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        call = StructuredProviderCall(
            system_prompt=system_prompt,
            payload=payload,
        )
        self.calls.append(call.model_copy(deep=True))
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
        ]
        if self.provider_name == "ollama":
            value = self.llm._post_chat(messages=messages, format_json=True)
            return self._require_object(value)
        if self.provider_name == "nvidia_nim":
            content = self.llm._post_chat(messages=messages, temperature=0.0)
            return self._parse_text(content)
        if self.provider_name == "openai":
            response = self.llm.client.chat.completions.create(
                model=self.llm.model,
                messages=messages,
                temperature=0,
            )
            content = response.choices[0].message.content or ""
            return self._parse_text(content)
        raise StructuredProviderError(
            f"unsupported structured LLM provider: {self.provider_name}"
        )

    @staticmethod
    def _parse_text(content: str) -> dict[str, Any]:
        try:
            value = json.loads(strip_code_fences(content).strip())
        except (AttributeError, json.JSONDecodeError) as exc:
            raise StructuredProviderError(
                "LLM provider returned invalid JSON for structured output"
            ) from exc
        return ExistingLLMStructuredBackend._require_object(value)

    @staticmethod
    def _require_object(value: Any) -> dict[str, Any]:
        if not isinstance(value, dict):
            raise StructuredProviderError(
                "LLM provider must return exactly one JSON object"
            )
        return value


def create_structured_backend(
    provider_name: str | None = None,
) -> ExistingLLMStructuredBackend:
    """Create a backend from SPHINXSIM_LLM_PROVIDER and existing provider settings."""
    return ExistingLLMStructuredBackend(provider_name)
