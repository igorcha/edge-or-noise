"""Hand-built bars with known answers for each block."""

import json

import numpy as np
import pandas as pd
import pytest
from pydantic import ValidationError

from engine.blocks import REGISTRY, Bars, catalog, compute, resolve
from engine.data.sessions import NY

NAN = np.nan


def bars_from(rows, start_ny="2026-03-10 09:30", tf=5):
    """Consecutive bars from OHLC tuples; the first bar opens at start_ny (New York time)."""
    opens = pd.date_range(pd.Timestamp(start_ny, tz=NY), periods=len(rows), freq=f"{tf}min")
    index = (opens + pd.Timedelta(minutes=tf)).tz_convert("UTC")
    return Bars.from_frame(pd.DataFrame(rows, columns=["open", "high", "low", "close"], index=index), tf, 0.01)


def bars_at(spec, tf=5):
    """Bars at given NY open times: spec is [(\"YYYY-MM-DD HH:MM\", o, h, l, c), ...]."""
    opens = pd.DatetimeIndex([pd.Timestamp(s[0], tz=NY) for s in spec])
    index = (opens + pd.Timedelta(minutes=tf)).tz_convert("UTC")
    return Bars.from_frame(pd.DataFrame([s[1:] for s in spec], columns=["open", "high", "low", "close"], index=index), tf, 0.01)


def flat(times, tf=5):
    return bars_at([(t, 100, 100, 100, 100) for t in times], tf)


# --- time_window / day_of_week --------------------------------------------------

@pytest.mark.parametrize("day", ["2026-03-06", "2026-03-09"])  # EST, then EDT
def test_time_window_ny_am_across_dst(day):
    b = flat([f"{day} 08:25", f"{day} 08:30", f"{day} 10:55", f"{day} 11:00"])
    assert compute("time_window", b, {"session": "ny_am"})["value"].tolist() == [False, True, True, False]


def test_time_window_wraps_midnight():
    b = flat(["2026-03-10 19:55", "2026-03-10 20:00", "2026-03-10 23:55", "2026-03-11 00:00"])
    assert compute("time_window", b, {"session": "asia"})["value"].tolist() == [False, True, True, False]


def test_time_window_custom():
    b = flat(["2026-03-10 09:25", "2026-03-10 09:30", "2026-03-10 09:55", "2026-03-10 10:00"])
    out = compute("time_window", b, {"session": "custom", "start": "09:30", "end": "10:00"})["value"]
    assert out.tolist() == [False, True, True, False]


@pytest.mark.parametrize("params", [{"session": "custom"}, {"session": "ny_am", "start": "09:30", "end": "10:00"},
                                    {"session": "custom", "start": "09:30", "end": "09:30"},
                                    {"session": "custom", "start": "25:00", "end": "10:00"}, {"bogus": 1}])
def test_time_window_rejects_bad_params(params):
    with pytest.raises(ValidationError):
        compute("time_window", flat(["2026-03-10 10:00"]), params)


def test_window_edges_must_be_on_the_bar_grid():
    params = {"session": "custom", "start": "09:30", "end": "09:50"}
    compute("time_window", flat(["2026-03-10 09:30"], tf=5), params)
    with pytest.raises(ValueError, match="grid"):
        compute("time_window", flat(["2026-03-10 09:30"], tf=15), params)
    with pytest.raises(ValueError, match="grid"):
        compute("window_high_low", flat(["2026-03-10 09:30"], tf=15), {"window": "custom", "start": "09:30", "end": "09:50"})


def test_time_and_day_params_are_normalised():
    assert REGISTRY["time_window"].params(session="custom", start="9:30", end="10:00").start == "09:30"
    assert REGISTRY["day_of_week"].params(days=["thu", "tue", "thu"]).days == ("tue", "thu")
    with pytest.raises(ValidationError):
        REGISTRY["day_of_week"].params(days=[])


def test_day_of_week_uses_trading_day():
    b = flat(["2026-03-08 18:00", "2026-03-09 10:00", "2026-03-13 10:00"])  # Sun evening, Mon, Fri
    assert compute("day_of_week", b, {"days": ["mon"]})["value"].tolist() == [True, True, False]


# --- window_high_low ----------------------------------------------------------------

def test_prior_day_appears_at_next_trading_day():
    b = bars_at([
        ("2026-03-10 10:00", 100, 110, 95, 105),
        ("2026-03-10 11:00", 105, 130, 104, 120),
        ("2026-03-10 16:10", 120, 121, 90, 100),
        ("2026-03-10 18:00", 100, 101, 99, 100),  # first bar of trading day Mar 11
        ("2026-03-11 10:00", 100, 140, 80, 100),
    ])
    out = compute("window_high_low", b, {"window": "day"})
    np.testing.assert_array_equal(out["high"], [NAN, NAN, NAN, 130, 130])
    np.testing.assert_array_equal(out["low"], [NAN, NAN, NAN, 90, 90])
    assert np.flatnonzero(out["updated"]).tolist() == [3]


