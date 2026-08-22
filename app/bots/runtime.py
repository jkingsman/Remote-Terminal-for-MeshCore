"""Loading and validating DB-stored bot source into callable handlers.

Two authoring styles are supported:

* **Decorated** (the native style): ``from remoteterm import bot`` +
  ``@bot.on_keyword(...)`` / ``on_message`` / ``on_cron`` / ``on_event`` /
  ``on_webhook`` handlers taking ``(ctx, msg)`` or ``(ctx)``.

* **Legacy** (migrated fanout bots): a module-level ``def bot(**kwargs)`` (any
  of the historical signatures). It is auto-wrapped as a catch-all message
  handler; its ``str`` / ``list[str]`` / ``{"region", "message"}`` return
  shapes are honored via ``ctx``.

Execution of the source happens once per (re)load, in-process — the same trust
model the fanout bot editor always had.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any

from app.bots.api import BotContext, BotMessage, HandlerCollector, collect_handlers

logger = logging.getLogger(__name__)

# Shared persistent dict across all bots and reloads (legacy `_bot_globals`).
_bot_globals: dict[str, Any] = {}


class BotCodeError(ValueError):
    """Raised when bot source fails to compile, execute, or declare handlers."""


@dataclass
class LoadedCode:
    """The runnable form of one bot's source."""

    collector: HandlerCollector
    source: str = ""
    is_legacy: bool = False
    namespace: dict[str, Any] = field(default_factory=dict)

    @property
    def declared_keywords(self) -> list[str]:
        out: list[str] = []
        for trig in self.collector.keywords:
            out.extend(trig.keywords)
        return out

    @property
    def declared_crons(self) -> list[str]:
        return [t.expression for t in self.collector.crons if t.expression]

    @property
    def has_generic_keyword_handler(self) -> bool:
        return any(not t.keywords for t in self.collector.keywords)

    @property
    def has_generic_cron_handler(self) -> bool:
        return any(not t.expression for t in self.collector.crons)


def load_bot_code(code: str) -> LoadedCode:
    """Compile + execute bot source, collecting its declared handlers.

    Raises :class:`BotCodeError` with a human-readable message on syntax
    errors, execution errors, or a source that declares nothing runnable.
    """
    if not code or not code.strip():
        raise BotCodeError("bot code is empty")

    try:
        compiled = compile(code, "<bot>", "exec")
    except SyntaxError as exc:
        line = f" (line {exc.lineno})" if exc.lineno else ""
        raise BotCodeError(f"syntax error{line}: {exc.msg}") from exc

    namespace: dict[str, Any] = {"__builtins__": __builtins__, "_bot_globals": _bot_globals}

    def _execute() -> None:
        exec(compiled, namespace)  # noqa: S102 - deliberate: operator-authored bot code

    try:
        collector = collect_handlers(_execute)
    except BotCodeError:
        raise
    except Exception as exc:
        raise BotCodeError(f"error while loading bot code: {exc}") from exc

    loaded = LoadedCode(collector=collector, source=code, namespace=namespace)

    if collector.is_empty():
        legacy = namespace.get("bot")
        if callable(legacy):
            from app.fanout.bot_exec import _analyze_bot_signature

            try:
                _analyze_bot_signature(legacy)
            except ValueError as exc:
                raise BotCodeError(str(exc)) from exc
            loaded.is_legacy = True
        else:
            raise BotCodeError(
                "bot code declares no triggers — add a @bot.on_keyword / on_message / "
                "on_cron / on_event / on_webhook handler (or a legacy def bot(**kwargs))"
            )

    # Validate code-declared cron expressions eagerly so a typo fails at save
    # time rather than silently never firing.
    from app.bots.cron import validate_cron

    for trig in collector.crons:
        if trig.expression:
            error = validate_cron(trig.expression)
            if error:
                raise BotCodeError(f"invalid cron expression {trig.expression!r}: {error}")

    return loaded


async def call_handler(
    handler: Any,
    ctx: BotContext,
    msg: BotMessage | None,
    event: dict[str, Any] | None,
    timeout_seconds: float,
    executor: Any,
) -> None:
    """Invoke a decorated handler (async on the loop, sync in the thread pool)."""
    args: tuple[Any, ...]
    if msg is not None:
        args = (ctx, msg)
    elif event is not None:
        args = (ctx, event)
    else:
        args = (ctx,)

    # Sync or async, a handler may return a reply string/list like legacy bots
    # do — the shapes _apply_return_value knows — instead of calling ctx.reply.
    if asyncio.iscoroutinefunction(handler):
        result = await asyncio.wait_for(handler(*args), timeout=timeout_seconds)
        await _apply_return_value(result, ctx)
        return

    loop = asyncio.get_running_loop()

    def _run_sync() -> Any:
        return handler(*args)

    result = await asyncio.wait_for(loop.run_in_executor(executor, _run_sync), timeout_seconds)
    await _apply_return_value(result, ctx)


async def call_legacy(
    loaded: LoadedCode,
    ctx: BotContext,
    msg: BotMessage,
    timeout_seconds: float,
    executor: Any,
) -> None:
    """Invoke a legacy ``def bot(...)`` bot via the historical execution path.

    Reuses :func:`app.fanout.bot_exec.execute_bot_code` verbatim — same
    signature analysis, same call styles, same return coercion — so migrated
    fanout bots behave exactly as before.
    """
    from app.fanout.bot_exec import execute_bot_code

    loop = asyncio.get_running_loop()

    def _run() -> Any:
        return execute_bot_code(
            loaded.source,
            msg.sender_name,
            msg.sender_key,
            msg.text,
            msg.is_dm,
            msg.channel_key,
            msg.channel_name,
            msg.sender_timestamp,
            msg.path,
            msg.is_outgoing,
            msg.path_bytes_per_hop,
            None,  # packet_hash (not tracked in the new engine path)
            msg.region,
            msg.scoped,
        )

    result = await asyncio.wait_for(loop.run_in_executor(executor, _run), timeout_seconds)
    await _apply_return_value(result, ctx)


async def _apply_return_value(result: Any, ctx: BotContext) -> None:
    """Send legacy-style return shapes (str / list[str] / BotReply) via ctx."""
    from app.fanout.bot_exec import BotReply

    if result is None:
        return
    if isinstance(result, BotReply):
        for text in result.messages:
            await ctx.reply(text, region=result.flood_scope_override)
        return
    if isinstance(result, str):
        if result.strip():
            await ctx.reply(result)
        return
    if isinstance(result, list):
        for item in result:
            if isinstance(item, str) and item.strip():
                await ctx.reply(item)
