"""Engine-level moderation: banned senders and outgoing profanity filtering.

The profanity list is intentionally small and word-boundary matched — it exists
so the Censor/Drop modes have real behavior, not to be an exhaustive filter.
Operators needing more can extend BASE_PROFANITY via a custom bot or PR.
"""

from __future__ import annotations

import re

BASE_PROFANITY = (
    "fuck",
    "shit",
    "bitch",
    "asshole",
    "cunt",
    "dickhead",
    "motherfucker",
)

_WORD_RE = re.compile(
    r"\b(" + "|".join(re.escape(w) for w in BASE_PROFANITY) + r")\b", re.IGNORECASE
)


def is_banned_sender(sender_name: str | None, banned: list[str]) -> bool:
    """Prefix match, like meshcore-bot: banning "Awful" bans "Awful Username"."""
    if not sender_name or not banned:
        return False
    return any(entry and sender_name.startswith(entry) for entry in banned)


def contains_profanity(text: str) -> bool:
    return bool(_WORD_RE.search(text))


def censor(text: str) -> str:
    return _WORD_RE.sub(lambda m: m.group(0)[0] + "*" * (len(m.group(0)) - 1), text)


def apply_profanity_mode(text: str, mode: str) -> str | None:
    """Return the text to send, or None when the message should be dropped."""
    if mode == "censor":
        return censor(text)
    if mode == "drop" and contains_profanity(text):
        return None
    return text
