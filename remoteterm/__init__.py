"""Import surface for RemoteTerm bot code (``from remoteterm import bot``).

This package exists so DB-stored bot scripts have a stable, importable module
name. The actual registration machinery lives in :mod:`app.bots.api`.
"""

from app.bots.api import BotContext, BotMessage, bot

__all__ = ["BotContext", "BotMessage", "bot"]
