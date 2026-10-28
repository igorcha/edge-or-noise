"""Honest core: holdout sealing, trial identity, baseline matching, verdict math, stress test."""

import math

import numpy as np
import pandas as pd
import pytest

from engine.blocks import Bars
from engine.data import io
from engine.data.build import resample, to_close_time
from engine.honest import Dataset, Ledger, baseline, cost_stress, run_trial, verdict
from engine.honest.verdict import MIN_TRADES, deflated_sharpe, expected_max_sharpe
from engine.sim import Costs
from engine.spec import validate_spec
from engine.spec.examples import FVG_LONG
from engine.tests.test_data import synth_raw

COSTS = Costs(0.5, 1.0, commission=0.0)


def make_dataset(days=None, exam=False, boundary=None):
    m1 = to_close_time(synth_raw(days or [d.strftime("%Y-%m-%d") for d in pd.bdate_range("2024-01-02", "2024-06-28")])[0])
    tf = resample(m1, 5)
    return Dataset("US100", "5m", Bars.from_frame(tf, 5, 0.01), Bars.from_frame(m1, 1, 0.01), COSTS, boundary, exam)


@pytest.fixture(scope="module")
def ds():
    if (io.bars_dir("US100") / "manifest.json").exists():
        tf = io.load_bars("US100", "5m").loc["2023-01-01":"2023-07-01"]
        m1 = io.load_bars("US100", "1m").loc["2023-01-01":"2023-07-01"]
        return Dataset("US100", "5m", Bars.from_frame(tf, 5, 0.01), Bars.from_frame(m1, 1, 0.01), COSTS,
                       io.holdout_boundary("US100"))
    return make_dataset()


@pytest.fixture(scope="module")
def spec():
    return validate_spec(FVG_LONG)


@pytest.fixture(scope="module")
def trial(spec, ds):
    return run_trial(spec, ds)


# --- trials and the ledger ------------------------------------------------------------

def test_trial_runs_and_has_metrics(trial):
    assert trial.stage == "development" and trial.n > 5
    assert trial.metrics["n"] == trial.n and trial.spec_hash == trial.spec_hash.lower()
    assert set(trial.skipped) >= {"busy"} or trial.n == trial.metrics["n"]


def test_spec_slippage_overrides_dataset_costs(spec, ds):
    cheap = run_trial(spec, ds)
    slipped = run_trial(validate_spec({**FVG_LONG, "slippage_pts": 0.5}), ds)
    assert slipped.spec_hash != cheap.spec_hash
    assert slipped.metrics["expectancy_R"] < cheap.metrics["expectancy_R"]


def test_trial_refuses_wrong_dataset(spec, ds):
    with pytest.raises(ValueError):
        run_trial(validate_spec({**FVG_LONG, "timeframe": "15m"}), ds)


def test_ledger_counts_new_hashes_only(spec):
    ledger = Ledger()
    assert ledger.number_for(spec) == (1, True)
    assert ledger.number_for(validate_spec({**FVG_LONG, "entry": {"type": "limit", "at": {"ref": "gap.top"}}})) == (1, False)
    assert ledger.number_for(validate_spec({**FVG_LONG, "target": {"type": "r_multiple", "r": 3}})) == (2, True)
    assert ledger.number_for(spec) == (1, False) and ledger.count == 2


# --- holdout ----------------------------------------------------------------------------

def test_exam_keeps_only_holdout_trades(spec):
    days = [d.strftime("%Y-%m-%d") for d in pd.bdate_range("2024-01-02", "2024-06-28")]
    boundary = pd.Timestamp("2024-05-01 21:00", tz="UTC")
    full = make_dataset(days, exam=True, boundary=boundary)
    exam = run_trial(spec, full)
    everything = run_trial(spec, make_dataset(days))
    assert exam.stage == "holdout"
    assert (exam.trades["signal_time"] > boundary).all()
    assert exam.n == (everything.trades["signal_time"] > boundary).sum() and exam.n < everything.n
    with pytest.raises(ValueError):
        run_trial(spec, make_dataset(days, exam=True))  # exam without a boundary


