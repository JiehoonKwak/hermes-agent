"""Pure pre-retain normalization for Hindsight auto-retained turns.

This module intentionally knows only Hermes-authored transport/runtime envelopes.
It does not classify natural-language durability; Hindsight's retain mission/model
continues to own that semantic decision. Keeping the helper standalone makes the
local patch easy to drop when upstream gains equivalent provenance support.
"""

from __future__ import annotations

import json
import re
from typing import Any, Mapping, Sequence

_SCHEMA = "hermes-completed-turn-v2"
_STEER_OPEN = (
    "[OUT-OF-BAND USER MESSAGE — a direct message from the user, "
    "delivered mid-turn; not tool output]"
)
_STEER_CLOSE = "[/OUT-OF-BAND USER MESSAGE]"

_DISCORD_TRIGGER_RE = re.compile(
    r"^\[Triggering message id:\s*`?[^\]`]+`?\s*[—-].*?\]\s*\n+",
    re.DOTALL,
)
_REPLY_PREFIXES = (
    "[Replying to your previous message: ",
    "[Replying to: ",
)
_VOICE_STATE_RE = re.compile(r"^\[Voice channel now:.*?\]\s*$", re.MULTILINE)
_VOICE_TRANSCRIPT_HEADER_RE = re.compile(r"^\[Voice transcript\]\s*", re.IGNORECASE)
_ATTACHMENT_PLACEHOLDER_RE = re.compile(
    r"^\[(?:base64 .*? data URL omitted from Hindsight retain.*?|"
    r"(?:image|video|audio|document|attachment).*?omitted from Hindsight retain.*?|"
    r"User sent (?:an image|audio|a video|a file):.*?|"
    r"(?:image|video|audio|document) ['\"].*?['\"] saved at:.*?)\]\s*$",
    re.IGNORECASE | re.MULTILINE,
)
_MULTIMODAL_IMAGE_MARKER_RE = re.compile(
    r"^\[\d+\s+images?\]\s*",
    re.IGNORECASE,
)
_ATTACHMENT_NOTE_RE = re.compile(
    r"^\[The user sent an? (?:audio file attachment|video attachment|"
    r"(?:text )?(?:document|file) attachment|document).*?\]\s*",
    re.IGNORECASE | re.DOTALL,
)
_CONTENT_OF_RE = re.compile(r"^\[Content of [^\]]+\]:\s*", re.IGNORECASE)
_SHARED_SENDER_RE = re.compile(r"^\[([^\]\n]{1,160})\]\s+(.*)$", re.DOTALL)

_INTERNAL_EVENT_PREFIXES = (
    "[ASYNC DELEGATION BATCH COMPLETE",
    "[ASYNC DELEGATION COMPLETE",
    "[IMPORTANT: Background process ",
    "[IMPORTANT: Watch patterns disabled for process ",
    "[System: Your previous response was truncated",
    "[System: The previous response was cut off",
    "[System: Your previous tool call",
    "[Your active task list was preserved across context compression]",
)


def _text_content(message: Mapping[str, Any]) -> str:
    content = message.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for part in content:
            if isinstance(part, Mapping):
                text = part.get("text")
                if isinstance(text, str):
                    parts.append(text)
            elif isinstance(part, str):
                parts.append(part)
        return "\n".join(parts)
    return ""


def extract_current_turn_steers(
    messages: Sequence[Mapping[str, Any]] | None,
) -> list[str]:
    """Extract exact mid-turn user steers after the latest user message only."""
    if not messages:
        return []
    latest_user = -1
    for index, message in enumerate(messages):
        if str(message.get("role") or "").lower() == "user":
            latest_user = index

    found: list[str] = []
    for message in messages[latest_user + 1 :]:
        content = _text_content(message)
        pos = 0
        while content:
            start = content.find(_STEER_OPEN, pos)
            if start < 0:
                break
            start += len(_STEER_OPEN)
            end = content.find(_STEER_CLOSE, start)
            if end < 0:
                break
            steer = content[start:end].strip()
            if steer:
                found.append(steer)
            pos = end + len(_STEER_CLOSE)
    return found


def _split_reply(text: str) -> tuple[str, str]:
    for prefix in _REPLY_PREFIXES:
        if not text.startswith(prefix):
            continue
        boundary = text.find("]\n\n")
        if boundary < 0:
            return text, ""
        quote = text[len(prefix) : boundary].strip().strip('"')
        return text[boundary + 3 :].lstrip(), quote
    return text, ""


