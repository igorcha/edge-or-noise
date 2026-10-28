"""Random baseline: what the strategy's exits, costs and limits earn with random entries.

Each run draws entry bars at random from the bars where the strategy's filters hold (same
sessions, same conditions), as many as the strategy had signals, enters at market on the
next open, and uses the strategy's own exit rules: stop distances sampled from the
strategy's trades, the same R target (or the realized R of a level target), the same time
stop, daily cap, direction and costs. The simulator applies the same one-position rule, and
the number of draws is calibrated so the runs end up with about the strategy's trade count.

The result is the 5/50/95 percentile equity curves by trade number and the share of runs
the strategy's expectancy beats. A strategy whose entries carry information beats most of
them; one that only rode the exits and the session doesn't.
"""

from dataclasses import dataclass

import numpy as np

from engine.honest.data import Dataset
from engine.honest.trial import Trial
from engine.interpreter.signals import Signals, filter_mask, interpret
from engine.sim import kernel
from engine.sim.simulate import m1_ranges, run_kernel
from engine.spec.models import RTarget

N_RUNS = 1000
PILOT_RUNS = 8  # calibration runs to match the strategy's trade count
PERCENTILES = (5, 50, 95)


@dataclass(frozen=True)
class Baseline:
    n_runs: int
    n_signals: int  # random entries drawn per run
    expectancy: np.ndarray  # per run, NaN for runs without trades
    trade_counts: np.ndarray  # per run
    curves: dict[str, np.ndarray]  # "p5" / "p50" / "p95": cumulative R by trade number, length = strategy trades
    beaten: float | None  # share of runs with lower expectancy than the strategy (None without strategy trades)
    expectancy_pct: dict[str, float | None]  # baseline expectancy at the 5/50/95 percentiles

    def to_json(self) -> dict:
        return {"n_runs": self.n_runs, "n_signals": self.n_signals, "beaten": self.beaten,
                "expectancy_pct": self.expectancy_pct, "trade_count_median": float(np.median(self.trade_counts)),
                "curves": {k: v.round(4).tolist() for k, v in self.curves.items()}}


def baseline(trial: Trial, ds: Dataset, n_runs: int = N_RUNS, seed: int = 0) -> Baseline:
    spec, bars, m1 = trial.spec, ds.bars, ds.m1
    strategy_signals = interpret(spec, bars)
    n_signals = len(strategy_signals)
    eligible = np.flatnonzero(filter_mask(spec, bars)[:-1])  # the last bar has no next open
    if ds.exam and ds.boundary is not None:
        eligible = eligible[bars.close_time[eligible] > ds.boundary]
    trades = trial.trades
    n_trades = len(trades)
    if n_signals == 0 or n_trades == 0 or len(eligible) == 0:
        return _empty(n_runs, n_signals, n_trades)

    risks = ((trades["entry"] - trades["stop"]) * trades["direction"]).to_numpy(np.float64)
    if isinstance(spec.target, RTarget):
        target_r = spec.target.r
    else:  # a level target: use the R multiple it worked out to
        target_r = float(np.median(((trades["target"] - trades["entry"]) * trades["direction"]).to_numpy() / risks))
    costs = ds.costs
    spread = costs.spread_per_bar(m1.open_minute_ny)
    ranges = m1_ranges(bars, m1)
    rng = np.random.default_rng(seed)

    def run_once(k: int) -> np.ndarray:
        nan = np.full(k, np.nan)
        sig = Signals(
            bar=np.sort(rng.choice(eligible, k, replace=False)), direction=strategy_signals.direction,
            entry_kind="market", entry_price=nan, expiry_bars=0, stop_price=nan,
            stop_distance=rng.choice(risks, k), target_price=nan, target_r=target_r,
            max_bars_held=strategy_signals.max_bars_held, max_trades_per_day=strategy_signals.max_trades_per_day,
            slippage_pts=strategy_signals.slippage_pts, price_step=bars.price_step,
        )
        out = run_kernel(sig, bars, m1, costs, spread, ranges)
        return out[8][out[0] == kernel.OK]

    # Random entries spread out evenly, so fewer of them collide with an open position than the
    # strategy's clustered signals do. A short pilot finds how many draws give about n_trades trades.
    k = min(n_signals, len(eligible))
    pilot = np.median([len(run_once(k)) for _ in range(PILOT_RUNS)])
    if pilot > 0:
        k = int(np.clip(round(k * n_trades / pilot), 1, len(eligible)))

    expectancy = np.full(n_runs, np.nan)
    counts = np.zeros(n_runs, np.int64)
    curves = np.zeros((n_runs, n_trades))
    for run in range(n_runs):
        r = run_once(k)
        counts[run] = len(r)
        if len(r):
            expectancy[run] = r.mean()
            eq = np.cumsum(r[:n_trades])
            curves[run, : len(eq)] = eq
            curves[run, len(eq):] = eq[-1]  # a shorter run stays flat at its final equity

    strat = trial.metrics["expectancy_R"]
    valid = ~np.isnan(expectancy)
    beaten = float((expectancy[valid] < strat).mean()) if valid.any() else None
    pct = {f"p{p}": (float(np.nanpercentile(expectancy, p)) if valid.any() else None) for p in PERCENTILES}
    return Baseline(n_runs, k, expectancy, counts,
                    {f"p{p}": np.percentile(curves, p, axis=0) for p in PERCENTILES}, beaten, pct)


def _empty(n_runs: int, n_signals: int, n_trades: int) -> Baseline:
    return Baseline(n_runs, n_signals, np.full(n_runs, np.nan), np.zeros(n_runs, np.int64),
                    {f"p{p}": np.zeros(n_trades) for p in PERCENTILES}, None, {f"p{p}": None for p in PERCENTILES})
