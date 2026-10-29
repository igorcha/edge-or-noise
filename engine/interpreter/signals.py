"""Spec -> signal arrays.

`interpret(spec, bars)` evaluates the spec's blocks on the bars, finds every bar where the
setup sequence completes with the filters true, and works out that signal's entry, stop
and target. It doesn't simulate anything: the simulator takes `Signals` and scans each one
forward from the bar after the signal (one position at a time, daily cap, fills, exits).

Lookahead: every block output is causal, and a signal at bar t only reads outputs at bars
<= t (step geometry is read at the step's own bar, which is <= t). The simulator acts from
t + 1. Prices that aren't known until the fill (ATR/points stops, R-multiple targets) are
expressed as distances / multiples and resolved by the simulator at fill time, which also
skips (and counts) fills where an absolute stop or target ends up on the wrong side.

Unknown values (NaN, e.g. an EMA still warming up) make a comparison neither true nor false,
so they never pass a filter, not even under `not`.
"""

import json
from dataclasses import dataclass, field

import numpy as np

from engine.blocks import Bars, compute
from engine.blocks.registry import resolve
from engine.blocks.series import atr_values
from engine.blocks.types import LEVEL
from engine.data.instruments import TIMEFRAMES
from engine.spec.checks import canonicalize
from engine.spec.models import (
    CROSS_OPS, All, AnyOf, AtrStop, BlockRef, Compare, Condition, Event, LevelStop, LevelTarget, LimitEntry, Not, Price,
    PointsStop, PriceRef, RTarget, StepRef, Strategy,
)


@dataclass(frozen=True)
class Signals:
    """One row per signal. Prices are NaN where they're only known at fill time.

    The simulator computes `stop = fill -/+ stop_distance` and `target = fill +/- target_r * risk`
    for the relative forms; absolute forms are used as is.
    """
    bar: np.ndarray  # signal bar index: the decision bar; act from bar + 1
    direction: int  # +1 long, -1 short
    entry_kind: str  # "market" | "limit"
    entry_price: np.ndarray  # limit price, NaN for market
    expiry_bars: int  # limit only: cancel if unfilled this many bars after the signal
    stop_price: np.ndarray  # absolute stop, NaN when relative
    stop_distance: np.ndarray  # points from the fill, NaN when absolute
    target_price: np.ndarray  # absolute target, NaN when relative
    target_r: float  # R multiple, NaN when absolute
    max_bars_held: int  # 0 = no time stop
    max_trades_per_day: int
    slippage_pts: float  # per side, market fills only
    price_step: float
    steps: dict[str, np.ndarray] = field(default_factory=dict)  # step name -> bar it fired on
    geometry: dict[str, np.ndarray] = field(default_factory=dict)  # "gap.top" -> level at that step's bar
    dropped: dict[str, int] = field(default_factory=dict)  # why candidate signals were discarded

    def __len__(self) -> int:
        return len(self.bar)

    def take(self, mask: np.ndarray) -> "Signals":
        arrays = {k: v[mask] for k, v in self.__dict__.items() if isinstance(v, np.ndarray)}
        return Signals(**{**self.__dict__, **arrays,
                          "steps": {k: v[mask] for k, v in self.steps.items()},
                          "geometry": {k: v[mask] for k, v in self.geometry.items()}})


