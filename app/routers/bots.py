"""REST API for the Bots workspace: bots, schedules, feeds, logs, engine, hooks."""

from __future__ import annotations

import logging
import time
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Header, HTTPException, Query

from app.bots.cron import parse_cron, validate_cron
from app.bots.engine import bot_engine
from app.bots.library import get_library_entry, list_library
from app.bots.runtime import BotCodeError, load_bot_code
from app.models import (
    Bot,
    BotCreateRequest,
    BotEngineSettingsUpdate,
    BotEngineStatus,
    BotFeed,
    BotFeedCreateRequest,
    BotFeedTestRequest,
    BotFeedUpdateRequest,
    BotLogEntry,
    BotRun,
    BotSchedule,
    BotScheduleCreateRequest,
    BotScheduleUpdateRequest,
    BotTestRequest,
    BotTestResponse,
    BotUpdateRequest,
)
from app.repository.bots import (
    BotEngineSettingsRepository,
    BotFeedRepository,
    BotRepository,
    BotRunRepository,
    BotScheduleRepository,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/bots", tags=["bots"])
hooks_router = APIRouter(prefix="/hooks", tags=["bots"])

_WINDOW_SECONDS = {"1h": 3600, "24h": 86400, "7d": 7 * 86400}

DEFAULT_NEW_BOT_CODE = '''"""New bot — reacts to a keyword. See the API hints below the editor."""

from remoteterm import bot

BOT_META = {
    "key": "",  # built-ins only; leave empty for custom bots
    "name": "my-bot",
    "category": "Custom",
    "description": "Describe what this bot does",
    "version": "1.0.0",
}


@bot.on_keyword("mybot")
async def handle(ctx, msg):
    # msg.args holds words after the keyword; ctx.settings holds typed settings.
    # Replies that may exceed one RF frame: ctx.reply_split(text) sends (i/n) parts.
    await ctx.reply(f"Hello {msg.sender_name or 'there'}!")
'''


def _extract_meta_updates(code: str) -> dict[str, Any]:
    """Refresh schema-ish fields from the code's BOT_META on save (best effort)."""
    try:
        loaded = load_bot_code(code)
    except BotCodeError:
        return {}
    meta = loaded.namespace.get("BOT_META")
    if not isinstance(meta, dict):
        return {}
    updates: dict[str, Any] = {}
    schema = meta.get("settings_schema")
    if isinstance(schema, list):
        updates["settings_schema"] = schema
    return updates


async def _validate_code_or_400(code: str) -> None:
    try:
        load_bot_code(code)
    except BotCodeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def _runs_24h_by_bot(runs: list[BotRun]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for run in runs:
        if not run.test_run:
            counts[run.bot_id] = counts.get(run.bot_id, 0) + 1
    return counts


# ---------------------------------------------------------------------------
# Bots CRUD
# ---------------------------------------------------------------------------


@router.get("", response_model=list[Bot])
async def list_bots() -> list[Bot]:
    records = await BotRepository.get_all()
    cutoff = int(time.time()) - 86400
    recent = await BotRunRepository.recent(limit=2000)
    counts: dict[str, int] = {}
    for run in recent:
        if not run.test_run and run.started_at >= cutoff:
            counts[run.bot_id] = counts.get(run.bot_id, 0) + 1
    out = []
    for record in records:
        decorated = bot_engine.decorate_record(record)
        decorated.runs_24h = counts.get(record.id, 0)
        out.append(decorated)
    return out


@router.get("/library")
async def get_library() -> list[dict[str, Any]]:
    """The built-in library (for New Bot → From library), without code bodies."""
    installed = {b.builtin_key for b in await BotRepository.get_all() if b.builtin_key}
    return [
        {
            "key": entry["key"],
            "name": entry["name"],
            "category": entry["category"],
            "description": entry["description"],
            "version": entry["version"],
            "installed": entry["key"] in installed,
        }
        for entry in list_library()
    ]


@router.get("/engine", response_model=BotEngineStatus)
async def get_engine_status() -> BotEngineStatus:
    from app.config import settings as server_settings

    records = await BotRepository.get_all()
    stats = await BotRunRepository.stats(86400)
    return BotEngineStatus(
        settings=bot_engine.settings,
        disabled_until_restart=bot_engine.disabled_until_restart,
        disabled_by_env=bool(getattr(server_settings, "disable_bots", False)),
        total_bots=len(records),
        enabled_bots=sum(1 for r in records if r.enabled),
        erroring_bots=sum(1 for r in records if r.enabled and r.last_error),
        runs_24h=stats["runs"],
    )


@router.patch("/engine", response_model=BotEngineStatus)
async def update_engine_settings(body: BotEngineSettingsUpdate) -> BotEngineStatus:
    fields = body.model_dump(exclude_unset=True)
    if "mention_mode" in fields and fields["mention_mode"] not in ("also", "only", "off"):
        raise HTTPException(status_code=400, detail="mention_mode must be also|only|off")
    if "profanity_mode" in fields and fields["profanity_mode"] not in ("off", "censor", "drop"):
        raise HTTPException(status_code=400, detail="profanity_mode must be off|censor|drop")
    if "admin_users" in fields and fields["admin_users"] is not None:
        for user in fields["admin_users"]:
            key = user["public_key"] if isinstance(user, dict) else user.public_key
            if len(key) != 64:
                raise HTTPException(
                    status_code=400, detail="admin public keys must be 64 hex chars"
                )
        fields["admin_users"] = [
            user if isinstance(user, dict) else user.model_dump() for user in fields["admin_users"]
        ]
    await BotEngineSettingsRepository.update(**fields)
    await bot_engine.reload_settings()
    return await get_engine_status()


@router.post("/engine/disable-until-restart")
async def disable_until_restart() -> dict[str, Any]:
    """Kill switch: silence the bot engine AND any legacy fanout bot modules."""
    bot_engine.disabled_until_restart = True
    bot_engine.log("WARN", "engine", "All bots disabled until restart (operator action)")

    from app.fanout.manager import fanout_manager

    await fanout_manager.disable_bots_until_restart()

    from app.services.radio_runtime import radio_runtime as radio_manager
    from app.websocket import broadcast_health

    broadcast_health(radio_manager.is_connected, radio_manager.connection_info)
    return {"status": "ok", "disabled_until_restart": True}


@router.get("/logs", response_model=list[BotLogEntry])
async def get_logs(limit: int = Query(default=200, ge=1, le=500)) -> list[BotLogEntry]:
    entries = list(bot_engine.log_ring)
    return entries[-limit:]


@router.get("/stats")
async def get_stats(window: str = Query(default="24h")) -> dict[str, Any]:
    seconds = _WINDOW_SECONDS.get(window)
    if seconds is None:
        raise HTTPException(status_code=400, detail="window must be 1h|24h|7d")
    return await BotRunRepository.stats(seconds)


@router.get("/runs", response_model=list[BotRun])
async def get_runs(
    bot_id: str | None = None, limit: int = Query(default=50, ge=1, le=200)
) -> list[BotRun]:
    return await BotRunRepository.recent(bot_id=bot_id, limit=limit)


# Registered before POST /{bot_id}/test — with the parameterized route first,
# FastAPI would match this path with bot_id="feeds".
@router.post("/feeds/test")
async def test_feed(body: BotFeedTestRequest) -> dict[str, Any]:
    """Fetch a feed URL and preview the first 3 formatted items (no sends)."""
    from app.bots.feeds import FeedError, fetch_feed_items, format_item

    try:
        items = await fetch_feed_items(
            feed_type=body.feed_type, url=body.url, items_path=body.items_path
        )
    except FeedError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    preview = [format_item(body.format, item) for item in items[:3]]
    return {"item_count": len(items), "preview": preview}


@router.post("", response_model=Bot)
async def create_bot(body: BotCreateRequest) -> Bot:
    name = body.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="name is required")
    if await BotRepository.name_exists(name):
        raise HTTPException(status_code=409, detail=f"a bot named {name!r} already exists")

    code = body.code
    category = body.category
    description = body.description
    settings_schema: list[dict[str, Any]] = []
    settings: dict[str, Any] = {}
    meta_defaults: dict[str, Any] = {}
    if body.from_builtin_key:
        entry = get_library_entry(body.from_builtin_key)
        if entry is None:
            raise HTTPException(status_code=404, detail="library bot not found")
        code = entry["code"]
        category = category if category != "Custom" else entry["category"]
        description = description or entry["description"]
        settings_schema = entry.get("settings_schema") or []
        settings = entry.get("settings") or {}
        meta_defaults = entry
    elif not code.strip():
        code = DEFAULT_NEW_BOT_CODE

    await _validate_code_or_400(code)
    bot = await BotRepository.create(
        name=name,
        category=category,
        description=description,
        code=code,
        enabled=body.enabled,
        respond_to_dms=bool(meta_defaults.get("respond_to_dms", True)),
        admin_only=bool(meta_defaults.get("admin_only", False)),
        cooldown_seconds=float(meta_defaults.get("cooldown_seconds", 0)),
        settings_schema=settings_schema,
        settings=settings,
        modified=not bool(body.from_builtin_key),
    )
    await bot_engine.reload_bot(bot.id)
    return bot_engine.decorate_record(bot)


@router.get("/{bot_id}", response_model=Bot)
async def get_bot(bot_id: str) -> Bot:
    record = await BotRepository.get(bot_id)
    if record is None:
        raise HTTPException(status_code=404, detail="bot not found")
    return bot_engine.decorate_record(record)


@router.patch("/{bot_id}", response_model=Bot)
async def update_bot(bot_id: str, body: BotUpdateRequest) -> Bot:
    record = await BotRepository.get(bot_id)
    if record is None:
        raise HTTPException(status_code=404, detail="bot not found")
    fields = body.model_dump(exclude_unset=True)

    if "name" in fields:
        name = (fields["name"] or "").strip()
        if not name:
            raise HTTPException(status_code=400, detail="name cannot be empty")
        if await BotRepository.name_exists(name, exclude_id=bot_id):
            raise HTTPException(status_code=409, detail=f"a bot named {name!r} already exists")
        fields["name"] = name

    if "code" in fields and fields["code"] is not None:
        await _validate_code_or_400(fields["code"])
        fields.update(_extract_meta_updates(fields["code"]))
        if record.builtin_key and fields["code"] != record.code:
            fields["modified"] = True

    if "ui_triggers" in fields and fields["ui_triggers"] is not None:
        for trig in fields["ui_triggers"]:
            kind = trig.get("kind")
            spec = (trig.get("spec") or "").strip()
            if kind not in ("keyword", "cron") or not spec:
                raise HTTPException(
                    status_code=400, detail="ui_triggers entries need kind keyword|cron and a spec"
                )
            if kind == "cron":
                error = validate_cron(spec)
                if error:
                    raise HTTPException(status_code=400, detail=f"invalid cron {spec!r}: {error}")

    updated = await BotRepository.update(bot_id, **fields)
    assert updated is not None
    await bot_engine.reload_bot(bot_id)
    return bot_engine.decorate_record(updated)


@router.delete("/{bot_id}")
async def delete_bot(bot_id: str) -> dict[str, str]:
    deleted = await BotRepository.delete(bot_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="bot not found")
    bot_engine.remove_bot(bot_id)
    return {"status": "deleted"}


@router.post("/{bot_id}/reset", response_model=Bot)
async def reset_bot(bot_id: str) -> Bot:
    """Restore a built-in bot's code and schema from the shipped library."""
    record = await BotRepository.get(bot_id)
    if record is None:
        raise HTTPException(status_code=404, detail="bot not found")
    if not record.builtin_key:
        raise HTTPException(status_code=400, detail="custom bots have no built-in to reset to")
    entry = get_library_entry(record.builtin_key)
    if entry is None:
        raise HTTPException(status_code=404, detail="library source for this bot is missing")
    updated = await BotRepository.update(
        bot_id,
        code=entry["code"],
        description=entry["description"],
        category=entry["category"],
        settings_schema=entry.get("settings_schema") or [],
        builtin_version=entry["version"],
        modified=False,
    )
    assert updated is not None
    await bot_engine.reload_bot(bot_id)
    return bot_engine.decorate_record(updated)


@router.post("/{bot_id}/test", response_model=BotTestResponse)
async def test_bot(bot_id: str, body: BotTestRequest) -> BotTestResponse:
    record = await BotRepository.get(bot_id)
    if record is None:
        raise HTTPException(status_code=404, detail="bot not found")
    return await bot_engine.test_run(record, body)


# ---------------------------------------------------------------------------
# Schedules
# ---------------------------------------------------------------------------


def _decorate_schedule(schedule: BotSchedule, channel_names: dict[str, str]) -> BotSchedule:
    schedule.channel_name = channel_names.get(schedule.channel_key)
    if schedule.enabled:
        try:
            nxt = parse_cron(schedule.cron).next_fire(datetime.now())
            schedule.next_run_at = int(nxt.timestamp()) if nxt else None
        except Exception:
            schedule.next_run_at = None
    return schedule


async def _channel_names() -> dict[str, str]:
    from app.repository import ChannelRepository

    return {c.key: c.name for c in await ChannelRepository.get_all()}


@router.get("/schedules/all", response_model=list[BotSchedule])
async def list_schedules() -> list[BotSchedule]:
    names = await _channel_names()
    return [_decorate_schedule(s, names) for s in await BotScheduleRepository.get_all()]


@router.post("/schedules", response_model=BotSchedule)
async def create_schedule(body: BotScheduleCreateRequest) -> BotSchedule:
    error = validate_cron(body.cron)
    if error:
        raise HTTPException(status_code=400, detail=f"invalid cron: {error}")
    if not body.label.strip() or not body.message.strip():
        raise HTTPException(status_code=400, detail="label and message are required")
    schedule = await BotScheduleRepository.create(
        label=body.label.strip(),
        cron=body.cron.strip(),
        channel_key=body.channel_key,
        message=body.message,
        flood_scope=body.flood_scope,
        enabled=body.enabled,
    )
    return _decorate_schedule(schedule, await _channel_names())


@router.patch("/schedules/{schedule_id}", response_model=BotSchedule)
async def update_schedule(schedule_id: str, body: BotScheduleUpdateRequest) -> BotSchedule:
    fields = body.model_dump(exclude_unset=True)
    if "cron" in fields and fields["cron"]:
        error = validate_cron(fields["cron"])
        if error:
            raise HTTPException(status_code=400, detail=f"invalid cron: {error}")
    schedule = await BotScheduleRepository.update(schedule_id, **fields)
    if schedule is None:
        raise HTTPException(status_code=404, detail="schedule not found")
    bot_engine._schedule_next.pop(schedule_id, None)  # recompute next fire
    return _decorate_schedule(schedule, await _channel_names())


@router.delete("/schedules/{schedule_id}")
async def delete_schedule(schedule_id: str) -> dict[str, str]:
    if not await BotScheduleRepository.delete(schedule_id):
        raise HTTPException(status_code=404, detail="schedule not found")
    bot_engine._schedule_next.pop(schedule_id, None)
    return {"status": "deleted"}


@router.get("/schedules/validate-cron")
async def validate_cron_expression(cron: str) -> dict[str, Any]:
    error = validate_cron(cron)
    if error:
        return {"valid": False, "error": error, "next_runs": []}
    schedule = parse_cron(cron)
    runs: list[int] = []
    moment = datetime.now()
    for _ in range(3):
        nxt = schedule.next_fire(moment)
        if nxt is None:
            break
        runs.append(int(nxt.timestamp()))
        moment = nxt
    return {"valid": True, "error": None, "next_runs": runs}


# ---------------------------------------------------------------------------
# Feeds
# ---------------------------------------------------------------------------


def _decorate_feed(feed: BotFeed, channel_names: dict[str, str]) -> BotFeed:
    feed.channel_name = channel_names.get(feed.channel_key)
    return feed


@router.get("/feeds/all", response_model=list[BotFeed])
async def list_feeds() -> list[BotFeed]:
    names = await _channel_names()
    return [_decorate_feed(f, names) for f in await BotFeedRepository.get_all()]


@router.post("/feeds", response_model=BotFeed)
async def create_feed(body: BotFeedCreateRequest) -> BotFeed:
    from app.bots.feeds import validate_feed_url

    if body.feed_type not in ("rss", "api"):
        raise HTTPException(status_code=400, detail="feed_type must be rss|api")
    error = validate_feed_url(body.url)
    if error:
        raise HTTPException(status_code=400, detail=error)
    feed = await BotFeedRepository.create(
        name=body.name.strip(),
        feed_type=body.feed_type,
        url=body.url.strip(),
        channel_key=body.channel_key,
        interval_seconds=max(60, body.interval_seconds),
        format=body.format,
        items_path=body.items_path,
        max_posts_per_check=body.max_posts_per_check,
        enabled=body.enabled,
    )
    return _decorate_feed(feed, await _channel_names())


@router.patch("/feeds/{feed_id}", response_model=BotFeed)
async def update_feed(feed_id: str, body: BotFeedUpdateRequest) -> BotFeed:
    from app.bots.feeds import validate_feed_url

    fields = body.model_dump(exclude_unset=True)
    if "url" in fields and fields["url"]:
        error = validate_feed_url(fields["url"])
        if error:
            raise HTTPException(status_code=400, detail=error)
    if "interval_seconds" in fields and fields["interval_seconds"]:
        fields["interval_seconds"] = max(60, fields["interval_seconds"])
    feed = await BotFeedRepository.update(feed_id, **fields)
    if feed is None:
        raise HTTPException(status_code=404, detail="feed not found")
    return _decorate_feed(feed, await _channel_names())


@router.delete("/feeds/{feed_id}")
async def delete_feed(feed_id: str) -> dict[str, str]:
    if not await BotFeedRepository.delete(feed_id):
        raise HTTPException(status_code=404, detail="feed not found")
    return {"status": "deleted"}


# ---------------------------------------------------------------------------
# Inbound webhooks (bot trigger)
# ---------------------------------------------------------------------------


@hooks_router.post("/{slug}")
async def inbound_hook(
    slug: str,
    payload: dict[str, Any],
    x_hook_token: str | None = Header(default=None),
) -> dict[str, str]:
    found = bot_engine.find_webhook(slug)
    if found is None:
        raise HTTPException(status_code=404, detail="no enabled bot listens on this hook")
    loaded, handler = found
    expected = str(loaded.record.settings.get("webhook_token", "") or "")
    if not expected:
        raise HTTPException(
            status_code=403,
            detail="webhook token not configured — set webhook_token in the bot's settings",
        )
    if x_hook_token != expected:
        raise HTTPException(status_code=403, detail="invalid webhook token")
    await bot_engine.run_webhook(loaded, handler, slug, payload)
    return {"status": "ok"}
