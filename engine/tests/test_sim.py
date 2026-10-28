"""Simulator: fills, exits, costs and limits on hand-built 1m bars with known outcomes."""

import numpy as np
import pandas as pd
import pytest

from engine.blocks import Bars
from engine.data import io
from engine.data.build import resample, to_close_time
from engine.data.sessions import NY
from engine.interpreter import interpret
from engine.interpreter.signals import Signals
from engine.sim import Costs, metrics, simulate, warm
from engine.spec import validate_spec
from engine.spec.examples import EXAMPLES
from engine.tests.test_data import synth_raw

NAN = np.nan
TICK = 0.01
FREE = Costs(0.0, 0.0)


def m1_bars(rows, start_ny="2026-03-10 09:30"):
    """1m bars from (o, h, l, c) rows, plus the 5m bars built from them."""
    opens = pd.date_range(pd.Timestamp(start_ny, tz=NY), periods=len(rows), freq="1min")
    df = pd.DataFrame(rows, columns=["open", "high", "low", "close"], dtype=np.float64)
    df.index = (opens + pd.Timedelta(minutes=1)).tz_convert("UTC")
    df.index.name = "close_time"
    return Bars.from_frame(df, 1, TICK), Bars.from_frame(resample(df, 5), 5, TICK)


def sig(bar=0, direction=1, entry=NAN, stop=NAN, stop_dist=NAN, target=NAN, target_r=2.0, expiry=2,
        max_bars_held=0, cap=1, **kw):
    bar = np.atleast_1d(bar)
    arr = lambda v: np.full(len(bar), v, np.float64)  # noqa: E731
    return Signals(bar=bar, direction=direction, entry_kind="limit" if not np.isnan(entry) else "market",
                   entry_price=arr(entry), expiry_bars=expiry, stop_price=arr(stop), stop_distance=arr(stop_dist),
                   target_price=arr(target), target_r=target_r, max_bars_held=max_bars_held, max_trades_per_day=cap,
                   slippage_pts=0.0, price_step=TICK, **kw)


FLAT = (100, 100.5, 99.5, 100)
# 5m bar 0 = 1m bars 0-4 (signal bar), bar 1 = 1m bars 5-9 (entry at the open of 1m bar 5), bar 2 = 10-14 ...
ROWS = [FLAT] * 5 + [(100, 100.5, 99.5, 100), (100, 101, 99.8, 100.8), (100.8, 102.5, 100.5, 102), (102, 103, 101, 102.5),
                     (102.5, 104.2, 102.4, 104)] + [FLAT] * 5


def one(result):
    assert len(result.trades) == 1, result
    return result.trades.iloc[0]


# --- market entry, stop and target ------------------------------------------------------

def test_market_entry_fills_next_open_and_target_needs_trade_through():
    m1, tf = m1_bars(ROWS)
    t = one(simulate(sig(stop_dist=2.0, target_r=2.0), tf, m1, FREE))  # entry 100, stop 98, target 104
    assert (t.entry, t.stop, t.target, t.entry_m1) == (100, 98, 104, 5)
    assert t.exit_reason == "target" and t.exit == 104 and t.exit_m1 == 9  # bar 9 high 104.2 >= 104.01
    assert t.R == 2 and t.cost_R == 0 and t.bars_held == 0
    assert t.entry_time == m1.close_time[5] - pd.Timedelta(minutes=1) and t.exit_time == m1.close_time[9]
    assert t.signal_time == tf.close_time[0] and t.signal_bar == 0 and t.entry_bar == 1 == t.exit_bar
    assert t.mfe_R == pytest.approx(2.1) and t.mae_R == pytest.approx(0.25)  # low 99.5 on the fill bar counts


def test_target_touch_without_trade_through_does_not_fill():
    rows = [FLAT] * 5 + [(100, 102, 99.8, 101)] + [FLAT] * 5  # high exactly 102 = target
    m1, tf = m1_bars(rows)
    t = one(simulate(sig(stop_dist=1.0, target_r=2.0), tf, m1, FREE))
    assert t.exit_reason == "end"


