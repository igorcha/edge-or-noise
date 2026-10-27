"""Small causal array helpers shared by blocks."""

import numpy as np
import pandas as pd


def ffill(a: np.ndarray) -> np.ndarray:
    """Carry the last non-NaN value forward."""
    valid = ~np.isnan(a)
    pos = np.maximum.accumulate(np.where(valid, np.arange(len(a)), -1))
    out = np.full_like(a, np.nan)
    has = pos >= 0
    out[has] = a[pos[has]]
    return out


def lag(a: np.ndarray, n: int = 1) -> np.ndarray:
    """Value from n bars ago (NaN / False for the first n bars)."""
    fill = False if a.dtype == bool else np.nan
    out = np.full_like(a, fill)
    if n < len(a):
        out[n:] = a[: len(a) - n]
    return out


def rolling_max(a: np.ndarray, n: int) -> np.ndarray:
    """Max over the last n bars including this one (partial windows at the start)."""
    return pd.Series(a).rolling(n, min_periods=1).max().to_numpy()


def rolling_min(a: np.ndarray, n: int) -> np.ndarray:
    return pd.Series(a).rolling(n, min_periods=1).min().to_numpy()
