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

ASYNC_RETURN = """
import asyncio
from remoteterm import bot

@bot.on_keyword("echo")
async def echo(ctx, msg):
    # e.g. blocking work pushed to a thread whose result is the reply
    return await asyncio.to_thread(lambda: f"echo: {msg.arg_text}")
"""

ASYNC_RETURN_LIST = """
from remoteterm import bot

@bot.on_keyword("story")
async def story(ctx, msg):
    return ["(1/2) part one", "(2/2) part two"]
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

    async def test_async_handler_return_value_sent(self):
        loaded = load_bot_code(ASYNC_RETURN)
        ctx = make_ctx(origin_is_dm=True, origin_sender_key="cd" * 32)
        msg = BotMessage(
            text="echo abc", keyword="echo", args=["abc"], is_dm=True, sender_key="cd" * 32
        )
        handler = loaded.collector.keywords[0].handler
        with ThreadPoolExecutor(max_workers=1) as pool:
            await call_handler(handler, ctx, msg, None, 5.0, pool)
        assert [s["text"] for s in ctx.captured_sends] == ["echo: abc"]

    async def test_async_handler_return_list_sends_each_part(self):
        loaded = load_bot_code(ASYNC_RETURN_LIST)
        ctx = make_ctx(origin_is_dm=True, origin_sender_key="cd" * 32)
        msg = BotMessage(text="story", keyword="story", is_dm=True, sender_key="cd" * 32)
        handler = loaded.collector.keywords[0].handler
        with ThreadPoolExecutor(max_workers=1) as pool:
            await call_handler(handler, ctx, msg, None, 5.0, pool)
        assert [s["text"] for s in ctx.captured_sends] == [
            "(1/2) part one",
            "(2/2) part two",
        ]

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


class TestSplitReplies:
    def test_short_text_passes_through_unnumbered(self):
        ctx = make_ctx()
        assert ctx.split_text("fits in one frame") == ["fits in one frame"]

    def test_empty_text_yields_nothing(self):
        ctx = make_ctx()
        assert ctx.split_text("") == []
        assert ctx.split_text("   ") == []

    def test_long_text_split_into_numbered_parts_within_budget(self):
        ctx = make_ctx()
        text = " ".join(f"word{i}" for i in range(100))
        parts = ctx.split_text(text, max_bytes=60)
        assert len(parts) > 1
        assert parts[0].startswith("(1/")
        assert parts[-1].startswith(f"({len(parts)}/{len(parts)})")
        for part in parts:
            assert len(part.encode("utf-8")) <= 60
        # No content lost: stripping the (i/n) prefixes restores every word.
        rebuilt = " ".join(p.split(") ", 1)[1] for p in parts)
        assert rebuilt.split() == text.split()

    def test_multibyte_text_never_cut_mid_character(self):
        ctx = make_ctx()
        text = "héllo wörld ✅ " * 30
        parts = ctx.split_text(text, max_bytes=48)
        assert len(parts) > 1
        for part in parts:
            assert len(part.encode("utf-8")) <= 48
            part.encode("utf-8").decode("utf-8")  # would raise if cut mid-char

    async def test_reply_split_sends_each_part_in_order(self):
        ctx = make_ctx(origin_is_dm=True, origin_sender_key="ab" * 32)
        text = " ".join(f"word{i}" for i in range(60))
        await ctx.reply_split(text, max_bytes=60)
        texts = [s["text"] for s in ctx.captured_sends]
        assert len(texts) > 1
        assert [t.split("/")[0] for t in texts] == [f"({i}" for i in range(1, len(texts) + 1)]

    async def test_reply_split_short_text_is_one_plain_reply(self):
        ctx = make_ctx(origin_is_dm=True, origin_sender_key="ab" * 32)
        await ctx.reply_split("short answer")
        assert [s["text"] for s in ctx.captured_sends] == ["short answer"]

    def test_split_text_compressed_packs_more_than_raw(self):
        from app.bots.api import split_text_compressed
        from app.compression import encode_outbound

        text = " ".join(["compression"] * 80)  # long and very compressible
        max_bytes = 60
        parts = split_text_compressed(text, max_bytes, version=2)
        assert len(parts) >= 1
        # Every emitted part fits the budget once compressed...
        for p in parts:
            assert len(encode_outbound(p, version=2).encode("utf-8")) <= max_bytes
        # ...yet packs more raw text than a raw split would allow per part.
        assert any(len(p.encode("utf-8")) > max_bytes for p in parts)
        # No content lost (strip the (i/n) prefixes).
        rebuilt = " ".join(p.split(") ", 1)[1] if p.startswith("(") else p for p in parts)
        assert rebuilt.split() == text.split()

    async def test_reply_split_uses_compression_when_conversation_enabled(self, test_db):
        from app.repository import ContactRepository

        key = "cd" * 32
        await ContactRepository.upsert({"public_key": key, "name": "Bob"})
        assert await ContactRepository.set_mcmp_enabled(key, True)  # version defaults to 2

        ctx = make_ctx(origin_is_dm=True, origin_sender_key=key)
        text = " ".join(["compression"] * 80)
        await ctx.reply_split(text, max_bytes=60)

        compressed_parts = [s["text"] for s in ctx.captured_sends]
        raw_parts = ctx.split_text(text, max_bytes=60)
        # Compression packs more per message -> strictly fewer parts than raw.
        assert 1 <= len(compressed_parts) < len(raw_parts)


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
