import asyncio
import logging
from typing import Literal
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from app.models import CONTACT_TYPE_REPEATER, CONTACT_TYPE_ROOM
from app.repository import AppSettingsRepository

logger = logging.getLogger(__name__)


def _build_message_text(
    *,
    msg_type: Literal["PRIV", "CHAN"],
    conversation_name: str | None,
    sender_name: str | None,
    text: str,
    path: str | None = None,
) -> tuple[str, str]:
    effective_path = "" if (msg_type == "PRIV" and path is None) else path
    via_suffix = _format_via_suffix(effective_path)

    if msg_type == "PRIV":
        sender = sender_name or conversation_name or "Unknown contact"
        title = ""
        body = f"**DM:** {sender}: {text}"
        if via_suffix:
            body = f"{body} {via_suffix}"
        return title, body

    channel = conversation_name or "Unknown channel"
    sender = sender_name or "Unknown sender"
    title = ""
    body = f"**{channel}:** {sender}: {text}"
    if via_suffix:
        body = f"{body} {via_suffix}"
    return title, body


def _parse_apprise_urls(raw_urls: str) -> list[str]:
    return [line.strip() for line in raw_urls.splitlines() if line.strip()]


def _format_via_suffix(path: str | None) -> str | None:
    if path is None:
        return None

    path = path.strip().lower()
    if path == "":
        hops = ["direct"]
    else:
        hops = [path[i : i + 2] for i in range(0, len(path), 2) if len(path[i : i + 2]) == 2]
        if not hops:
            return None

    hop_list = ", ".join(f"`{hop}`" for hop in hops)
    return f"**via:** [{hop_list}]"


def _normalize_apprise_url(url: str, *, preserve_identity: bool) -> str:
    if not preserve_identity:
        return url

    parts = urlsplit(url)
    scheme = parts.scheme.lower()
    host = parts.netloc.lower()
    is_discord_scheme = scheme in {"discord", "discords"}
    is_discord_webhook_https = (
        scheme in {"http", "https"}
        and host
        in {
            "discord.com",
            "discordapp.com",
        }
        and parts.path.lower().startswith("/api/webhooks/")
    )

    if not (is_discord_scheme or is_discord_webhook_https):
        return url

    query = dict(parse_qsl(parts.query, keep_blank_values=True))
    query["avatar"] = "no"
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))


def _send_apprise_notification_sync(
    raw_urls: str, title: str, body: str, preserve_identity: bool
) -> None:
    # Import lazily so the app can run even if this optional dependency is missing.
    import apprise

    urls = _parse_apprise_urls(raw_urls)
    if not urls:
        return

    notifier = apprise.Apprise()
    added_any = False
    for url in urls:
        normalized_url = _normalize_apprise_url(url, preserve_identity=preserve_identity)
        if notifier.add(normalized_url):
            added_any = True
        else:
            logger.warning("Skipping invalid Apprise URL: %s", url)
    if not added_any:
        return

    success = notifier.notify(title=title, body=body)
    if not success:
        logger.warning("Apprise notify returned failure")


async def _send_apprise_notification(
    url: str, title: str, body: str, preserve_identity: bool
) -> None:
    try:
        await asyncio.to_thread(
            _send_apprise_notification_sync, url, title, body, preserve_identity
        )
    except Exception:
        logger.exception("Failed to send Apprise notification")


async def enqueue_incoming_message_notification(
    *,
    msg_type: Literal["PRIV", "CHAN"],
    conversation_id: str,
    text: str,
    conversation_name: str | None = None,
    sender_name: str | None = None,
    contact_type: int | None = None,
    path: str | None = None,
) -> None:
    """Queue a best-effort notification for an incoming message."""
    settings = await AppSettingsRepository.get()
    apprise_url = settings.apprise_url.strip()
    if not settings.apprise_enabled or not apprise_url:
        return

    # Never notify for repeater/room traffic.
    if contact_type in (CONTACT_TYPE_REPEATER, CONTACT_TYPE_ROOM):
        return

    if settings.apprise_mode == "selected":
        allowed = any(
            target.type == ("channel" if msg_type == "CHAN" else "contact")
            and target.id == conversation_id
            for target in settings.apprise_targets
        )
        if not allowed:
            return

    title, body = _build_message_text(
        msg_type=msg_type,
        conversation_name=conversation_name,
        sender_name=sender_name,
        text=text,
        path=path,
    )
    asyncio.create_task(
        _send_apprise_notification(apprise_url, title, body, settings.apprise_preserve_identity)
    )
