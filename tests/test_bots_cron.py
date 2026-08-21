"""Tests for the bot cron parser (5-field crontab + presets, dow 0=Monday)."""

from datetime import datetime

import pytest

from app.bots.cron import CronParseError, parse_cron, validate_cron


class TestParsing:
    def test_simple_daily(self):
        schedule = parse_cron("0 8 * * *")
        assert schedule.minutes == frozenset({0})
        assert schedule.hours == frozenset({8})

    def test_presets(self):
        assert parse_cron("@daily").hours == frozenset({0})
        assert parse_cron("@hourly").minutes == frozenset({0})
        # @weekly = Monday under APScheduler-style numbering (0=Monday)
        assert parse_cron("@weekly").days_of_week == frozenset({0})

    def test_steps_ranges_lists(self):
        schedule = parse_cron("*/15 1-3 1,15 * mon-fri")
        assert schedule.minutes == frozenset({0, 15, 30, 45})
        assert schedule.hours == frozenset({1, 2, 3})
        assert schedule.days_of_month == frozenset({1, 15})
        assert schedule.days_of_week == frozenset({0, 1, 2, 3, 4})

    def test_month_names(self):
        assert parse_cron("0 0 1 jan,jul *").months == frozenset({1, 7})

    @pytest.mark.parametrize(
        "expr",
        ["", "0 8 * *", "60 * * * *", "* 24 * * *", "* * * * 7", "@fortnightly", "a b c d e"],
    )
    def test_invalid_expressions(self, expr):
        with pytest.raises(CronParseError):
            parse_cron(expr)
        assert validate_cron(expr) is not None

    def test_validate_ok(self):
        assert validate_cron("30 18 * * 3") is None


class TestNextFire:
    def test_next_daily(self):
        schedule = parse_cron("0 8 * * *")
        # 2026-08-21 is a Friday
        assert schedule.next_fire(datetime(2026, 8, 21, 9, 0)) == datetime(2026, 8, 22, 8, 0)
        assert schedule.next_fire(datetime(2026, 8, 21, 7, 59)) == datetime(2026, 8, 21, 8, 0)

    def test_fire_is_strictly_after(self):
        schedule = parse_cron("0 8 * * *")
        assert schedule.next_fire(datetime(2026, 8, 21, 8, 0)) == datetime(2026, 8, 22, 8, 0)

    def test_dow_zero_is_monday(self):
        schedule = parse_cron("0 9 * * 0")
        nxt = schedule.next_fire(datetime(2026, 8, 21, 12, 0))  # Friday
        assert nxt is not None
        assert nxt.weekday() == 0  # Monday
        assert (nxt.hour, nxt.minute) == (9, 0)

    def test_dow_three_is_thursday(self):
        schedule = parse_cron("30 18 * * 3")
        nxt = schedule.next_fire(datetime(2026, 8, 21, 12, 0))
        assert nxt is not None
        assert nxt.weekday() == 3

    def test_dom_or_dow_semantics(self):
        # Both restricted: fire when EITHER matches (standard cron).
        schedule = parse_cron("0 0 15 * 0")
        nxt = schedule.next_fire(datetime(2026, 8, 21, 12, 0))  # Friday Aug 21
        assert nxt is not None
        # Monday Aug 24 comes before the 15th of next month.
        assert nxt == datetime(2026, 8, 24, 0, 0)

    def test_impossible_date_returns_none(self):
        schedule = parse_cron("0 0 30 2 *")  # Feb 30 never exists
        assert schedule.next_fire(datetime(2026, 1, 1)) is None

    def test_month_rollover(self):
        schedule = parse_cron("0 0 1 1 *")  # Jan 1
        assert schedule.next_fire(datetime(2026, 8, 21)) == datetime(2027, 1, 1, 0, 0)
