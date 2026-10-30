import json

import numpy as np
import pandas as pd
import pytest

from engine.data import io
from engine.data.build import (
    DataError, build, holdout_start_day, resample, spread_schedule, to_close_time, validate_raw,
)
from engine.data.instruments import INSTRUMENTS
from engine.data.sessions import NY, minute_of_day_ny, trading_day, trading_day_start

# Trading days on both sides of the 2026-03-08 DST switch (EST -> EDT).
DAYS = ["2026-03-04", "2026-03-05", "2026-03-06", "2026-03-09", "2026-03-10", "2026-03-11"]


def synth_raw(days=DAYS, seed=0):
    """1m bid/ask indexed by open time, 18:00-16:15 NY sessions, spread 1.0 RTH / 1.5 overnight."""
    idx = pd.DatetimeIndex([])
    for d in days:
        day = pd.Timestamp(d)
        start = (day - pd.Timedelta(hours=6)).tz_localize(NY)
        end = (day + pd.Timedelta(hours=16, minutes=15)).tz_localize(NY)
        idx = idx.append(pd.date_range(start, end, freq="1min", inclusive="left").tz_convert("UTC"))
    idx.name = "open_time"
    rng = np.random.default_rng(seed)
    close = 20000 + np.cumsum(rng.normal(0, 2, len(idx))).round(2)
    open_ = np.r_[close[0], close[:-1]]
    wick = np.abs(rng.normal(0, 1, (2, len(idx)))).round(2)
    bid = pd.DataFrame({"open": open_, "high": np.maximum(open_, close) + wick[0],
                        "low": np.minimum(open_, close) - wick[1], "close": close}, index=idx)
    m = minute_of_day_ny(idx)
    spread = np.where((m >= 570) & (m < 960), 1.0, 1.5)
    ask = bid + spread[:, None]
    return bid, ask


# --- validation -------------------------------------------------------------

def test_validate_accepts_clean_data():
    validate_raw(*synth_raw())


def _ask_below_bid(bid, ask):
    ask.iloc[100, ask.columns.get_loc("close")] = bid.close.iloc[100] - 1
    return bid, ask


def _duplicate(bid, ask):
    return pd.concat([bid, bid.iloc[[5]]]).sort_index(), pd.concat([ask, ask.iloc[[5]]]).sort_index()


def _bar_in_break(bid, ask):
    t = pd.Timestamp("2026-03-05 16:30", tz=NY).tz_convert("UTC")
    row = bid.iloc[[0]].set_axis(pd.DatetimeIndex([t], name="open_time"))
    return pd.concat([bid, row]).sort_index(), pd.concat([ask, row + 1]).sort_index()


def _misaligned(bid, ask):
    return bid, ask.drop(ask.index[10])


def _bad_high(bid, ask):
    bid.iloc[50, bid.columns.get_loc("high")] = bid.low.iloc[50] - 1
    return bid, ask


@pytest.mark.parametrize("corrupt", [_ask_below_bid, _duplicate, _bar_in_break, _misaligned, _bad_high])
def test_validate_rejects(corrupt):
    with pytest.raises(DataError):
        validate_raw(*corrupt(*synth_raw()), INSTRUMENTS["US100"])


def test_around_the_clock_instruments_have_no_break():
    validate_raw(*_bar_in_break(*synth_raw()), INSTRUMENTS["BTCUSD"])  # crypto quotes through 16:15-18:00
    assert INSTRUMENTS["US500"].daily_break == INSTRUMENTS["US100"].daily_break


# --- close-time indexing and resampling ---------------------------------------

def test_resample_ohlc_and_close_time_labels():
    bid, _ = synth_raw()
    m1 = to_close_time(bid)
    assert m1.index[0] == bid.index[0] + pd.Timedelta("1min")
    m5 = resample(m1, 5)
    assert (m5.index == m5.index.ceil("5min")).all()
    first = m1.iloc[:5]
    assert m5.index[0] == first.index[-1]
    assert m5.iloc[0].to_dict() == {"open": first.open.iloc[0], "high": first.high.max(),
                                    "low": first.low.min(), "close": first.close.iloc[-1]}


@pytest.mark.parametrize("minutes", [5, 15])
def test_resample_creates_no_bars_in_break(minutes):
    bars = resample(to_close_time(synth_raw()[0]), minutes)
    m = minute_of_day_ny(bars.index)
    assert not ((m > 16 * 60 + 15) & (m <= 18 * 60)).any()


@pytest.mark.parametrize("minutes", [5, 15])
def test_resample_truncation(minutes):
    """A higher-TF bar known at time T is identical whether built from data[:T] or all data."""
    m1 = to_close_time(synth_raw()[0])
    m1 = m1.drop(m1.index[np.random.default_rng(1).choice(len(m1), 200, replace=False)])  # holes
    full = resample(m1, minutes)
    for t in np.random.default_rng(2).integers(0, len(m1), 50):
        now = m1.index[t]
        part = resample(m1.iloc[: t + 1], minutes)
        pd.testing.assert_frame_equal(part.loc[:now], full.loc[:now])


