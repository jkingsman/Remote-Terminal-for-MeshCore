"""The built-in bot library: seeding and reset.

Every file in ``library/code/*.py`` is one seedable bot. Files are never
imported — their source text is stored into the ``bots`` table and executed by
the runtime like any user bot. Each must declare a module-level ``BOT_META``
dict (read by exec'ing the source through the normal loader):

    BOT_META = {
        "key": "wx",                  # stable identity (builtin_key)
        "name": "wx",                 # default display name
        "category": "Weather",
        "description": "...",
        "version": "1.0.0",
        "settings_schema": [...],      # optional; drives the Settings tab
        "settings": {...},             # optional defaults for ctx.settings
        "respond_to_dms": True,        # optional (default True)
        "admin_only": False,           # optional
        "cooldown_seconds": 0,         # optional
        "per_user_cooldown_seconds": 0,
        "queue_threshold_seconds": 0,
    }

Seeding is additive and non-destructive: a bot the operator modified
(``modified = 1``) is never touched; an unmodified built-in is refreshed when
the library ships a newer ``version``. All seeded bots start **disabled** —
enabling what a node answers to is the operator's call.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

CODE_DIR = Path(__file__).parent / "code"


class LibraryError(ValueError):
    """Raised when a library code file is malformed."""


def _extract_meta(source: str, filename: str) -> dict[str, Any]:
    from app.bots.runtime import BotCodeError, load_bot_code

    try:
        loaded = load_bot_code(source)
    except BotCodeError as exc:
        raise LibraryError(f"{filename}: {exc}") from exc
    meta = loaded.namespace.get("BOT_META")
    if not isinstance(meta, dict):
        raise LibraryError(f"{filename}: missing module-level BOT_META dict")
    for required in ("key", "name", "category", "description", "version"):
        if not meta.get(required):
            raise LibraryError(f"{filename}: BOT_META.{required} is required")
    return meta


def list_library() -> list[dict[str, Any]]:
    """Return ``[{meta..., code}]`` for every library file, sorted by key."""
    entries: list[dict[str, Any]] = []
    if not CODE_DIR.exists():
        return entries
    for path in sorted(CODE_DIR.glob("*.py")):
        if path.name.startswith("_"):
            continue
        source = path.read_text(encoding="utf-8")
        try:
            meta = _extract_meta(source, path.name)
        except LibraryError as exc:
            logger.error("Skipping library bot: %s", exc)
            continue
        entries.append({**meta, "code": source})
    return entries


def get_library_entry(builtin_key: str) -> dict[str, Any] | None:
    for entry in list_library():
        if entry["key"] == builtin_key:
            return entry
    return None


async def ensure_seeded() -> int:
    """Insert missing library bots; refresh unmodified ones on version bumps.

    Returns the number of rows inserted or refreshed.
    """
    from app.repository.bots import BotRepository

    changed = 0
    for entry in list_library():
        existing = await BotRepository.get_by_builtin_key(entry["key"])
        if existing is None:
            name = entry["name"]
            suffix = 2
            while await BotRepository.name_exists(name):
                name = f"{entry['name']} {suffix}"
                suffix += 1
            await BotRepository.create(
                name=name,
                category=entry["category"],
                description=entry["description"],
                code=entry["code"],
                enabled=False,
                admin_only=bool(entry.get("admin_only", False)),
                respond_to_dms=bool(entry.get("respond_to_dms", True)),
                scope=entry.get("scope"),
                cooldown_seconds=float(entry.get("cooldown_seconds", 0)),
                per_user_cooldown_seconds=float(entry.get("per_user_cooldown_seconds", 0)),
                queue_threshold_seconds=float(entry.get("queue_threshold_seconds", 0)),
                settings_schema=entry.get("settings_schema") or [],
                settings=entry.get("settings") or {},
                builtin_key=entry["key"],
                builtin_version=entry["version"],
            )
            changed += 1
        elif not existing.modified and existing.builtin_version != entry["version"]:
            await BotRepository.update(
                existing.id,
                code=entry["code"],
                description=entry["description"],
                category=entry["category"],
                settings_schema=entry.get("settings_schema") or [],
                builtin_version=entry["version"],
            )
            changed += 1
    if changed:
        logger.info("Bot library seeding applied %d change(s)", changed)
    return changed
