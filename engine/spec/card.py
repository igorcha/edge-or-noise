"""The rules card: plain-English text rendered from a canonical spec by templates.

The LLM never writes this. It's generated from the spec the engine will actually run, so
what the user reads is what gets tested. `render_card` returns the sections and the joined
text; the UI shows the sections next to the editable form.
"""

from engine.blocks.timing import SESSIONS
from engine.spec.checks import canonicalize
from engine.spec.models import (
    All, AnyOf, AtrStop, BlockRef, Compare, Condition, Event, LevelStop, LevelTarget, LimitEntry, Not, PointsStop,
    Price, PriceRef, RTarget, StepRef, Strategy,
)

SESSION_NAMES = {"asia": "Asia", "london": "London", "ny_am": "NY AM", "ny_pm": "NY PM"}
DAY_NAMES = {"mon": "Monday", "tue": "Tuesday", "wed": "Wednesday", "thu": "Thursday", "fri": "Friday"}
TF_NAMES = {"1m": "1-minute", "5m": "5-minute", "15m": "15-minute"}


def render_card(spec: Strategy) -> dict:
    spec = canonicalize(spec)
    steps = {s.name: s.event for s in spec.setup if isinstance(s.event, BlockRef)}  # a cross has no prices
    sections = {
        "direction": f"{spec.direction.capitalize()} {spec.instrument} on {TF_NAMES.get(spec.timeframe, spec.timeframe)} bars.",
        "filters": _filters(spec),
        "setup": _setup(spec),
        "entry": _entry(spec, steps),
        "stop": _stop(spec, steps),
        "target": _target(spec, steps),
        "limits": _limits(spec),
    }
    text = "\n".join(f"{k.capitalize()}: {v}" for k, v in sections.items())
    return {"sections": sections, "text": text}


# --- blocks ---------------------------------------------------------------------------------

def _window(p: dict) -> str:
    w = p["window"]
    if w == "day":
        return "prior day"
    if w == "week":
        return "prior week"
    if w == "custom":
        return f"{p['start']}-{p['end']} NY range"
    return f"{SESSION_NAMES[w]} session"


def describe(ref: BlockRef, output: str | None = None) -> str:
    """A phrase for a block output: an event ("a bullish fair value gap forms") or a price ("the prior day high")."""
    p, out = ref.params, output or ref.output
    match ref.block:
        case "time_window":
            if p["session"] == "custom":
                return f"between {p['start']} and {p['end']} NY"
            a, b = SESSIONS[p["session"]]
            return f"during the {SESSION_NAMES[p['session']]} killzone ({a}-{b} NY)"
        case "day_of_week":
            return "on " + ", ".join(DAY_NAMES[d] for d in p["days"])
        case "window_high_low":
            if out == "updated":
                return f"the {_window(p)} completes"
            return f"the {_window(p)} {out}"
        case "swing":
            kind = "high" if out in ("high", "new_high") else "low"
            if out.startswith("new_"):
                return f"a swing {kind} is confirmed ({p['left']} bars left, {p['right']} right)"
            return f"the last confirmed swing {kind} ({p['left']}/{p['right']})"
        case "fvg":
            if out == "formed":
                size = f", at least {p['min_size_atr']:g} ATR" if p["min_size_atr"] else ""
                d = "" if p["direction"] == "both" else f"{p['direction']} "
                return f"a {d}fair value gap forms{size}"
            return f"the gap's {out}"
        case "sweep":
            if out == "extreme":
                return "the sweep's extreme"
            level = describe(ref.inputs["level"])
            bars = "the same candle" if p["reclaim_bars"] == 1 else f"{p['reclaim_bars']} bars"
            return f"price sweeps {level} and closes back {'above' if p['side'] == 'low' else 'below'} it within {bars}"
        case "displacement":
            d = "" if p["direction"] == "both" else f"{p['direction']} "
            return f"a {d}displacement candle (body >= {p['min_body_atr']:g} ATR, >= {p['min_body_ratio']:.0%} of its range)"
        case "ema":
            return f"the {p['length']} EMA" + ("" if p["source"] == "close" else f" of {p['source']}")
        case "atr":
            return f"ATR({p['length']})"
    params = ", ".join(f"{k}={v}" for k, v in p.items())
    return f"{ref.block}.{out}" + (f" ({params})" if params else "")