# --- trading day and DST --------------------------------------------------------

def test_trading_day_rolls_at_1700_ny_across_dst():
    closes = pd.DatetimeIndex([
        pd.Timestamp("2026-03-06 16:15", tz=NY),  # Fri last bar
        pd.Timestamp("2026-03-08 18:01", tz=NY),  # Sun open, after the DST switch
        pd.Timestamp("2026-03-09 17:00", tz=NY),  # closes exactly on the roll
        pd.Timestamp("2026-03-09 18:01", tz=NY),
    ]).tz_convert("UTC")
    assert list(trading_day(closes).strftime("%Y-%m-%d")) == ["2026-03-06", "2026-03-09", "2026-03-09", "2026-03-10"]
    assert trading_day_start(pd.Timestamp("2026-03-06")) == pd.Timestamp("2026-03-05 22:00", tz="UTC")  # EST
    assert trading_day_start(pd.Timestamp("2026-03-09")) == pd.Timestamp("2026-03-08 21:00", tz="UTC")  # EDT


# --- holdout and spread -------------------------------------------------------------

def test_holdout_is_last_20_percent():
    days = pd.bdate_range("2020-01-01", periods=1000)
    assert holdout_start_day(days) == days[800]


def test_holdout_is_at_least_six_months():
    days = pd.bdate_range("2025-01-01", periods=300)  # 20% = 60 days, under 6 months
    start = holdout_start_day(days)
    assert start <= days[-1] - pd.DateOffset(months=6)
    assert days[days > start][0] > days[-1] - pd.DateOffset(months=6)


def test_holdout_rejects_short_dataset():
    with pytest.raises(DataError):
        holdout_start_day(pd.bdate_range("2026-01-01", periods=60))


def test_spread_schedule_splits_rth_and_overnight():
    bid, ask = synth_raw()
    s = spread_schedule(bid, ask, INSTRUMENTS["US100"])
    assert (s["rth"], s["overnight"]) == (1.0, 1.5)
    assert s["unit"] == "points" and "rth_relative" not in s


def test_crypto_spread_schedule_is_also_a_share_of_price():
    bid, ask = synth_raw()  # prices near 20,000; spread 1.0 RTH / 1.5 overnight
    s = spread_schedule(bid, ask, INSTRUMENTS["BTCUSD"])
    assert s["unit"] == "relative"
    assert 5e-5 <= s["rth_relative"] <= 6e-5  # 1 point at ~20,000 = 0.5bp, rounded up to the next 0.1bp
    assert s["overnight_relative"] > s["rth_relative"]


# --- build + loader: the holdout is sealed by default ---------------------------------

@pytest.fixture
def built(tmp_path, monkeypatch):
    bid, ask = synth_raw()
    for side, df in (("bid", bid), ("ask", ask)):
        d = tmp_path / "raw" / "US100" / side
        d.mkdir(parents=True)
        for month, part in df.groupby(df.index.strftime("%Y-%m")):
            part.to_parquet(d / f"{month}.parquet")
    manifest = build("US100", tmp_path / "raw", tmp_path / "bars", holdout_min_months=0)
    monkeypatch.setenv("BACKTESTER_DATA_DIR", str(tmp_path))
    return manifest


def test_build_writes_manifest(built, tmp_path):
    on_disk = json.loads((tmp_path / "bars" / "US100" / "manifest.json").read_text())
    assert on_disk == built
    assert built["holdout"]["start_trading_day"] == "2026-03-10"  # last ceil(20% of 6) = 2 days
    assert built["holdout"]["holdout_trading_days"] == 2
    assert set(built["timeframes"]) == {"1m", "5m", "15m"}


def test_load_bars_excludes_holdout_by_default(built):
    boundary = io.holdout_boundary("US100")
    for tf in ("1m", "5m", "15m"):
        dev = io.load_bars("US100", tf)
        full = io.load_bars("US100", tf, include_holdout=True)
        assert dev.index.max() <= boundary < full.index.max()
        assert len(full) == built["timeframes"][tf]["rows"]


def test_bars_window_refuses_holdout(built):
    boundary = io.holdout_boundary("US100")
    window = io.bars_window("US100", "5m", boundary - pd.Timedelta("2h"), boundary)
    assert not window.empty
    with pytest.raises(io.HoldoutSealed):
        io.bars_window("US100", "5m", boundary - pd.Timedelta("2h"), boundary + pd.Timedelta("1min"))
    assert not io.bars_window("US100", "5m", boundary, boundary + pd.Timedelta("2h"), include_holdout=True).empty