def test_stop_wins_when_both_hit_on_one_bar():
    rows = [FLAT] * 5 + [(100, 103, 97, 101)] + [FLAT] * 4
    m1, tf = m1_bars(rows)
    t = one(simulate(sig(stop_dist=1.0, target_r=2.0), tf, m1, FREE))
    assert t.exit_reason == "stop" and t.exit == 99 and t.R == -1


def test_gap_through_stop_fills_at_open_and_loses_more_than_1r():
    rows = [FLAT] * 5 + [(100, 100.5, 99.5, 100), (97, 98, 96, 97.5)] + [FLAT] * 3
    m1, tf = m1_bars(rows)
    t = one(simulate(sig(stop_dist=1.0), tf, m1, FREE))
    assert t.exit_reason == "stop" and t.exit == 97 and t.R == -3 and t.exit_m1 == 6


def test_fill_bar_only_the_stop_can_hit():
    rows = [FLAT] * 5 + [(100, 105, 99.5, 104)] + [FLAT] * 4  # would be stop AND target; and the target first by price
    m1, tf = m1_bars(rows)
    t = one(simulate(sig(stop_dist=0.4, target_r=2.0), tf, m1, FREE))
    assert t.exit_reason == "stop"
    rows = [FLAT] * 5 + [(100, 105, 99.9, 104)] + [FLAT] * 4  # target range on the fill bar, no stop: not taken
    m1, tf = m1_bars(rows)
    t = one(simulate(sig(stop_dist=0.4, target_r=2.0), tf, m1, FREE))
    assert t.exit_reason == "stop" and t.exit_m1 > 5  # the flat bars after it hit the 99.6 stop


# --- limit entries --------------------------------------------------------------------------

def test_limit_needs_trade_through_then_fills_at_limit():
    rows = [FLAT] * 5 + [(100, 100.5, 99.5, 100), (100, 100.2, 99.0, 99.5), (99.5, 99.8, 98.99, 99.6)] + [FLAT] * 7
    m1, tf = m1_bars(rows)
    t = one(simulate(sig(entry=99.0, stop=98.0, target=101.0), tf, m1, FREE))
    assert t.entry == 99 and t.entry_m1 == 7  # bar 6 low 99.0 is a touch, bar 7 low 98.99 trades through


def test_limit_gapping_through_fills_at_open():
    rows = [FLAT] * 5 + [(98.5, 100.5, 98, 100)] + [FLAT] * 9
    m1, tf = m1_bars(rows)
    t = one(simulate(sig(entry=99.0, stop=97.0, target=101.0), tf, m1, FREE))
    assert t.entry == 98.5 and t.stop == 97 and t.target == 101


def test_limit_expires_and_blocks_new_signals_while_working():
    rows = [FLAT] * 25
    m1, tf = m1_bars(rows)
    res = simulate(sig(bar=[0, 1, 2, 3], entry=99.0, stop=98.0, target=101.0, expiry=2), tf, m1, FREE)
    assert len(res.trades) == 0
    assert res.skipped == {"expired": 2, "busy": 2}  # bar 0's order works through 5m bars 1-2; bar 3's through 4


def test_long_limit_pays_its_price_and_no_spread_charge():
    # buy limit 99: the ask (bid + 0.5) trades through it on bar 6; the bid trades through the 101 target on bar 8
    rows = [FLAT] * 5 + [(100, 100.5, 99.5, 100), (99.5, 99.6, 98.4, 98.8), (98.8, 100, 98.8, 99.9),
                         (100, 101.2, 99.9, 101)] + [FLAT] * 6
    m1, tf = m1_bars(rows)
    t = one(simulate(sig(entry=99.0, stop=98.0, target=101.0, expiry=3), tf, m1, Costs(0.5, 0.5)))
    assert (t.entry, t.exit, t.exit_reason) == (99, 101, "target")
    assert t.cost_R == 0 and t.R == 2  # bought at 99, sold at 101, risk 1: the spread is already in the fill
    rows = [FLAT] * 5 + [(98.2, 100.5, 98, 100)] + [FLAT] * 9  # ask open 98.7 is below the limit: fill there
    m1, tf = m1_bars(rows)
    t = one(simulate(sig(entry=99.0, stop=97.0, target=101.0), tf, m1, Costs(0.5, 0.5)))
    assert t.entry == pytest.approx(98.7) and t.cost_R == 0