def _strip_attachment_notes(text: str) -> tuple[str, bool]:
    omitted = False
    while True:
        match = _ATTACHMENT_NOTE_RE.match(text)
        if not match:
            break
        omitted = True
        text = text[match.end() :].lstrip()

    # Older gateway builds could inline a text document before an explicit user
    # request sentinel. Preserve only the user's request when that boundary exists.
    if _CONTENT_OF_RE.match(text):
        sentinel = "\n\n[User request]\n"
        if sentinel in text:
            _, text = text.split(sentinel, 1)
            omitted = True
    return text, omitted


def _strip_shared_sender(
    text: str,
    *,
    shared_session: bool,
    user_name: str,
) -> tuple[str, str]:
    if not shared_session:
        return text, ""
    match = _SHARED_SENDER_RE.match(text)
    if not match:
        return text, ""
    candidate = match.group(1).strip()
    # ``[label] body`` is valid user-authored text too (for example
    # ``[TODO] ...``), so the wrapper is ambiguous without the current turn's
    # structured gateway sender. Only unwrap an exact match. Cached agents can
    # hold a stale initialize-time user name in shared sessions; callers pass
    # the per-turn name separately.
    expected = str(user_name or "").strip()
    if not expected or candidate != expected:
        return text, ""
    return match.group(2).lstrip(), candidate


def _is_internal_event(text: str) -> bool:
    return text.startswith(_INTERNAL_EVENT_PREFIXES)


def normalize_auto_retain_turn(
    user_content: str,
    assistant_content: str,
    *,
    user_name: str = "User",
    agent_name: str = "Hermes Agent",
    platform: str = "",
    chat_type: str = "",
    messages: Sequence[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Build a provenance payload from one completed Hermes turn."""
    text = str(user_content or "").strip()
    assistant = str(assistant_content or "").strip()
    quoted = ""

    # Reply is the outermost gateway wrapper; preserve its antecedent separately.
    text, quoted = _split_reply(text)
    text = _DISCORD_TRIGGER_RE.sub("", text, count=1).strip()
    text, attachment_omitted = _strip_attachment_notes(text)

    # Backfill is context for the main model, not fresh retain evidence.
    new_message_marker = "\n\n[New message]\n"
    if new_message_marker in text:
        text = text.rsplit(new_message_marker, 1)[1].strip()

    internal_kind = ""
    if _is_internal_event(text):
        internal_kind = "internal_runtime_event"
        text = ""

    # These exact Hermes transport markers must be removed before interpreting
    # a shared-session sender wrapper. Otherwise ``[Voice transcript]`` and
    # ``[1 image]`` are indistinguishable from ``[sender name]``.
    text = _VOICE_STATE_RE.sub("", text).strip()
    text = _VOICE_TRANSCRIPT_HEADER_RE.sub("", text, count=1).strip()
    if _ATTACHMENT_PLACEHOLDER_RE.search(text):
        attachment_omitted = True
        text = _ATTACHMENT_PLACEHOLDER_RE.sub("", text).strip()
    if _MULTIMODAL_IMAGE_MARKER_RE.match(text):
        attachment_omitted = True
        text = _MULTIMODAL_IMAGE_MARKER_RE.sub("", text, count=1).strip()

    shared = str(chat_type or "").lower() in {"group", "channel", "thread"}
    text, sender = _strip_shared_sender(
        text,
        shared_session=shared,
        user_name=user_name,
    )
    author = sender or str(user_name or "User").strip() or "User"

    # Synthetic events can themselves be wrapped by a real shared-session
    # sender. Re-check after removing that exact wrapper.
    if _is_internal_event(text):
        internal_kind = "internal_runtime_event"
        text = ""

    steers = extract_current_turn_steers(messages)
    direct_parts = [part for part in (text, *steers) if part]

    payload: dict[str, Any] = {
        "schema": _SCHEMA,
        "source": {
            "platform": str(platform or "unknown"),
            "event_kind": internal_kind or "direct_user_turn",
        },
    }
    if attachment_omitted:
        payload["source"]["attachment_context_omitted"] = True
    if direct_parts:
        direct_text = "\n\n".join(direct_parts)
        payload["direct_user"] = {
            "author": author,
            "authority": "direct_user_authored",
            "content": f"DIRECT_USER by {author}: {direct_text}",
        }
    if assistant:
        clean_agent = str(agent_name or "Hermes Agent").strip() or "Hermes Agent"
        payload["agent_final"] = {
            "author": clean_agent,
            "authority": "agent_generated",
            "content": f"AGENT_FINAL by {clean_agent}: {assistant}",
        }
    if quoted:
        payload["quoted_context"] = {
            "authority": "context_only",
            "content": f"QUOTED_CONTEXT, not direct evidence: {quoted}",
        }
    return payload


def serialize_auto_retain_turn(*args: Any, **kwargs: Any) -> str:
    return json.dumps(
        normalize_auto_retain_turn(*args, **kwargs),
        ensure_ascii=False,
    )