def test_dev_dataset_never_includes_holdout_bars(ds):
    if ds.boundary is not None and not ds.exam:
        assert ds.bars.close_time.max() <= ds.boundary and ds.m1.close_time.max() <= ds.boundary


# --- baseline ----------------------------------------------------------------------------

def test_baseline_is_matched_to_the_strategy(trial, ds):
    base = baseline(trial, ds, n_runs=50, seed=1)
    assert base.n_runs == 50 and 0 <= base.beaten <= 1
    median_count = np.median(base.trade_counts)
    assert 0.7 * trial.n <= median_count <= 1.4 * trial.n, (trial.n, median_count)
    for k in ("p5", "p50", "p95"):
        assert len(base.curves[k]) == trial.n
    assert (base.curves["p5"] <= base.curves["p50"]).all() and (base.curves["p50"] <= base.curves["p95"]).all()
    assert base.expectancy_pct["p5"] <= base.expectancy_pct["p50"] <= base.expectancy_pct["p95"]
    j = base.to_json()
    assert set(j["curves"]) == {"p5", "p50", "p95"}


def test_baseline_is_deterministic_per_seed(trial, ds):
    a, b = baseline(trial, ds, n_runs=10, seed=3), baseline(trial, ds, n_runs=10, seed=3)
    np.testing.assert_array_equal(a.expectancy, b.expectancy)
    assert not np.array_equal(a.expectancy, baseline(trial, ds, n_runs=10, seed=4).expectancy)


def test_baseline_entries_respect_the_filters(trial, ds):
    """With an all-false filter nothing is eligible: the baseline is empty, not an error."""
    spec = validate_spec({**FVG_LONG, "filters": [{"left": {"price": "close"}, "op": "<", "right": 0}]})
    t = run_trial(spec, ds)
    base = baseline(t, ds, n_runs=5)
    assert t.n == 0 and base.beaten is None and base.trade_counts.sum() == 0


def test_baseline_on_random_walk_is_centered_near_zero(trial, ds):
    base = baseline(trial, ds, n_runs=100, seed=2)
    # synthetic bars are a random walk with costs: random entries can't be far above zero
    assert base.expectancy_pct["p50"] < 0.3


# --- verdict ----------------------------------------------------------------------------------

def fake_trial(r, stage="development"):
    trades = pd.DataFrame({"R": r, "cost_R": 0.05, "bars_held": 3, "exit_reason": "stop", "mae_R": 0.5, "mfe_R": 0.5})
    from engine.sim import metrics
    return type("T", (), {"trades": trades, "stage": stage, "metrics": metrics(trades), "n": len(trades)})()


def fake_base(beaten, p50=0.0):
    return type("B", (), {"beaten": beaten, "n_runs": 1000, "expectancy_pct": {"p5": -0.2, "p50": p50, "p95": 0.2}})()


def test_verdict_few_trades_is_cant_tell():
    rng = np.random.default_rng(0)
    v = verdict(fake_trial(rng.normal(1.0, 0.5, 10)), fake_base(1.0), n_trials=1)
    assert v.label == "can't tell yet" and v.n == 10 and v.trades_needed == 0
    assert any("fewer than" in s for s in v.reasons)
    assert verdict(fake_trial([1.0]), None, 1).label == "can't tell yet"


def test_verdict_promising():
    rng = np.random.default_rng(0)
    v = verdict(fake_trial(rng.normal(0.8, 1.0, 200)), fake_base(0.99), n_trials=1)
    assert v.label == "promising" and v.expectancy_ci[0] > 0 and v.deflated_sharpe > 0.95
    assert v.edge_over_baseline_R == pytest.approx(v.expectancy_R) and v.expected_max_sharpe == 0