def test_limit_price_is_the_ask_for_longs():
    rows = [FLAT] * 5 + [(100, 100.5, 98.9, 100)] + [FLAT] * 9  # bid low 98.9: ask low 99.4 with a 0.5 spread
    m1, tf = m1_bars(rows)
    assert len(simulate(sig(entry=99.0, stop=98.0, target=101.0), tf, m1, Costs(0.5, 0.5)).trades) == 0
    assert len(simulate(sig(entry=99.0, stop=98.0, target=101.0), tf, m1, FREE).trades) == 1


# --- costs ---------------------------------------------------------------------------------------

def test_spread_slippage_and_commission():
    m1, tf = m1_bars(ROWS)
    costs = Costs(spread_rth=0.3, spread_overnight=1.0, commission=0.05, slippage=0.1)
    t = one(simulate(sig(stop_dist=2.0, target_r=2.0), tf, m1, costs))
    assert (t.entry, t.stop, t.target) == (100, 98, 104)  # chart prices; the spread is a cost, not a worse fill
    assert t.exit_reason == "target" and t.exit == 104
    assert t.cost_R == pytest.approx((0.3 + 0.1 + 2 * 0.05) / 2)  # entry spread + entry slippage + commissions
    assert t.R == pytest.approx(2 - t.cost_R)
    m1, tf = m1_bars([FLAT] * 20)
    t = one(simulate(sig(stop_dist=2.0, max_bars_held=1), tf, m1, costs))  # time exit: a market sell, slippage only
    assert t.exit_reason == "time" and t.cost_R == pytest.approx((0.3 + 0.1 + 0.1 + 2 * 0.05) / 2)


def test_scaled_costs_lower_r():
    m1, tf = m1_bars(ROWS)
    base = Costs(0.2, 0.5, commission=0.02, slippage=0.05)
    r1 = one(simulate(sig(stop_dist=2.0, target_r=1.0), tf, m1, base)).R
    r2 = one(simulate(sig(stop_dist=2.0, target_r=1.0), tf, m1, base.scaled(1.5))).R
    assert r2 < r1


def test_overnight_spread_outside_rth():
    costs = Costs(spread_rth=0.3, spread_overnight=1.0)
    m1, tf = m1_bars([FLAT] * 15, start_ny="2026-03-10 03:00")
    assert one(simulate(sig(stop_dist=2.0), tf, m1, costs)).cost_R == pytest.approx(1.0 / 2)


# --- shorts ----------------------------------------------------------------------------------

def test_short_mirror_with_spread_on_the_cover():
    rows = [FLAT] * 5 + [(100, 100.5, 99.5, 100), (100, 100.2, 98, 98.5), (98.5, 98.6, 96.9, 97)] + [FLAT] * 7
    m1, tf = m1_bars(rows)
    costs = Costs(0.1, 0.1)
    t = one(simulate(sig(direction=-1, stop_dist=1.0, target_r=2.0), tf, m1, costs))
    assert t.entry == 100 and t.stop == 101 and t.target == 98  # sold at the bid, no spread on the way in
    assert t.exit_reason == "target" and t.exit == 98 and t.exit_m1 == 7  # ask low = 96.9 + 0.1 <= 97.99
    assert t.R == 2 and t.cost_R == 0  # the spread's price was waiting for the ask to reach the target
    assert t.mae_R == pytest.approx(0.6) and t.mfe_R == pytest.approx(3.0)  # ask high 100.6 on the fill bar
    costs = Costs(0.1, 0.1, slippage=0.05)
    m1, tf = m1_bars([FLAT] * 20)
    t = one(simulate(sig(direction=-1, stop_dist=1.0, max_bars_held=1), tf, m1, costs))  # time exit: cover at market
    assert t.exit_reason == "time" and t.cost_R == pytest.approx(0.05 + 0.1 + 0.05)  # entry slip, cover spread + slip


