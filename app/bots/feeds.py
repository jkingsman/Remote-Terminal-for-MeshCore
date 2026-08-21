"""RSS / JSON-API feed polling: parsing, item formatting, and SSRF guarding.

Feed rows live in ``bot_feeds``; the engine's feed loop calls
:func:`check_feed` on due feeds and posts formatted new items to the feed's
channel. Formats are ``{field|filter:arg}`` templates over item dicts, e.g.
``[Blog] {title|truncate:80}\n{link}``.
"""

from __future__ import annotations

import ipaddress
import logging
import socket
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

MAX_RESPONSE_BYTES = 2 * 1024 * 1024
FETCH_TIMEOUT_SECONDS = 10.0

_ATOM_NS = "{http://www.w3.org/2005/Atom}"


class FeedError(ValueError):
    """Raised when a feed cannot be fetched or parsed."""


def _is_private_host(hostname: str) -> bool:
    """True when the hostname resolves only to private/loopback/link-local IPs."""
    try:
        infos = socket.getaddrinfo(hostname, None)
    except OSError:
        return True  # unresolvable — treat as unsafe
    for info in infos:
        try:
            addr = ipaddress.ip_address(info[4][0])
        except ValueError:
            continue
        if not (addr.is_private or addr.is_loopback or addr.is_link_local or addr.is_reserved):
            return False
    return True


def validate_feed_url(url: str, *, allow_private: bool = False) -> str | None:
    """Return an error string when the URL is unusable, else None."""
    parsed = urlparse(url.strip())
    if parsed.scheme not in ("http", "https"):
        return "feed URL must be http(s)"
    if not parsed.hostname:
        return "feed URL has no host"
    if not allow_private and _is_private_host(parsed.hostname):
        return "feed URL resolves to a private/loopback address (blocked; SSRF guard)"
    return None


def _text(elem: ET.Element | None) -> str:
    return (elem.text or "").strip() if elem is not None else ""


def parse_rss(body: str) -> list[dict[str, str]]:
    """Parse RSS 2.0 ``<item>`` or Atom ``<entry>`` elements into item dicts.

    Items are returned in document order (typically newest first). Each item
    carries ``id`` (guid/atom-id/link fallback), ``title``, ``link``,
    ``summary`` and ``published``.
    """
    try:
        root = ET.fromstring(body)
    except ET.ParseError as exc:
        raise FeedError(f"feed XML did not parse: {exc}") from exc

    items: list[dict[str, str]] = []

    for item in root.iter("item"):  # RSS 2.0
        link = _text(item.find("link"))
        guid = _text(item.find("guid")) or link
        items.append(
            {
                "id": guid,
                "title": _text(item.find("title")),
                "link": link,
                "summary": _text(item.find("description")),
                "published": _text(item.find("pubDate")),
            }
        )

    if not items:
        for entry in root.iter(f"{_ATOM_NS}entry"):  # Atom
            link = ""
            for link_el in entry.findall(f"{_ATOM_NS}link"):
                rel = link_el.get("rel", "alternate")
                if rel == "alternate" and link_el.get("href"):
                    link = link_el.get("href", "")
                    break
                if not link and link_el.get("href"):
                    link = link_el.get("href", "")
            atom_id = _text(entry.find(f"{_ATOM_NS}id")) or link
            items.append(
                {
                    "id": atom_id,
                    "title": _text(entry.find(f"{_ATOM_NS}title")),
                    "link": link,
                    "summary": _text(entry.find(f"{_ATOM_NS}summary"))
                    or _text(entry.find(f"{_ATOM_NS}content")),
                    "published": _text(entry.find(f"{_ATOM_NS}updated"))
                    or _text(entry.find(f"{_ATOM_NS}published")),
                }
            )

    return items


