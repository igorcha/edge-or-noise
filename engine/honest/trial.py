"""A trial: one spec run on one dataset, and the ledger that counts them.

A trial is a run with a new spec hash. Re-running an identical spec is the same trial; any
change to a rule, a block parameter or slippage is a new one. Stress tests and the final exam
use `run_trial` too but don't go through the ledger, so they don't count.
"""

from dataclasses import dataclass, field, replace

import pandas as pd

from engine.honest.data import Dataset
from engine.interpreter import interpret
from engine.sim import Costs, Result, metrics, simulate
from engine.spec import Strategy, canonicalize, spec_hash


@dataclass(frozen=True)
class Trial:
    spec: Strategy  # canonical
    spec_hash: str
    stage: str  # "development" | "holdout"
    trades: pd.DataFrame
    skipped: dict[str, int]
    metrics: dict
    costs: Costs  # what this run was charged; the random baseline is charged exactly the same

    @property
    def n(self) -> int:
        return len(self.trades)


def trial_costs(spec: Strategy, ds: Dataset, factor: float = 1.0) -> Costs:
    """The dataset's spread and commission with the spec's own slippage (it's part of the spec,
    so part of the trial), everything times `factor` for the cost stress test."""
    return replace(ds.costs, slippage=spec.slippage_pts).scaled(factor)


def run_trial(spec: Strategy, ds: Dataset, cost_factor: float = 1.0) -> Trial:
    spec = canonicalize(spec)
    if spec.instrument != ds.instrument or spec.timeframe != ds.timeframe:
        raise ValueError(f"spec is for {spec.instrument} {spec.timeframe}, dataset is {ds.instrument} {ds.timeframe}")
    costs = trial_costs(spec, ds, cost_factor)
    res: Result = simulate(interpret(spec, ds.bars), ds.bars, ds.m1, costs)
    trades = res.trades
    if ds.exam:
        if ds.boundary is None:
            raise ValueError("an exam dataset needs a holdout boundary")
        trades = trades[trades["signal_time"] > ds.boundary].reset_index(drop=True)  # holdout trades only
    return Trial(spec, spec_hash(spec), ds.stage, trades, res.skipped, metrics(trades), costs)


@dataclass
class Ledger:
    """Trial numbers by spec hash. The API keeps the real one in the database; this is the
    in-memory version for scripts and tests, with the same rule: a hash seen before isn't a new trial."""
    numbers: dict[str, int] = field(default_factory=dict)

    @property
    def count(self) -> int:
        return len(self.numbers)

    def number_for(self, spec: Strategy) -> tuple[int, bool]:
        """(trial number, is_new). The number a run of `spec` gets, assigning the next one if it's new."""
        h = spec_hash(spec)
        if h in self.numbers:
            return self.numbers[h], False
        self.numbers[h] = self.count + 1
        return self.numbers[h], True