def test_short_stop_uses_the_ask():
    rows = [FLAT] * 5 + [(100, 100.5, 99.5, 100), (100, 100.95, 99.5, 100)] + [FLAT] * 8
    m1, tf = m1_bars(rows)
    assert one(simulate(sig(direction=-1, stop_dist=1.0), tf, m1, Costs(0.1, 0.1))).exit_reason == "stop"
    assert one(simulate(sig(direction=-1, stop_dist=1.0), tf, m1, FREE)).exit_reason != "stop"


# --- limits: one at a time, daily cap, time stop ---------------------------------------------------

def test_one_position_at_a_time():
    m1, tf = m1_bars([FLAT] * 60)
    res = simulate(sig(bar=[0, 1, 2, 5], stop_dist=1.0, max_bars_held=2, cap=10), tf, m1, FREE)
    assert res.trades.signal_bar.tolist() == [0, 5] and res.skipped == {"busy": 2}
    assert res.trades.exit_reason.tolist() == ["time", "time"] and res.trades.bars_held.tolist() == [2, 2]


def test_daily_cap_counts_the_signal_day():
    rows = [FLAT] * 40
    m1, tf = m1_bars(rows, start_ny="2026-03-10 16:45")  # 5m bars 0-2 close by the 17:00 roll, 3-7 after
    res = simulate(sig(bar=[0, 2, 3, 5], stop_dist=1.0, max_bars_held=1, cap=1), tf, m1, FREE)
    assert res.trades.signal_bar.tolist() == [0, 3] and res.skipped == {"daily_cap": 2}


def test_time_stop_exits_at_the_open_after_n_bars():
    m1, tf = m1_bars([FLAT] * 30)
    t = one(simulate(sig(stop_dist=1.0, max_bars_held=3), tf, m1, FREE))
    assert t.exit_reason == "time" and t.exit_m1 == 20 and t.bars_held == 3 and t.exit == 100


def test_bad_geometry_after_fill_is_skipped():
    rows = [FLAT] * 5 + [(97, 97.5, 96.5, 97)] + [FLAT] * 9  # gap down through a level stop at 98
    m1, tf = m1_bars(rows)
    res = simulate(sig(stop=98.0, target_r=2.0), tf, m1, FREE)
    assert len(res.trades) == 0 and res.skipped == {"bad_geometry": 1}


def test_signal_on_last_bar_has_no_next_open():
    m1, tf = m1_bars([FLAT] * 10)
    res = simulate(sig(bar=1, stop_dist=1.0), tf, m1, FREE)
    assert res.skipped == {"no_data": 1}


# --- metrics and warm-up ---------------------------------------------------------------------------

def test_metrics():
    trades = pd.DataFrame({"R": [2.0, -1.0, -1.0, 1.0], "cost_R": [0.1] * 4, "bars_held": [3, 1, 2, 2],
                           "exit_reason": ["target", "stop", "stop", "time"], "mae_R": [0.2, 1, 1, 0.5],
                           "mfe_R": [2.1, 0.1, 0.3, 1.0]})
    m = metrics(trades)
    assert m["n"] == 4 and m["expectancy_R"] == 0.25 and m["win_rate"] == 0.5 and m["profit_factor"] == 1.5
    assert m["max_drawdown_R"] == 2.0 and m["total_R"] == 1.0 and m["exit_mix"] == {"stop": 2, "target": 1, "time": 1}
    assert m["se_R"] == pytest.approx(m["sd_R"] / 2)
    assert metrics(trades.iloc[:0])["n"] == 0


def test_warm_runs():
    warm()


# --- end to end on real or synthetic bars ------------------------------------------------------------

