"""Tests for Ollama unread channel summarization."""

from unittest.mock import AsyncMock, patch

import pytest

from app.models import ChannelUnreadSummaryRequest, Message
from app.repository import AppSettingsRepository, ChannelRepository, MessageRepository
from app.routers.channels import summarize_channel_unread
from app.routers.settings import AppSettingsUpdate, update_settings
from app.services.ollama_summary import format_messages_for_prompt, normalize_ollama_base_url


@pytest.mark.asyncio
async def test_settings_persist_ollama_fields(test_db):
    result = await update_settings(
        AppSettingsUpdate(
            ollama_base_url="http://ollama.local:11434/",
            ollama_model="llama3.2",
        )
    )
    assert result.ollama_base_url == "http://ollama.local:11434"
    assert result.ollama_model == "llama3.2"

    loaded = await AppSettingsRepository.get()
    assert loaded.ollama_base_url == "http://ollama.local:11434"
    assert loaded.ollama_model == "llama3.2"


def test_normalize_ollama_base_url():
    assert normalize_ollama_base_url("") == "http://localhost:11434"
    assert normalize_ollama_base_url("http://host:11434/") == "http://host:11434"


def test_format_messages_for_prompt():
    messages = [
        Message(
            id=1,
            type="CHAN",
            conversation_key="AA" * 16,
            text="Alice: hello",
            sender_timestamp=1,
            received_at=1,
            outgoing=False,
            acked=0,
        ),
        Message(
            id=2,
            type="CHAN",
            conversation_key="AA" * 16,
            text="reply",
            sender_timestamp=2,
            received_at=2,
            outgoing=True,
            acked=0,
        ),
    ]
    assert format_messages_for_prompt(messages) == "Alice: hello\nYou: reply"


@pytest.mark.asyncio
async def test_summarize_skipped_when_disabled(test_db):
    key = "DD" * 16
    await ChannelRepository.upsert(key=key, name="#off")
    await AppSettingsRepository.update(ollama_enabled=False, ollama_model="phi3:mini")
    result = await summarize_channel_unread(key, ChannelUnreadSummaryRequest(after=0))
    assert result.skipped is True
    assert "disabled" in (result.reason or "").lower()


@pytest.mark.asyncio
async def test_summarize_skipped_when_model_empty(test_db):
    key = "BB" * 16
    await ChannelRepository.upsert(key=key, name="#test")
    await AppSettingsRepository.update(ollama_enabled=True, ollama_model="")
    result = await summarize_channel_unread(key, ChannelUnreadSummaryRequest(after=0))
    assert result.skipped is True
    assert result.summary is None
    assert "not configured" in (result.reason or "")


@pytest.mark.asyncio
async def test_summarize_calls_ollama(test_db):
    key = "CC" * 16
    await ChannelRepository.upsert(key=key, name="#mesh")
    await AppSettingsRepository.update(
        ollama_base_url="http://localhost:11434",
        ollama_model="phi3:mini",
    )
    await MessageRepository.create(
        msg_type="CHAN",
        conversation_key=key,
        text="Bob: meeting at noon",
        sender_timestamp=100,
        received_at=1000,
        outgoing=False,
    )
    await MessageRepository.create(
        msg_type="CHAN",
        conversation_key=key,
        text="Carol: bring snacks",
        sender_timestamp=101,
        received_at=1001,
        outgoing=False,
    )

    with patch(
        "app.routers.channels.summarize_channel_messages",
        new_callable=AsyncMock,
        return_value="Meeting at noon; bring snacks.",
    ) as mock_summarize:
        result = await summarize_channel_unread(key, ChannelUnreadSummaryRequest(after=0))

    assert result.skipped is False
    assert result.message_count == 2
    assert result.summary == "Meeting at noon; bring snacks."
    mock_summarize.assert_awaited_once()
    kwargs = mock_summarize.await_args.kwargs
    assert kwargs["model"] == "phi3:mini"
    assert kwargs["channel_name"] == "#mesh"
    assert len(kwargs["messages"]) == 2
