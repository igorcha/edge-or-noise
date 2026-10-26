"""Download Dukascopy 1m bid and ask bars into monthly raw Parquet files.

    python scripts/fetch_data.py --instrument US100 --start 2020-01

Output: data/raw/<INSTRUMENT>/<side>/YYYY-MM.parquet, one file per month and side.
Raw files are immutable: existing files are never overwritten. Delete one to refetch it.
The MVP dataset is frozen (see engine/data/instruments.py), so don't refetch for it.

Raw files keep Dukascopy's labelling: the index is the bar OPEN time in UTC
(`open_time`). Conversion to close-time indexing happens in engine/data/build.py,
never here.
"""

import argparse
import functools
import logging
import sys
import time
import types
from datetime import datetime, timezone

import dukascopy_python as dk
import pandas as pd
import requests

from engine.data.instruments import INSTRUMENTS
from engine.data.io import data_dir

# dukascopy_python calls requests.get with no timeout; a stalled socket would hang forever.
dk.requests = types.SimpleNamespace(get=functools.partial(requests.get, timeout=60))

SIDES = {"bid": dk.OFFER_SIDE_BID, "ask": dk.OFFER_SIDE_ASK}

log = logging.getLogger("fetch_data")


def month_starts(start: pd.Timestamp, end: pd.Timestamp) -> list[pd.Timestamp]:
    return list(pd.date_range(start, end, freq="MS", tz="UTC"))


def fetch_month(symbol: str, side: str, month: pd.Timestamp, now: pd.Timestamp) -> pd.DataFrame:
    nxt = month + pd.offsets.MonthBegin(1)
    stop = min(nxt, now)
    df = dk.fetch(symbol, dk.INTERVAL_MIN_1, SIDES[side], month.to_pydatetime(), stop.to_pydatetime())
    # fetch's end bound is inclusive; keep [month, next month).
    df = df[(df.index >= month) & (df.index < nxt)]
    df = df[~df.index.duplicated(keep="last")].sort_index()
    df.index.name = "open_time"
    return df.astype("float64")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--instrument", default="US100", choices=sorted(INSTRUMENTS))
    ap.add_argument("--start", default="2020-01", help="first month, YYYY-MM")
    ap.add_argument("--end", default=None, help="last month, YYYY-MM (default: current month)")
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", stream=sys.stdout)
    logging.getLogger("DUKASCRIPT").setLevel(logging.WARNING)

    now = pd.Timestamp(datetime.now(timezone.utc)).floor("min")
    current = now.normalize().replace(day=1)
    end = pd.Timestamp(args.end, tz="UTC") if args.end else current
    symbol = INSTRUMENTS[args.instrument].dukascopy_symbol
    root = data_dir() / "raw"

    for month in month_starts(pd.Timestamp(args.start, tz="UTC"), end):
        for side in SIDES:
            out = root / args.instrument / side / f"{month:%Y-%m}.parquet"
            if out.exists():
                continue
            t0 = time.time()
            df = fetch_month(symbol, side, month, now)
            if df.empty:
                log.warning("%s %s %s: no bars", args.instrument, side, f"{month:%Y-%m}")
                continue
            out.parent.mkdir(parents=True, exist_ok=True)
            tmp = out.with_suffix(".tmp")
            df.to_parquet(tmp)
            tmp.replace(out)
            log.info("%s %s %s: %d bars, %s -> %s (%.1fs)", args.instrument, side, f"{month:%Y-%m}",
                     len(df), df.index[0], df.index[-1], time.time() - t0)
    return 0


if __name__ == "__main__":
    sys.exit(main())
