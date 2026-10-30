"""Build engine-ready bars from raw Dukascopy files.

raw/<INST>/{bid,ask}/YYYY-MM.parquet  (1m, indexed by bar OPEN time, never modified)
    -> validate -> bid OHLC indexed by bar CLOSE time -> resample
    -> bars/<INST>/{1m,5m,15m}.parquet + manifest.json

The bars hold bid prices only. Costs come from the spread schedule in the
manifest, applied by the simulator, so dev and holdout are charged the same.
"""

import hashlib
import json
import math
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from engine.data.instruments import DATA_END, DATA_START, INSTRUMENTS, TIMEFRAMES, Instrument
from engine.data.sessions import minute_of_day_ny, trading_day, trading_day_start

OHLC = ["open", "high", "low", "close"]


class DataError(ValueError):
    pass


def read_raw(raw_dir: Path, start: pd.Timestamp, end: pd.Timestamp) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Concatenate monthly raw files for both sides, keeping bars that open in [start, end)."""
    sides = []
    for side in ("bid", "ask"):
        files = sorted((raw_dir / side).glob("*.parquet"))
        if not files:
            raise DataError(f"no raw {side} files in {raw_dir / side}")
        df = pd.concat([pd.read_parquet(f, columns=OHLC) for f in files])
        sides.append(df[(df.index >= start) & (df.index < end)])
    return sides[0], sides[1]


def validate_raw(bid: pd.DataFrame, ask: pd.DataFrame, inst: Instrument | None = None) -> None:
    """Refuse raw data the engine can't trust. `inst` brings the instrument's daily break (US100's
    16:15-18:00 NY halt), in which no bar may open; crypto has none."""
    problems = []
    for name, d in (("bid", bid), ("ask", ask)):
        if d.empty:
            problems.append(f"{name}: no bars")
            continue
        if not d.index.is_monotonic_increasing or d.index.has_duplicates:
            problems.append(f"{name}: index not strictly increasing")
        if (d.index != d.index.floor("min")).any():
            problems.append(f"{name}: timestamps not on the minute")
        if d.isna().any().any():
            problems.append(f"{name}: NaN prices")
        body_hi = d[["open", "close"]].max(axis=1)
        body_lo = d[["open", "close"]].min(axis=1)
        if ((d.high < body_hi) | (d.low > body_lo)).any():
            problems.append(f"{name}: high/low outside open/close")
    if not bid.index.equals(ask.index):
        problems.append("bid and ask timestamps differ")
    elif ((ask.open < bid.open) | (ask.close < bid.close)).any():
        problems.append("ask below bid")
    brk = inst.daily_break if inst else None
    if brk:
        m = minute_of_day_ny(bid.index)
        lo, hi = (t.hour * 60 + t.minute for t in brk)
        in_break = (m >= lo) & (m < hi)
        if in_break.any():
            problems.append(f"{int(in_break.sum())} bars inside the {brk[0]:%H:%M}-{brk[1]:%H:%M} NY break")
    if problems:
        raise DataError("; ".join(problems))


def to_close_time(bars: pd.DataFrame, minutes: int = 1) -> pd.DataFrame:
    out = bars.copy()
    out.index = out.index + pd.Timedelta(minutes=minutes)
    out.index.name = "close_time"
    return out


def resample(bars_1m: pd.DataFrame, minutes: int) -> pd.DataFrame:
    """Aggregate close-time-indexed 1m bars into `minutes` bars labelled by close time.

    A 1m bar closing at c falls in the bin closing at ceil(c). The label is the
    nominal close, never earlier than the last 1m bar in the bin, so it can't leak.
    Bins with no 1m bars (breaks, holes) are not created.
    """
    if minutes == 1:
        return bars_1m
    key = bars_1m.index.ceil(f"{minutes}min").rename("close_time")
    return bars_1m.groupby(key).agg(
        open=("open", "first"), high=("high", "max"), low=("low", "min"), close=("close", "last")
    )


def spread_schedule(bid: pd.DataFrame, ask: pd.DataFrame, inst: Instrument, months: int = 6) -> dict:
    """Mean quoted spread (ask - bid at bar close) over the last `months` of data, split RTH / overnight.

    Rounded up to the price step. Applied to all history: it reflects what trading
    costs now, and dev and holdout get charged the same.
    """
    end = bid.index[-1] + pd.Timedelta(minutes=1)
    start = end - pd.DateOffset(months=months)
    sel = (bid.index >= start) & (bid.index < end)
    spread = (ask.close - bid.close)[sel]
    m = minute_of_day_ny(spread.index)
    lo, hi = (t.hour * 60 + t.minute for t in inst.rth)
    rth = (m >= lo) & (m < hi)
    if rth.all() or not rth.any():
        raise DataError("spread window needs both RTH and overnight bars")

    def up(x: float) -> float:
        return round(math.ceil(x / inst.price_step - 1e-6) * inst.price_step, 6)

    out = {
        "rth": up(spread[rth].mean()),
        "overnight": up(spread[~rth].mean()),
        "rth_window_ny": [inst.rth[0].strftime("%H:%M"), inst.rth[1].strftime("%H:%M")],
        "derived_from": [start.isoformat(), end.isoformat()],
        "stat": "mean ask-bid at bar close, rounded up to price step",
        "unit": inst.spread_unit,
    }
    if inst.spread_unit == "relative":  # what's charged: the same means as a share of the bid, rounded up to 0.1bp
        rel = spread / bid.close[sel]
        out["rth_relative"] = math.ceil(rel[rth].mean() * 1e5 - 1e-9) / 1e5
        out["overnight_relative"] = math.ceil(rel[~rth].mean() * 1e5 - 1e-9) / 1e5
        out["stat_relative"] = "mean (ask-bid)/bid at bar close, rounded up to 0.1 basis point; charged times the price"
    return out


def holdout_start_day(days: pd.DatetimeIndex, fraction: float = 0.2, min_months: int = 6) -> pd.Timestamp:
    """First trading day of the holdout: the last `fraction` of trading days, at least `min_months` long."""
    start = days[-math.ceil(fraction * len(days))]
    latest_allowed = days[-1] - pd.DateOffset(months=min_months)
    if start > latest_allowed:
        candidates = days[days <= latest_allowed]
        if candidates.empty:
            raise DataError(f"dataset too short for a {min_months}-month holdout")
        start = candidates[-1]
    return start


def _write_parquet(df: pd.DataFrame, path: Path) -> str:
    tmp = path.with_suffix(".tmp")
    df.to_parquet(tmp)
    tmp.replace(path)
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build(instrument: str, raw_root: Path, bars_root: Path, *, start: pd.Timestamp = DATA_START,
          end: pd.Timestamp = DATA_END, holdout_fraction: float = 0.2, holdout_min_months: int = 6) -> dict:
    inst = INSTRUMENTS[instrument]
    bid, ask = read_raw(raw_root / instrument, start, end)
    validate_raw(bid, ask, inst)

    bars_1m = to_close_time(bid[OHLC])
    days = trading_day(bars_1m.index).unique()
    hold_day = holdout_start_day(days, holdout_fraction, holdout_min_months)
    boundary = trading_day_start(hold_day)

    out = bars_root / instrument
    out.mkdir(parents=True, exist_ok=True)
    files = {}
    for tf, minutes in TIMEFRAMES.items():
        bars = resample(bars_1m, minutes).astype(np.float64)
        files[tf] = {
            "rows": len(bars),
            "first_close": bars.index[0].isoformat(),
            "last_close": bars.index[-1].isoformat(),
            "sha256": _write_parquet(bars, out / f"{tf}.parquet"),
        }

    manifest = {
        "instrument": instrument,
        "source": f"Dukascopy {inst.dukascopy_symbol} 1m bid+ask",
        "prices": "bid",
        "index": "close_time, UTC, bar close",
        "data_start": start.isoformat(),
        "data_end_exclusive": end.isoformat(),
        "holdout": {
            "fraction": holdout_fraction,
            "min_months": holdout_min_months,
            "start_trading_day": hold_day.date().isoformat(),
            "boundary_utc": boundary.isoformat(),
            "rule": "bars with close_time > boundary_utc are sealed",
            "dev_trading_days": int((days < hold_day).sum()),
            "holdout_trading_days": int((days >= hold_day).sum()),
        },
        "spread_pts": spread_schedule(bid, ask, inst),
        "timeframes": files,
        "built_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    tmp = out / "manifest.json.tmp"
    tmp.write_text(json.dumps(manifest, indent=2) + "\n")
    tmp.replace(out / "manifest.json")
    return manifest
