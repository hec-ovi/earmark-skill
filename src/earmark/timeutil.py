"""Time for the board: UTC, second precision, ISO text 'YYYY-MM-DDTHH:MM:SSZ'.

`now()` goes through a replaceable clock so tests can freeze or advance time
(`set_clock(fn)`, `reset_clock()`). Due dates are plain 'YYYY-MM-DD' in UTC.
"""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta, timezone
from typing import Callable

from earmark.errors import InvalidRequest

ISO_FORMAT = "%Y-%m-%dT%H:%M:%SZ"
_UNITS = {"s": 1, "m": 60, "h": 3600, "d": 86400, "w": 604800}
_DURATION = re.compile(r"(\d+)([smhdw])")
_RELATIVE_DATE = re.compile(r"([+-])(\d+)([dw])")

_clock: Callable[[], datetime] | None = None


def set_clock(fn: Callable[[], datetime] | None) -> None:
    """Install a clock returning aware or naive-UTC datetimes; None restores the system clock."""
    global _clock
    _clock = fn


def reset_clock() -> None:
    set_clock(None)


def now() -> datetime:
    current = _clock() if _clock is not None else datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    return current.astimezone(timezone.utc).replace(microsecond=0)


def iso(moment: datetime) -> str:
    if moment.tzinfo is not None:
        moment = moment.astimezone(timezone.utc)
    return moment.strftime(ISO_FORMAT)


def now_iso() -> str:
    return iso(now())


def parse_iso(text: str) -> datetime:
    try:
        return datetime.strptime(text, ISO_FORMAT).replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        raise InvalidRequest(f"invalid timestamp {text!r}; expected YYYY-MM-DDTHH:MM:SSZ") from None


def today() -> date:
    return now().date()


def parse_duration(text: str | int | float) -> timedelta:
    """'30m', '2h', '3d', '1w', '45s', or combined '1h30m'. Bare numbers are seconds."""
    if isinstance(text, (int, float)) and not isinstance(text, bool):
        if text < 0:
            raise InvalidRequest(f"invalid duration {text!r}")
        return timedelta(seconds=text)
    raw = str(text or "").strip().lower()
    if raw.isdigit():
        return timedelta(seconds=int(raw))
    parts = _DURATION.findall(raw)
    if not raw or "".join(n + u for n, u in parts) != raw:
        raise InvalidRequest(f"invalid duration {text!r}; use e.g. 30m, 2h, 3d, 1w")
    return timedelta(seconds=sum(int(n) * _UNITS[u] for n, u in parts))


def parse_date(text: str) -> str:
    """'today', 'tomorrow', 'yesterday', '+3d', '+2w', '-1d' or 'YYYY-MM-DD' → 'YYYY-MM-DD'."""
    raw = str(text or "").strip().lower()
    base = today()
    named = {"today": 0, "tomorrow": 1, "yesterday": -1}
    if raw in named:
        return (base + timedelta(days=named[raw])).isoformat()
    match = _RELATIVE_DATE.fullmatch(raw)
    if match:
        sign, count, unit = match.groups()
        days = int(count) * (7 if unit == "w" else 1)
        return (base + timedelta(days=days if sign == "+" else -days)).isoformat()
    try:
        return date.fromisoformat(raw).isoformat() if len(raw) == 10 else _bad_date(text)
    except ValueError:
        return _bad_date(text)


def _bad_date(text: str) -> str:
    raise InvalidRequest(f"invalid date {text!r}; use YYYY-MM-DD, today, tomorrow, +3d or +2w")


def parse_when(text: str) -> str:
    """A point in the past for --updated-since/--created-since: a duration ago ('2h'),
    a date ('2026-11-01', 'today', '-3d') or a full timestamp. Returns ISO text."""
    raw = str(text or "").strip()
    if not raw:
        raise InvalidRequest("empty time filter")
    if raw.endswith("Z") and "T" in raw:
        return iso(parse_iso(raw))
    try:
        return iso(now() - parse_duration(raw))
    except InvalidRequest:
        pass
    try:
        return parse_date(raw) + "T00:00:00Z"
    except InvalidRequest:
        raise InvalidRequest(f"invalid time {text!r}; use 30m, 2h, 3d, 1w or a date") from None


def add_minutes(moment: datetime, minutes: int) -> str:
    return iso(moment + timedelta(minutes=minutes))
