"""MCMP compose-counter estimate + per-conversation toggle endpoints."""

import pytest
from fastapi import HTTPException

from app.models import McmpEnabledRequest, McmpEstimateRequest
from app.repository import ChannelRepository, ContactRepository
from app.routers.messages import estimate_mcmp
from app.routers.settings import set_mcmp_enabled

_LONG_TEXT = "Battery at 40%, switching to power save and checking channel five for traffic."


class TestEstimateEndpoint:
    @pytest.mark.asyncio
    async def test_compressible_text_reports_smaller_wire_size(self):
        result = await estimate_mcmp(McmpEstimateRequest(text=_LONG_TEXT))
        assert result.compressed is True
        assert result.wire_bytes < len(_LONG_TEXT.encode("utf-8"))

    @pytest.mark.asyncio
    async def test_tiny_text_is_not_compressed(self):
        result = await estimate_mcmp(McmpEstimateRequest(text="ok"))
        assert result.compressed is False
        assert result.wire_bytes == 2

    @pytest.mark.asyncio
    async def test_empty_text(self):
        result = await estimate_mcmp(McmpEstimateRequest(text=""))
        assert result.compressed is False
        assert result.wire_bytes == 0


class TestToggleEndpoint:
    @pytest.mark.asyncio
    async def test_enable_and_disable_contact(self, test_db):
        pub_key = "aa" * 32
        await ContactRepository.upsert({"public_key": pub_key, "name": "Alice"})

        resp = await set_mcmp_enabled(McmpEnabledRequest(type="contact", id=pub_key, enabled=True))
        assert resp.enabled is True
        contact = await ContactRepository.get_by_key(pub_key)
        assert contact is not None and contact.mcmp_enabled is True

        await set_mcmp_enabled(McmpEnabledRequest(type="contact", id=pub_key, enabled=False))
        contact = await ContactRepository.get_by_key(pub_key)
        assert contact is not None and contact.mcmp_enabled is False

    @pytest.mark.asyncio
    async def test_enable_channel(self, test_db):
        chan_key = "bb" * 16
        await ChannelRepository.upsert(key=chan_key, name="#general")

        resp = await set_mcmp_enabled(McmpEnabledRequest(type="channel", id=chan_key, enabled=True))
        assert resp.enabled is True
        channel = await ChannelRepository.get_by_key(chan_key)
        assert channel is not None and channel.mcmp_enabled is True

    @pytest.mark.asyncio
    async def test_missing_contact_returns_404(self, test_db):
        with pytest.raises(HTTPException) as exc:
            await set_mcmp_enabled(McmpEnabledRequest(type="contact", id="cc" * 32, enabled=True))
        assert exc.value.status_code == 404

    @pytest.mark.asyncio
    async def test_missing_channel_returns_404(self, test_db):
        with pytest.raises(HTTPException) as exc:
            await set_mcmp_enabled(McmpEnabledRequest(type="channel", id="dd" * 16, enabled=True))
        assert exc.value.status_code == 404
