"""Read built bars. The holdout is cut here, so callers get dev data unless they opt in."""

import json
import os
from functools import lru_cache
from pathlib import Path

import pandas as pd


class HoldoutSealed(PermissionError):
    pass


def data_dir() -> Path:
    default = Path(__file__).resolve().parents[2] / "data"
    return Path(os.environ.get("BACKTESTER_DATA_DIR", default))


def bars_dir(instrument: str) -> Path:
    return data_dir() / "bars" / instrument


@lru_cache(maxsize=None)
def _read_manifest(path: Path) -> dict:
    return json.loads(path.read_text())


@lru_cache(maxsize=None)
def _read_bars(path: Path) -> pd.DataFrame:
    return pd.read_parquet(path)


def manifest(instrument: str) -> dict:
    return _read_manifest(bars_dir(instrument) / "manifest.json")


def holdout_boundary(instrument: str) -> pd.Timestamp:
    """Bars closing after this instant are the sealed holdout."""
    return pd.Timestamp(manifest(instrument)["holdout"]["boundary_utc"])


def load_bars(instrument: str, tf: str, *, include_holdout: bool = False) -> pd.DataFrame:
    """Bid OHLC indexed by close time. Only the final exam passes include_holdout=True."""
    df = _read_bars(bars_dir(instrument) / f"{tf}.parquet")
    if include_holdout:
        return df.copy(deep=False)
    return df.loc[: holdout_boundary(instrument)]


def bars_window(instrument: str, tf: str, start: pd.Timestamp, end: pd.Timestamp, *,
                include_holdout: bool = False) -> pd.DataFrame:
    """Bars with start <= close_time <= end, e.g. the chart window around one trade."""
    if not include_holdout and end > holdout_boundary(instrument):
        raise HoldoutSealed(f"{instrument} bars after {holdout_boundary(instrument)} are sealed")
    return load_bars(instrument, tf, include_holdout=include_holdout).loc[start:end]