def test_verdict_noise_when_baseline_does_as_well():
    rng = np.random.default_rng(1)
    r = rng.normal(0.3, 1.0, 100)
    assert verdict(fake_trial(r), fake_base(0.3), n_trials=1).label == "probably noise"
    assert verdict(fake_trial(r), fake_base(0.9), n_trials=1).label == "can't tell yet"
    assert verdict(fake_trial(-np.abs(r)), fake_base(0.9), n_trials=1).label == "probably noise"


def test_many_trials_deflate_the_verdict():
    rng = np.random.default_rng(2)
    r = rng.normal(0.25, 1.0, 120)
    one = verdict(fake_trial(r), fake_base(0.97), n_trials=1)
    sharpes = list(rng.normal(0.1, 0.15, 40))
    many = verdict(fake_trial(r), fake_base(0.97), n_trials=40, trial_sharpes=sharpes)
    assert many.expected_max_sharpe > one.expected_max_sharpe == 0
    assert many.deflated_sharpe < one.deflated_sharpe
    assert one.label == "promising" and many.label != "promising"


def test_trades_needed_shrinks_with_n():
    rng = np.random.default_rng(3)
    small = verdict(fake_trial(rng.normal(0.2, 1.0, 40)), None, 1)
    assert small.trades_needed > 0 and small.label == "can't tell yet"
    assert verdict(fake_trial(np.full(50, -0.1) + rng.normal(0, 0.01, 50)), None, 1).trades_needed is None


def test_holdout_stage_is_carried():
    assert verdict(fake_trial([1.0, -1.0, 0.5], stage="holdout"), None, 1).stage == "holdout"


def test_dsr_math():
    assert expected_max_sharpe([0.1], 1) == 0 and expected_max_sharpe([0.1, 0.1], 5) == 0
    assert expected_max_sharpe([0.0, 0.2, -0.1, 0.3], 10) > expected_max_sharpe([0.0, 0.2, -0.1, 0.3], 4) > 0
    r = np.random.default_rng(0).normal(0.5, 1.0, 100)
    assert deflated_sharpe(r, 0.0) > deflated_sharpe(r, 0.3) > 0
    assert deflated_sharpe(np.array([1.0, 1.0, 1.0]), 0.0) is None
    assert deflated_sharpe(r, 0.0) == pytest.approx(
        0.5 * (1 + math.erf((r.mean() / r.std(ddof=1)) * math.sqrt(99) / math.sqrt(
            1 - ((r - r.mean()) / r.std(ddof=1)) ** 3 @ np.ones(100) / 100 * (r.mean() / r.std(ddof=1))
            + ((((r - r.mean()) / r.std(ddof=1)) ** 4).mean() - 1) / 4 * (r.mean() / r.std(ddof=1)) ** 2) / math.sqrt(2))))


def test_verdict_json(trial, ds):
    import json
    base = baseline(trial, ds, n_runs=20)
    v = verdict(trial, base, n_trials=3, trial_sharpes=[0.1, 0.0, 0.05])
    json.dumps(v.to_json())
    json.dumps(base.to_json())
    assert v.label in {"probably noise", "can't tell yet", "promising"}
    assert MIN_TRADES == 30


# --- stress ---------------------------------------------------------------------------------------

def test_cost_stress_lowers_expectancy(spec, ds, trial):
    # A limit entry never pays a wider spread, it just fills less often: expectancy can't rise.
    s = cost_stress(spec, ds, trial)
    assert s.factor == 1.5 and s.stressed_expectancy_R <= s.base_expectancy_R
    assert s.survives == (s.stressed_expectancy_R > 0) and s.metrics["n"] <= trial.n
    # A market entry pays it on every trade.
    market = validate_spec({**FVG_LONG, "entry": {"type": "market"}})
    m = cost_stress(market, ds)
    assert m.stressed_expectancy_R < m.base_expectancy_R and m.metrics["n"] == run_trial(market, ds).n
