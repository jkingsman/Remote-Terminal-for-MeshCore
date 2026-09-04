"""Ollama-backed unread channel message summarization."""

from __future__ import annotations

import logging

import httpx

from app.models import Message

logger = logging.getLogger(__name__)

DEFAULT_OLLAMA_BASE_URL = "http://localhost:11434"
DEFAULT_OLLAMA_MODEL = "phi3:mini"
MAX_MESSAGES_FOR_SUMMARY = 100
MAX_CHARS_PER_MESSAGE = 500
OLLAMA_TIMEOUT_SECONDS = 60.0

_SYSTEM_PROMPT = (
    "You summarize unread mesh radio channel messages for an operator catching up. "
    "Write 2-4 short sentences covering the main topics, notable decisions, questions, "
    "and who said what when useful. Do not invent content. Do not use markdown headings "
    "or bullet lists unless there are clearly distinct topics. Be concise."
)


def normalize_ollama_base_url(url: str | None) -> str:
    cleaned = (url or "").strip().rstrip("/")
    return cleaned or DEFAULT_OLLAMA_BASE_URL


def format_messages_for_prompt(messages: list[Message]) -> str:
    lines: list[str] = []
    for msg in messages:
        text = (msg.text or "").strip()
        if not text:
            continue
        if len(text) > MAX_CHARS_PER_MESSAGE:
            text = text[: MAX_CHARS_PER_MESSAGE - 1] + "…"
        prefix = "You" if msg.outgoing else ""
        if prefix:
            lines.append(f"{prefix}: {text}")
        else:
            lines.append(text)
    return "\n".join(lines)


async def summarize_channel_messages(
    *,
    base_url: str,
    model: str,
    channel_name: str,
    messages: list[Message],
) -> str:
    """Call Ollama to summarize channel messages. Raises on transport/API failure."""
    model = model.strip()
    if not model:
        raise ValueError("Ollama model is not configured")

    transcript = format_messages_for_prompt(messages)
    if not transcript:
        raise ValueError("No message text to summarize")

    url = f"{normalize_ollama_base_url(base_url)}/api/chat"
    payload = {
        "model": model,
        "stream": False,
        "messages": [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {
                "role": "user",
                "content": (
                    f"Summarize these {len(messages)} unread message(s) "
                    f"from channel {channel_name}:\n\n{transcript}"
                ),
            },
        ],
    }

    async with httpx.AsyncClient(timeout=OLLAMA_TIMEOUT_SECONDS) as client:
        response = await client.post(url, json=payload)
        response.raise_for_status()
        data = response.json()

    message = data.get("message") if isinstance(data, dict) else None
    content = message.get("content") if isinstance(message, dict) else None
    if not isinstance(content, str) or not content.strip():
        raise RuntimeError("Ollama returned an empty summary")
    return content.strip()
