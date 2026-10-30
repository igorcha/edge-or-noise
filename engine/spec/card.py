"""The rules card: plain-English text rendered from a canonical spec by templates.

The LLM never writes this. It's generated from the spec the engine will actually run, so
what the user reads is what gets tested. `render_card` returns the sections and the joined
text; the UI shows the sections next to the editable form.
"""

from engine.blocks.timing import SESSIONS
from engine.spec.checks import canonicalize
from engine.spec.models import (
    All, AnyOf, AtrStop, BlockRef, Compare, Condition, Event, LevelStop, LevelTarget, LimitEntry, Not, PointsStop,
    CANDLE_PRICES, Price, PriceRef, RTarget, StepRef, Strategy,
)

SESSION_NAMES = {"asia": "Asia", "london": "London", "ny_am": "NY AM", "ny_pm": "NY PM"}
DAY_NAMES = {"mon": "Monday", "tue": "Tuesday", "wed": "Wednesday", "thu": "Thursday", "fri": "Friday"}
TF_NAMES = {"1m": "1-minute", "5m": "5-minute", "15m": "15-minute"}


def render_card(spec: Strategy) -> dict:
    spec = canonicalize(spec)
    steps = {s.name: s.event for s in spec.setup}  # in order: the last one is the signal
    sections = {
        "direction": f"{_cap(spec.direction)} {spec.instrument} on {TF_NAMES.get(spec.timeframe, spec.timeframe)} bars.",
        "filters": _filters(spec),
        "setup": _setup(spec),
        "entry": _entry(spec, steps),
        "stop": _stop(spec, steps),
        "target": _target(spec, steps),
        "limits": _limits(spec),
    }
    text = "\n".join(f"{_cap(k)}: {v}" for k, v in sections.items())
    return {"sections": sections, "text": text}


def _cap(s: str) -> str:
    """First letter up, the rest untouched (str.capitalize would turn "NY AM" into "ny am")."""
    return s[:1].upper() + s[1:]


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


def _pts(v: float) -> str:
    return f"{v:g} point" + ("" if v == 1 else "s")


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
            days = [DAY_NAMES[d] for d in p["days"]]
            return "on " + (days[0] if len(days) == 1 else ", ".join(days[:-1]) + " or " + days[-1])
        case "window_high_low":
            if out == "updated":
                return f"the {_window(p)} completes"
            return f"the {_window(p)} {out}"
        case "swing":
            kind = "high" if out.endswith("high") else "low"
            size = (f"{p['left']} bars each side" if p["left"] == p["right"]
                    else f"{p['left']} bars left, {p['right']} right")
            if out.startswith("new_"):
                return f"a swing {kind} is confirmed ({size})"
            if out != kind:
                than = "above" if out.startswith("higher") else "below"
                return f"a {out.replace('_', ' ')} is confirmed: a swing {kind} {than} the previous one ({size})"
            return f"the last confirmed swing {kind} ({size})"
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


def _price(ref: PriceRef, steps: dict) -> str:
    if isinstance(ref, StepRef) and ref.output in CANDLE_PRICES:
        names = list(steps)
        candle = "the signal candle" if ref.step == names[-1] else f"the candle of setup step {names.index(ref.step) + 1}"
        return f"the {CANDLE_PRICES[ref.output]} of {candle}"
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
        text = _cap(_condition(s.event) if isinstance(s.event, Compare) else describe(s.event))
        if s.within is not None:
            text += f", within {s.within} bar{'s' if s.within != 1 else ''} of the previous step"
        parts.append(f"{i}. {text}")
    return " ".join(parts) + "."


def _entry(spec: Strategy, steps) -> str:
    e = spec.entry
    if isinstance(e, LimitEntry):
        where = _price(e.at, steps)
        if e.offset_pts:
            where = f"{_pts(abs(e.offset_pts))} {'above' if e.offset_pts > 0 else 'below'} {where}"
        return f"Limit order at {where}; cancelled if not filled within {e.expiry_bars} bars."
    return "Market order at the open of the next bar."


def _stop(spec: Strategy, steps) -> str:
    s = spec.stop
    if isinstance(s, LevelStop):
        side = "below" if spec.direction == "long" else "above"
        prefix = f"{_pts(s.buffer_pts)} {side} " if s.buffer_pts else "At "
        return f"{prefix}{_price(s.at, steps)}."
    if isinstance(s, AtrStop):
        return f"{s.multiple:g} x ATR({s.length}) from the entry."
    assert isinstance(s, PointsStop)
    return f"{_pts(s.points)} from the entry."


def _target(spec: Strategy, steps) -> str:
    t = spec.target
    if isinstance(t, RTarget):
        return f"{t.r:g}R (reward {t.r:g} times the risk)."
    assert isinstance(t, LevelTarget)
    return f"{_cap(_price(t.at, steps))}."


def _limits(spec: Strategy) -> str:
    n = spec.limits.max_trades_per_day
    parts = [f"At most {n} trade{'s' if n != 1 else ''} per trading day, one position at a time"]
    if spec.limits.max_bars_held:
        parts.append(f"exit after {spec.limits.max_bars_held} bars if still open")
    parts.append(f"{_pts(spec.slippage_pts)} slippage per side")
    return "; ".join(parts) + "."
