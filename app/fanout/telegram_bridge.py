"""Telegram bridge fanout module: mesh channels → Telegram chats (one-way).

Ported from meshcore-bot's TelegramBridge service. DMs are never bridged.

Config blob:
- ``api_token``: Telegram bot API token
- ``mappings``: [{"channel_key": "<32-hex>", "chat_id": "@channel or -100…"}]
- ``disable_web_page_preview`` (bool, default true)
- ``filter_profanity`` ("off" | "censor" | "drop", default "off")
"""

from __future__ import annotations

import asyncio
import logging

from app.fanout.base import FanoutModule

logger = logging.getLogger(__name__)

_SEND_TIMEOUT = 8.0


class TelegramBridgeModule(FanoutModule):
    def __init__(self, config_id: str, config: dict, *, name: str = "") -> None:
        super().__init__(config_id, config, name=name)
        self._tasks: set[asyncio.Task] = set()

    async def start(self) -> None:
        self._set_last_error(None)

    async def stop(self) -> None:
        for task in self._tasks:
            task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()

    def _chats_for_channel(self, channel_key: str) -> list[str]:
        mappings = self.config.get("mappings", [])
        if not isinstance(mappings, list):
            return []
        chats: list[str] = []
        for mapping in mappings:
            if not isinstance(mapping, dict):
                continue
            if str(mapping.get("channel_key", "")).upper() == channel_key.upper():
                chat_id = mapping.get("chat_id")
                if chat_id:
                    chats.append(str(chat_id))
        return chats

    async def on_message(self, data: dict) -> None:
        if data.get("type") != "CHAN":
            return  # DMs are never bridged
        if data.get("outgoing") and not self.config.get("bridge_bot_responses", False):
            return
        token = str(self.config.get("api_token", "") or "")
        if not token:
            return
        chats = self._chats_for_channel(data.get("conversation_key", ""))
        if not chats:
            return

        sender = data.get("sender_name") or "mesh"
        text = data.get("text", "")
        if sender and text.startswith(f"{sender}: "):
            text = text[len(f"{sender}: ") :]
        if not text.strip():
            return

        from app.bots.moderation import apply_profanity_mode

        filtered = apply_profanity_mode(text, str(self.config.get("filter_profanity", "off")))
        if filtered is None:
            return

        task = asyncio.create_task(self._deliver(token, chats, f"{sender}: {filtered}"))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _deliver(self, token: str, chats: list[str], text: str) -> None:
        import httpx

        url = f"https://api.telegram.org/bot{token}/sendMessage"
        disable_preview = bool(self.config.get("disable_web_page_preview", True))
        try:
            async with httpx.AsyncClient(timeout=_SEND_TIMEOUT) as client:
                for chat_id in chats:
                    resp = await client.post(
                        url,
                        json={
                            "chat_id": chat_id,
                            "text": text[:4000],
                            "disable_web_page_preview": disable_preview,
                        },
                    )
                    if resp.status_code >= 400:
                        self._set_last_error(f"Telegram API returned {resp.status_code}")
                        logger.warning("Telegram bridge '%s': %s", self.name, self._last_error)
                        return
            self._set_last_error(None)
        except httpx.HTTPError as exc:
            self._set_last_error(f"Telegram delivery failed: {exc}")
            logger.warning("Telegram bridge '%s': %s", self.name, self._last_error)

    @property
    def status(self) -> str:
        return "error" if self._last_error else "connected"
