"""Explicit exchange-calendars boundary; no network or venue fallback."""
from dataclasses import dataclass
from datetime import timedelta
from importlib.metadata import version
from types import MappingProxyType

import pandas as pd

from app.scanner.history_quality_contracts import SessionCalendar, MarketSession, day


EXPECTED_CALENDAR_VERSION = "4.13.2"
# Only mappings exercised in the initial calendar acceptance are enabled here.
CALENDAR_BINDINGS = MappingProxyType({"BIT": "XMIL", "XETRA": "XETR", "NYSE": "XNYS", "NASDAQ": "NASDAQ"})


@dataclass(frozen=True)
class CalendarContext:
    calendar: SessionCalendar
    timezone: str
    calendar_code: str


class ExchangeSessionCalendarProvider:
    def __init__(self, *, factory=None, package_version=None):
        self.factory = factory
        self.package_version = package_version

    def build(self, exchange, *, start, end):
        day(start)
        day(end)
        if start > end:
            raise ValueError("Invalid calendar interval")
        if exchange not in CALENDAR_BINDINGS:
            raise ValueError("Unsupported calendar venue; no fallback")
        installed = self.package_version or version("exchange-calendars")
        if installed != EXPECTED_CALENDAR_VERSION:
            raise ValueError("Calendar package version differs from validated version 4.13.2")
        factory = self.factory
        if factory is None:
            import exchange_calendars as xcals
            factory = xcals.get_calendar
        code = CALENDAR_BINDINGS[exchange]
        # Padding allows requests whose boundaries are holidays/weekends.
        value = factory(code, start=(start - timedelta(days=7)).isoformat(),
                        end=(end + timedelta(days=7)).isoformat())
        schedule = value.schedule
        if (not isinstance(schedule, pd.DataFrame) or "close" not in schedule
                or not schedule.columns.is_unique or not isinstance(schedule.index, pd.DatetimeIndex)
                or schedule.index.has_duplicates or schedule.index.hasnans or schedule.empty):
            raise ValueError("Invalid calendar schedule")
        if schedule.index[0].date() > start or schedule.index[-1].date() < end:
            raise ValueError("Calendar does not cover requested bounds")
        sessions = []
        for label, close in schedule["close"].items():
            if label != label.normalize():
                raise ValueError("Calendar labels must be session dates")
            if not start <= label.date() <= end:
                continue
            close = pd.Timestamp(close)
            if pd.isna(close) or close.tzinfo is None:
                raise ValueError("Calendar close must be timezone-aware")
            sessions.append(MarketSession(label.date(), close.tz_convert("UTC").to_pydatetime()))
        if not sessions:
            # A weekend-only request can legitimately have zero sessions.
            if (end - start).days > 14:
                raise ValueError("Unexpected empty calendar interval")
        calendar = SessionCalendar(exchange, f"exchange-calendars:{code}", installed,
                                   start, end, tuple(sessions))
        return CalendarContext(calendar, str(value.tz), code)
