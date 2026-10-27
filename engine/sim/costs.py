"""Trading costs. Bars are bid; a buy pays bid + spread. Everything is in price points.

Spread is a fixed schedule per instrument: one value in RTH (9:30-16:00 NY), one overnight,
taken from the manifest (mean ask - bid over the dataset's last 6 months) so dev and holdout
are charged the same. Slippage is per side on market fills (market entries, stop exits), set
by the user. Commission is per side, from the instrument config.
"""

from dataclasses import dataclass, replace

import numpy as np

from engine.data import io
from engine.data.instruments import INSTRUMENTS

RTH = (9 * 60 + 30, 16 * 60)  # NY minutes of day, [start, end)


@dataclass(frozen=True)
class Costs:
    spread_rth: float
    spread_overnight: float
    commission: float = 0.0  # points per side
    slippage: float = 0.0  # points per side, market fills only

    @classmethod
    def for_instrument(cls, instrument: str, slippage_ticks: int = 0) -> "Costs":
        inst = INSTRUMENTS[instrument]
        spread = io.manifest(instrument)["spread_pts"]
        return cls(spread["rth"], spread["overnight"], getattr(inst, "commission_pts", 0.0),
                   slippage_ticks * inst.price_step)

    def scaled(self, factor: float) -> "Costs":
        """Every cost times `factor`: the costs x1.5 stress test."""
        return replace(self, spread_rth=self.spread_rth * factor, spread_overnight=self.spread_overnight * factor,
                       commission=self.commission * factor, slippage=self.slippage * factor)

    def spread_per_bar(self, open_minute_ny: np.ndarray) -> np.ndarray:
        rth = (open_minute_ny >= RTH[0]) & (open_minute_ny < RTH[1])
        return np.where(rth, self.spread_rth, self.spread_overnight).astype(np.float64)
