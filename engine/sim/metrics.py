"""Summary metrics of a trade log, all in R. Reads only the trade log."""

import numpy as np
import pandas as pd


def metrics(trades: pd.DataFrame) -> dict:
    r = trades["R"].to_numpy(np.float64)
    n = len(r)
    if n == 0:
        return {"n": 0, "expectancy_R": None, "sd_R": None, "se_R": None, "win_rate": None, "profit_factor": None,
                "max_drawdown_R": None, "total_R": 0.0, "cost_share": None, "avg_bars_held": None, "exit_mix": {},
                "mae_R_median": None, "mfe_R_median": None}
    wins, losses = r[r > 0], r[r < 0]
    sd = float(r.std(ddof=1)) if n > 1 else None
    equity = np.cumsum(r)
    drawdown = np.maximum.accumulate(np.r_[0.0, equity]) [1:] - equity
    gross = float(np.abs(r).sum())
    cost = float(trades["cost_R"].sum())
    return {
        "n": int(n),
        "expectancy_R": float(r.mean()),
        "sd_R": sd,
        "se_R": sd / np.sqrt(n) if sd is not None else None,
        "win_rate": float((r > 0).mean()),
        "profit_factor": float(wins.sum() / -losses.sum()) if len(losses) and losses.sum() < 0 else None,
        "max_drawdown_R": float(drawdown.max()),
        "total_R": float(r.sum()),
        "cost_share": cost / (gross + cost) if gross + cost > 0 else None,  # share of gross P&L eaten by costs
        "avg_bars_held": float(trades["bars_held"].mean()),
        "exit_mix": {k: int(v) for k, v in trades["exit_reason"].value_counts().items() if v},
        "mae_R_median": float(trades["mae_R"].median()),
        "mfe_R_median": float(trades["mfe_R"].median()),
    }
