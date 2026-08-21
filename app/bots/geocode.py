"""Shared geocoding helper for bots (Nominatim, in-process TTL cache)."""

from __future__ import annotations

import logging
import time
from typing import Any

logger = logging.getLogger(__name__)

_CACHE: dict[str, tuple[float, dict[str, Any] | None]] = {}
_CACHE_TTL_SECONDS = 30 * 24 * 3600
_CACHE_MAX = 512

NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
_USER_AGENT = "RemoteTerm-MeshCore-bots/1.0"


async def geocode_query(query: str) -> dict[str, Any] | None:
    """Resolve a free-form place query to ``{lat, lon, name}`` or None."""
    normalized = query.strip().lower()
    if not normalized:
        return None

    cached = _CACHE.get(normalized)
    now = time.monotonic()
    if cached and now - cached[0] < _CACHE_TTL_SECONDS:
        return cached[1]

    import httpx

    result: dict[str, Any] | None = None
    try:
        async with httpx.AsyncClient(
            timeout=6.0, follow_redirects=True, headers={"User-Agent": _USER_AGENT}
        ) as client:
            resp = await client.get(
                NOMINATIM_URL,
                params={"q": query.strip(), "format": "json", "limit": 1},
            )
            resp.raise_for_status()
            rows = resp.json()
            if isinstance(rows, list) and rows:
                row = rows[0]
                result = {
                    "lat": float(row["lat"]),
                    "lon": float(row["lon"]),
                    "name": str(row.get("display_name", query.strip())),
                }
    except Exception:
        logger.warning("Geocode failed for %r", query, exc_info=True)
        return None

    if len(_CACHE) >= _CACHE_MAX:
        _CACHE.pop(next(iter(_CACHE)))
    _CACHE[normalized] = (now, result)
    return result