def interpret(spec: Strategy, bars: Bars) -> Signals:
    spec = canonicalize(spec)
    if bars.tf_minutes != TIMEFRAMES[spec.timeframe]:
        raise ValueError(f"the spec is {spec.timeframe} but the bars are {bars.tf_minutes}m")
    ev = _Evaluator(bars)
    sign = 1 if spec.direction == "long" else -1
    dropped: dict[str, int] = {}

    # Setup sequence -> candidate signal bars and the bar each step fired on.
    step_events = [ev.event(s.event) for s in spec.setup]
    step_bars = chain([s.within for s in spec.setup], step_events)
    signal = step_bars[-1]
    mask = np.ones(len(signal), bool)
    for c in spec.filters:
        mask &= ev.condition(c)[signal]
    _drop(dropped, "filters", mask)
    signal, step_bars = signal[mask], [b[mask] for b in step_bars]
    steps = {s.name: b for s, b in zip(spec.setup, step_bars)}
    geometry = {f"{s.name}.{out}": ev.output(s.event, out)[b]
                for s, b in zip(spec.setup, step_bars) if isinstance(s.event, BlockRef)  # a cross has no levels
                for out, kind in resolve(s.event.block).outputs.items() if kind == LEVEL}

    def price(ref: PriceRef) -> np.ndarray:
        if isinstance(ref, StepRef):
            return ev.output(_step_block(spec, ref.step), ref.output)[steps[ref.step]]
        return ev.array(ref)[signal]

    n = len(signal)
    nan = np.full(n, np.nan)
    entry_price, expiry = nan, 0
    if isinstance(spec.entry, LimitEntry):
        entry_price = price(spec.entry.at) + spec.entry.offset_pts
        expiry = spec.entry.expiry_bars

    stop_price, stop_distance = nan, nan
    if isinstance(spec.stop, LevelStop):
        stop_price = price(spec.stop.at) - sign * spec.stop.buffer_pts
    elif isinstance(spec.stop, AtrStop):
        stop_distance = spec.stop.multiple * atr_values(bars, spec.stop.length)[signal]
    elif isinstance(spec.stop, PointsStop):
        stop_distance = np.full(n, spec.stop.points)

    target_price, target_r = nan, np.nan
    if isinstance(spec.target, LevelTarget):
        target_price = price(spec.target.at)
    elif isinstance(spec.target, RTarget):
        target_r = spec.target.r

    # Discard signals whose known prices are missing or on the wrong side. Relative stops and
    # targets are checked against the fill by the simulator; here only what is known now.
    limit = isinstance(spec.entry, LimitEntry)
    checks: list[tuple[str, np.ndarray]] = []
    if limit:
        checks.append(("entry price unknown", ~np.isnan(entry_price)))
    if isinstance(spec.stop, LevelStop):
        checks.append(("stop price unknown", ~np.isnan(stop_price)))
        if limit:
            checks.append(("stop not beyond entry", (entry_price - stop_price) * sign > 0))
    else:
        checks.append(("stop distance unknown", stop_distance > 0))  # NaN > 0 is False
    if isinstance(spec.target, LevelTarget):
        checks.append(("target price unknown", ~np.isnan(target_price)))
        if limit:
            checks.append(("target not beyond entry", (target_price - entry_price) * sign > 0))
    keep = np.ones(n, bool)
    with np.errstate(invalid="ignore"):
        for reason, ok in checks:
            _drop(dropped, reason, ok | ~keep)  # count each signal once, under the first failing check
            keep &= ok

    out = Signals(
        bar=signal, direction=sign, entry_kind=spec.entry.type, entry_price=entry_price, expiry_bars=expiry,
        stop_price=stop_price, stop_distance=stop_distance, target_price=target_price, target_r=target_r,
        max_bars_held=spec.limits.max_bars_held or 0, max_trades_per_day=spec.limits.max_trades_per_day,
        slippage_pts=spec.slippage_pts, price_step=bars.price_step, steps=steps, geometry=geometry, dropped=dropped,
    )
    return out.take(keep) if not keep.all() else out


def filter_mask(spec: Strategy, bars: Bars) -> np.ndarray:
    """Bars where all the spec's filters hold (True everywhere without filters).

    The random baseline draws its entry bars from here, so it trades the same sessions and
    conditions as the strategy and differs only in the setup.
    """
    spec = canonicalize(spec)
    ev = _Evaluator(bars)
    mask = np.ones(len(bars), bool)
    for c in spec.filters:
        mask &= ev.condition(c)
    return mask


def chain(within: list[int | None], events: list[np.ndarray]) -> list[np.ndarray]:
    """Bars where the sequence completes, as one array per step.

    Step k completes at bar t if its event fires at t and step k-1 completed at some s with
    1 <= t - s <= within[k]; the most recent such s is used. Returns, for each completion of
    the last step, the bar of every step (the last array is the signal bars themselves).
    """
    n = len(events[0])
    idx = np.arange(n)
    done = events[0].copy()
    prev_of = [np.full(n, -1)]  # for each step k > 0: completion bar of step k-1 chained from, per bar
    for k in range(1, len(events)):
        last_done = _ffill_index(done)  # most recent completion of step k-1 at or before each bar
        last_before = np.r_[-1, last_done[:-1]]  # strictly before
        ok = events[k] & (last_before >= 0) & (idx - last_before <= within[k])
        done = ok
        prev_of.append(np.where(ok, last_before, -1))
    signal = np.flatnonzero(done)
    bars = [signal]
    for k in range(len(events) - 1, 0, -1):
        bars.append(prev_of[k][bars[-1]])
    return bars[::-1]