def extract_api_items(payload: Any, items_path: str | None) -> list[dict[str, Any]]:
    """Pull the item list out of a JSON API response.

    ``items_path`` is a dotted path (``data.results``); empty means the payload
    itself is the list. Each item must be an object; an ``id`` key (or ``guid``
    / ``link`` / ``url``) identifies it for dedup.
    """
    node: Any = payload
    if items_path:
        for part in items_path.split("."):
            if not isinstance(node, dict) or part not in node:
                raise FeedError(f"items path {items_path!r} not found in response")
            node = node[part]
    if not isinstance(node, list):
        raise FeedError("feed items are not a list")
    items: list[dict[str, Any]] = []
    for raw in node:
        if not isinstance(raw, dict):
            continue
        item = dict(raw)
        item_id = item.get("id") or item.get("guid") or item.get("link") or item.get("url")
        item["id"] = str(item_id) if item_id is not None else ""
        items.append(item)
    return items


def _apply_filter(value: str, name: str, arg: str | None) -> str:
    if name == "truncate":
        try:
            limit = int(arg or "120")
        except ValueError:
            limit = 120
        return value if len(value) <= limit else value[: max(0, limit - 1)].rstrip() + "…"
    if name == "upper":
        return value.upper()
    if name == "lower":
        return value.lower()
    if name == "strip":
        return value.strip()
    if name == "firstline":
        return value.splitlines()[0] if value else value
    return value


def format_item(template: str, item: dict[str, Any]) -> str:
    """Render ``{field|filter:arg|...}`` placeholders from an item dict.

    Unknown fields render empty; unknown filters pass through. ``\\n`` escapes
    in the template become real newlines.
    """
    out: list[str] = []
    i = 0
    text = template.replace("\\n", "\n")
    while i < len(text):
        ch = text[i]
        if ch != "{":
            out.append(ch)
            i += 1
            continue
        end = text.find("}", i)
        if end == -1:
            out.append(text[i:])
            break
        expr = text[i + 1 : end]
        parts = expr.split("|")
        field = parts[0].strip()
        raw = item.get(field, "")
        value = "" if raw is None else str(raw)
        for filt in parts[1:]:
            filt = filt.strip()
            if not filt:
                continue
            if ":" in filt:
                fname, farg = filt.split(":", 1)
            else:
                fname, farg = filt, None
            value = _apply_filter(value, fname.strip(), farg)
        out.append(value)
        i = end + 1
    return "".join(out).strip()


@dataclass
class FeedCheckResult:
    items: list[dict[str, Any]]
    new_items: list[dict[str, Any]]
    newest_id: str | None


async def fetch_feed_items(
    *,
    feed_type: str,
    url: str,
    items_path: str | None,
    allow_private: bool = False,
) -> list[dict[str, Any]]:
    """Fetch + parse a feed, newest first. Raises FeedError on any failure."""
    error = validate_feed_url(url, allow_private=allow_private)
    if error:
        raise FeedError(error)

    import httpx

    try:
        async with httpx.AsyncClient(
            timeout=FETCH_TIMEOUT_SECONDS,
            follow_redirects=True,
            headers={"User-Agent": "RemoteTerm-MeshCore-feeds/1.0"},
        ) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            body = resp.content[:MAX_RESPONSE_BYTES]
    except httpx.HTTPError as exc:
        raise FeedError(f"fetch failed: {exc}") from exc

    if feed_type == "api":
        import json

        try:
            payload = json.loads(body)
        except json.JSONDecodeError as exc:
            raise FeedError(f"response is not JSON: {exc}") from exc
        return extract_api_items(payload, items_path)

    return parse_rss(body.decode("utf-8", errors="replace"))


def select_new_items(
    items: list[dict[str, Any]], last_item_id: str | None, max_posts: int
) -> FeedCheckResult:
    """Pick unseen items (oldest-first for posting), bounded by ``max_posts``.

    Items are assumed newest-first as parsed. On first check (no
    ``last_item_id``) nothing is posted — the newest id is just recorded, so
    subscribing to a busy feed doesn't flood the channel with history.
    """
    newest_id = items[0]["id"] if items else None
    if last_item_id is None:
        return FeedCheckResult(items=items, new_items=[], newest_id=newest_id)

    new_items: list[dict[str, Any]] = []
    for item in items:
        if item["id"] and item["id"] == last_item_id:
            break
        new_items.append(item)

    new_items.reverse()  # oldest first
    if max_posts > 0:
        new_items = new_items[-max_posts:] if len(new_items) > max_posts else new_items
    return FeedCheckResult(items=items, new_items=new_items, newest_id=newest_id)
