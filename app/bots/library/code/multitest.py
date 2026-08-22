"""Collects unique message paths for 6 seconds. Seeded from meshcore-bot's multitest.

Say ``multitest``, then have stations transmit; after 6 seconds it reports the
distinct routing paths of everything heard in the window, each shown as its
repeater hops separated by commas (``2f52f0,bf61f2,8e31d2``). Flood repeats
that merely extend an already-observed route by more hops are stages of the
same propagation, not new routes, and are collapsed into the longest
observation.
"""

import asyncio
import time

from remoteterm import bot

BOT_META = {
    "key": "multitest",
    "name": "multitest",
    "category": "Mesh",
    "description": "Collects unique message paths heard during a 6s window",
    "version": "1.2.0",
    "cooldown_seconds": 30,
}

WINDOW_SECONDS = 6


def _hop_width(message_path) -> int:
    """Bytes per hop for one path observation (legacy rows imply 1-byte hops)."""
    if not message_path.path or not message_path.path_len:
        return 1
    return max(1, (len(message_path.path) // 2) // message_path.path_len)


def _format_route(route: str, width: int) -> str:
    """Hex route -> comma-separated repeater hops, matching the app's display."""
    step = max(1, width) * 2
    return ",".join(route[i : i + step] for i in range(0, len(route), step))


def _maximal_routes(message) -> tuple[set[str], bool]:
    """Distinct terminal routes for one message, plus whether it arrived direct.

    A route that is a hop-aligned prefix of a longer route observed for the
    same message is an intermediate flood stage of that longer route — keep
    only the maximal ones. Prefix comparison stays within one hop width so a
    1-byte route never swallows an unrelated multibyte one. Routes come back
    already hop-formatted (comma-separated).
    """
    by_width: dict[int, set[str]] = {}
    direct = False
    for message_path in message.paths or []:
        route = message_path.path or ""
        if not route:
            direct = True
            continue
        by_width.setdefault(_hop_width(message_path), set()).add(route)
    routes: set[str] = set()
    for width, group in by_width.items():
        for route in group:
            if any(other != route and other.startswith(route) for other in group):
                continue
            routes.add(_format_route(route, width))
    return routes, direct


@bot.on_keyword("multitest", "mt")
async def multitest(ctx, msg):
    start = int(time.time())
    await asyncio.sleep(WINDOW_SECONDS)

    # Read-only lookup against this app's own message store (allowed for
    # mesh-introspection bots).
    from app.repository import MessageRepository

    # after_id=0 is load-bearing: get_all only applies the received_at cursor
    # when both after and after_id are set. With `after` alone the filter is
    # ignored and the newest 100 rows of all time come back.
    messages = await MessageRepository.get_all(limit=100, after=start - 1, after_id=0)
    paths: dict[str, int] = {}
    direct = 0
    for message in messages:
        if message.outgoing:
            continue
        routes, arrived_direct = _maximal_routes(message)
        if arrived_direct or not message.paths:
            direct += 1
        for route in routes:
            paths[route] = paths.get(route, 0) + 1

    if not paths and not direct:
        await ctx.reply(f"Heard nothing in {WINDOW_SECONDS}s.")
        return
    parts = [f"{p}×{n}" if n > 1 else p for p, n in sorted(paths.items())]
    if direct:
        parts.append(f"direct×{direct}" if direct > 1 else "direct")
    summary = " | ".join(parts)
    # Busy meshes overflow one RF frame — split instead of truncating.
    await ctx.reply_split(f"{len(parts)} unique path(s) in {WINDOW_SECONDS}s: {summary}")