def _operand(o) -> str:
    if isinstance(o, Price):
        return f"the {o.price}"
    if isinstance(o, BlockRef):
        return describe(o)
    return f"{o:g}"


OPS = {">": "is above", "<": "is below", ">=": "is at or above", "<=": "is at or below",
       "crosses_above": "crosses above", "crosses_below": "crosses below"}


def _condition(c: Condition) -> str:
    if isinstance(c, Event):
        return describe(c.event)
    if isinstance(c, Compare):
        return f"{_operand(c.left)} {OPS[c.op]} {_operand(c.right)}"
    if isinstance(c, All):
        return " and ".join(_wrap(x) for x in c.all)
    if isinstance(c, AnyOf):
        return " or ".join(_wrap(x) for x in c.any)
    if isinstance(c, Not):
        return f"not {_wrap(c.not_)}"
    raise TypeError(type(c))


def _wrap(c: Condition) -> str:
    text = _condition(c)
    return f"({text})" if isinstance(c, (All, AnyOf)) else text


def _price(ref: PriceRef, steps: dict[str, BlockRef]) -> str:
    if isinstance(ref, StepRef):
        return describe(steps[ref.step], ref.output)
    return describe(ref)


# --- sections -------------------------------------------------------------------------------------

def _filters(spec: Strategy) -> str:
    if not spec.filters:
        return "No filters: any bar can start a setup."
    if len(spec.filters) == 1:
        return "Only " + _condition(spec.filters[0]) + "."
    return "Only " + " and ".join(_wrap(c) for c in spec.filters) + "."


def _setup(spec: Strategy) -> str:
    parts = []
    for i, s in enumerate(spec.setup, 1):
        text = _condition(s.event) if isinstance(s.event, Compare) else describe(s.event)
        if s.within is not None:
            text += f", within {s.within} bar{'s' if s.within != 1 else ''} of the previous step"
        parts.append(f"{i}. {text}")
    return " ".join(parts) + "."


def _entry(spec: Strategy, steps) -> str:
    e = spec.entry
    if isinstance(e, LimitEntry):
        where = _price(e.at, steps)
        if e.offset_pts:
            where = f"{abs(e.offset_pts):g} points {'above' if e.offset_pts > 0 else 'below'} {where}"
        return f"Limit order at {where}; cancelled if not filled within {e.expiry_bars} bars."
    return "Market order at the open of the next bar."


def _stop(spec: Strategy, steps) -> str:
    s = spec.stop
    if isinstance(s, LevelStop):
        side = "below" if spec.direction == "long" else "above"
        prefix = f"{s.buffer_pts:g} points {side} " if s.buffer_pts else "At "
        return f"{prefix}{_price(s.at, steps)}."
    if isinstance(s, AtrStop):
        return f"{s.multiple:g} x ATR({s.length}) from the entry."
    assert isinstance(s, PointsStop)
    return f"{s.points:g} points from the entry."


def _target(spec: Strategy, steps) -> str:
    t = spec.target
    if isinstance(t, RTarget):
        return f"{t.r:g}R (reward {t.r:g} times the risk)."
    assert isinstance(t, LevelTarget)
    return f"{_price(t.at, steps).capitalize()}."


def _limits(spec: Strategy) -> str:
    n = spec.limits.max_trades_per_day
    parts = [f"At most {n} trade{'s' if n != 1 else ''} per trading day, one position at a time"]
    if spec.limits.max_bars_held:
        parts.append(f"exit after {spec.limits.max_bars_held} bars if still open")
    parts.append(f"{spec.slippage_pts:g} points slippage per side")
    return "; ".join(parts) + "."
