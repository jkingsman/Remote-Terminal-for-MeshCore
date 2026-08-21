"""Tests for bot engine gating logic and the bots repository."""

import pytest

from app.bots.api import BotMessage
from app.bots.engine import BotEngine
from app.bots.moderation import apply_profanity_mode, censor, is_banned_sender
from app.bots.translate import detect_language
from app.models import Bot, BotAdminUser, BotEngineSettings


def make_bot(**overrides) -> Bot:
    base = {
        "id": "b1",
        "name": "t",
        "category": "Basic",
        "description": "",
        "code": "",
        "enabled": True,
        "admin_only": False,
        "respond_to_dms": True,
        "scope": {"channels": "all"},
    }
    base.update(overrides)
    return Bot(**base)


def make_msg(**overrides) -> BotMessage:
    base = {
        "text": "hello",
        "sender_name": "K0PHX",
        "sender_key": "ab" * 32,
        "is_dm": False,
        "channel_key": "A" * 32,
        "channel_name": "#general",
    }
    base.update(overrides)
    return BotMessage(**base)


class TestScope:
    def setup_method(self):
        self.engine = BotEngine()

    def test_all_channels(self):
        assert self.engine._scope_allows(make_bot(), make_msg())

    def test_only_list(self):
        bot = make_bot(scope={"channels": {"only": ["A" * 32]}})
        assert self.engine._scope_allows(bot, make_msg())
        assert not self.engine._scope_allows(bot, make_msg(channel_key="B" * 32))

    def test_except_list(self):
        bot = make_bot(scope={"channels": {"except": ["A" * 32]}})
        assert not self.engine._scope_allows(bot, make_msg())
        assert self.engine._scope_allows(bot, make_msg(channel_key="B" * 32))

    def test_dm_gate(self):
        bot = make_bot(respond_to_dms=False)
        assert not self.engine._scope_allows(bot, make_msg(is_dm=True, channel_key=None))
        assert self.engine._scope_allows(make_bot(), make_msg(is_dm=True, channel_key=None))

    def test_none_channels(self):
        bot = make_bot(scope={"channels": "none"})
        assert not self.engine._scope_allows(bot, make_msg())


class TestPrefixAndAdmin:
    def setup_method(self):
        self.engine = BotEngine()
        self.engine.settings = BotEngineSettings(command_prefix="!, ~")

    def test_prefix_stripped_longest_match(self):
        text, had_prefix, _ = self.engine._strip_prefix_and_mention("!wx seattle")
        assert text == "wx seattle"
        assert had_prefix

    def test_no_prefix_passthrough(self):
        text, had_prefix, _ = self.engine._strip_prefix_and_mention("wx seattle")
        assert text == "wx seattle"
        assert not had_prefix

    def test_admin_check(self):
        self.engine.settings = BotEngineSettings(
            admin_users=[BotAdminUser(public_key="AB" * 32, name="K0PHX")]
        )
        assert self.engine._is_admin_sender(make_msg(sender_key="ab" * 32))
        assert not self.engine._is_admin_sender(make_msg(sender_key="cd" * 32))
        assert not self.engine._is_admin_sender(make_msg(sender_key=None))


class TestModeration:
    def test_banned_prefix_match(self):
        assert is_banned_sender("Awful Username 2", ["Awful"])
        assert not is_banned_sender("Nice Person", ["Awful"])
        assert not is_banned_sender(None, ["Awful"])

    def test_profanity_modes(self):
        assert apply_profanity_mode("what the fuck", "off") == "what the fuck"
        assert apply_profanity_mode("what the fuck", "censor") == "what the f***"
        assert apply_profanity_mode("what the fuck", "drop") is None
        assert apply_profanity_mode("all clean here", "drop") == "all clean here"

    def test_censor_word_boundaries(self):
        # "Scunthorpe problem": substrings inside words must survive.
        assert censor("scunthorpe") == "scunthorpe"


class TestLanguageDetection:
    def test_french(self):
        assert detect_language("bonjour tout le monde") == "fr"

    def test_german(self):
        assert detect_language("guten morgen wetter bitte") == "de"

    def test_default_for_neutral(self):
        assert detect_language("wx 98101", "en") == "en"


