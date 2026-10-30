"""Trading costs. Bars are bid; a buy pays bid + spread. Everything is in price points.

Spread is a fixed schedule per instrument: one value in RTH (9:30-16:00 NY), one overnight,
taken from the manifest (mean ask - bid over the dataset's last 6 months) so dev and holdout
are charged the same. For crypto (`relative`) the schedule is a share of price, charged times
each 1m bar's open: its price moved 30x over the data, so fixed points would overcharge the
early years tenfold. Slippage is per side on market fills (market entries, stop exits), set
by the user. Commission is per side, from the instrument config.
"""

from dataclasses import dataclass, replace

import numpy as np

from engine.data import io
from engine.data.instruments import INSTRUMENTS

RTH = (9 * 60 + 30, 16 * 60)  # default RTH window, NY minutes of day, [start, end)


@dataclass(frozen=True)
class Costs:
    spread_rth: float
    spread_overnight: float
    commission: float = 0.0  # points per side
    slippage: float = 0.0  # points per side, market fills only
    rth: tuple[int, int] = RTH  # NY minutes of day that get the RTH spread
    relative: bool = False  # spreads are shares of price (crypto), not points

    @classmethod
    def for_instrument(cls, instrument: str, slippage_pts: float = 0.0) -> "Costs":
        inst = INSTRUMENTS[instrument]
        spread = io.manifest(instrument)["spread_pts"]
        rth = tuple(t.hour * 60 + t.minute for t in inst.rth)
        if inst.spread_unit == "relative":
            return cls(spread["rth_relative"], spread["overnight_relative"], inst.commission_pts, slippage_pts, rth,
                       relative=True)
        return cls(spread["rth"], spread["overnight"], inst.commission_pts, slippage_pts, rth)

    def scaled(self, factor: float) -> "Costs":
        """Every cost times `factor`: the costs x1.5 stress test."""
        return replace(self, spread_rth=self.spread_rth * factor, spread_overnight=self.spread_overnight * factor,
                       commission=self.commission * factor, slippage=self.slippage * factor)

    def spread_per_bar(self, open_minute_ny: np.ndarray, price: np.ndarray | None = None) -> np.ndarray:
        """The spread in points for each 1m bar. A relative schedule needs the bars' prices (their opens:
        known when the bar starts, so no lookahead)."""
        rth = (open_minute_ny >= self.rth[0]) & (open_minute_ny < self.rth[1])
        spread = np.where(rth, self.spread_rth, self.spread_overnight).astype(np.float64)
        if self.relative:
            if price is None:
                raise ValueError("a relative spread needs the bars' prices")
            spread = spread * np.asarray(price, np.float64)
        return spread
