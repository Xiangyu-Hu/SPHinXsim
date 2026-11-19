"""Provider-neutral attachment values and message conversion helpers."""

from __future__ import annotations

import base64
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable


SUPPORTED_IMAGE_TYPES = frozenset(
    {
        "image/gif",
        "image/jpeg",
        "image/png",
        "image/webp",
    }
)
SUPPORTED_DOCUMENT_TYPES = frozenset({"application/pdf"})
SUPPORTED_ATTACHMENT_TYPES = SUPPORTED_IMAGE_TYPES | SUPPORTED_DOCUMENT_TYPES
MAX_ATTACHMENT_BYTES = 20 * 1024 * 1024


class AttachmentError(ValueError):
    """Base class for safe, user-facing attachment errors."""


class UnsupportedAttachmentError(AttachmentError):
    """The selected provider or model cannot consume an attachment."""


@dataclass(frozen=True)
class ChatAttachment:
    """One local file uploaded with a user chat message."""

    path: Path
    name: str
    media_type: str

    def __post_init__(self) -> None:
        resolved = Path(self.path).resolve()
        object.__setattr__(self, "path", resolved)
        object.__setattr__(self, "name", self.name.strip() or resolved.name)
        object.__setattr__(self, "media_type", self.media_type.lower().strip())
        if not resolved.is_file():
            raise AttachmentError(f"Uploaded file is no longer available: {self.name}")
        if self.media_type not in SUPPORTED_ATTACHMENT_TYPES:
            raise UnsupportedAttachmentError(
                f"Unsupported attachment type '{self.media_type}' for {self.name}. "
                "This chatbot currently accepts PDF, PNG, JPEG, GIF, and WebP files."
            )
        size = resolved.stat().st_size
        if size > MAX_ATTACHMENT_BYTES:
            raise AttachmentError(
                f"Attachment {self.name} is too large ({size / 1024 / 1024:.1f} MB). "
                "The maximum supported size is 20 MB per file."
            )

    def base64_data(self) -> str:
        return base64.b64encode(self.path.read_bytes()).decode("ascii")

    def data_url(self) -> str:
        return f"data:{self.media_type};base64,{self.base64_data()}"


def message_attachments(message: dict[str, Any]) -> tuple[ChatAttachment, ...]:
    raw = message.get("attachments", ())
    return tuple(item for item in raw if isinstance(item, ChatAttachment))


def all_attachments(messages: Iterable[dict[str, Any]]) -> tuple[ChatAttachment, ...]:
    return tuple(
        attachment
        for message in messages
        for attachment in message_attachments(message)
    )


def text_only_messages(messages: Iterable[dict[str, Any]]) -> list[dict[str, str]]:
    """Drop internal attachment metadata for text-only provider calls."""
    return [
        {
            "role": str(message.get("role", "user")),
            "content": str(message.get("content", "")),
        }
        for message in messages
    ]


def unsupported_provider_message(provider: str, model: str, detail: str = "") -> str:
    suffix = f" Detail: {detail}" if detail else ""
    return (
        f"The configured {provider} model '{model}' could not process the attached "
        "file(s). It may support text only or may not support this attachment type. "
        f"Select a multimodal model or send a text-only message.{suffix}"
    )
