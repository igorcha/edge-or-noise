"""Checks on the real built dataset. Skipped where data/bars hasn't been built."""

import hashlib

import pandas as pd
import pytest

from engine.data import io
from engine.data.sessions import NY, minute_of_day_ny

INST = "US100"
pytestmark = pytest.mark.skipif(not (io.bars_dir(INST) / "manifest.json").exists(), reason=f"{INST} bars not built")


def test_files_match_frozen_manifest():
    for tf, meta in io.manifest(INST)["timeframes"].items():
        digest = hashlib.sha256((io.bars_dir(INST) / f"{tf}.parquet").read_bytes()).hexdigest()
        assert digest == meta["sha256"], f"{tf}.parquet changed since the build"


@pytest.mark.parametrize("tf", ["1m", "5m", "15m"])
def test_bars_are_clean(tf):
    df = io.load_bars(INST, tf, include_holdout=True)
    assert df.index.is_monotonic_increasing and not df.index.has_duplicates
    assert not df.isna().any().any()
    assert (df.high >= df[["open", "close"]].max(axis=1)).all()
    assert (df.low <= df[["open", "close"]].min(axis=1)).all()
    m = minute_of_day_ny(df.index)
    assert not ((m > 16 * 60 + 15) & (m <= 18 * 60)).any(), "bar closing inside the daily break"


def test_holdout_is_sealed_at_a_trading_day_start():
    boundary = io.holdout_boundary(INST)
    assert boundary.tz_convert(NY).strftime("%H:%M") == "17:00"
    dev = io.load_bars(INST, "1m")
    full = io.load_bars(INST, "1m", include_holdout=True)
    assert dev.index.max() <= boundary
    assert (full.index > boundary).sum() == len(full) - len(dev)
    assert boundary <= full.index.max() - pd.DateOffset(months=6)
