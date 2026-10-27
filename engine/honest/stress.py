"""Stress tests: the same spec under harsher assumptions. Not trials; they never touch the ledger."""

from dataclasses import dataclass

from engine.honest.data import Dataset
from engine.honest.trial import Trial, run_trial
from engine.spec import Strategy

COST_FACTOR = 1.5


@dataclass(frozen=True)
class CostStress:
    factor: float
    base_expectancy_R: float | None
    stressed_expectancy_R: float | None
    survives: bool | None  # expectancy still positive with the harsher costs
    metrics: dict

    def to_json(self) -> dict:
        return dict(self.__dict__)


def cost_stress(spec: Strategy, ds: Dataset, base: Trial | None = None, factor: float = COST_FACTOR) -> CostStress:
    base = base if base is not None else run_trial(spec, ds)
    stressed = run_trial(spec, ds, costs=ds.costs.scaled(factor))
    b, s = base.metrics["expectancy_R"], stressed.metrics["expectancy_R"]
    return CostStress(factor, b, s, (s > 0) if s is not None else None, stressed.metrics)
