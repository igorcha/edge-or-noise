"""Run a spec end to end on the dev data and print the honest core's output.

    python scripts/run_trial.py --example fvg_long
    python scripts/run_trial.py --spec my_spec.json --runs 300

Needs data/bars/<INST> built. Uses the development data only; the holdout stays sealed.
"""

import argparse
import json
import sys
import time

from engine.honest import Ledger, baseline, cost_stress, load_dev, run_trial, verdict
from engine.sim import warm
from engine.spec import validate_spec
from engine.spec.examples import EXAMPLES


def main() -> int:
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--example", choices=sorted(EXAMPLES))
    g.add_argument("--spec", help="path to a spec JSON file")
    ap.add_argument("--runs", type=int, default=1000, help="baseline runs")
    ap.add_argument("--trades", action="store_true", help="print the trade log")
    args = ap.parse_args()

    data = EXAMPLES[args.example] if args.example else json.load(open(args.spec))
    spec = validate_spec(data)
    ledger = Ledger()
    number, _ = ledger.number_for(spec)

    t0 = time.time()
    warm()
    ds = load_dev(spec.instrument, spec.timeframe, spec.slippage_pts)
    print(f"{spec.instrument} {spec.timeframe}: {len(ds.bars):,} bars to {ds.bars.close_time[-1]}, "
          f"holdout sealed after {ds.boundary} ({time.time() - t0:.1f}s)")

    t0 = time.time()
    trial = run_trial(spec, ds)
    print(f"\ntrial {number} ({trial.spec_hash[:12]}): {trial.n} trades in {time.time() - t0:.2f}s, skipped {trial.skipped}")
    print(json.dumps(trial.metrics, indent=1, default=str))
    if args.trades:
        print(trial.trades.to_string())

    t0 = time.time()
    base = baseline(trial, ds, n_runs=args.runs)
    print(f"\nbaseline: {base.n_runs} runs of {base.n_signals} random entries in {time.time() - t0:.1f}s, "
          f"beaten {base.beaten}, expectancy pct {base.expectancy_pct}")

    v = verdict(trial, base, n_trials=ledger.count, trial_sharpes=[trial.metrics["expectancy_R"] / trial.metrics["sd_R"]]
                if trial.metrics.get("sd_R") else None)
    print(f"\n{v.stage} verdict: {v.label.upper()}")
    for reason in v.reasons:
        print(f"  - {reason}")

    s = cost_stress(spec, ds, trial)
    print(f"\ncosts x{s.factor}: expectancy {s.base_expectancy_R} -> {s.stressed_expectancy_R}, survives: {s.survives}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
