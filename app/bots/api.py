"""The authoring surface bot code programs against.

Bot source (stored in the DB) imports a single object::

    from remoteterm import bot

    @bot.on_keyword("wx", "weather")
    async def weather(ctx, msg):
        await ctx.reply("...")

Decorators register handlers into the *active collector* — set by
``app.bots.runtime`` while it executes a bot's source. Importing ``remoteterm``
outside a bot load is fine; calling a decorator outside one is an error.

Handlers may be ``async def`` (run on the event loop) or plain ``def`` (run in
the bot thread pool). Both receive ``ctx`` first; message-ish handlers also
receive ``msg``.
"""

from __future__ import annotations

import asyncio
import logging
import threading
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

# Sentinel: distinguishes "region not passed" from an explicit None/"" (unscoped).
_UNSET = object()


@dataclass(frozen=True)
class KeywordTrigger:
    keywords: tuple[str, ...]  # empty tuple = keywords come from the bot's UI trigger list
    handler: Callable[..., Any]


@dataclass(frozen=True)
class MessageTrigger:
    """Catch-all: runs for every in-scope message (greeter-style bots)."""

    handler: Callable[..., Any]


@dataclass(frozen=True)
class CronTrigger:
    expression: str  # empty string = expressions come from the bot's UI trigger list
    handler: Callable[..., Any]


@dataclass(frozen=True)
class EventTrigger:
    event: str  # e.g. "new_contact"
    handler: Callable[..., Any]


@dataclass(frozen=True)
class WebhookTrigger:
    slug: str
    handler: Callable[..., Any]


@dataclass
class HandlerCollector:
    """Accumulates the triggers a bot's source declares while it executes."""

    keywords: list[KeywordTrigger] = field(default_factory=list)
    messages: list[MessageTrigger] = field(default_factory=list)
    crons: list[CronTrigger] = field(default_factory=list)
    events: list[EventTrigger] = field(default_factory=list)
    webhooks: list[WebhookTrigger] = field(default_factory=list)

    def is_empty(self) -> bool:
        return not (self.keywords or self.messages or self.crons or self.events or self.webhooks)


_collector_lock = threading.Lock()
_active_collector: HandlerCollector | None = None


class _BotDecorators:
    """The ``bot`` object bot code imports. Pure registration — no behavior."""

    @staticmethod
    def _collector() -> HandlerCollector:
        if _active_collector is None:
            raise RuntimeError(
                "bot decorators may only be used inside bot code loaded by RemoteTerm"
            )
        return _active_collector

    def on_keyword(self, *keywords: str) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
        """Trigger on messages whose first word matches one of ``keywords``.

        With no arguments, the bot receives the keywords configured on its
        Triggers tab instead of hardcoding them.
        """
        normalized = tuple(k.strip().lower() for k in keywords if k and k.strip())

        def decorator(fn: Callable[..., Any]) -> Callable[..., Any]:
            self._collector().keywords.append(KeywordTrigger(normalized, fn))
            return fn

        return decorator

    def on_message(self) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
        """Trigger on every in-scope message (no keyword matching)."""

        def decorator(fn: Callable[..., Any]) -> Callable[..., Any]:
            self._collector().messages.append(MessageTrigger(fn))
            return fn

        return decorator

    def on_cron(self, expression: str = "") -> Callable[[Callable[..., Any]], Callable[..., Any]]:
        """Trigger on a cron schedule (5-field crontab or @preset; dow 0=Monday).

        With no expression, the handler fires for schedules added on the bot's
        Triggers tab.
        """

        def decorator(fn: Callable[..., Any]) -> Callable[..., Any]:
            self._collector().crons.append(CronTrigger(expression.strip(), fn))
            return fn

        return decorator

    def on_event(self, event: str) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
        """Trigger on a mesh event. Currently: ``new_contact``."""

        def decorator(fn: Callable[..., Any]) -> Callable[..., Any]:
            self._collector().events.append(EventTrigger(event.strip(), fn))
            return fn

        return decorator

    def on_webhook(self, slug: str) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
        """Trigger on ``POST /api/hooks/{slug}`` (token-gated via bot settings)."""

        def decorator(fn: Callable[..., Any]) -> Callable[..., Any]:
            self._collector().webhooks.append(WebhookTrigger(slug.strip().lstrip("/"), fn))
            return fn

        return decorator


bot = _BotDecorators()


