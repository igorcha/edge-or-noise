"""Series blocks: indicators that give a number per bar."""

from typing import Literal

import numpy as np
import pandas as pd
from pydantic import Field

from engine.blocks.context import Bars
from engine.blocks.registry import Params, block
from engine.blocks.types import SERIES


def atr_values(bars: Bars, length: int) -> np.ndarray:
    """Wilder ATR. True range uses the previous close, so gaps (daily break, weekends) count."""
    prev_close = np.r_[np.nan, bars.close[:-1]]
    tr = np.fmax(bars.high - bars.low, np.fmax(np.abs(bars.high - prev_close), np.abs(bars.low - prev_close)))
    return pd.Series(tr).ewm(alpha=1 / length, adjust=False, min_periods=length).mean().to_numpy()


class EmaParams(Params):
    length: int = Field(20, ge=2, le=1000)
    source: Literal["close", "open", "high", "low"] = "close"


# Exponential moving average of a price.
# Use it as a trend filter ("only longs while close is above the 50 EMA"), a cross event
# ("EMA 9 crosses EMA 21"), or a dynamic level for entries and stops.
# No lookahead: computed recursively from past bars only; NaN until `length` bars exist.
@block(
    "ema",
    params=EmaParams,
    outputs={"value": SERIES},
    aliases=("exponential moving average", "moving average", "ma"),
    description="Exponential moving average of the chosen price (default close).",
)
def ema(bars: Bars, p: EmaParams) -> dict:
    src = getattr(bars, p.source)
    value = pd.Series(src).ewm(span=p.length, adjust=False, min_periods=p.length).mean().to_numpy()
    return {"value": value}


class AtrParams(Params):
    length: int = Field(14, ge=2, le=500)


# Average true range: how far price typically moves per bar, in points.
# Use it to size stops and targets in volatility terms ("stop 1.5 ATR below entry"),
# as a minimum-size filter for FVGs and displacement, or as a volatility filter.
# No lookahead: Wilder smoothing over past true ranges only; NaN until `length` bars exist.
@block(
    "atr",
    params=AtrParams,
    outputs={"value": SERIES},
    aliases=("average true range", "volatility"),
    description="Average true range (Wilder) in price points.",
)
def atr(bars: Bars, p: AtrParams) -> dict:
    return {"value": atr_values(bars, p.length)}
