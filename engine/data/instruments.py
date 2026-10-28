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


@dataclass(frozen=True)
class Instrument:
    name: str
    dukascopy_symbol: str
    price_step: float  # quote precision
    rth: tuple[time, time] = (time(9, 30), time(16, 0))  # NY window that gets the RTH spread
    commission_pts: float = 0.0  # per side
    slippage_pts: float = 0.0  # per side; default only, the user sets it per project


INSTRUMENTS = {
    i.name: i
    for i in (
        Instrument("US100", "E_NQ-100", 0.01),
        Instrument("US500", "E_SandP-500", 0.01),
        Instrument("JP225", "E_N225Jap", 0.01),
        Instrument("XAUUSD", "XAU/USD", 0.001),
    )
}