def collect_handlers(execute: Callable[[], None]) -> HandlerCollector:
    """Run ``execute`` (which exec()s bot source) with a fresh active collector."""
    global _active_collector
    with _collector_lock:
        collector = HandlerCollector()
        _active_collector = collector
        try:
            execute()
        finally:
            _active_collector = None
    return collector


@dataclass
class BotMessage:
    """The message a keyword/message handler is reacting to."""

    text: str
    keyword: str | None = None
    args: list[str] = field(default_factory=list)
    sender_name: str | None = None
    sender_key: str | None = None
    is_dm: bool = False
    channel_key: str | None = None
    channel_name: str | None = None
    sender_timestamp: int | None = None
    path: str | None = None
    path_bytes_per_hop: int | None = None
    region: str | None = None
    scoped: bool = False
    is_outgoing: bool = False

    @property
    def arg_text(self) -> str:
        return " ".join(self.args)


class BotHttp:
    """Small async HTTP helper handed to bots as ``ctx.http`` (httpx-backed)."""

    def __init__(self, timeout_seconds: float = 6.0) -> None:
        self._timeout = timeout_seconds

    async def get_json(self, url: str, *, headers: dict[str, str] | None = None) -> Any:
        import httpx

        async with httpx.AsyncClient(timeout=self._timeout, follow_redirects=True) as client:
            resp = await client.get(url, headers=headers)
            resp.raise_for_status()
            return resp.json()

    async def get_text(self, url: str, *, headers: dict[str, str] | None = None) -> str:
        import httpx

        async with httpx.AsyncClient(timeout=self._timeout, follow_redirects=True) as client:
            resp = await client.get(url, headers=headers)
            resp.raise_for_status()
            return resp.text

    async def post_json(
        self,
        url: str,
        payload: Any,
        *,
        headers: dict[str, str] | None = None,
    ) -> Any:
        import httpx

        async with httpx.AsyncClient(timeout=self._timeout, follow_redirects=True) as client:
            resp = await client.post(url, json=payload, headers=headers)
            resp.raise_for_status()
            return resp.json()