def test_session_high_low_final_at_session_end():
    b = bars_at([
        ("2026-03-10 04:50", 100, 105, 99, 100),
        ("2026-03-10 04:55", 100, 108, 97, 100),  # closes 05:00 = London end
        ("2026-03-10 05:00", 100, 120, 90, 100),  # outside London, doesn't count
    ])
    out = compute("window_high_low", b, {"window": "london"})
    np.testing.assert_array_equal(out["high"], [NAN, 108, 108])
    np.testing.assert_array_equal(out["low"], [NAN, 97, 97])
    assert np.flatnonzero(out["updated"]).tolist() == [1]


def test_session_with_missing_last_bar_finalises_on_next_bar():
    b = bars_at([
        ("2026-03-10 04:50", 100, 105, 99, 100),  # 04:55 bar missing
        ("2026-03-10 05:00", 100, 120, 90, 100),
    ])
    out = compute("window_high_low", b, {"window": "london"})
    np.testing.assert_array_equal(out["high"], [NAN, 105])


def test_custom_window_cannot_span_roll():
    with pytest.raises(ValidationError):
        compute("window_high_low", flat(["2026-03-10 10:00"]), {"window": "custom", "start": "16:00", "end": "18:00"})
    compute("window_high_low", flat(["2026-03-10 10:00"]), {"window": "custom", "start": "17:00", "end": "19:00"})


# --- swing ----------------------------------------------------------------------------

def test_swing_confirmed_after_right_bars():
    highs = [1, 2, 5, 3, 2, 1, 1]
    b = bars_from([(h - 0.25, h, h - 0.5, h - 0.25) for h in highs])
    out = compute("swing", b, {"left": 2, "right": 2})
    assert np.flatnonzero(out["new_high"]).tolist() == [4]
    np.testing.assert_array_equal(out["high"], [NAN, NAN, NAN, NAN, 5, 5, 5])
    assert not out["new_low"].any()



def test_swing_higher_and_lower_compare_with_the_previous_swing():
    lows = [9, 8, 5, 8, 9, 7, 6, 7, 9, 9, 6, 9, 9, 9]  # swing lows 5 (bar 2), 6 (bar 6), 6 (bar 10)
    b = bars_from([(lo + 1, lo + 2, lo, lo + 1) for lo in lows])
    out = compute("swing", b, {"left": 2, "right": 2})
    assert np.flatnonzero(out["new_low"]).tolist() == [4, 8, 12]
    assert np.flatnonzero(out["higher_low"]).tolist() == [8]  # 6 > 5, known when 6 is confirmed
    assert not out["lower_low"].any()  # the first swing has nothing to compare with; 6 = 6 is neither
    assert not (out["higher_high"] | out["lower_high"]).any()

# --- fvg ---------------------------------------------------------------------------------

FVG_ROWS = [
    (100, 101, 99, 100.5),
    (100.5, 106, 100.5, 105.5),
    (105.5, 107, 102, 106.5),  # low 102 > bar 0 high 101: bullish gap [101, 102]
    (106.5, 107, 103, 104),
    (104, 104.5, 100.5, 101),  # trades below 101: gap invalidated here
]


def test_fvg_bullish_zone_and_invalidation():
    out = compute("fvg", bars_from(FVG_ROWS))
    z = out["zones"]
    assert (z.created.tolist(), z.ended.tolist(), z.direction.tolist()) == ([2], [4], [1])
    assert (z.bottom[0], z.top[0]) == (101, 102)
    assert np.flatnonzero(out["formed"]).tolist() == [2]
    assert (out["top"][2], out["bottom"][2]) == (102, 101)


def test_fvg_expires_after_max_age():
    assert compute("fvg", bars_from(FVG_ROWS), {"max_age": 1})["zones"].ended.tolist() == [3]


def test_fvg_still_active_at_end_of_data():
    assert compute("fvg", bars_from(FVG_ROWS[:4]))["zones"].ended.tolist() == [-1]


def test_fvg_needs_consecutive_candles():
    b = bars_at([("2026-03-13 16:05", *FVG_ROWS[0][:4]), ("2026-03-13 16:10", *FVG_ROWS[1][:4]),
                 ("2026-03-15 18:00", *FVG_ROWS[2][:4])])  # Friday close, then Sunday open
    assert len(compute("fvg", b)["zones"]) == 0
    assert len(compute("fvg", bars_from(FVG_ROWS[:3]))["zones"]) == 1


