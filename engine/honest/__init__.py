"""The honest core: sealed holdout, trial ledger, random baseline, verdict, stress test.

    ds = load_dev("US100", "5m")                 # holdout cut off; load_exam() is the one-time final exam
    trial = run_trial(spec, ds)                  # interpret + simulate + metrics
    base = baseline(trial, ds)                   # ~1,000 random-entry runs matched to the strategy
    v = verdict(trial, base, n_trials=ledger.count, trial_sharpes=[...])
    s = cost_stress(spec, ds, trial)             # costs x1.5 rerun, not a trial
"""

from engine.honest.baseline import Baseline, baseline
from engine.honest.data import Dataset, load_dev, load_exam
from engine.honest.stress import CostStress, cost_stress
from engine.honest.trial import Ledger, Trial, run_trial
from engine.honest.verdict import Verdict, deflated_sharpe, verdict

__all__ = ["Baseline", "CostStress", "Dataset", "Ledger", "Trial", "Verdict", "baseline", "cost_stress",
           "deflated_sharpe", "load_dev", "load_exam", "run_trial", "verdict"]
