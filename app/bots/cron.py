"""Minimal 5-field crontab parser used by bot cron triggers and scheduled messages.

Grammar: ``minute hour day-of-month month day-of-week`` plus the ``@yearly``,
``@annually``, ``@monthly``, ``@weekly``, ``@daily``, ``@midnight`` and
``@hourly`` presets. Fields accept ``*``, single values, names (``mon``,
``jan``), ranges (``1-5``), lists (``1,3,5``) and steps (``*/15``, ``1-9/2``).

Day-of-week numbering is **0 = Monday … 6 = Sunday** (APScheduler style, kept
for meshcore-bot schedule compatibility) — not Vixie cron's 0 = Sunday.

Standard cron day matching applies: when both day-of-month and day-of-week are
restricted, a time matches if EITHER field matches.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

_PRESETS = {
    "@yearly": "0 0 1 1 *",
    "@annually": "0 0 1 1 *",
    "@monthly": "0 0 1 * *",
    "@weekly": "0 0 * * 0",  # Monday under 0=Monday numbering
    "@daily": "0 0 * * *",
    "@midnight": "0 0 * * *",
    "@hourly": "0 * * * *",
}

_DOW_NAMES = {"mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6}
_MONTH_NAMES = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}  # fmt: skip


class CronParseError(ValueError):
    """Raised when a cron expression cannot be parsed."""


def _parse_atom(atom: str, lo: int, hi: int, names: dict[str, int]) -> int:
    atom = atom.strip().lower()
    if atom in names:
        return names[atom]
    try:
        value = int(atom)
    except ValueError as exc:
        raise CronParseError(f"invalid value {atom!r}") from exc
    if not lo <= value <= hi:
        raise CronParseError(f"value {value} out of range {lo}-{hi}")
    return value


def _parse_field(
    field: str, lo: int, hi: int, names: dict[str, int] | None = None
) -> frozenset[int]:
    names = names or {}
    values: set[int] = set()
    for part in field.split(","):
        part = part.strip()
        if not part:
            raise CronParseError("empty list item")
        step = 1
        if "/" in part:
            part, step_str = part.split("/", 1)
            try:
                step = int(step_str)
            except ValueError as exc:
                raise CronParseError(f"invalid step {step_str!r}") from exc
            if step < 1:
                raise CronParseError(f"invalid step {step}")
        if part == "*" or part == "":
            start, end = lo, hi
        elif "-" in part:
            start_s, end_s = part.split("-", 1)
            start = _parse_atom(start_s, lo, hi, names)
            end = _parse_atom(end_s, lo, hi, names)
            if end < start:
                raise CronParseError(f"inverted range {part!r}")
        else:
            start = end = _parse_atom(part, lo, hi, names)
        values.update(range(start, end + 1, step))
    return frozenset(values)


@dataclass(frozen=True)
class CronSchedule:
    """A parsed cron expression that can compute its next fire time."""

    expression: str
    minutes: frozenset[int]
    hours: frozenset[int]
    days_of_month: frozenset[int]
    months: frozenset[int]
    days_of_week: frozenset[int]
    dom_restricted: bool
    dow_restricted: bool

    def _day_matches(self, moment: datetime) -> bool:
        dom_ok = moment.day in self.days_of_month
        dow_ok = (
            moment.weekday() in self.days_of_week
        )  # weekday(): 0=Monday, matching our numbering
        if self.dom_restricted and self.dow_restricted:
            return dom_ok or dow_ok
        return dom_ok and dow_ok

    def matches(self, moment: datetime) -> bool:
        return (
            moment.minute in self.minutes
            and moment.hour in self.hours
            and moment.month in self.months
            and self._day_matches(moment)
        )

    def next_fire(self, after: datetime) -> datetime | None:
        """Return the first matching minute strictly after ``after`` (local time).

        Scans minute-by-minute with day-level skipping; bounded to four years so
        an impossible expression (e.g. Feb 30) returns None instead of hanging.
        """
        moment = after.replace(second=0, microsecond=0) + timedelta(minutes=1)
        limit = after + timedelta(days=4 * 366)
        while moment <= limit:
            if moment.month not in self.months:
                # Jump to the first day of the next month
                if moment.month == 12:
                    moment = moment.replace(year=moment.year + 1, month=1, day=1, hour=0, minute=0)
                else:
                    moment = moment.replace(month=moment.month + 1, day=1, hour=0, minute=0)
                continue
            if not self._day_matches(moment):
                moment = (moment + timedelta(days=1)).replace(hour=0, minute=0)
                continue
            if moment.hour not in self.hours:
                moment = (moment + timedelta(hours=1)).replace(minute=0)
                continue
            if moment.minute not in self.minutes:
                moment += timedelta(minutes=1)
                continue
            return moment
        return None


def parse_cron(expression: str) -> CronSchedule:
    """Parse a 5-field cron expression or ``@preset`` into a CronSchedule."""
    raw = expression.strip()
    if not raw:
        raise CronParseError("empty cron expression")
    lowered = raw.lower()
    if lowered.startswith("@"):
        if lowered not in _PRESETS:
            raise CronParseError(f"unknown preset {raw!r}")
        raw = _PRESETS[lowered]
    fields = raw.split()
    if len(fields) != 5:
        raise CronParseError(f"expected 5 fields, got {len(fields)} in {expression!r}")
    minute_f, hour_f, dom_f, month_f, dow_f = fields
    return CronSchedule(
        expression=expression.strip(),
        minutes=_parse_field(minute_f, 0, 59),
        hours=_parse_field(hour_f, 0, 23),
        days_of_month=_parse_field(dom_f, 1, 31),
        months=_parse_field(month_f, 1, 12, _MONTH_NAMES),
        days_of_week=_parse_field(dow_f, 0, 6, _DOW_NAMES),
        dom_restricted=dom_f.split("/")[0] != "*",
        dow_restricted=dow_f.split("/")[0] != "*",
    )


def validate_cron(expression: str) -> str | None:
    """Return an error string when the expression is invalid, else None."""
    try:
        parse_cron(expression)
    except CronParseError as exc:
        return str(exc)
    return None
