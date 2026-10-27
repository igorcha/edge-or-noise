"""Simulator: Signals + bars -> trade log. One position at a time, exits resolved on 1m bars."""

from engine.sim.costs import Costs
from engine.sim.metrics import metrics
from engine.sim.simulate import TRADE_COLUMNS, Result, run_kernel, simulate, warm

__all__ = ["Costs", "Result", "TRADE_COLUMNS", "metrics", "run_kernel", "simulate", "warm"]