class TestRepository:
    async def test_bot_crud_and_state(self, test_db):
        from app.repository.bots import BotRepository

        bot = await BotRepository.create(name="crud-test", code="def bot(**kw):\n    return None")
        assert bot.name == "crud-test"
        assert await BotRepository.name_exists("crud-test")
        assert not await BotRepository.name_exists("crud-test", exclude_id=bot.id)

        updated = await BotRepository.update(bot.id, enabled=True, cooldown_seconds=15)
        assert updated is not None and updated.enabled and updated.cooldown_seconds == 15

        await BotRepository.set_state(bot.id, {"count": 3})
        assert await BotRepository.get_state(bot.id) == {"count": 3}

        assert await BotRepository.delete(bot.id)
        assert await BotRepository.get(bot.id) is None

    async def test_runs_and_stats(self, test_db):
        import time

        from app.repository.bots import BotRepository, BotRunRepository

        bot = await BotRepository.create(name="stats-test")
        now = int(time.time())
        for result, replies in (("replied", 1), ("replied", 2), ("error", 0)):
            await BotRunRepository.record(
                bot_id=bot.id,
                started_at=now,
                duration_ms=500,
                trigger="kw test",
                sender_name="K0PHX",
                sender_key="ab" * 32,
                channel_key="A" * 32,
                channel_name="#general",
                is_dm=False,
                result=result,
                replies=replies,
                error="boom" if result == "error" else None,
            )
        stats = await BotRunRepository.stats(3600)
        assert stats["runs"] == 3
        assert stats["replies"] == 3
        assert stats["errors"] == 1
        assert stats["top_bots"][0]["label"] == "stats-test"
        runs = await BotRunRepository.recent(bot_id=bot.id, limit=10)
        assert len(runs) == 3
        assert runs[0].bot_name == "stats-test"

    async def test_schedules_and_feeds(self, test_db):
        from app.repository.bots import BotFeedRepository, BotScheduleRepository

        schedule = await BotScheduleRepository.create(
            label="morning", cron="0 8 * * *", channel_key="A" * 32, message="gm {total_contacts}"
        )
        assert schedule.enabled
        updated = await BotScheduleRepository.update(schedule.id, enabled=False)
        assert updated is not None and not updated.enabled
        assert await BotScheduleRepository.delete(schedule.id)

        feed = await BotFeedRepository.create(
            name="blog",
            feed_type="rss",
            url="https://example.com/feed.xml",
            channel_key="A" * 32,
            interval_seconds=1800,
            format="{title}",
        )
        await BotFeedRepository.record_check(feed.id, newest_id="g1", posted=2, error=None)
        reloaded = await BotFeedRepository.get(feed.id)
        assert reloaded is not None
        assert reloaded.last_item_id == "g1"
        assert reloaded.items_posted == 2
        await BotFeedRepository.record_check(feed.id, newest_id=None, posted=0, error="HTTP 503")
        reloaded = await BotFeedRepository.get(feed.id)
        assert reloaded is not None and reloaded.error_count == 1

    async def test_engine_settings_roundtrip(self, test_db):
        from app.repository.bots import BotEngineSettingsRepository

        settings = await BotEngineSettingsRepository.get()
        assert settings.command_prefix == "!"
        updated = await BotEngineSettingsRepository.update(
            require_prefix=True,
            banned_users=["Spam"],
            admin_users=[{"public_key": "AB" * 32, "name": "K0PHX"}],
            profanity_mode="censor",
        )
        assert updated.require_prefix
        assert updated.banned_users == ["Spam"]
        assert updated.admin_users[0].name == "K0PHX"
        assert updated.profanity_mode == "censor"


class TestEngineTestRun:
    @pytest.fixture
    def engine(self):
        return BotEngine()

    async def test_test_run_keyword_match(self, test_db, engine):
        from app.models import BotTestRequest
        from app.repository.bots import BotRepository

        code = (
            "from remoteterm import bot\n"
            '@bot.on_keyword("echo")\n'
            "async def f(ctx, msg):\n"
            '    await ctx.reply(f"echo {msg.arg_text}")\n'
        )
        bot = await BotRepository.create(name="echo-test", code=code)
        response = await engine.test_run(bot, BotTestRequest(text="echo hi there"))
        assert response.matched
        assert response.trigger == "kw echo"
        assert response.error is None
        assert [r["text"] for r in response.replies] == ["echo hi there"]

    async def test_test_run_no_match(self, test_db, engine):
        from app.models import BotTestRequest
        from app.repository.bots import BotRepository

        code = (
            "from remoteterm import bot\n"
            '@bot.on_keyword("echo")\n'
            "async def f(ctx, msg):\n"
            "    pass\n"
        )
        bot = await BotRepository.create(name="nomatch-test", code=code)
        response = await engine.test_run(bot, BotTestRequest(text="something else"))
        assert not response.matched
        assert response.error is not None
