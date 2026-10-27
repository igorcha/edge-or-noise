"""Glue between the views and the engine. Datasets are loaded once per process and kept in memory."""

from functools import lru_cache

import numpy as np
import pandas as pd
from django.conf import settings

from engine.honest import Dataset, baseline, cost_stress, load_dev, load_exam, run_trial, verdict
from engine.honest.trial import Trial as EngineTrial
from engine.spec import Strategy, canonical_dict
from engine.spec.card import render_card


@lru_cache(maxsize=8)
def dataset(instrument: str, timeframe: str) -> Dataset:
    return load_dev(instrument, timeframe)


def exam_dataset(instrument: str, timeframe: str) -> Dataset:
    return load_exam(instrument, timeframe)


def run(spec: Strategy, ds: Dataset, n_trials: int, prior_sharpes: list[float], with_stress: bool = True) -> dict:
    """Everything one trial produces, as JSON-ready pieces."""
    trial: EngineTrial = run_trial(spec, ds)
    base = baseline(trial, ds, n_runs=settings.BASELINE_RUNS)
    sharpes = [*prior_sharpes, _sharpe(trial.metrics)]
    v = verdict(trial, base, n_trials=n_trials, trial_sharpes=[s for s in sharpes if s is not None])
    return {
        "spec": canonical_dict(spec),
        "spec_hash": trial.spec_hash,
        "stage": trial.stage,
        "card": render_card(spec)["text"],
        "metrics": trial.metrics,
        "verdict": v.to_json(),
        "baseline": base.to_json(),
        "stress": cost_stress(spec, ds, trial).to_json() if with_stress else None,
        "skipped": trial.skipped,
        "trades": trade_records(trial.trades),
    }


def _sharpe(metrics: dict) -> float | None:
    if metrics.get("sd_R"):
        return metrics["expectancy_R"] / metrics["sd_R"]
    return None


def trade_records(trades: pd.DataFrame) -> list[dict]:
    df = trades.copy()
    for col in ("signal_time", "entry_time", "exit_time"):
        df[col] = df[col].dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    df["exit_reason"] = df["exit_reason"].astype(str)
    df = df.replace({np.nan: None})
    return [{k: (v.item() if isinstance(v, np.generic) else v) for k, v in row.items()} for row in df.to_dict("records")]


def bars_window(ds: Dataset, first_bar: int, last_bar: int, before: int, after: int) -> dict:
    """OHLC of strategy bars around [first_bar, last_bar]. Dev datasets hold no holdout bars, so none can leak."""
    bars = ds.bars
    lo, hi = max(0, first_bar - before), min(len(bars), last_bar + after + 1)
    idx = np.arange(lo, hi)
    # Lightweight Charts wants the bar's open time in epoch seconds
    close_s = bars.close_time[lo:hi].tz_convert("UTC").tz_localize(None).to_numpy().astype("datetime64[s]").astype(np.int64)
    open_s = close_s - bars.tf_minutes * 60
    return {
        "first_bar": int(lo),
        "bars": [{"bar": int(i), "time": int(t), "open": float(o), "high": float(h), "low": float(l), "close": float(c)}
                 for i, t, o, h, l, c in zip(idx, open_s, bars.open[lo:hi], bars.high[lo:hi], bars.low[lo:hi],
                                             bars.close[lo:hi])],
    }


def spec_diff(old: dict | None, new: dict) -> str:
    """One line saying what changed between two canonical specs, for the trial history."""
    if old is None:
        return "first run"
    changes = [f"{k}: {a} -> {b}" for k, (a, b) in _diff(_flatten(old), _flatten(new)).items()]
    return "; ".join(changes) if changes else "no change"


def _flatten(d, prefix="") -> dict:
    out = {}
    if isinstance(d, dict):
        for k, v in d.items():
            out.update(_flatten(v, f"{prefix}.{k}" if prefix else k))
    elif isinstance(d, list):
        for i, v in enumerate(d):
            out.update(_flatten(v, f"{prefix}[{i}]"))
    else:
        out[prefix] = d
    return out


def _diff(a: dict, b: dict) -> dict:
    keys = sorted(set(a) | set(b))
    return {k: (a.get(k, "-"), b.get(k, "-")) for k in keys if a.get(k, "-") != b.get(k, "-")}
