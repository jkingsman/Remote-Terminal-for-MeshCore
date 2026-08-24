"""Mesh-stat placeholders for scheduled messages (``{total_contacts}`` etc.).

Ported from meshcore-bot's ``[Keywords]`` placeholder set, trimmed to the
stats RemoteTerm already tracks. Resolved at fire time. The same summary backs
``ctx.mesh_stats()`` for bots; the query itself lives in the repository layer
(``StatisticsRepository.get_mesh_summary``) so tests can patch the DB handle.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

SUPPORTED_PLACEHOLDERS = (
    "{total_contacts}",
    "{total_repeaters}",
    "{contacts_24h}",
    "{repeaters_24h}",
    "{new_contacts_7d}",
    "{messages_24h}",
)


async def gather_mesh_stats() -> dict[str, int]:
    """Small mesh summary used by placeholders and ``ctx.mesh_stats()``."""
    from app.repository.settings import StatisticsRepository

    return await StatisticsRepository.get_mesh_summary()


async def resolve_placeholders(text: str) -> str:
    """Replace supported ``{placeholder}`` tokens; unknown tokens pass through."""
    if "{" not in text:
        return text

    try:
        stats = await gather_mesh_stats()
    except Exception:
        logger.warning("Failed to resolve schedule placeholders", exc_info=True)
        return text

    for name, value in stats.items():
        text = text.replace("{" + name + "}", str(value))
    return text