@pytest.fixture(scope="module")
def data():
    if (io.bars_dir("US100") / "manifest.json").exists():
        m1 = io.load_bars("US100", "1m").loc["2023-01-01":"2023-03-01"]
        tf = io.load_bars("US100", "5m").loc["2023-01-01":"2023-03-01"]
    else:
        m1 = to_close_time(synth_raw()[0])
        tf = resample(m1, 5)
    return Bars.from_frame(tf, 5, 0.01), Bars.from_frame(m1, 1, 0.01)


@pytest.mark.parametrize("name", sorted(EXAMPLES))
def test_examples_end_to_end(data, name):
    tf, m1 = data
    spec = validate_spec({**EXAMPLES[name], "timeframe": "5m"})
    res = simulate(interpret(spec, tf), tf, m1, Costs(0.5, 1.0, commission=0.0, slippage=0.0))
    t = res.trades
    assert (t.entry_time >= t.signal_time).all() and (t.exit_time > t.entry_time).all()  # market fills at the next open = signal close
    assert (t.entry_bar > t.signal_bar).all() and (t.exit_bar >= t.entry_bar).all()
    assert (t.exit_m1 >= t.entry_m1).all()
    assert (t.exit_m1.to_numpy()[:-1] < t.entry_m1.to_numpy()[1:]).all(), "positions overlap"
    d = spec.direction == "long"
    assert ((t.stop < t.entry) == d).all() and ((t.target > t.entry) == d).all()
    stopped = t[t.exit_reason == "stop"]
    assert (stopped.R <= 0).all() or stopped.empty
    assert (t[t.exit_reason == "target"].R > 0).all()
    assert (t.mae_R >= 0).all() and (t.mfe_R >= 0).all()
    assert (t.groupby(t.signal_time.dt.date).size() <= spec.limits.max_trades_per_day * 2).all()
    metrics(t)


TRADE_FIELDS = ["entry", "exit", "stop", "target", "exit_reason", "R", "cost_R", "mae_R", "mfe_R", "bars_held",
                "signal_bar", "entry_bar", "exit_bar", "entry_m1", "exit_m1"]
ENTRY_FIELDS = ["entry", "stop", "target", "signal_bar", "entry_bar", "entry_m1"]


@pytest.mark.parametrize("name", sorted(EXAMPLES))
def test_examples_truncation(data, name):
    """Cut both bar sets at a strategy bar's close: trades that closed by then are identical, trades
    still open are the same entries closed at the cut ("end"), and nothing later exists."""
    import zlib
    tf, m1 = data
    spec = validate_spec({**EXAMPLES[name], "timeframe": "5m"})
    costs = Costs(0.5, 1.0, commission=0.02, slippage=0.1)
    full = simulate(interpret(spec, tf), tf, m1, costs).trades
    rng = np.random.default_rng(zlib.crc32(name.encode()))
    marks = np.unique(np.r_[full.entry_bar, full.exit_bar, full.exit_bar - 1, full.signal_bar]).astype(int)
    marks = marks[(marks >= 0) & (marks < len(tf))]
    cuts = np.r_[rng.integers(0, len(tf), 10), rng.choice(marks, min(25, len(marks)), replace=False)]
    for t in cuts:
        k = int(np.searchsorted(m1.close_time.asi8, tf.close_time.asi8[t], side="right"))  # 1m bars kept
        part = simulate(interpret(spec, tf.head(t + 1)), tf.head(t + 1), m1.head(k), costs).trades
        want = full[full.entry_m1 <= k - 1].reset_index(drop=True)
        assert len(part) == len(want), f"t={t}"
        closed = (want.exit_m1 <= k - 1).to_numpy()
        pd.testing.assert_frame_equal(part.loc[closed, TRADE_FIELDS], want.loc[closed, TRADE_FIELDS], obj=f"t={t}")
        pd.testing.assert_frame_equal(part.loc[~closed, ENTRY_FIELDS], want.loc[~closed, ENTRY_FIELDS], obj=f"t={t}")
        assert (part.loc[~closed, "exit_reason"] == "end").all()
