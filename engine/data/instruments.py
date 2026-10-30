"""Static per-instrument settings.

Numbers derived from the data (spread schedule, holdout boundary) live in the
dataset manifest written by the build, not here.
"""

from dataclasses import dataclass
from datetime import time

import pandas as pd

# The MVP dataset is frozen. Bars opening at or after this instant are ignored by
# the build, so a refetch can't shift the holdout boundary.
DATA_START = pd.Timestamp("2020-01-01", tz="UTC")
DATA_END = pd.Timestamp("2026-10-24", tz="UTC")

TIMEFRAMES = {"1m": 1, "5m": 5, "15m": 15}


# The CME equity-index halt, [16:15, 18:00) NY by bar open time: the index CFDs don't quote in it.
CME_BREAK = (time(16, 15), time(18, 0))


@dataclass(frozen=True)
class Instrument:
    name: str
    dukascopy_symbol: str
    price_step: float  # quote precision
    title: str = ""  # for people: "Nasdaq 100"
    rth: tuple[time, time] = (time(9, 30), time(16, 0))  # NY window that gets the RTH spread
    daily_break: tuple[time, time] | None = CME_BREAK  # no bars may open in it; None trades around the clock
    # How the spread schedule is charged. "points": a fixed price distance. "relative": a share of price, for
    # crypto, whose price went from $4k to $126k in the data: a fixed 50-point BTC spread would cost ten times
    # more of the price in 2020 than now.
    spread_unit: str = "points"
    commission_pts: float = 0.0  # per side
    slippage_pts: float = 0.0  # per side; default only, the user sets it per project


INSTRUMENTS = {
    i.name: i
    for i in (
        Instrument("US100", "E_NQ-100", 0.01, "Nasdaq 100"),
        Instrument("US500", "E_SandP-500", 0.01, "S&P 500"),
        Instrument("JP225", "E_N225Jap", 0.01, "Nikkei 225"),
        Instrument("XAUUSD", "XAU/USD", 0.001, "Gold"),
        # Crypto trades every day around the clock. Its trading day still rolls at 17:00 NY like the rest, and
        # the "RTH" spread window is just the NY stock session there.
        Instrument("BTCUSD", "BTC/USD", 0.1, "Bitcoin", daily_break=None, spread_unit="relative"),
        Instrument("ETHUSD", "ETH/USD", 0.1, "Ether", daily_break=None, spread_unit="relative"),
    )
}
