"""Interpreter: sequence chaining, geometry, entry/stop/target, filters, and no lookahead."""

import zlib

import numpy as np
import pytest

from engine.blocks import Bars
from engine.data import io
from engine.data.build import resample, to_close_time
from engine.interpreter import interpret
from engine.interpreter.signals import chain
from engine.spec import validate_spec
from engine.spec.examples import EXAMPLES
from engine.tests.test_blocks import bars_from
from engine.tests.test_data import synth_raw

NAN = np.nan

# A swing low confirmed at bar 2 (left=right=1), then a bullish FVG [100, 101] formed at bar 5.
ROWS = [
    (100, 101, 99, 100),
    (100, 100.5, 98, 99),      # pivot low 98
    (99, 100, 98.5, 99.5),     # confirms the swing low
    (99.5, 100, 99, 99.8),     # FVG candle 1, high 100
    (99.8, 103, 99.8, 102.8),
    (102.8, 104, 101, 103),    # candle 3, low 101: gap [100, 101] known here
    (103, 103.5, 102, 102.5),
]

BASE = {
    "instrument": "US100", "timeframe": "5m", "direction": "long",
    "filters": [{"event": {"block": "time_window", "params": {"session": "ny_am"}}}],
    "setup": [
        {"name": "low", "event": {"block": "swing", "params": {"left": 1, "right": 1}, "output": "new_low"}},
        {"name": "gap", "event": {"block": "fvg", "params": {"direction": "bullish"}, "output": "formed"}, "within": 5},
    ],
    "entry": {"type": "limit", "at": {"ref": "gap.top"}, "expiry_bars": 10},
    "stop": {"type": "level", "at": {"ref": "gap.bottom"}, "buffer_pts": 0.1},
    "target": {"type": "r_multiple", "r": 2},
}


def run(rows=ROWS, **changes):
    return interpret(validate_spec({**BASE, **changes}), bars_from(rows))


# --- the sequence ------------------------------------------------------------------------

def test_chain_picks_most_recent_a_within_n():
    a = np.array([1, 0, 1, 0, 0, 0, 0, 1, 0], bool)
    b = np.array([0, 1, 0, 0, 1, 0, 1, 1, 0], bool)
    a_bar, b_bar = chain([None, 2], [a, b])
    assert b_bar.tolist() == [1, 4]  # bar 6 is 4 after the last A, bar 7 is the same bar as an A
    assert a_bar.tolist() == [0, 2]


def test_chain_three_steps():
    a = np.array([1, 0, 0, 0, 0, 0], bool)
    b = np.array([0, 0, 1, 0, 0, 0], bool)
    c = np.array([0, 0, 0, 0, 0, 1], bool)
    bars = chain([None, 2, 3], [a, b, c])
    assert [x.tolist() for x in bars] == [[0], [2], [5]]
    assert all(len(x) == 0 for x in chain([None, 2, 2], [a, b, c]))


def test_signal_geometry_entry_stop_target():
    s = run()
    assert s.bar.tolist() == [5] and s.direction == 1 and s.entry_kind == "limit"
    assert s.steps["low"].tolist() == [2] and s.steps["gap"].tolist() == [5]
    assert (s.geometry["gap.top"][0], s.geometry["gap.bottom"][0]) == (101, 100)
    assert np.isnan(s.geometry["low.high"][0]) and s.geometry["low.low"][0] == 98  # no swing high confirmed yet
    assert s.entry_price[0] == 101 and s.expiry_bars == 10
    assert s.stop_price[0] == pytest.approx(99.9) and np.isnan(s.stop_distance[0])
    assert np.isnan(s.target_price[0]) and s.target_r == 2
    assert s.max_trades_per_day == 1 and s.max_bars_held == 0 and s.dropped == {}


def test_within_too_small_gives_no_signal():
    spec = {**BASE, "setup": [BASE["setup"][0], {**BASE["setup"][1], "within": 2}]}
    assert len(run(**spec)) == 0


def test_block_level_is_read_at_the_signal_bar():
    """A stop at a block level (not a step ref) reads the level as of the signal bar."""
    s = run(stop={"type": "level", "at": {"block": "swing", "params": {"left": 1, "right": 1}, "output": "low"}})
    assert s.stop_price[0] == 98


# --- filters --------------------------------------------------------------------------------

def test_filters_apply_on_the_signal_bar():
    assert len(run(filters=[{"event": {"block": "time_window", "params": {"session": "asia"}}}])) == 0
    assert len(run(filters=[{"not": {"event": {"block": "time_window", "params": {"session": "asia"}}}}])) == 1
    assert len(run(filters=[{"left": {"price": "close"}, "op": ">", "right": 102.9}])) == 1
    assert len(run(filters=[{"left": {"price": "close"}, "op": ">", "right": 103.1}])) == 0


def test_nan_in_a_compare_is_false():
    ema = {"block": "ema", "params": {"length": 50}}  # NaN on 7 bars
    assert len(run(filters=[{"left": {"price": "close"}, "op": ">", "right": ema}])) == 0
    assert len(run(filters=[{"any": [{"left": {"price": "close"}, "op": "<", "right": ema},
                                      {"left": {"price": "close"}, "op": ">", "right": 1}]}])) == 1


# --- relative stops and targets, market entry -------------------------------------------------

