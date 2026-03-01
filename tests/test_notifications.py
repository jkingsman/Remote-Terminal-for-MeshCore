from unittest.mock import AsyncMock, patch

import pytest

from app.models import CONTACT_TYPE_REPEATER, CONTACT_TYPE_ROOM, AppSettings, Favorite
from app.notifications import (
    _build_message_text,
    _normalize_apprise_url,
    enqueue_incoming_message_notification,
)


class TestAppriseNotifications:
    @staticmethod
    def _capture_task(coro):
        coro.close()
        return None

    @pytest.mark.asyncio
    async def test_queues_notification_when_enabled_for_all(self):
        settings = AppSettings(
            apprise_enabled=True,
            apprise_url="https://example.invalid/apprise",
            apprise_mode="all",
        )

        with (
            patch(
                "app.notifications.AppSettingsRepository.get", new=AsyncMock(return_value=settings)
            ),
            patch(
                "app.notifications.asyncio.create_task", side_effect=self._capture_task
            ) as mock_task,
        ):
            await enqueue_incoming_message_notification(
                msg_type="PRIV",
                conversation_id="aa" * 32,
                text="hello",
                conversation_name="Alice",
                sender_name="Alice",
                contact_type=1,
            )

        mock_task.assert_called_once()

    @pytest.mark.asyncio
    async def test_selected_mode_skips_unlisted_conversation(self):
        settings = AppSettings(
            apprise_enabled=True,
            apprise_url="https://example.invalid/apprise",
            apprise_mode="selected",
            apprise_targets=[Favorite(type="channel", id="ABCD")],
        )

        with (
            patch(
                "app.notifications.AppSettingsRepository.get", new=AsyncMock(return_value=settings)
            ),
            patch(
                "app.notifications.asyncio.create_task", side_effect=self._capture_task
            ) as mock_task,
        ):
            await enqueue_incoming_message_notification(
                msg_type="PRIV",
                conversation_id="bb" * 32,
                text="hello",
                contact_type=1,
            )

        mock_task.assert_not_called()

    def test_channel_message_format(self):
        title, body = _build_message_text(
            msg_type="CHAN",
            conversation_name="#general",
            sender_name="Alice",
            text="Hello world",
            path="202797",
        )

        assert title == ""
        assert body == "**#general:** Alice: Hello world **via:** [`20`, `27`, `97`]"

    def test_direct_message_format(self):
        title, body = _build_message_text(
            msg_type="PRIV",
            conversation_name="Alice",
            sender_name="Alice",
            text="Hello world",
            path="",
        )

        assert title == ""
        assert body == "**DM:** Alice: Hello world **via:** [`direct`]"

    def test_direct_message_defaults_to_direct_when_path_missing(self):
        title, body = _build_message_text(
            msg_type="PRIV",
            conversation_name="Alice",
            sender_name="Alice",
            text="Hello world",
            path=None,
        )

        assert title == ""
        assert body == "**DM:** Alice: Hello world **via:** [`direct`]"

    def test_discord_url_normalization_preserves_webhook_identity(self):
        assert (
            _normalize_apprise_url("discord://123/abc", preserve_identity=True)
            == "discord://123/abc?avatar=no"
        )
        assert (
            _normalize_apprise_url("discord://123/abc?foo=1", preserve_identity=True)
            == "discord://123/abc?foo=1&avatar=no"
        )
        assert (
            _normalize_apprise_url("discord://123/abc?avatar=yes", preserve_identity=True)
            == "discord://123/abc?avatar=no"
        )
        assert (
            _normalize_apprise_url("discord://123/abc", preserve_identity=False)
            == "discord://123/abc"
        )
        assert (
            _normalize_apprise_url(
                "https://discord.com/api/webhooks/123/abc", preserve_identity=True
            )
            == "https://discord.com/api/webhooks/123/abc?avatar=no"
        )

    @pytest.mark.asyncio
    async def test_selected_mode_allows_listed_conversation(self):
        contact_id = "cc" * 32
        settings = AppSettings(
            apprise_enabled=True,
            apprise_url="https://example.invalid/apprise",
            apprise_mode="selected",
            apprise_targets=[Favorite(type="contact", id=contact_id)],
        )

        with (
            patch(
                "app.notifications.AppSettingsRepository.get", new=AsyncMock(return_value=settings)
            ),
            patch(
                "app.notifications.asyncio.create_task", side_effect=self._capture_task
            ) as mock_task,
        ):
            await enqueue_incoming_message_notification(
                msg_type="PRIV",
                conversation_id=contact_id,
                text="hello",
                contact_type=1,
            )

        mock_task.assert_called_once()

    @pytest.mark.asyncio
    async def test_skips_repeater_and_room_contacts(self):
        settings = AppSettings(
            apprise_enabled=True,
            apprise_url="https://example.invalid/apprise",
            apprise_mode="all",
        )

        with (
            patch(
                "app.notifications.AppSettingsRepository.get", new=AsyncMock(return_value=settings)
            ),
            patch(
                "app.notifications.asyncio.create_task", side_effect=self._capture_task
            ) as mock_task,
        ):
            await enqueue_incoming_message_notification(
                msg_type="PRIV",
                conversation_id="dd" * 32,
                text="hello",
                contact_type=CONTACT_TYPE_REPEATER,
            )
            await enqueue_incoming_message_notification(
                msg_type="PRIV",
                conversation_id="ee" * 32,
                text="hello",
                contact_type=CONTACT_TYPE_ROOM,
            )

        mock_task.assert_not_called()
