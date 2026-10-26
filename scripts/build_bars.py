"""Build engine bars from the frozen raw dataset.

    python scripts/build_bars.py --instrument US100

Reads data/raw/<INST>/, writes data/bars/<INST>/{1m,5m,15m}.parquet and manifest.json.
Raw files are only read. Validation runs first; nothing is written if it fails.
"""

import argparse
import json
import sys
import time

from engine.data.build import build
from engine.data.instruments import INSTRUMENTS
from engine.data.io import data_dir


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--instrument", default="US100", choices=sorted(INSTRUMENTS))
    args = ap.parse_args()

    t0 = time.time()
    manifest = build(args.instrument, data_dir() / "raw", data_dir() / "bars")
    print(json.dumps({k: manifest[k] for k in ("holdout", "spread_pts", "timeframes")}, indent=2))
    print(f"built {args.instrument} in {time.time() - t0:.1f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
