"""Tests for bot code loading: decorated handlers, legacy detection, validation."""

import asyncio
from concurrent.futures import ThreadPoolExecutor

import pytest

from app.bots.api import BotContext, BotMessage
from app.bots.runtime import BotCodeError, call_handler, call_legacy, load_bot_code

DECORATED = """
from remoteterm import bot

BOT_META = {"key": "t", "name": "t", "category": "Basic", "description": "d", "version": "1.0.0"}

@bot.on_keyword("hello", "hi")
async def greet(ctx, msg):
    await ctx.reply(f"hey {msg.sender_name}")

@bot.on_cron("0 7 * * *")
async def morning(ctx):
    await ctx.send("#general", "gm")

@bot.on_event("new_contact")
async def contact(ctx, event):
    pass

@bot.on_webhook("send")
async def hook(ctx, payload):
    pass
"""

LEGACY = """
def bot(**kwargs):
    if "ping" in kwargs.get("message_text", ""):
        return "pong"
    return None
"""

SYNC_RETURN = """
from remoteterm import bot

@bot.on_keyword("echo")
def echo(ctx, msg):
    return f"echo: {msg.arg_text}"
"""


def make_ctx(**kwargs) -> BotContext:
    return BotContext(
        bot_id="b1",
        bot_name="t",
        settings={},
        state={},
        is_test=True,
        loop=asyncio.get_event_loop(),
        **kwargs,
    )


class TestLoading:
    def test_decorated_collection(self):
        loaded = load_bot_code(DECORATED)
        assert not loaded.is_legacy
        assert loaded.declared_keywords == ["hello", "hi"]
        assert loaded.declared_crons == ["0 7 * * *"]
        assert [t.event for t in loaded.collector.events] == ["new_contact"]
        assert [t.slug for t in loaded.collector.webhooks] == ["send"]
        assert loaded.namespace["BOT_META"]["key"] == "t"

    def test_legacy_detection(self):
        loaded = load_bot_code(LEGACY)
        assert loaded.is_legacy
        assert loaded.collector.is_empty()

    def test_empty_code_rejected(self):
        with pytest.raises(BotCodeError, match="empty"):
            load_bot_code("   ")

    def test_syntax_error_reports_line(self):
        with pytest.raises(BotCodeError, match="line 2"):
            load_bot_code("x = 1\ndef broken(:\n")

    def test_no_triggers_rejected(self):
        with pytest.raises(BotCodeError, match="no triggers"):
            load_bot_code("x = 1\n")

    def test_bad_cron_in_code_rejected(self):
        code = (
            'from remoteterm import bot\n@bot.on_cron("99 * * * *")\nasync def f(ctx):\n    pass\n'
        )
        with pytest.raises(BotCodeError, match="invalid cron"):
            load_bot_code(code)

    def test_generic_handlers_flagged(self):
        code = (
            "from remoteterm import bot\n"
            "@bot.on_keyword()\nasync def a(ctx, msg):\n    pass\n"
            "@bot.on_cron()\nasync def b(ctx):\n    pass\n"
        )
        loaded = load_bot_code(code)
        assert loaded.has_generic_keyword_handler
        assert loaded.has_generic_cron_handler

    def test_decorator_outside_load_rejected(self):
        from remoteterm import bot as bot_decorators

        with pytest.raises(RuntimeError):
            bot_decorators.on_keyword("x")(lambda ctx, msg: None)


class TestExecution:
    async def test_async_handler_reply_captured(self):
        loaded = load_bot_code(DECORATED)
        ctx = make_ctx(origin_is_dm=True, origin_sender_key="ab" * 32)
        msg = BotMessage(text="hello", sender_name="K0PHX", is_dm=True, sender_key="ab" * 32)
        handler = loaded.collector.keywords[0].handler
        with ThreadPoolExecutor(max_workers=1) as pool:
            await call_handler(handler, ctx, msg, None, 5.0, pool)
        assert ctx.captured_sends == [
            {
                "is_dm": True,
                "destination": "ab" * 32,
                "channel_key": None,
                "text": "hey K0PHX",
                "region": None,
            }
        ]

    async def test_sync_handler_return_value_sent(self):
        loaded = load_bot_code(SYNC_RETURN)
        ctx = make_ctx(origin_is_dm=True, origin_sender_key="cd" * 32)
        msg = BotMessage(
            text="echo abc", keyword="echo", args=["abc"], is_dm=True, sender_key="cd" * 32
        )
        handler = loaded.collector.keywords[0].handler
        with ThreadPoolExecutor(max_workers=1) as pool:
            await call_handler(handler, ctx, msg, None, 5.0, pool)
        assert [s["text"] for s in ctx.captured_sends] == ["echo: abc"]

    async def test_legacy_call_roundtrip(self):
        loaded = load_bot_code(LEGACY)
        ctx = make_ctx(origin_is_dm=True, origin_sender_key="ef" * 32)
        msg = BotMessage(text="ping", is_dm=True, sender_key="ef" * 32)
        with ThreadPoolExecutor(max_workers=1) as pool:
            await call_legacy(loaded, ctx, msg, 5.0, pool)
        assert [s["text"] for s in ctx.captured_sends] == ["pong"]

    async def test_timeout_raises(self):
        code = (
            "from remoteterm import bot\nimport time\n"
            '@bot.on_keyword("slow")\n'
            "def slow(ctx, msg):\n    time.sleep(2)\n"
        )
        loaded = load_bot_code(code)
        ctx = make_ctx()
        msg = BotMessage(text="slow")
        with ThreadPoolExecutor(max_workers=1) as pool:
            with pytest.raises(TimeoutError):
                await call_handler(loaded.collector.keywords[0].handler, ctx, msg, None, 0.2, pool)


class TestLibraryIntegrity:
    def test_every_library_bot_loads(self):
        from app.bots.library import list_library

        entries = list_library()
        assert len(entries) >= 30
        keys = [e["key"] for e in entries]
        assert len(keys) == len(set(keys)), "duplicate builtin keys"
        for entry in entries:
            loaded = load_bot_code(entry["code"])
            assert not loaded.collector.is_empty(), entry["key"]
