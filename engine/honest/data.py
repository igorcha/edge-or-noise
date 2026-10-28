"""What a trial runs on: strategy bars, 1m bars, the holdout boundary and the costs.

`load_dev` is what every trial uses: `load_bars` cuts the holdout, so the engine can't see it.
`load_exam` is the only place that passes `include_holdout=True`. It loads the full history so
indicators warm up normally, and `run_trial` then keeps only trades signalled after the
boundary. The one-time lock (a project may take the exam once, and the spec is frozen by it)
is the API's job; the engine just makes the holdout impossible to reach by accident.
"""

from dataclasses import dataclass

import pandas as pd

from engine.blocks.context import Bars
from engine.data import io
from engine.sim.costs import Costs


@dataclass(frozen=True)
class Dataset:
    instrument: str
    timeframe: str
    bars: Bars  # strategy timeframe
    m1: Bars  # exits are resolved on these
    costs: Costs
    boundary: pd.Timestamp | None  # holdout starts after this close time; None = synthetic/test data
    exam: bool = False  # True only for the final exam: bars include the holdout

    @property
    def stage(self) -> str:
        return "holdout" if self.exam else "development"


def load_dev(instrument: str, timeframe: str, slippage_pts: float = 0.0) -> Dataset:
    return Dataset(instrument, timeframe, Bars.load(instrument, timeframe), Bars.load(instrument, "1m"),
                   Costs.for_instrument(instrument, slippage_pts), io.holdout_boundary(instrument))


def load_exam(instrument: str, timeframe: str, slippage_pts: float = 0.0) -> Dataset:
    """Full history including the holdout. Call once per project, from the final exam only."""
    return Dataset(instrument, timeframe, Bars.load(instrument, timeframe, include_holdout=True),
                   Bars.load(instrument, "1m", include_holdout=True),
                   Costs.for_instrument(instrument, slippage_pts), io.holdout_boundary(instrument), exam=True)
