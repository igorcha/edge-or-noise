"""Time blocks: sessions/killzones and day of week. All times are New York wall-clock."""

from typing import Annotated, Literal

import numpy as np
from pydantic import AfterValidator, Field, field_validator, model_validator

from engine.blocks.context import Bars
from engine.blocks.registry import Params, block
from engine.blocks.types import EVENT

# Pinned killzone defaults, NY time, [start, end).
SESSIONS = {
    "asia": ("20:00", "00:00"),
    "london": ("02:00", "05:00"),
    "ny_am": ("08:30", "11:00"),
    "ny_pm": ("13:30", "16:00"),
}
Session = Literal["asia", "london", "ny_am", "ny_pm"]


def to_minute(hhmm: str) -> int:
    h, m = hhmm.split(":")
    if not (0 <= int(h) < 24 and 0 <= int(m) < 60):
        raise ValueError(f"bad time {hhmm!r}, expected HH:MM")
    return int(h) * 60 + int(m)


def _hhmm(value: str) -> str:
    m = to_minute(value)
    return f"{m // 60:02d}:{m % 60:02d}"


TimeOfDay = Annotated[str, AfterValidator(_hhmm)]  # "9:30" is stored as "09:30", so both hash the same


def check_on_grid(start: str, end: str, tf_minutes: int) -> None:
    """Window edges must fall on bar boundaries. Otherwise one bar straddles the edge and the
    window silently includes minutes outside it (09:30-09:50 on 15m bars would run to 10:00)."""
    for t in (start, end):
        if to_minute(t) % tf_minutes:
            raise ValueError(f"window edge {t} isn't on the {tf_minutes}m bar grid; "
                             f"use times that are a multiple of {tf_minutes} minutes")


def in_window(minutes: np.ndarray, start: str, end: str) -> np.ndarray:
    """minutes in [start, end), wrapping past midnight when end <= start."""
    s, e = to_minute(start), to_minute(end)
    return (minutes >= s) & (minutes < e) if s < e else (minutes >= s) | (minutes < e)


def window_bounds(session: str, start: str | None, end: str | None) -> tuple[str, str]:
    return SESSIONS[session] if session != "custom" else (start, end)


class TimeWindowParams(Params):
    session: Session | Literal["custom"] = "ny_am"
    start: TimeOfDay | None = None  # HH:MM NY, custom only
    end: TimeOfDay | None = None

    def check_timeframe(self, tf_minutes: int) -> None:
        check_on_grid(*window_bounds(self.session, self.start, self.end), tf_minutes)

    @model_validator(mode="after")
    def _custom_needs_times(self):
        if (self.session == "custom") != (self.start is not None and self.end is not None):
            raise ValueError("start and end are required for session='custom' and only allowed there")
        if self.session == "custom" and to_minute(self.start) == to_minute(self.end):
            raise ValueError("custom window is empty")
        return self


# Session / killzone filter. True on bars that open inside a NY time window, either a
# preset killzone (Asia, London, NY AM, NY PM) or a custom HH:MM-HH:MM.
# Use it in `filters` ("only trade the NY AM killzone") or to limit where a setup step may happen.
# No lookahead: membership uses the bar's open time, which is known before the bar starts.
@block(
    "time_window",
    params=TimeWindowParams,
    outputs={"value": EVENT},
    aliases=("session", "killzone", "kill zone", "time filter", "trading hours", "asia session",
             "london session", "london killzone", "new york session", "ny killzone", "ny am", "ny pm"),
    description="True while the bar opens inside a NY-time window: a preset killzone "
                f"({', '.join(f'{k} {a}-{b}' for k, (a, b) in SESSIONS.items())}) or a custom start-end.",
)
def time_window(bars: Bars, p: TimeWindowParams) -> dict:
    start, end = window_bounds(p.session, p.start, p.end)
    return {"value": in_window(bars.open_minute_ny, start, end)}


Weekday = Literal["mon", "tue", "wed", "thu", "fri", "sat", "sun"]  # weekends only exist for crypto
WEEKDAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")


class DayOfWeekParams(Params):
    days: tuple[Weekday, ...] = Field(("mon", "tue", "wed", "thu", "fri"), min_length=1)

    @field_validator("days")
    @classmethod
    def _in_week_order(cls, days):  # the same days in any order are the same filter, and the same hash
        return tuple(sorted(set(days), key=WEEKDAYS.index))


# Day-of-week filter. True on bars whose trading day is one of the listed weekdays.
# Use it in `filters`, e.g. "skip Mondays" or "Tuesday to Thursday only".
# The trading day rolls at 17:00 NY, so Sunday-evening bars count as Monday.
@block(
    "day_of_week",
    params=DayOfWeekParams,
    outputs={"value": EVENT},
    aliases=("weekday", "day filter", "trading days"),
    description="True on bars whose trading day (rolling at 17:00 NY) is one of the listed weekdays.",
)
def day_of_week(bars: Bars, p: DayOfWeekParams) -> dict:
    allowed = [WEEKDAYS.index(d) for d in p.days]
    return {"value": np.isin(bars.weekday, allowed)}