class BotContext:
    """Per-run context: sending, settings, persistent state, HTTP, i18n, logging.

    Sends go through the engine's shared TX spacing lock, exactly like the
    legacy fanout bots. In test runs sends are captured instead of transmitted.
    """

    def __init__(
        self,
        *,
        bot_id: str,
        bot_name: str,
        settings: dict[str, Any],
        state: dict[str, Any],
        origin_is_dm: bool = False,
        origin_sender_key: str | None = None,
        origin_channel_key: str | None = None,
        locale: str = "en",
        is_test: bool = False,
        log_fn: Callable[[str, str], None] | None = None,
        send_fn: Callable[..., Awaitable[None]] | None = None,
        translator: Any = None,
        loop: asyncio.AbstractEventLoop | None = None,
    ) -> None:
        self.bot_id = bot_id
        self.bot_name = bot_name
        self.settings: dict[str, Any] = settings
        self.state: dict[str, Any] = state
        self.locale = locale
        self.is_test = is_test
        self.http = BotHttp()
        self.replies_sent = 0
        self.captured_sends: list[dict[str, Any]] = []
        self._origin_is_dm = origin_is_dm
        self._origin_sender_key = origin_sender_key
        self._origin_channel_key = origin_channel_key
        self._log_fn = log_fn
        self._send_fn = send_fn
        self._translator = translator
        self._loop = loop or asyncio.get_event_loop()
        self._state_dirty = False

    # -- persistence -----------------------------------------------------
    def mark_state_dirty(self) -> None:
        self._state_dirty = True

    @property
    def state_dirty(self) -> bool:
        # Conservative: assume any run that touched state mutated it. Bots that
        # only read pay one cheap JSON write; correctness beats cleverness here.
        return self._state_dirty or bool(self.state)

    # -- i18n --------------------------------------------------------------
    def t(self, key: str, **kwargs: Any) -> str:
        """Translate ``key`` in the run's locale; falls back to the key itself."""
        if self._translator is None:
            return key
        return self._translator.translate(key, self.locale, **kwargs)

    # -- logging -----------------------------------------------------------
    def log(self, message: str, level: str = "INFO") -> None:
        if self._log_fn is not None:
            self._log_fn(level, message)

    # -- sending -----------------------------------------------------------
    async def _dispatch_send(
        self,
        *,
        is_dm: bool,
        destination: str | None,
        channel_key: str | None,
        text: str,
        flood_scope_override: str | None,
    ) -> None:
        if not text or not text.strip():
            return
        if self.is_test or self._send_fn is None:
            self.captured_sends.append(
                {
                    "is_dm": is_dm,
                    "destination": destination,
                    "channel_key": channel_key,
                    "text": text,
                    "region": flood_scope_override,
                }
            )
            self.replies_sent += 1
            return
        await self._send_fn(
            is_dm=is_dm,
            destination=destination,
            channel_key=channel_key,
            text=text,
            flood_scope_override=flood_scope_override,
        )
        self.replies_sent += 1

    async def reply(self, text: str, *, region: Any = _UNSET) -> None:
        """Reply where the triggering message came from (DM sender or channel).

        ``region`` (channel replies only): omit for the channel default, pass a
        region name to scope this send, or ``None``/``""`` to force unscoped.
        """
        scope: str | None
        if region is _UNSET:
            scope = None
        elif region is None or region == "":
            scope = ""
        else:
            scope = str(region)
        if self._origin_is_dm:
            await self._dispatch_send(
                is_dm=True,
                destination=self._origin_sender_key,
                channel_key=None,
                text=text,
                flood_scope_override=None,
            )
        else:
            await self._dispatch_send(
                is_dm=False,
                destination=None,
                channel_key=self._origin_channel_key,
                text=text,
                flood_scope_override=scope,
            )

    async def send(self, channel: str, text: str, *, region: Any = _UNSET) -> None:
        """Send to any channel by name (``#chan`` / ``Public``) or 32-hex key."""
        from app.repository import ChannelRepository

        key: str | None = None
        candidate = channel.strip()
        if len(candidate) == 32 and all(c in "0123456789abcdefABCDEF" for c in candidate):
            key = candidate.upper()
        else:
            channels = await ChannelRepository.get_all()
            wanted = candidate.lstrip("#").lower()
            for ch in channels:
                if ch.name.lstrip("#").lower() == wanted:
                    key = ch.key
                    break
        if key is None:
            raise ValueError(f"unknown channel {channel!r}")
        scope: str | None
        if region is _UNSET:
            scope = None
        elif region is None or region == "":
            scope = ""
        else:
            scope = str(region)
        await self._dispatch_send(
            is_dm=False,
            destination=None,
            channel_key=key,
            text=text,
            flood_scope_override=scope,
        )

    async def send_dm(self, public_key: str, text: str) -> None:
        """Send a direct message to a contact by full public key."""
        await self._dispatch_send(
            is_dm=True,
            destination=public_key,
            channel_key=None,
            text=text,
            flood_scope_override=None,
        )

    # -- geocoding -----------------------------------------------------------
    async def geocode(self, query: str) -> dict[str, Any] | None:
        """Resolve a place name / postal code via Nominatim. Cached in-process."""
        from app.bots.geocode import geocode_query

        return await geocode_query(query)

    # -- introspection ---------------------------------------------------------
    async def mesh_stats(self) -> dict[str, int]:
        """Small mesh summary: total_contacts, total_repeaters, contacts_24h,
        repeaters_24h, new_contacts_7d, messages_24h."""
        from app.bots.placeholders import gather_mesh_stats

        return await gather_mesh_stats()

    def get_enabled_bots(self) -> list[dict[str, Any]]:
        """Metadata for every enabled bot: name, category, description, keywords."""
        from app.bots.engine import bot_engine

        out: list[dict[str, Any]] = []
        for loaded in bot_engine.bots.values():
            record = loaded.record
            if not record.enabled or loaded.code is None:
                continue
            keywords: list[str] = []
            for kws, _handler in loaded.keyword_map:
                keywords.extend(kws)
            out.append(
                {
                    "name": record.name,
                    "category": record.category,
                    "description": record.description,
                    "keywords": keywords,
                }
            )
        out.sort(key=lambda b: (b["category"], b["name"]))
        return out

    # -- sync bridges (for plain ``def`` handlers running in the thread pool) --
    def reply_sync(self, text: str, *, region: Any = _UNSET) -> None:
        asyncio.run_coroutine_threadsafe(self.reply(text, region=region), self._loop).result(
            timeout=30
        )

    def send_sync(self, channel: str, text: str, *, region: Any = _UNSET) -> None:
        asyncio.run_coroutine_threadsafe(
            self.send(channel, text, region=region), self._loop
        ).result(timeout=30)

    def send_dm_sync(self, public_key: str, text: str) -> None:
        asyncio.run_coroutine_threadsafe(self.send_dm(public_key, text), self._loop).result(
            timeout=30
        )
