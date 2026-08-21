"""The bot engine: loads DB bots, matches triggers, runs handlers, sends replies.

One singleton (``bot_engine``) is started from the app lifespan, fed decoded
messages and contact upserts from ``broadcast_event`` (the same tap the fanout
bus uses), and runs three periodic concerns on a single ticker: bot cron
triggers, standalone scheduled messages, and feed polling.

Ordering / limits (engine settings, Bots › Engine tab):

* keyword triggers pass through banned-sender, hop, scope, prefix/mention,
  admin, global-reply and per-user limiters, then per-bot cooldowns (with a
  short queue window) before running;
* catch-all ``on_message`` handlers and legacy ``def bot(...)`` bots only pass
  moderation/scope gates — they see every in-scope message, exactly like the
  historical fanout bots, and do their own filtering;
* every outgoing bot send is serialized behind a TX-spacing lock.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import time
from collections import OrderedDict, deque
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from app.bots.api import BotContext, BotMessage
from app.bots.cron import CronSchedule, parse_cron
from app.bots.moderation import apply_profanity_mode, is_banned_sender
from app.bots.runtime import BotCodeError, LoadedCode, call_handler, call_legacy, load_bot_code
from app.bots.translate import Translator, detect_language
from app.models import Bot, BotEngineSettings, BotLogEntry, BotTestRequest, BotTestResponse

logger = logging.getLogger(__name__)

BOT_EXECUTION_TIMEOUT = 10.0
SETTLE_DELAY_SECONDS = 2.0
TICK_SECONDS = 15.0
LOG_RING_SIZE = 500
MAX_TRACKED_USERS = 1000

_bot_executor = ThreadPoolExecutor(max_workers=32, thread_name_prefix="botws_")


@dataclass
class LoadedBot:
    """A DB bot row plus its executed code, ready to dispatch."""

    record: Bot
    code: LoadedCode | None
    load_error: str | None = None
    cron_next: dict[str, datetime] = field(default_factory=dict)
    last_run_monotonic: float = 0.0
    user_cooldowns: OrderedDict[str, float] = field(default_factory=OrderedDict)
    queued_users: set[str] = field(default_factory=set)

    @property
    def keyword_map(self) -> list[tuple[tuple[str, ...], Any]]:
        """(keywords, handler) pairs — UI keywords route to generic handlers."""
        if self.code is None:
            return []
        pairs: list[tuple[tuple[str, ...], Any]] = []
        ui_keywords = tuple(
            t["spec"].strip().lower()
            for t in self.record.ui_triggers
            if t.get("kind") == "keyword" and t.get("spec", "").strip()
        )
        for trig in self.code.collector.keywords:
            if trig.keywords:
                pairs.append((trig.keywords, trig.handler))
            elif ui_keywords:
                pairs.append((ui_keywords, trig.handler))
        return pairs

    def cron_entries(self) -> list[tuple[str, str, Any]]:
        """(cache_key, expression, handler) for code + UI cron triggers."""
        if self.code is None:
            return []
        entries: list[tuple[str, str, Any]] = []
        generic_handlers = [t.handler for t in self.code.collector.crons if not t.expression]
        for idx, trig in enumerate(self.code.collector.crons):
            if trig.expression:
                entries.append((f"code:{idx}:{trig.expression}", trig.expression, trig.handler))
        if generic_handlers:
            for t_idx, trig in enumerate(self.record.ui_triggers):
                if trig.get("kind") == "cron" and trig.get("spec", "").strip():
                    expr = trig["spec"].strip()
                    for h_idx, handler in enumerate(generic_handlers):
                        entries.append((f"ui:{t_idx}:{h_idx}:{expr}", expr, handler))
        return entries


class BotEngine:
    """Singleton owning all loaded bots, limits, and the periodic ticker."""

    def __init__(self) -> None:
        self.bots: dict[str, LoadedBot] = {}
        self.settings = BotEngineSettings()
        self.disabled_until_restart = False
        self.log_ring: deque[BotLogEntry] = deque(maxlen=LOG_RING_SIZE)
        self._ticker_task: asyncio.Task | None = None
        self._tasks: set[asyncio.Task] = set()
        self._send_lock = asyncio.Lock()
        self._last_send_monotonic = 0.0
        self._last_global_accept = 0.0
        self._per_user_accepts: OrderedDict[str, float] = OrderedDict()
        self._known_contact_keys: set[str] = set()
        self._schedule_next: dict[str, datetime] = {}
        self._feed_locks: set[str] = set()
        self.translator = Translator()
        self._started = False

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------
    async def start(self) -> None:
        from app.config import settings as server_settings

        self.settings = await self._load_settings()
        self.translator = Translator(self.settings.default_language)
        if getattr(server_settings, "disable_bots", False):
            self.log("WARN", "engine", "Bots disabled by MESHCORE_DISABLE_BOTS — engine idle")
        await self.reload_all()
        await self._prime_known_contacts()
        self._ticker_task = asyncio.create_task(self._ticker())
        self._started = True
        self.log("INFO", "engine", f"Bot engine started with {len(self.bots)} bots")

    async def stop(self) -> None:
        self._started = False
        if self._ticker_task:
            self._ticker_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._ticker_task
            self._ticker_task = None
        for task in list(self._tasks):
            task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()

    @property
    def disabled(self) -> bool:
        from app.config import settings as server_settings

        return self.disabled_until_restart or getattr(server_settings, "disable_bots", False)

    async def _load_settings(self) -> BotEngineSettings:
        from app.repository.bots import BotEngineSettingsRepository

        return await BotEngineSettingsRepository.get()

    async def reload_settings(self) -> None:
        self.settings = await self._load_settings()
        self.translator = Translator(self.settings.default_language)

    async def reload_all(self) -> None:
        from app.repository.bots import BotRepository

        records = await BotRepository.get_all()
        loaded: dict[str, LoadedBot] = {}
        for record in records:
            loaded[record.id] = self._load_one(record)
        self.bots = loaded

    def _load_one(self, record: Bot) -> LoadedBot:
        if not record.code.strip():
            return LoadedBot(record=record, code=None, load_error="bot code is empty")
        try:
            code = load_bot_code(record.code)
            return LoadedBot(record=record, code=code)
        except BotCodeError as exc:
            self.log("ERROR", record.name, f"failed to load: {exc}")
            return LoadedBot(record=record, code=None, load_error=str(exc))

    async def reload_bot(self, bot_id: str) -> None:
        from app.repository.bots import BotRepository

        record = await BotRepository.get(bot_id)
        if record is None:
            self.bots.pop(bot_id, None)
            return
        self.bots[bot_id] = self._load_one(record)

    def remove_bot(self, bot_id: str) -> None:
        self.bots.pop(bot_id, None)

    async def _prime_known_contacts(self) -> None:
        from app.database import db

        try:
            async with db.readonly() as conn:
                async with conn.execute("SELECT public_key FROM contacts") as cursor:
                    rows = await cursor.fetchall()
            self._known_contact_keys = {row["public_key"] for row in rows}
        except Exception:
            logger.exception("Failed to prime known contacts for new-contact events")

    # ------------------------------------------------------------------
    # Logging
    # ------------------------------------------------------------------
    def log(self, level: str, source: str, message: str) -> None:
        entry = BotLogEntry(timestamp=time.time(), level=level, source=source, message=message)
        self.log_ring.append(entry)
        try:
            from app.websocket import ws_manager

            asyncio.get_running_loop()
            asyncio.create_task(ws_manager.broadcast("bot_log", entry.model_dump()))
        except RuntimeError:
            pass  # no running loop (tests calling synchronously)

    # ------------------------------------------------------------------
    # Sending (shared TX spacing across all bots + schedules + feeds)
    # ------------------------------------------------------------------
    async def send_bot_message(
        self,
        *,
        is_dm: bool,
        destination: str | None,
        channel_key: str | None,
        text: str,
        flood_scope_override: str | None,
    ) -> None:
        filtered = apply_profanity_mode(text, self.settings.profanity_mode)
        if filtered is None:
            self.log("WARN", "moderation", "outgoing message dropped by profanity filter")
            return
        text = filtered

        from fastapi import HTTPException

        from app.models import SendChannelMessageRequest, SendDirectMessageRequest
        from app.routers.messages import send_channel_message, send_direct_message

        async with self._send_lock:
            spacing = max(0.0, self.settings.tx_spacing_seconds)
            elapsed = time.monotonic() - self._last_send_monotonic
            if self._last_send_monotonic > 0 and elapsed < spacing:
                await asyncio.sleep(spacing - elapsed)
            try:
                if is_dm:
                    if not destination:
                        self.log("WARN", "engine", "DM send skipped: no destination key")
                        return
                    await send_direct_message(
                        SendDirectMessageRequest(destination=destination, text=text)
                    )
                elif channel_key:
                    await send_channel_message(
                        SendChannelMessageRequest(
                            channel_key=channel_key,
                            text=text,
                            flood_scope_override=flood_scope_override,
                        )
                    )
                else:
                    self.log("WARN", "engine", "send skipped: no destination")
                    return
            except HTTPException as exc:
                self.log("ERROR", "engine", f"send failed: {exc.detail}")
                return
            except Exception as exc:
                self.log("ERROR", "engine", f"send failed: {exc}")
                return
            self._last_send_monotonic = time.monotonic()

    # ------------------------------------------------------------------
    # Message dispatch
    # ------------------------------------------------------------------
    def handle_message_event(self, data: dict) -> None:
        """Non-blocking entry from broadcast_event."""
        if self.disabled or not self._started:
            return
        task = asyncio.create_task(self._handle_message(data))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    def handle_contact_event(self, data: dict) -> None:
        if not self._started:
            return
        task = asyncio.create_task(self._handle_contact(data))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _build_message(self, data: dict) -> tuple[BotMessage, int | None]:
        msg_type = data.get("type", "")
        is_dm = msg_type == "PRIV"
        conversation_key = data.get("conversation_key", "")
        sender_name = data.get("sender_name")
        text = data.get("text", "")
        is_outgoing = bool(data.get("outgoing", False))

        channel_key: str | None = None
        channel_name: str | None = None
        sender_key: str | None = None
        if is_dm:
            sender_key = data.get("sender_key") or conversation_key
            if is_outgoing:
                sender_name = None
            elif sender_name is None:
                from app.repository import ContactRepository

                contact = await ContactRepository.get_by_key(conversation_key)
                sender_name = contact.name if contact else None
        else:
            channel_key = conversation_key
            sender_key = data.get("sender_key")
            channel_name = data.get("channel_name")
            if channel_name is None:
                from app.repository import ChannelRepository

                channel = await ChannelRepository.get_by_key(conversation_key)
                channel_name = channel.name if channel else None
            if sender_name and text.startswith(f"{sender_name}: "):
                text = text[len(f"{sender_name}: ") :]

        paths = data.get("paths")
        path_value = data.get("path")
        path_hops: int | None = None
        if isinstance(paths, list) and paths and isinstance(paths[0], dict):
            if path_value is None:
                path_value = paths[0].get("path")
            raw_len = paths[0].get("path_len")
            if isinstance(raw_len, int):
                path_hops = raw_len

        path_bytes_per_hop: int | None = None
        if isinstance(path_value, str) and path_value and path_hops:
            path_bytes = len(path_value) // 2
            if path_bytes % path_hops == 0 and path_bytes // path_hops in (1, 2, 3):
                path_bytes_per_hop = path_bytes // path_hops

        message = BotMessage(
            text=text,
            sender_name=sender_name,
            sender_key=sender_key,
            is_dm=is_dm,
            channel_key=channel_key,
            channel_name=channel_name,
            sender_timestamp=data.get("sender_timestamp"),
            path=path_value if isinstance(path_value, str) else None,
            path_bytes_per_hop=path_bytes_per_hop,
            region=data.get("region"),
            scoped=data.get("transport_code") is not None,
            is_outgoing=is_outgoing,
        )
        return message, path_hops

    def _node_name(self) -> str | None:
        try:
            from app.services.radio_runtime import radio_runtime

            info = getattr(radio_runtime.meshcore, "self_info", None)
            if isinstance(info, dict):
                name = info.get("name")
                return str(name) if name else None
        except Exception:
            return None
        return None

    def _strip_prefix_and_mention(self, text: str) -> tuple[str, bool, bool]:
        """Returns (text for keyword matching, had_prefix, had_mention)."""
        stripped = text.strip()
        had_mention = False
        node_name = self._node_name()
        if node_name:
            mention = f"@[{node_name}]"
            if stripped.lower().startswith(mention.lower()):
                stripped = stripped[len(mention) :].strip()
                had_mention = True

        prefixes = [p.strip() for p in (self.settings.command_prefix or "").split(",") if p.strip()]
        had_prefix = False
        best = ""
        for prefix in prefixes:
            if stripped.startswith(prefix) and len(prefix) > len(best):
                best = prefix
        if best:
            stripped = stripped[len(best) :].lstrip()
            had_prefix = True
        return stripped, had_prefix, had_mention

    def _scope_allows(self, bot: Bot, msg: BotMessage) -> bool:
        if msg.is_dm:
            return bot.respond_to_dms
        channels = bot.scope.get("channels", "all") if isinstance(bot.scope, dict) else "all"
        if channels == "all":
            return True
        if channels == "none":
            return False
        if isinstance(channels, dict):
            key = (msg.channel_key or "").upper()
            only = channels.get("only")
            if isinstance(only, list):
                return key in {str(k).upper() for k in only}
            except_list = channels.get("except")
            if isinstance(except_list, list):
                return key not in {str(k).upper() for k in except_list}
        return True

    def _is_admin_sender(self, msg: BotMessage) -> bool:
        if not msg.sender_key:
            return False
        wanted = msg.sender_key.lower()
        return any(u.public_key.lower() == wanted for u in self.settings.admin_users)

    def _touch_user_accept(self, user: str) -> None:
        self._per_user_accepts[user] = time.monotonic()
        self._per_user_accepts.move_to_end(user)
        while len(self._per_user_accepts) > MAX_TRACKED_USERS:
            self._per_user_accepts.popitem(last=False)

    async def _handle_message(self, data: dict) -> None:
        try:
            msg, path_hops = await self._build_message(data)
        except Exception:
            logger.exception("Bot engine failed to parse message event")
            return

        if is_banned_sender(msg.sender_name, self.settings.banned_users):
            return
        if (
            path_hops is not None
            and self.settings.max_response_hops > 0
            and path_hops > self.settings.max_response_hops
        ):
            return

        match_text, had_prefix, had_mention = self._strip_prefix_and_mention(msg.text)
        lowered = match_text.lower()

        keyword_allowed = True
        if self.settings.require_prefix and not (had_prefix or had_mention or msg.is_dm):
            keyword_allowed = False
        if self.settings.mention_mode == "only" and not (had_mention or msg.is_dm):
            keyword_allowed = False
        if self.settings.mention_mode == "off" and had_mention:
            keyword_allowed = False

        user_id = msg.sender_key or msg.sender_name or "unknown"

        for loaded in list(self.bots.values()):
            bot = loaded.record
            if not bot.enabled or loaded.code is None:
                continue
            if not self._scope_allows(bot, msg):
                continue

            # Catch-all + legacy bots: see everything in scope, filter themselves.
            if loaded.code.is_legacy:
                self._spawn_run(loaded, "message", msg, handler=None, legacy=True)
                continue
            for trig in loaded.code.collector.messages:
                self._spawn_run(loaded, "message", msg, handler=trig.handler)

            if not keyword_allowed or msg.is_outgoing:
                continue
            if bot.admin_only and not self._is_admin_sender(msg):
                continue

            matched_handler = None
            matched_keyword = None
            for keywords, handler in loaded.keyword_map:
                for keyword in keywords:
                    if lowered == keyword or lowered.startswith(keyword + " "):
                        matched_handler = handler
                        matched_keyword = keyword
                        break
                if matched_handler:
                    break
            if not matched_handler or matched_keyword is None:
                continue

            now = time.monotonic()
            if (
                self.settings.global_reply_seconds > 0
                and now - self._last_global_accept < self.settings.global_reply_seconds
                and self._last_global_accept > 0
            ):
                self.log(
                    "WARN",
                    "rate-limit",
                    f"global reply limit — skipped {bot.name} for {user_id[:16]}",
                )
                continue
            last_user = self._per_user_accepts.get(user_id, 0.0)
            if (
                self.settings.per_user_seconds > 0
                and last_user > 0
                and now - last_user < self.settings.per_user_seconds
            ):
                self.log(
                    "WARN", "rate-limit", f"per-user limit — skipped {bot.name} for {user_id[:16]}"
                )
                continue

            delay = 0.0
            if bot.cooldown_seconds > 0 and loaded.last_run_monotonic > 0:
                remaining = bot.cooldown_seconds - (now - loaded.last_run_monotonic)
                if remaining > 0:
                    if (
                        remaining <= bot.queue_threshold_seconds
                        and user_id not in loaded.queued_users
                    ):
                        delay = remaining
                        loaded.queued_users.add(user_id)
                        self.log("INFO", bot.name, f"queued for {remaining:.1f}s cooldown")
                    else:
                        continue
            if bot.per_user_cooldown_seconds > 0:
                last_bot_user = loaded.user_cooldowns.get(user_id, 0.0)
                if last_bot_user > 0 and now - last_bot_user < bot.per_user_cooldown_seconds:
                    continue
                loaded.user_cooldowns[user_id] = now
                loaded.user_cooldowns.move_to_end(user_id)
                while len(loaded.user_cooldowns) > MAX_TRACKED_USERS:
                    loaded.user_cooldowns.popitem(last=False)

            self._last_global_accept = now
            self._touch_user_accept(user_id)
            loaded.last_run_monotonic = now

            remainder = match_text[len(matched_keyword) :].strip()
            run_msg = BotMessage(
                **{**msg.__dict__, "keyword": matched_keyword, "args": remainder.split()}
            )
            self._spawn_run(
                loaded,
                f"kw {matched_keyword}",
                run_msg,
                handler=matched_handler,
                delay=delay,
                user_id=user_id,
            )

    async def _handle_contact(self, data: dict) -> None:
        public_key = data.get("public_key")
        if not public_key:
            return
        if public_key in self._known_contact_keys:
            return
        self._known_contact_keys.add(public_key)
        if self.disabled:
            return
        for loaded in list(self.bots.values()):
            if not loaded.record.enabled or loaded.code is None:
                continue
            for trig in loaded.code.collector.events:
                if trig.event == "new_contact":
                    self._spawn_event_run(loaded, "event new_contact", dict(data), trig.handler)

    # ------------------------------------------------------------------
    # Run plumbing
    # ------------------------------------------------------------------
    def _spawn_run(
        self,
        loaded: LoadedBot,
        trigger: str,
        msg: BotMessage,
        *,
        handler: Any,
        legacy: bool = False,
        delay: float = 0.0,
        user_id: str | None = None,
    ) -> None:
        task = asyncio.create_task(
            self._run_bot(
                loaded,
                trigger,
                msg=msg,
                handler=handler,
                legacy=legacy,
                delay=delay,
                user_id=user_id,
            )
        )
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    def _spawn_event_run(self, loaded: LoadedBot, trigger: str, event: dict, handler: Any) -> None:
        task = asyncio.create_task(self._run_bot(loaded, trigger, event=event, handler=handler))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _make_context(
        self,
        loaded: LoadedBot,
        *,
        msg: BotMessage | None,
        is_test: bool = False,
    ) -> BotContext:
        from app.repository.bots import BotRepository

        bot = loaded.record
        state = await BotRepository.get_state(bot.id)
        locale = self.settings.default_language
        if msg is not None and self.settings.auto_detect_language and msg.text:
            locale = detect_language(msg.text, self.settings.default_language)

        def _log(level: str, message: str) -> None:
            self.log(level, bot.name, message)

        return BotContext(
            bot_id=bot.id,
            bot_name=bot.name,
            settings=dict(bot.settings),
            state=state,
            origin_is_dm=bool(msg.is_dm) if msg else False,
            origin_sender_key=msg.sender_key if msg else None,
            origin_channel_key=msg.channel_key if msg else None,
            locale=locale,
            is_test=is_test,
            log_fn=_log,
            send_fn=self.send_bot_message,
            translator=self.translator,
            loop=asyncio.get_running_loop(),
        )

    async def _run_bot(
        self,
        loaded: LoadedBot,
        trigger: str,
        *,
        msg: BotMessage | None = None,
        event: dict | None = None,
        handler: Any = None,
        legacy: bool = False,
        delay: float = 0.0,
        user_id: str | None = None,
    ) -> None:
        from app.repository.bots import BotRepository, BotRunRepository

        bot = loaded.record
        if delay > 0:
            await asyncio.sleep(delay)
            if user_id:
                loaded.queued_users.discard(user_id)
        elif msg is not None and not legacy and trigger.startswith("kw "):
            # Let retransmissions dedupe before reacting (parity with fanout bots).
            await asyncio.sleep(SETTLE_DELAY_SECONDS)
        elif legacy:
            await asyncio.sleep(SETTLE_DELAY_SECONDS)

        started_at = int(time.time())
        start = time.monotonic()
        ctx = await self._make_context(loaded, msg=msg)
        state_before = json.dumps(ctx.state, sort_keys=True, default=str)
        result = "no_reply"
        error: str | None = None
        try:
            if legacy:
                assert loaded.code is not None and msg is not None
                await call_legacy(loaded.code, ctx, msg, BOT_EXECUTION_TIMEOUT, _bot_executor)
            else:
                await call_handler(handler, ctx, msg, event, BOT_EXECUTION_TIMEOUT, _bot_executor)
            if ctx.replies_sent > 0:
                result = "replied"
        except TimeoutError:
            result = "timeout"
            error = f"execution exceeded {BOT_EXECUTION_TIMEOUT:.0f}s timeout"
            self.log("ERROR", bot.name, error)
        except Exception as exc:  # noqa: BLE001 - operator code can raise anything
            result = "error"
            error = f"{type(exc).__name__}: {exc}"
            self.log("ERROR", bot.name, error)
            logger.warning("Bot %s failed", bot.name, exc_info=True)

        duration_ms = int((time.monotonic() - start) * 1000)

        # Persist state when it changed.
        try:
            state_after = json.dumps(ctx.state, sort_keys=True, default=str)
            if state_after != state_before:
                await BotRepository.set_state(bot.id, ctx.state)
        except Exception:
            logger.warning("Failed to persist state for bot %s", bot.name, exc_info=True)

        try:
            await BotRepository.set_last_error(bot.id, error)
            await BotRunRepository.record(
                bot_id=bot.id,
                started_at=started_at,
                duration_ms=duration_ms,
                trigger=trigger,
                sender_name=msg.sender_name if msg else None,
                sender_key=msg.sender_key if msg else None,
                channel_key=msg.channel_key if msg else None,
                channel_name=msg.channel_name if msg else None,
                is_dm=bool(msg.is_dm) if msg else False,
                result=result,
                replies=ctx.replies_sent,
                error=error,
            )
        except Exception:
            logger.warning("Failed to record bot run for %s", bot.name, exc_info=True)

        if result == "replied":
            where = "DM" if (msg and msg.is_dm) else (msg.channel_name if msg else trigger)
            self.log(
                "INFO",
                bot.name,
                f"{trigger} → {ctx.replies_sent} repl{'y' if ctx.replies_sent == 1 else 'ies'} ({where}, {duration_ms / 1000:.1f}s)",
            )

    # ------------------------------------------------------------------
    # Webhooks
    # ------------------------------------------------------------------
    def find_webhook(self, slug: str) -> tuple[LoadedBot, Any] | None:
        for loaded in self.bots.values():
            if loaded.code is None or not loaded.record.enabled:
                continue
            for trig in loaded.code.collector.webhooks:
                if trig.slug == slug:
                    return loaded, trig.handler
        return None

    async def run_webhook(self, loaded: LoadedBot, handler: Any, slug: str, payload: dict) -> None:
        await self._run_bot(loaded, f"webhook /{slug}", event=payload, handler=handler)

    # ------------------------------------------------------------------
    # Ticker: cron triggers, schedules, feeds
    # ------------------------------------------------------------------
    async def _ticker(self) -> None:
        while True:
            try:
                await asyncio.sleep(TICK_SECONDS)
                if self.disabled:
                    continue
                now = datetime.now()
                self._tick_bot_crons(now)
                await self._tick_schedules(now)
                await self._tick_feeds()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Bot engine ticker iteration failed")

    def _cron_next(self, expression: str, after: datetime) -> datetime | None:
        try:
            schedule: CronSchedule = parse_cron(expression)
        except Exception:
            return None
        return schedule.next_fire(after)

    def _tick_bot_crons(self, now: datetime) -> None:
        for loaded in list(self.bots.values()):
            if not loaded.record.enabled or loaded.code is None:
                continue
            for cache_key, expression, handler in loaded.cron_entries():
                nxt = loaded.cron_next.get(cache_key)
                if nxt is None:
                    nxt = self._cron_next(expression, now)
                    if nxt is None:
                        continue
                    loaded.cron_next[cache_key] = nxt
                    continue
                if now >= nxt:
                    loaded.cron_next[cache_key] = self._cron_next(expression, now) or nxt
                    self.log("INFO", loaded.record.name, f"cron {expression} fired")
                    task = asyncio.create_task(
                        self._run_bot(loaded, f"cron {expression}", handler=handler)
                    )
                    self._tasks.add(task)
                    task.add_done_callback(self._tasks.discard)

    async def _tick_schedules(self, now: datetime) -> None:
        from app.bots.placeholders import resolve_placeholders
        from app.repository.bots import BotScheduleRepository

        try:
            schedules = await BotScheduleRepository.get_all()
        except Exception:
            logger.exception("Failed to load schedules")
            return
        live_ids = set()
        for schedule in schedules:
            live_ids.add(schedule.id)
            if not schedule.enabled:
                self._schedule_next.pop(schedule.id, None)
                continue
            nxt = self._schedule_next.get(schedule.id)
            if nxt is None:
                computed = self._cron_next(schedule.cron, now)
                if computed is not None:
                    self._schedule_next[schedule.id] = computed
                continue
            if now < nxt:
                continue
            self._schedule_next[schedule.id] = self._cron_next(schedule.cron, now) or nxt
            text = await resolve_placeholders(schedule.message)
            self.log(
                "INFO",
                "scheduler",
                f"fired '{schedule.label}' → channel {schedule.channel_key[:8]}",
            )
            try:
                await self.send_bot_message(
                    is_dm=False,
                    destination=None,
                    channel_key=schedule.channel_key,
                    text=text,
                    flood_scope_override=schedule.flood_scope,
                )
                await BotScheduleRepository.record_run(schedule.id, "sent")
            except Exception as exc:
                await BotScheduleRepository.record_run(schedule.id, f"error: {exc}")
                self.log("ERROR", "scheduler", f"'{schedule.label}' failed: {exc}")
        for stale in set(self._schedule_next) - live_ids:
            self._schedule_next.pop(stale, None)

    async def _tick_feeds(self) -> None:
        from app.repository.bots import BotFeedRepository

        try:
            feeds = await BotFeedRepository.get_all()
        except Exception:
            logger.exception("Failed to load feeds")
            return
        now = int(time.time())
        for feed in feeds:
            if not feed.enabled or feed.id in self._feed_locks:
                continue
            due_at = (feed.last_check_at or 0) + max(60, feed.interval_seconds)
            if feed.last_check_at is not None and now < due_at:
                continue
            self._feed_locks.add(feed.id)
            task = asyncio.create_task(self._check_feed(feed.id))
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)

    async def _check_feed(self, feed_id: str) -> None:
        from app.bots.feeds import FeedError, fetch_feed_items, format_item, select_new_items
        from app.repository.bots import BotFeedRepository

        try:
            feed = await BotFeedRepository.get(feed_id)
            if feed is None or not feed.enabled:
                return
            try:
                items = await fetch_feed_items(
                    feed_type=feed.feed_type, url=feed.url, items_path=feed.items_path
                )
            except FeedError as exc:
                await BotFeedRepository.record_check(
                    feed.id, newest_id=None, posted=0, error=str(exc)
                )
                self.log("ERROR", "feeds", f"{feed.name}: {exc}")
                return
            result = select_new_items(items, feed.last_item_id, feed.max_posts_per_check)
            posted = 0
            for item in result.new_items:
                text = format_item(feed.format, item)
                if not text:
                    continue
                await self.send_bot_message(
                    is_dm=False,
                    destination=None,
                    channel_key=feed.channel_key,
                    text=text,
                    flood_scope_override=None,
                )
                posted += 1
            await BotFeedRepository.record_check(
                feed.id, newest_id=result.newest_id, posted=posted, error=None
            )
            if posted:
                self.log("INFO", "feeds", f"{feed.name}: posted {posted} new item(s)")
        except Exception as exc:
            logger.exception("Feed check failed for %s", feed_id)
            try:
                from app.repository.bots import BotFeedRepository as _repo

                await _repo.record_check(feed_id, newest_id=None, posted=0, error=str(exc))
            except Exception:
                pass
        finally:
            self._feed_locks.discard(feed_id)

    # ------------------------------------------------------------------
    # Test runs
    # ------------------------------------------------------------------
    async def test_run(self, record: Bot, request: BotTestRequest) -> BotTestResponse:
        """Run the bot against a simulated message; sends are captured."""
        try:
            code = load_bot_code(record.code)
        except BotCodeError as exc:
            return BotTestResponse(matched=False, error=f"code error: {exc}")

        msg = BotMessage(
            text=request.text,
            sender_name=request.sender_name,
            sender_key=request.sender_key,
            is_dm=request.is_dm,
            channel_key=request.channel_key
            or ("" if request.is_dm else "TESTCHANNEL000000000000000000000"),
            channel_name=request.channel_name or (None if request.is_dm else "#test"),
        )

        loaded = LoadedBot(record=record, code=code)
        match_text, _, _ = self._strip_prefix_and_mention(msg.text)
        lowered = match_text.lower()

        handler = None
        trigger = None
        matched_keyword = None
        for keywords, kw_handler in loaded.keyword_map:
            for keyword in keywords:
                if lowered == keyword or lowered.startswith(keyword + " "):
                    handler = kw_handler
                    matched_keyword = keyword
                    trigger = f"kw {keyword}"
                    break
            if handler:
                break
        legacy = False
        if handler is None and code.is_legacy:
            legacy = True
            trigger = "message (legacy)"
        if handler is None and not legacy and code.collector.messages:
            handler = code.collector.messages[0].handler
            trigger = "message"
        if handler is None and not legacy:
            return BotTestResponse(
                matched=False,
                error="no trigger matched this message — check the bot's keywords",
            )

        logs: list[str] = []
        ctx = await self._make_context(loaded, msg=msg, is_test=True)
        original_log = ctx._log_fn

        def capture_log(level: str, message: str) -> None:
            logs.append(f"{level} {message}")
            if original_log:
                original_log(level, message)

        ctx._log_fn = capture_log

        if matched_keyword:
            remainder = match_text[len(matched_keyword) :].strip()
            msg.keyword = matched_keyword
            msg.args = remainder.split()

        start = time.monotonic()
        error: str | None = None
        try:
            if legacy:
                await call_legacy(code, ctx, msg, BOT_EXECUTION_TIMEOUT, _bot_executor)
            else:
                await call_handler(handler, ctx, msg, None, BOT_EXECUTION_TIMEOUT, _bot_executor)
        except TimeoutError:
            error = f"execution exceeded {BOT_EXECUTION_TIMEOUT:.0f}s timeout"
        except Exception as exc:  # noqa: BLE001
            error = f"{type(exc).__name__}: {exc}"
        duration_ms = int((time.monotonic() - start) * 1000)

        from app.repository.bots import BotRunRepository

        try:
            await BotRunRepository.record(
                bot_id=record.id,
                started_at=int(time.time()),
                duration_ms=duration_ms,
                trigger=trigger or "test",
                sender_name=request.sender_name,
                sender_key=request.sender_key,
                channel_key=msg.channel_key,
                channel_name=msg.channel_name,
                is_dm=request.is_dm,
                result="error" if error else ("replied" if ctx.captured_sends else "no_reply"),
                replies=len(ctx.captured_sends),
                error=error,
                test_run=True,
            )
        except Exception:
            logger.warning("Failed to record test run", exc_info=True)

        return BotTestResponse(
            matched=True,
            trigger=trigger,
            duration_ms=duration_ms,
            replies=ctx.captured_sends,
            error=error,
            logs=logs,
        )

    # ------------------------------------------------------------------
    # Introspection for the API layer
    # ------------------------------------------------------------------
    def decorate_record(self, record: Bot) -> Bot:
        """Fill a Bot model's derived fields from the loaded runtime state."""
        loaded = self.bots.get(record.id)
        if loaded is None:
            loaded = self._load_one(record)
        if loaded.code is not None:
            record.declared_keywords = loaded.code.declared_keywords
            record.declared_crons = loaded.code.declared_crons
            record.declared_events = [t.event for t in loaded.code.collector.events]
            record.declared_webhooks = [t.slug for t in loaded.code.collector.webhooks]
            record.is_legacy = loaded.code.is_legacy
        record.load_error = loaded.load_error
        return record


bot_engine = BotEngine()
