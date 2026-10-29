"""The verdict: probably noise / can't tell yet / promising, and the numbers behind it.

It judges the mechanical rules, not the trader, and it's a "development verdict" until the
holdout has been used. Four ingredients:

  expectancy interval   mean R +/- 1.96 * SD / sqrt(n)
  edge over baseline    strategy expectancy minus the median random-entry expectancy, and the
                        share of random runs the strategy beat
  deflated Sharpe       Bailey & Lopez de Prado's DSR: the probability the per-trade Sharpe
                        ratio is really above the Sharpe you'd expect as the best of N trials
                        by luck alone, with skew and kurtosis of the R distribution accounted for.
                        The holdout verdict isn't deflated: it's one test of one locked spec, and
                        the choice between trials was made on other data, so the benchmark is 0
  trades needed         how many more trades, at this expectancy and SD, until the interval
                        would exclude zero

Labels:
  promising       interval above zero, beat >= 95% of the baseline, DSR >= 0.95 (all three must
                  exist: without a baseline or a DSR nothing is called promising)
  probably noise  n >= MIN_TRADES and (expectancy <= 0, or beat < 50% of the baseline, or DSR < 0.5)
  can't tell yet  everything else, which is where most strategies with few trades live
"""

import math
from dataclasses import dataclass
from statistics import NormalDist

import numpy as np

from engine.honest.baseline import Baseline
from engine.honest.trial import Trial

Z95 = 1.959963984540054
MIN_TRADES = 30  # below this nothing is called noise, only "can't tell yet"
EULER_GAMMA = 0.5772156649015329
_N = NormalDist()
HOLDOUT = "holdout"


@dataclass(frozen=True)
class Verdict:
    label: str  # "probably noise" | "can't tell yet" | "promising"
    stage: str  # "development" | "holdout"
    n: int
    n_trials: int
    expectancy_R: float | None
    expectancy_ci: tuple[float, float] | None
    se_R: float | None
    baseline_beaten: float | None
    edge_over_baseline_R: float | None
    sharpe_per_trade: float | None
    expected_max_sharpe: float | None  # what the best of n_trials would show by luck
    deflated_sharpe: float | None  # probability the Sharpe is really above that
    trades_needed: int | None  # more trades until the interval excludes zero; None if expectancy <= 0
    reasons: list[str]

    def to_json(self) -> dict:
        return {**self.__dict__, "expectancy_ci": list(self.expectancy_ci) if self.expectancy_ci else None}


def verdict(trial: Trial, base: Baseline | None, n_trials: int, trial_sharpes: list[float] | None = None) -> Verdict:
    """`n_trials` is the project's trial count including this one; `trial_sharpes` the per-trade
    Sharpe of every trial so far (used for the spread of outcomes the DSR deflates by). On the
    holdout both are only reported: the exam is never deflated."""
    r = trial.trades["R"].to_numpy(np.float64)
    n = len(r)
    stage = trial.stage
    reasons: list[str] = []
    if n < 2:
        return Verdict("can't tell yet", stage, n, n_trials, float(r.mean()) if n else None, None, None,
                       None, None, None, None, None, None, ["fewer than 2 trades"])

    mean, sd = float(r.mean()), float(r.std(ddof=1))
    se = sd / math.sqrt(n) if sd > 0 else 0.0
    ci = (mean - Z95 * se, mean + Z95 * se)
    sharpe = mean / sd if sd > 0 else None
    if stage == HOLDOUT:  # one test on unseen data: nothing was selected on it, nothing to deflate
        sr0 = 0.0
    else:
        sr0 = expected_max_sharpe(trial_sharpes if trial_sharpes else [sharpe or 0.0], n_trials, n)
    dsr = deflated_sharpe(r, sr0) if sharpe is not None else None

    beaten = base.beaten if base is not None else None
    edge = (mean - base.expectancy_pct["p50"]) if base is not None and base.expectancy_pct["p50"] is not None else None

    if mean > 0 and sd > 0:
        needed = math.ceil((Z95 * sd / mean) ** 2)
        trades_needed = max(0, needed - n)
    else:
        trades_needed = None

    reasons.append(f"expectancy {mean:+.2f}R, 95% interval {ci[0]:+.2f} to {ci[1]:+.2f} over {n} trades")
    if beaten is not None:
        reasons.append(f"beat {beaten:.0%} of {base.n_runs} random-entry runs")
    if dsr is not None and stage == HOLDOUT:
        reasons.append(f"Sharpe probability {dsr:.2f} on the holdout (one test of one spec, not deflated)")
    elif dsr is not None:
        reasons.append(f"deflated Sharpe {dsr:.2f} after {n_trials} trial{'s' if n_trials != 1 else ''}")
    if trades_needed:
        reasons.append(f"about {trades_needed} more trades needed for the interval to exclude zero")

    noise = mean <= 0 or (beaten is not None and beaten < 0.5) or (dsr is not None and dsr < 0.5)
    strong = ci[0] > 0 and beaten is not None and beaten >= 0.95 and dsr is not None and dsr >= 0.95
    if base is None:
        reasons.append("no random baseline: can't be called promising")
    if n >= MIN_TRADES and noise:
        label = "probably noise"
    elif strong and n >= MIN_TRADES:
        label = "promising"
    else:
        label = "can't tell yet"
        if n < MIN_TRADES:
            reasons.append(f"fewer than {MIN_TRADES} trades: nothing is called noise or promising yet")
    return Verdict(label, stage, n, n_trials, mean, ci, se, beaten, edge, sharpe, sr0, dsr, trades_needed, reasons)


def expected_max_sharpe(trial_sharpes: list[float], n_trials: int, n_trades: int | None = None) -> float:
    """E[max SR] of n_trials tries whose true SR is zero and whose estimates vary like the trials seen so far.
    With one trial, or no spread between trials, nothing is deflated. If there are several trials
    but fewer than two Sharpes to measure their spread (earlier trials had too few trades), the
    spread of a no-edge Sharpe over `n_trades` trades, 1 / (n - 1), stands in, so deflation
    never silently switches off."""
    n_trials = max(n_trials, len(trial_sharpes))
    if n_trials < 2:
        return 0.0
    if len(trial_sharpes) >= 2:
        var = float(np.var(np.asarray(trial_sharpes, np.float64), ddof=1))
    else:
        var = 1.0 / (n_trades - 1) if n_trades and n_trades > 1 else 0.0
    if var <= 0:
        return 0.0
    return math.sqrt(var) * ((1 - EULER_GAMMA) * _N.inv_cdf(1 - 1 / n_trials)
                             + EULER_GAMMA * _N.inv_cdf(1 - 1 / (n_trials * math.e)))


def deflated_sharpe(r: np.ndarray, sr0: float) -> float | None:
    """Probabilistic Sharpe ratio of the per-trade R series against the benchmark `sr0`."""
    n = len(r)
    if n < 3:
        return None
    sd = r.std(ddof=1)
    if sd <= 0:
        return None
    sr = r.mean() / sd
    z = (r - r.mean()) / sd
    skew = float((z ** 3).mean())
    kurt = float((z ** 4).mean())
    denom = 1 - skew * sr + (kurt - 1) / 4 * sr ** 2
    if denom <= 0:
        return None
    return _N.cdf((sr - sr0) * math.sqrt(n - 1) / math.sqrt(denom))
