"""Bot reply translations, ported from meshcore-bot's i18n system.

Locale files live in ``app/bots/translations/*.json`` as nested dicts; keys are
dotted paths (``greetings.hello``). Lookup falls back ``fr-CA`` → ``fr`` → the
engine default → ``en`` → the key itself, and values are ``str.format``-style
templates.

Language auto-detection is a lightweight keyword heuristic (no dependencies),
also ported from meshcore-bot — good enough to answer "bonjour" in French, not
a general-purpose detector.
"""

from __future__ import annotations

import json
import logging
from functools import cache
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

TRANSLATIONS_DIR = Path(__file__).parent / "translations"

SUPPORTED_LOCALES = ["en", "en-GB", "de", "es", "fr", "fr-CA", "nl", "pl", "pt", "pt-BR"]

# Minimal keyword → locale hints for auto-detection. Whole-word matches only.
_LANGUAGE_HINTS: dict[str, tuple[str, ...]] = {
    "fr": (
        "bonjour", "salut", "merci", "bonsoir", "allo", "allô", "oui", "météo",
        "svp", "s'il", "aujourd'hui", "demain", "quel", "quelle",
    ),
    "de": (
        "hallo", "danke", "guten", "morgen", "abend", "wetter", "bitte", "tschüss",
        "heute", "ja", "nein", "wie",
    ),
    "es": (
        "hola", "gracias", "buenos", "buenas", "adiós", "adios", "tiempo", "clima",
        "por", "favor", "hoy", "mañana", "cómo", "como", "qué",
    ),
    "nl": ("hallo", "dank", "goedemorgen", "goedenavond", "weer", "alsjeblieft", "vandaag"),
    "pl": ("cześć", "czesc", "dziękuję", "dziekuje", "dzień", "dzien", "dobry", "pogoda", "proszę"),
    "pt": ("olá", "ola", "obrigado", "obrigada", "bom", "boa", "tempo", "clima", "hoje", "amanhã"),
}  # fmt: skip


def _flatten(prefix: str, node: Any, out: dict[str, str]) -> None:
    if isinstance(node, dict):
        for key, value in node.items():
            _flatten(f"{prefix}.{key}" if prefix else str(key), value, out)
    elif isinstance(node, str):
        out[prefix] = node


@cache
def _load_locale(locale: str) -> dict[str, str]:
    path = TRANSLATIONS_DIR / f"{locale}.json"
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        logger.warning("Failed to load translations for locale %s", locale, exc_info=True)
        return {}
    flat: dict[str, str] = {}
    _flatten("", raw, flat)
    return flat


def _fallback_chain(locale: str, default_locale: str) -> list[str]:
    chain: list[str] = []
    for candidate in (
        locale,
        locale.split("-")[0],
        default_locale,
        default_locale.split("-")[0],
        "en",
    ):
        if candidate and candidate not in chain:
            chain.append(candidate)
    return chain


class Translator:
    """Dotted-key translator with locale fallback."""

    def __init__(self, default_locale: str = "en") -> None:
        self.default_locale = default_locale

    def translate(self, key: str, locale: str | None = None, **kwargs: Any) -> str:
        wanted = locale or self.default_locale
        for candidate in _fallback_chain(wanted, self.default_locale):
            table = _load_locale(candidate)
            template = table.get(key)
            if template is not None:
                try:
                    return template.format(**kwargs) if kwargs else template
                except (KeyError, IndexError, ValueError):
                    return template
        return key


def detect_language(text: str, default: str = "en") -> str:
    """Best-effort keyword-based language detection for short mesh messages."""
    words = {w.strip(".,!?;:()[]\"'").lower() for w in text.split()}
    if not words:
        return default
    best_locale = default
    best_hits = 0
    for locale, hints in _LANGUAGE_HINTS.items():
        hits = sum(1 for hint in hints if hint in words)
        if hits > best_hits:
            best_hits = hits
            best_locale = locale
    return best_locale if best_hits > 0 else default
