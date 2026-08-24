"""Discord bridge fanout module: mesh channels → Discord webhooks (one-way).

Ported from meshcore-bot's DiscordBridge service. Each mapping fans one mesh
channel out to one or more Discord webhook URLs. DMs are never bridged
(hardcoded, matching the original). Optional profanity filtering applies to
the bridged copy only.

Config blob:
- ``mappings``: [{"channel_key": "<32-hex>", "webhook_urls": ["https://discord.com/api/webhooks/..."]}]
- ``bridge_bot_responses`` (bool, default false) — forward our own outgoing messages too
- ``filter_profanity`` ("off" | "censor" | "drop", default "off")
"""

from __future__ import annotations

import asyncio
import logging

from app.fanout.base import FanoutModule

logger = logging.getLogger(__name__)

_SEND_TIMEOUT = 8.0


class DiscordBridgeModule(FanoutModule):
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

    def _urls_for_channel(self, channel_key: str) -> list[str]:
        mappings = self.config.get("mappings", [])
        if not isinstance(mappings, list):
            return []
        urls: list[str] = []
        for mapping in mappings:
            if not isinstance(mapping, dict):
                continue
            if str(mapping.get("channel_key", "")).upper() == channel_key.upper():
                for url in mapping.get("webhook_urls", []):
                    if isinstance(url, str) and url.startswith("https://"):
                        urls.append(url)
        return urls

    async def on_message(self, data: dict) -> None:
        if data.get("type") != "CHAN":
            return  # DMs are never bridged
        if data.get("outgoing") and not self.config.get("bridge_bot_responses", False):
            return
        channel_key = data.get("conversation_key", "")
        urls = self._urls_for_channel(channel_key)
        if not urls:
            return

        sender = data.get("sender_name") or "mesh"
        text = data.get("text", "")
        if sender and text.startswith(f"{sender}: "):
            text = text[len(f"{sender}: ") :]
        if not text.strip():
            return

        from app.bots.moderation import apply_profanity_mode

        mode = str(self.config.get("filter_profanity", "off"))
        filtered = apply_profanity_mode(text, mode)
        if filtered is None:
            return
        text = filtered

        task = asyncio.create_task(self._deliver(urls, sender, text))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _deliver(self, urls: list[str], sender: str, text: str) -> None:
        import httpx

        payload = {"username": sender[:80] or "mesh", "content": text[:1900]}
        try:
            async with httpx.AsyncClient(timeout=_SEND_TIMEOUT) as client:
                for url in urls:
                    resp = await client.post(url, json=payload)
                    if resp.status_code >= 400:
                        self._set_last_error(f"Discord webhook returned {resp.status_code}")
                        logger.warning("Discord bridge '%s': %s", self.name, self._last_error)
                        return
            self._set_last_error(None)
        except httpx.HTTPError as exc:
            self._set_last_error(f"Discord delivery failed: {exc}")
            logger.warning("Discord bridge '%s': %s", self.name, self._last_error)

    @property
    def status(self) -> str:
        return "error" if self._last_error else "connected"