def test_market_entry_with_atr_stop_and_level_target():
    rows = [(100, 101, 99, 100)] * 14 + ROWS  # flat warm-up so ATR(14) is known, no swings in it
    s = run(rows, filters=[], entry={"type": "market"}, stop={"type": "atr", "multiple": 2, "length": 14},
            target={"type": "level", "at": {"ref": "gap.top"}}, limits={"max_trades_per_day": 3, "max_bars_held": 8})
    assert s.bar.tolist() == [19] and s.dropped == {}
    assert s.entry_kind == "market" and np.isnan(s.entry_price).all() and s.expiry_bars == 0
    assert np.isnan(s.stop_price).all() and (s.stop_distance > 0).all()
    assert (s.target_price == 101).all() and np.isnan(s.target_r)
    assert s.max_trades_per_day == 3 and s.max_bars_held == 8


def test_points_stop():
    s = run(stop={"type": "points", "points": 1.5})
    assert s.stop_distance[0] == pytest.approx(1.5)


# --- crosses as setup steps ----------------------------------------------------------------------

def test_cross_step_fires_once_on_the_crossing_bar():
    # closes 100, 99, 99.5, 99.8, 102.8, 103, 102.5: close crosses above 102 at bar 4 only
    cross = {"name": "up", "event": {"left": {"price": "close"}, "op": "crosses_above", "right": 102}}
    s = run(setup=[cross], entry={"type": "market"}, stop={"type": "points", "points": 1})
    assert s.bar.tolist() == [4] and s.steps["up"].tolist() == [4] and s.geometry == {}
    down = {"name": "dn", "event": {"left": {"price": "close"}, "op": "crosses_below", "right": 102.6}}
    s = run(setup=[down], entry={"type": "market"}, stop={"type": "points", "points": 1}, direction="short")
    assert s.bar.tolist() == [6]


def test_cross_with_unknown_side_never_fires():
    ema = {"block": "ema", "params": {"length": 50}}  # NaN throughout
    cross = {"name": "up", "event": {"left": {"price": "close"}, "op": "crosses_above", "right": ema}}
    assert len(run(setup=[cross], entry={"type": "market"}, stop={"type": "points", "points": 1})) == 0


def test_cross_then_block_step_with_geometry():
    cross = {"name": "up", "event": {"left": {"price": "close"}, "op": "crosses_above", "right": 99.7}}
    gap = {"name": "gap", "event": {"block": "fvg", "params": {"direction": "bullish"}, "output": "formed"}, "within": 3}
    s = run(setup=[cross, gap])  # close crosses 99.7 at bar 3 (99.5 -> 99.8), gap at bar 5
    assert s.steps["up"].tolist() == [3] and s.bar.tolist() == [5] and s.geometry["gap.top"][0] == 101


# --- dropping what can't be traded --------------------------------------------------------------

def test_atr_stop_unknown_is_dropped():
    s = run(stop={"type": "atr", "multiple": 2, "length": 14})  # only 7 bars: ATR is NaN
    assert len(s) == 0 and s.dropped == {"stop distance unknown": 1}


def test_short_with_stop_below_limit_is_dropped():
    s = run(direction="short")  # limit at 101, stop at 100.1: wrong side for a short
    assert len(s) == 0 and s.dropped == {"stop not beyond entry": 1}


def test_target_behind_entry_is_dropped():
    s = run(target={"type": "level", "at": {"ref": "gap.bottom"}})
    assert len(s) == 0 and s.dropped == {"target not beyond entry": 1}


def test_unknown_level_is_dropped():
    s = run(stop={"type": "level", "at": {"block": "swing", "params": {"left": 5, "right": 5}, "output": "low"}})
    assert len(s) == 0 and s.dropped == {"stop price unknown": 1}


# --- examples on real or synthetic bars, and no lookahead ----------------------------------------------

@pytest.fixture(scope="module")
def bars():
    if (io.bars_dir("US100") / "manifest.json").exists():
        df = io.load_bars("US100", "5m").loc["2023-01-01":"2023-04-01"]
    else:
        df = resample(to_close_time(synth_raw()[0]), 5)
    return Bars.from_frame(df, 5, 0.01)


@pytest.mark.parametrize("name", sorted(EXAMPLES))
def test_examples_run(bars, name):
    spec = validate_spec({**EXAMPLES[name], "timeframe": "5m"})
    s = interpret(spec, bars)
    assert len(s.bar) == len(np.unique(s.bar)) and (np.diff(s.bar) > 0).all()
    for step, b in s.steps.items():
        assert (b <= s.bar).all(), step
    assert set(s.dropped) <= {"filters", "entry price unknown", "stop price unknown", "target price unknown",
                              "stop not beyond entry", "target not beyond entry"}


@pytest.mark.parametrize("name", sorted(EXAMPLES))
def test_examples_truncation(bars, name):
    """Signals found on bars[:t+1] are exactly the full-data signals at bars <= t."""
    spec = validate_spec({**EXAMPLES[name], "timeframe": "5m"})
    full = interpret(spec, bars)
    rng = np.random.default_rng(zlib.crc32(name.encode()))
    for t in np.r_[rng.integers(0, len(bars), 20), len(bars) - 1]:
        part = interpret(spec, bars.head(t + 1))
        want = full.take(full.bar <= t)
        assert part.bar.tolist() == want.bar.tolist(), f"t={t}"
        for k in ("entry_price", "stop_price", "stop_distance", "target_price"):
            np.testing.assert_array_equal(getattr(part, k), getattr(want, k), err_msg=f"{k} t={t}")
        for k in want.geometry:
            np.testing.assert_array_equal(part.geometry[k], want.geometry[k], err_msg=f"{k} t={t}")
