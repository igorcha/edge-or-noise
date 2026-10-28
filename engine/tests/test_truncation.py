"""Lookahead harness: every block, computed on bars[:t+1], must match its full-data values up to t.

Cut points: random bars, both ends, and bars where an output fires or changes (where a
"known too early" bug shows). Runs on real US100 dev bars when built, otherwise on synthetic bars.
"""

import zlib

import numpy as np
import pytest

from engine.blocks import REGISTRY, Bars, compute
from engine.blocks.types import ZONE, Zones
from engine.data import io
from engine.data.build import resample, to_close_time
from engine.tests.test_data import synth_raw


def _day_low(b):
    return {"level": compute("window_high_low", b, {"window": "day"})["low"]}


def _asia_high(b):
    return {"level": compute("window_high_low", b, {"window": "asia"})["high"]}


def _swing_low(b):
    return {"level": compute("swing", b)["low"]}


# (block, params, inputs built from the same bars). Every registered block needs at least one case.
CASES = [
    ("time_window", None, None),
    ("time_window", {"session": "asia"}, None),
    ("time_window", {"session": "custom", "start": "09:30", "end": "10:00"}, None),
    ("day_of_week", {"days": ["tue", "thu"]}, None),
    ("window_high_low", None, None),
    ("window_high_low", {"window": "week"}, None),
    ("window_high_low", {"window": "asia"}, None),
    ("window_high_low", {"window": "london"}, None),
    ("window_high_low", {"window": "custom", "start": "09:30", "end": "10:00"}, None),
    ("swing", None, None),
    ("swing", {"left": 3, "right": 5}, None),
    ("fvg", None, None),
    ("fvg", {"direction": "bullish", "min_size_atr": 0.5, "max_age": 20}, None),
    ("sweep", None, _day_low),
    ("sweep", {"side": "high", "reclaim_bars": 3}, _asia_high),
    ("sweep", None, _swing_low),
    ("displacement", None, None),
    ("displacement", {"direction": "bearish", "min_body_atr": 1.0}, None),
    ("ema", None, None),
    ("ema", {"length": 50, "source": "high"}, None),
    ("atr", None, None),
]

SLICES = {"5m": ("2023-01-01", "2023-04-01"), "15m": ("2022-07-01", "2023-04-01")}
N_POINTS = 30


@pytest.fixture(scope="module", params=sorted(SLICES))
def bars(request):
    tf = request.param
    if (io.bars_dir("US100") / "manifest.json").exists():
        df = io.load_bars("US100", tf).loc[SLICES[tf][0]: SLICES[tf][1]]
    else:
        df = resample(to_close_time(synth_raw()[0]), int(tf[:-1]))
    return Bars.from_frame(df, int(tf[:-1]), 0.01)


def test_every_block_has_a_case():
    assert {c[0] for c in CASES} == set(REGISTRY)
    assert all(c[2] for c in CASES if REGISTRY[c[0]].inputs), "blocks with inputs need an inputs builder"


def _change_points(out: dict) -> np.ndarray:
    """Bars where any output fires or changes value. Lookahead that stamps something too early
    shows up exactly when the data is cut at such a bar, so these get cut points of their own."""
    idx = []
    for v in out.values():
        if isinstance(v, Zones):
            idx += [v.created, v.ended[v.ended >= 0]]
        elif v.dtype == bool:
            idx.append(np.flatnonzero(v))
        else:
            same = (v[1:] == v[:-1]) | (np.isnan(v[1:]) & np.isnan(v[:-1]))
            idx.append(np.flatnonzero(~same) + 1)
    return np.unique(np.concatenate(idx)) if idx else np.array([], int)


def _assert_zones_match(part, full, t):
    known = full.created <= t
    assert part.created.tolist() == full.created[known].tolist()
    np.testing.assert_array_equal(part.top, full.top[known])
    np.testing.assert_array_equal(part.bottom, full.bottom[known])
    np.testing.assert_array_equal(part.direction, full.direction[known])
    full_ended = full.ended[known]
    expected = np.where((full_ended >= 0) & (full_ended <= t), full_ended, -1)
    np.testing.assert_array_equal(part.ended, expected)


@pytest.mark.parametrize("name,params,inputs", CASES, ids=[f"{c[0]}-{i}" for i, c in enumerate(CASES)])
def test_truncation(bars, name, params, inputs):
    def run(b):
        return compute(name, b, params, **(inputs(b) if inputs else {}))

    full = run(bars)
    rng = np.random.default_rng(zlib.crc32(f"{name}{params}".encode()))
    changes = _change_points(full)
    points = np.r_[rng.integers(0, len(bars), N_POINTS), [0, len(bars) - 1],
                   rng.choice(changes, min(N_POINTS, len(changes)), replace=False)]
    for t in points:
        part = run(bars.head(t + 1))
        for out, kind in REGISTRY[name].outputs.items():
            if kind == ZONE:
                _assert_zones_match(part[out], full[out], t)
            else:
                np.testing.assert_array_equal(part[out], full[out][: t + 1], err_msg=f"{name}.{out} at t={t}")
