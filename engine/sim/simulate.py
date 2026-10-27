"""simulate(signals, bars, m1, costs) -> Result: the trade log plus what was skipped and why.

The trade log is the contract. Everything downstream (metrics, verdict, baseline, prop sim,
chart) reads only it. One row per filled trade:

    direction        +1 / -1
    signal_time      close of the signal bar (decision time)
    entry_time       open of the 1m bar the entry filled on
    exit_time        close of the 1m bar the exit filled on
    entry, exit      fill prices, spread and slippage included
    stop, target     absolute prices used
    exit_reason      stop | target | time | end
    R                signed (exit - entry) / risk - commission_R; spread and slippage are in the prices
    cost_R           all costs paid (spread + slippage + commission) in R, for diagnostics
    mae_R, mfe_R     worst / best excursion while open, in R
    bars_held        strategy bars from entry to exit
    signal_bar, entry_bar, exit_bar   strategy-bar indexes, for the chart window
    entry_m1, exit_m1                 1m-bar indexes
    <step>.<level>   setup geometry at the signal (e.g. gap.top), for the chart and the log
"""

from dataclasses import dataclass

import numpy as np
import pandas as pd

from engine.blocks.context import Bars
from engine.interpreter.signals import Signals
from engine.sim import kernel
from engine.sim.costs import Costs

TRADE_COLUMNS = ["direction", "signal_time", "entry_time", "exit_time", "entry", "exit", "stop", "target",
                 "exit_reason", "R", "cost_R", "mae_R", "mfe_R", "bars_held", "signal_bar", "entry_bar", "exit_bar",
                 "entry_m1", "exit_m1"]


@dataclass(frozen=True)
class Result:
    trades: pd.DataFrame
    skipped: dict[str, int]  # signals that didn't become trades, by reason (plus the interpreter's drops)

    @property
    def R(self) -> np.ndarray:
        return self.trades["R"].to_numpy()


def m1_ranges(bars: Bars, m1: Bars) -> tuple[np.ndarray, np.ndarray]:
    """For each strategy bar, the [start, end) range of 1m bars inside it, joined on close time."""
    tf_close = bars.close_time.asi8
    m1_close = m1.close_time.asi8
    end = np.searchsorted(m1_close, tf_close, side="right")
    start = np.r_[0, end[:-1]]
    return start.astype(np.int64), end.astype(np.int64)


def run_kernel(signals: Signals, bars: Bars, m1: Bars, costs: Costs, spread: np.ndarray | None = None,
               ranges: tuple[np.ndarray, np.ndarray] | None = None):
    """Raw kernel outputs for the signals, in signal order.

    The baseline calls this in a batch and passes precomputed `spread` and `ranges`.
    """
    start, end = ranges if ranges is not None else m1_ranges(bars, m1)
    if spread is None:
        spread = costs.spread_per_bar(m1.open_minute_ny)
    return kernel.scan(
        signals.bar.astype(np.int64), signals.entry_price, signals.stop_price, signals.stop_distance,
        signals.target_price, float(signals.target_r), int(signals.direction), signals.entry_kind == "limit",
        int(signals.expiry_bars), int(signals.max_bars_held), int(signals.max_trades_per_day),
        bars.trading_day[signals.bar].astype(np.int64),
        start, end, len(bars),
        m1.open, m1.high, m1.low, m1.close, spread, float(bars.price_step), float(costs.slippage),
        float(costs.commission),
    )


def simulate(signals: Signals, bars: Bars, m1: Bars, costs: Costs) -> Result:
    if bars.tf_minutes < m1.tf_minutes:
        raise ValueError("exit bars must be at least as fine as the strategy bars")
    if len(signals) and signals.bar.max() >= len(bars):
        raise ValueError("signals don't belong to these bars")
    (status, entry_i, exit_i, entry_px, exit_px, stop_px, target_px, reason, r, cost_r, mae, mfe,
     bars_held, entry_bar, exit_bar) = run_kernel(signals, bars, m1, costs)

    ok = status == kernel.OK
    trades = pd.DataFrame({
        "direction": np.full(ok.sum(), signals.direction, np.int64),
        "signal_time": bars.close_time[signals.bar[ok]],
        "entry_time": m1.close_time[entry_i[ok]] - pd.Timedelta(minutes=m1.tf_minutes),
        "exit_time": m1.close_time[exit_i[ok]],
        "entry": entry_px[ok], "exit": exit_px[ok], "stop": stop_px[ok], "target": target_px[ok],
        "exit_reason": pd.Categorical.from_codes(reason[ok], kernel.EXIT_NAMES),
        "R": r[ok], "cost_R": cost_r[ok], "mae_R": mae[ok], "mfe_R": mfe[ok],
        "bars_held": bars_held[ok], "signal_bar": signals.bar[ok], "entry_bar": entry_bar[ok],
        "exit_bar": exit_bar[ok], "entry_m1": entry_i[ok], "exit_m1": exit_i[ok],
    })
    for name, values in signals.geometry.items():
        trades[name] = values[ok]
    skipped = dict(signals.dropped)
    for code, name in enumerate(kernel.STATUS_NAMES):
        if code != kernel.OK and (status == code).any():
            skipped[name] = int((status == code).sum())
    return Result(trades.reset_index(drop=True), skipped)


def warm() -> None:
    """Compile the kernel on a tiny input so the first real run doesn't pay for it."""
    idx = pd.date_range("2026-03-10 14:31", periods=12, freq="1min", tz="UTC")
    df = pd.DataFrame({"open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0}, index=idx)
    m1 = Bars.from_frame(df, 1, 0.01)
    tf = Bars.from_frame(df.iloc[4::5].assign(open=100.0, high=101.0, low=99.0, close=100.0), 5, 0.01)
    sig = Signals(bar=np.array([0]), direction=1, entry_kind="market", entry_price=np.array([np.nan]),
                  expiry_bars=0, stop_price=np.array([np.nan]), stop_distance=np.array([5.0]),
                  target_price=np.array([np.nan]), target_r=2.0, max_bars_held=0, max_trades_per_day=1,
                  slippage_ticks=0, price_step=0.01)
    simulate(sig, tf, m1, Costs(0.0, 0.0))
