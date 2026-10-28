"""Structure blocks: swings, fair value gaps, sweeps, displacement candles."""

from typing import Literal

import numpy as np
import pandas as pd
from numpy.lib.stride_tricks import sliding_window_view
from pydantic import Field

from engine.blocks.arrays import ffill, lag, rolling_max, rolling_min
from engine.blocks.context import Bars
from engine.blocks.registry import Params, block
from engine.blocks.series import atr_values
from engine.blocks.types import EVENT, LEVEL, ZONE, Zones

Direction = Literal["bullish", "bearish", "both"]


class SwingParams(Params):
    left: int = Field(2, ge=1, le=50)  # bars before the pivot that must be lower (higher for lows)
    right: int = Field(2, ge=1, le=50)  # bars after the pivot needed to confirm it


# Confirmed swing highs and lows (pivots / fractals).
# A swing high is a bar whose high beats `left` bars before it and isn't exceeded by `right` bars after.
# Use `high`/`low` as structure levels: stop placement ("stop below the last swing low"),
# targets, sweeps of swing points, or breaks of structure (close above the last swing high).
# `new_high`/`new_low` fire when a swing gets confirmed.
# No lookahead: a swing exists only from its confirmation bar (pivot + `right`), never at the pivot.
@block(
    "swing",
    params=SwingParams,
    outputs={"high": LEVEL, "low": LEVEL, "new_high": EVENT, "new_low": EVENT},
    aliases=("swing high", "swing low", "pivot", "fractal", "swing point", "structure high", "structure low"),
    description="Most recent confirmed swing high/low price, plus events when a new swing is confirmed. "
                "A swing is usable only `right` bars after its pivot.",
)
def swing(bars: Bars, p: SwingParams) -> dict:
    n, w = len(bars), p.left + p.right + 1
    out = {"high": np.full(n, np.nan), "low": np.full(n, np.nan),
           "new_high": np.zeros(n, bool), "new_low": np.zeros(n, bool)}
    if n < w:
        return out
    confirm = np.arange(w - 1, n)  # window j covers bars j..j+w-1; its pivot is j+left
    hw = sliding_window_view(bars.high, w)
    lw = sliding_window_view(bars.low, w)
    ph, pl = hw[:, p.left], lw[:, p.left]
    is_high = (ph > hw[:, : p.left].max(1)) & (ph >= hw[:, p.left + 1:].max(1))
    is_low = (pl < lw[:, : p.left].min(1)) & (pl <= lw[:, p.left + 1:].min(1))
    out["new_high"][confirm[is_high]] = True
    out["new_low"][confirm[is_low]] = True
    out["high"][confirm[is_high]] = ph[is_high]
    out["low"][confirm[is_low]] = pl[is_low]
    out["high"], out["low"] = ffill(out["high"]), ffill(out["low"])
    return out


class FvgParams(Params):
    direction: Direction = "both"
    min_size_atr: float = Field(0.0, ge=0, le=20)  # gap must be at least this many ATR(14); 0 = any size
    max_age: int = Field(100, ge=1, le=5000)  # bars a zone stays active if price never trades through it


def _zone_ends(high, low, created, top, bottom, direction, max_age) -> np.ndarray:
    """Bar where each zone stops being active: price trades through its far edge, or it expires."""
    n = len(high)
    ended = np.full(len(created), -1)
    for z, c in enumerate(created):
        stop = min(c + max_age, n)
        through = low[c + 1:stop] < bottom[z] if direction[z] > 0 else high[c + 1:stop] > top[z]
        if through.any():
            ended[z] = c + 1 + int(through.argmax())
        elif c + max_age < n:
            ended[z] = c + max_age
    return ended


# Fair value gap: a three-candle imbalance where candle 3 doesn't overlap candle 1.
# Bullish: candle 3's low is above candle 1's high; the gap is [candle 1 high, candle 3 low].
# Bearish: candle 3's high is below candle 1's low; the gap is [candle 3 high, candle 1 low].
# The three candles must be consecutive in time, so opening gaps over the daily break or the
# weekend don't count.
# Use it as a setup step ("then a bullish FVG forms within 10 bars") and enter at its edge
# (`top`/`bottom`, e.g. a limit at gap.top), with the stop beyond the far edge.
# The zone stays active until price trades through its far edge or `max_age` bars pass.
# No lookahead: the gap is known at candle 3's close, the bar it's stamped on. The size filter
# uses ATR as of candle 2, so candle 3 doesn't inflate its own yardstick (same as displacement).
@block(
    "fvg",
    params=FvgParams,
    outputs={"zones": ZONE, "formed": EVENT, "top": LEVEL, "bottom": LEVEL},
    aliases=("fair value gap", "imbalance", "price imbalance", "liquidity void"),
    description="Fair value gaps (3-candle imbalances). `formed` fires on candle 3; `top`/`bottom` give the gap "
                "edges on that bar. Zones stay active until price trades through the far edge or max_age bars pass.",
)
def fvg(bars: Bars, p: FvgParams) -> dict:
    n = len(bars)
    h, l = bars.high, bars.low
    bull = np.zeros(n, bool)
    bear = np.zeros(n, bool)
    contiguous = (bars.close_time[2:] - bars.close_time[:-2]) == pd.Timedelta(minutes=2 * bars.tf_minutes)
    bull[2:] = (l[2:] > h[:-2]) & contiguous
    bear[2:] = (h[2:] < l[:-2]) & contiguous
    want = np.zeros(n, bool)
    if p.direction != "bearish":
        want |= bull
    if p.direction != "bullish":
        want |= bear
    top = np.where(bull, l, lag(l, 2))
    bottom = np.where(bull, lag(h, 2), h)
    if p.min_size_atr > 0:
        want &= (top - bottom) >= p.min_size_atr * lag(atr_values(bars, 14), 1)

    created = np.flatnonzero(want)
    z_top, z_bottom, z_dir = top[created], bottom[created], np.where(bull[created], 1, -1)
    ended = _zone_ends(h, l, created, z_top, z_bottom, z_dir, p.max_age)
    return {"zones": Zones(created, ended, z_top, z_bottom, z_dir), "formed": want,
            "top": np.where(want, top, np.nan), "bottom": np.where(want, bottom, np.nan)}