def _ffill_index(flags: np.ndarray) -> np.ndarray:
    return np.maximum.accumulate(np.where(flags, np.arange(len(flags)), -1))


def _step_block(spec: Strategy, name: str) -> BlockRef:
    event = next(s.event for s in spec.setup if s.name == name)
    assert isinstance(event, BlockRef), "checks.py refuses step refs to a cross"
    return event


def _drop(dropped: dict[str, int], reason: str, keep: np.ndarray) -> None:
    if not keep.all():
        dropped[reason] = dropped.get(reason, 0) + int((~keep).sum())


class _Evaluator:
    """Computes block outputs on demand and caches them by the block reference's canonical JSON."""

    def __init__(self, bars: Bars):
        self.bars = bars
        self._cache: dict[str, dict] = {}

    def outputs(self, ref: BlockRef) -> dict:
        key = json.dumps(ref.model_dump(mode="json"), sort_keys=True)
        if key not in self._cache:
            inputs = {k: self.array(v) for k, v in ref.inputs.items()}
            self._cache[key] = compute(ref.block, self.bars, ref.params, **inputs)
        return self._cache[key]

    def output(self, ref: BlockRef, name: str) -> np.ndarray:
        return self.outputs(ref)[name]

    def array(self, ref: BlockRef) -> np.ndarray:
        return self.output(ref, ref.output)

    def operand(self, o: float | Price | BlockRef) -> np.ndarray | float:
        if isinstance(o, Price):
            return getattr(self.bars, o.price)
        if isinstance(o, BlockRef):
            return self.array(o)
        return o

    def event(self, e: BlockRef | Compare) -> np.ndarray:
        """A setup step's event: a block's event output, or a cross."""
        return self.compare(e)[0] if isinstance(e, Compare) else self.array(e)

    def compare(self, c: Compare) -> tuple[np.ndarray, np.ndarray]:
        """(known true, known false) per bar; both are False where a side is NaN."""
        n = len(self.bars)
        left, right = self.operand(c.left), self.operand(c.right)
        known = np.broadcast_to(~(np.isnan(left) | np.isnan(right)), n)
        with np.errstate(invalid="ignore"):
            if c.op in CROSS_OPS:
                # beyond on this bar and not beyond on the previous (closed) bar, both known
                gt, le = (np.greater, np.less_equal) if c.op == "crosses_above" else (np.less, np.greater_equal)
                now, before = np.broadcast_to(gt(left, right), n), np.broadcast_to(le(left, right), n)
                crossed, both_known = np.zeros(n, bool), np.zeros(n, bool)
                crossed[1:] = now[1:] & before[:-1]
                both_known[1:] = known[1:] & known[:-1]
                return crossed, both_known & ~crossed
            ops = {">": (np.greater, np.less_equal), "<": (np.less, np.greater_equal),
                   ">=": (np.greater_equal, np.less), "<=": (np.less_equal, np.greater)}[c.op]
            return np.broadcast_to(ops[0](left, right), n), np.broadcast_to(ops[1](left, right), n)

    def condition(self, c: Condition) -> np.ndarray:
        """Bars where the condition is known to hold."""
        return self._truth(c)[0]

    def _truth(self, c: Condition) -> tuple[np.ndarray, np.ndarray]:
        """(known true, known false) per bar. Unknown is neither, so `not` can't turn it into a pass."""
        if isinstance(c, Event):
            e = self.array(c.event)
            return e, ~e
        if isinstance(c, Compare):
            return self.compare(c)
        if isinstance(c, (All, AnyOf)):
            parts = [self._truth(x) for x in (c.all if isinstance(c, All) else c.any)]
            trues, falses = [p[0] for p in parts], [p[1] for p in parts]
            if isinstance(c, All):
                return np.logical_and.reduce(trues), np.logical_or.reduce(falses)
            return np.logical_or.reduce(trues), np.logical_and.reduce(falses)
        if isinstance(c, Not):
            true, false = self._truth(c.not_)
            return false, true
        raise TypeError(f"unknown condition {type(c).__name__}")
