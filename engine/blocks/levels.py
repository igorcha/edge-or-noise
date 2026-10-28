"""Level blocks: highs and lows of completed time windows."""

from typing import Literal

import numpy as np
from pydantic import model_validator

from engine.blocks.arrays import ffill
from engine.blocks.context import Bars
from engine.blocks.registry import Params, block
from engine.blocks.timing import Session, TimeOfDay, check_on_grid, in_window, to_minute, window_bounds
from engine.blocks.types import EVENT, LEVEL

ROLL_MINUTE = 17 * 60  # trading day rolls at 17:00 NY


class WindowHighLowParams(Params):
    window: Literal["day", "week"] | Session | Literal["custom"] = "day"
    start: TimeOfDay | None = None  # HH:MM NY, custom only
    end: TimeOfDay | None = None

    def check_timeframe(self, tf_minutes: int) -> None:
        if self.window not in ("day", "week"):
            check_on_grid(*window_bounds(self.window, self.start, self.end), tf_minutes)

    @model_validator(mode="after")
    def _custom_window(self):
        if (self.window == "custom") != (self.start is not None and self.end is not None):
            raise ValueError("start and end are required for window='custom' and only allowed there")
        if self.window == "custom":
            s, e = to_minute(self.start), to_minute(self.end)
            if s == e:
                raise ValueError("custom window is empty")
            if s != ROLL_MINUTE and in_window(np.array([ROLL_MINUTE]), self.start, self.end)[0]:
                raise ValueError("a custom window can't span the 17:00 NY trading-day roll")
        return self


# High and low of the most recently COMPLETED window: prior day (PDH/PDL), prior week,
# a session (Asia / London / NY range), or a custom range such as the 09:30-10:00 opening range.
# Use the outputs as reference prices: the level a `sweep` takes out, a target ("target the
# prior day high"), or a filter ("price above the Asia high").
# `updated` fires on the bar a new window's high/low appears. Use it as a setup step so a level is
# known to be today's: "after the opening range completes, close crosses above its high".
# No lookahead: a window's high/low appears only once the window is over, at the bar that
# closes exactly at the window end or else the first bar after it. Until then the previous
# window's values are shown. Day windows finish at the 17:00 NY roll, weeks at the next week's open.
@block(
    "window_high_low",
    params=WindowHighLowParams,
    outputs={"high": LEVEL, "low": LEVEL, "updated": EVENT},
    aliases=("prior day high", "prior day low", "previous day high", "previous day low", "pdh", "pdl",
             "prior week high", "prior week low", "pwh", "pwl", "asia high", "asia low", "asia range",
             "london high", "london low", "session high", "session low", "opening range",
             "range high", "range low"),
    description="High and low of the most recently completed window (prior trading day, prior week, "
                "a session, or a custom NY-time range). Updates only after the window has ended; "
                "`updated` fires on that bar.",
)
def window_high_low(bars: Bars, p: WindowHighLowParams) -> dict:
    n = len(bars)
    if p.window == "day":
        member, key, end_minute = np.ones(n, bool), bars.trading_day, ROLL_MINUTE
    elif p.window == "week":
        member, key, end_minute = np.ones(n, bool), bars.trading_day - bars.weekday, None
    else:
        start, end = window_bounds(p.window, p.start, p.end)
        member, key, end_minute = in_window(bars.open_minute_ny, start, end), bars.trading_day, to_minute(end)

    high, low, updated = np.full(n, np.nan), np.full(n, np.nan), np.zeros(n, bool)
    idx = np.flatnonzero(member)
    if idx.size:
        k = key[idx]
        starts = np.flatnonzero(np.r_[True, k[1:] != k[:-1]])
        g_high = np.maximum.reduceat(bars.high[idx], starts)
        g_low = np.minimum.reduceat(bars.low[idx], starts)
        last = idx[np.r_[starts[1:] - 1, idx.size - 1]]  # each window's last bar
        closes_at_end = bars.close_minute_ny[last] == end_minute if end_minute is not None else False
        final = np.where(closes_at_end, last, last + 1)  # first bar at which the window is known complete
        keep = (final < n) & np.r_[final[1:] != final[:-1], True]
        high[final[keep]] = g_high[keep]
        low[final[keep]] = g_low[keep]
        updated[final[keep]] = True
    return {"high": ffill(high), "low": ffill(low), "updated": updated}