def test_fvg_bearish_and_direction_filter():
    rows = [(200 - r[0], 200 - r[2], 200 - r[1], 200 - r[3]) for r in FVG_ROWS]  # mirror the prices
    z = compute("fvg", bars_from(rows))["zones"]
    assert (z.created.tolist(), z.direction.tolist(), z.bottom[0], z.top[0]) == ([2], [-1], 98, 99)
    assert len(compute("fvg", bars_from(rows), {"direction": "bullish"})["zones"]) == 0


# --- sweep ---------------------------------------------------------------------------------

SWEEP_ROWS = [
    (101, 102, 100.5, 101.5),
    (101.5, 102, 100.5, 101),
    (101, 101.5, 99, 100.5),   # wick below 100, close back above: sweep
    (100.5, 101, 97, 101),     # level is 100 before this bar (95 only from this bar): sweep
    (101, 101, 94, 94.5),      # below 95, closes below: no reclaim yet
    (94.5, 96, 95.2, 95.5),    # closes back above 95 one bar later: sweep only if reclaim_bars >= 2
]
SWEEP_LEVEL = np.array([NAN, 100, 100, 95, 95, 95])


def test_sweep_same_bar_reclaim():
    out = compute("sweep", bars_from(SWEEP_ROWS), level=SWEEP_LEVEL)
    assert np.flatnonzero(out["value"]).tolist() == [2, 3]
    assert (out["extreme"][2], out["extreme"][3]) == (99, 97)


def test_sweep_multi_bar_reclaim():
    out = compute("sweep", bars_from(SWEEP_ROWS), {"reclaim_bars": 2}, level=SWEEP_LEVEL)
    assert np.flatnonzero(out["value"]).tolist() == [2, 3, 5]
    assert out["extreme"][5] == 94


def test_sweep_of_a_high():
    b = bars_from([(99, 99.5, 98, 99), (99, 101, 98.5, 99.5), (99.5, 101, 99, 100.5)])
    out = compute("sweep", b, {"side": "high"}, level=np.array([100, 100, 100]))
    assert np.flatnonzero(out["value"]).tolist() == [1]
    assert out["extreme"][1] == 101


def test_cross_from_below_is_not_a_sweep():
    b = bars_from([(99, 99.5, 98, 99), (99, 100.5, 98.5, 100.5)])  # was below 100, crosses up
    assert not compute("sweep", b, level=np.array([100, 100]))["value"].any()


def test_sweep_requires_level_input():
    with pytest.raises(TypeError):
        compute("sweep", bars_from(SWEEP_ROWS))


# --- displacement ------------------------------------------------------------------------------

def test_displacement():
    base = [(100, 101.5, 99.5, 101) if i % 2 else (101, 101.5, 99.5, 100) for i in range(20)]  # range 2
    rows = base + [
        (100, 104.2, 99.9, 104),   # body 4 >= 1.5 x ATR(~2), small wicks: bullish displacement
        (104, 109, 99, 104.5),     # wide range, small body: not displacement
    ]
    b = bars_from(rows)
    assert np.flatnonzero(compute("displacement", b)["value"]).tolist() == [20]
    assert not compute("displacement", b, {"direction": "bearish"})["value"].any()


# --- ema / atr --------------------------------------------------------------------------------------

def test_ema_and_atr_on_constant_bars():
    b = bars_from([(100, 101, 99, 100)] * 30)
    ema = compute("ema", b, {"length": 10})["value"]
    assert np.isnan(ema[:9]).all() and np.allclose(ema[9:], 100)
    atr = compute("atr", b, {"length": 14})["value"]
    assert np.isnan(atr[:13]).all() and np.allclose(atr[13:], 2)


# --- registry / catalog -----------------------------------------------------------------------------

def test_catalog_is_json_and_aliases_unique():
    cat = catalog()
    json.dumps(cat)
    assert {b["name"] for b in cat["blocks"]} == set(REGISTRY)
    names = [a for s in REGISTRY.values() for a in (s.name, *s.aliases)]
    assert len(names) == len(set(names)), "an alias maps to two blocks"
    assert "vwap" in cat["unsupported"]
    assert '"title"' not in json.dumps(cat)  # pydantic noise stripped from the LLM prompt


@pytest.mark.parametrize("alias", ["Turtle Soup", "spring", "stop run"])
def test_sweep_aliases(alias):
    assert resolve(alias).name == "sweep"