class SweepParams(Params):
    side: Literal["high", "low"] = "low"  # sweep of a low (bullish) or of a high (bearish)
    reclaim_bars: int = Field(1, ge=1, le=20)  # bars allowed between trading through and closing back


# Liquidity sweep / stop run: price trades through a level, then closes back on the other side.
# Sweep of a low: coming from above (previous close above the level), a bar's low goes below
# the level and a close comes back above it within `reclaim_bars` (1 = same candle, a wick
# through). Crossing up from below is not a sweep. Mirror for a high.
# Feed it any level, e.g. the Asia low or prior day high from `window_high_low`, or a `swing` low.
# Use it as the first setup step ("sweep of the Asia low, then a bullish FVG within 10 bars");
# `extreme` (the sweep's lowest low / highest high) is the natural stop reference.
# Turtle Soup, spring, stop run and liquidity grab are all this block.
# No lookahead: compares against the level as it was on the previous bar, so a bar can't
# sweep a level it set itself. Known gap: a prior day/week level appears on the first bar of the
# new day/week, so that one bar is judged against the older level (18:00 NY, outside every killzone).
@block(
    "sweep",
    params=SweepParams,
    inputs={"level": LEVEL},
    outputs={"value": EVENT, "extreme": LEVEL},
    aliases=("liquidity sweep", "turtle soup", "spring", "upthrust", "stop run", "stop hunt",
             "liquidity grab", "raid", "liquidity raid", "false breakout"),
    description="Price trades through a level and closes back on the other side within reclaim_bars. "
                "`extreme` is the sweep's furthest price, set on the event bar.",
)
def sweep(bars: Bars, p: SweepParams, level: np.ndarray) -> dict:
    lv = lag(np.asarray(level, dtype=np.float64), 1)
    n_bars = p.reclaim_bars
    prev_close = lag(bars.close, 1)
    if p.side == "low":
        breaks = (bars.low < lv) & (prev_close > lv)  # trades through from above
        back = bars.close > lv
        was_beyond = prev_close <= lv
        extreme = rolling_min(bars.low, n_bars)
    else:
        breaks = (bars.high > lv) & (prev_close < lv)
        back = bars.close < lv
        was_beyond = prev_close >= lv
        extreme = rolling_max(bars.high, n_bars)
    recent = rolling_max(breaks.astype(np.float64), n_bars) > 0
    event = back & (breaks | (recent & was_beyond))
    return {"value": event, "extreme": np.where(event, extreme, np.nan)}


class DisplacementParams(Params):
    direction: Direction = "both"
    min_body_atr: float = Field(1.5, gt=0, le=20)  # body at least this many ATRs
    min_body_ratio: float = Field(0.6, ge=0, le=1)  # body / full range, filters long-wick candles
    atr_length: int = Field(14, ge=2, le=500)


# Displacement: one large, decisive candle, a body that's big for current volatility with small wicks.
# Use it to confirm intent before an entry ("sweep, then bullish displacement, then FVG")
# or as a filter on the candle that creates the FVG.
# No lookahead: compares the closed candle to the ATR as of the previous bar, so the
# candle doesn't inflate its own yardstick.
@block(
    "displacement",
    params=DisplacementParams,
    outputs={"value": EVENT},
    aliases=("displacement candle", "impulse candle", "momentum candle", "large body candle",
             "expansion candle", "marubozu"),
    description="Candle with body >= min_body_atr x ATR (previous bar) and body >= min_body_ratio of its range.",
)
def displacement(bars: Bars, p: DisplacementParams) -> dict:
    body = bars.close - bars.open
    size = np.abs(body)
    ref_atr = lag(atr_values(bars, p.atr_length), 1)
    event = (size >= p.min_body_atr * ref_atr) & (size >= p.min_body_ratio * (bars.high - bars.low)) & (size > 0)
    if p.direction == "bullish":
        event &= body > 0
    elif p.direction == "bearish":
        event &= body < 0
    return {"value": event}
