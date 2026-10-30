"""The golden eval: trader prompts with the result they must parse to. Score every prompt change on it.

    .venv/bin/python scripts/eval_llm.py            # table + score, live model
    .venv/bin/python -m pytest -m network           # the same cases as tests

A "ready" case passes only on an exact canonical match with one of its accepted specs (two specs that
mean the same, like "not Friday" and "Monday to Thursday", are both listed). "questions" and
"unsupported" cases pass when the parser stops instead of running something the trader didn't say.
None of the prompts are few-shots (fewshots.py), so this measures the model, not recall.
"""

from dataclasses import dataclass, field

from engine.llm.parse import QUESTIONS, READY, UNSUPPORTED_STATUS, ParseResult
from engine.spec import canonical_dict, validate_spec

NY_AM = {"event": {"block": "time_window", "params": {"session": "ny_am"}}}
NY_PM = {"event": {"block": "time_window", "params": {"session": "ny_pm"}}}
LONDON = {"event": {"block": "time_window", "params": {"session": "london"}}}


def _window(window: str, output: str, **params) -> dict:
    return {"block": "window_high_low", "params": {"window": window, **params}, "output": output}


def _sweep(side: str, level: dict) -> dict:
    return {"block": "sweep", "params": {"side": side}, "inputs": {"level": level}, "output": "value"}


@dataclass(frozen=True)
class Case:
    name: str
    timeframe: str
    prompt: str
    status: tuple[str, ...]  # accepted statuses
    specs: tuple[dict, ...] = ()  # accepted specs for a ready result (without instrument/timeframe)
    unsupported: tuple[str, ...] = ()  # concepts that must be listed
    note: str = field(default="", compare=False)


CASES = [
    Case("fvg_after_london_sweep", "5m",
         "Long setup: in the NY AM killzone price sweeps the London low, then a bullish FVG forms within 8 candles. "
         "Limit order at the top of the gap, cancel after 12 candles. Stop at the bottom of the gap, target 3R, "
         "max 2 trades a day.",
         (READY,),
         ({"direction": "long", "filters": [NY_AM],
           "setup": [{"name": "sweep", "event": _sweep("low", _window("london", "low"))},
                     {"name": "gap", "within": 8,
                      "event": {"block": "fvg", "params": {"direction": "bullish"}, "output": "formed"}}],
           "entry": {"type": "limit", "at": {"ref": "gap.top"}, "expiry_bars": 12},
           "stop": {"type": "level", "at": {"ref": "gap.bottom"}},
           "target": {"type": "r_multiple", "r": 3},
           "limits": {"max_trades_per_day": 2}},)),
    Case("short_pdh_sweep", "5m",
         "Short the sweep of the previous day's high in the NY AM session at market. Stop 5 points above the high "
         "of the sweep, target the previous day low.",
         (READY,),
         ({"direction": "short", "filters": [NY_AM],
           "setup": [{"name": "sweep", "event": _sweep("high", _window("day", "high"))}],
           "entry": {"type": "market"},
           "stop": {"type": "level", "at": {"ref": "sweep.extreme"}, "buffer_pts": 5},
           "target": {"type": "level", "at": _window("day", "low")}},)),
    Case("ema_cross_points_target", "15m",
         "Buy when the 20 EMA crosses above the 50 EMA during the London killzone. Enter at market, 25 point stop, "
         "50 point target.",
         (READY,),
         ({"direction": "long", "filters": [LONDON],
           "setup": [{"name": "cross", "event": {"left": {"block": "ema", "params": {"length": 20}},
                                                 "op": "crosses_above",
                                                 "right": {"block": "ema", "params": {"length": 50}}}}],
           "entry": {"type": "market"}, "stop": {"type": "points", "points": 25},
           "target": {"type": "r_multiple", "r": 2}},)),
    Case("displacement_days_time_stop", "5m",
         "On Mondays and Wednesdays during the NY PM session, short at the next open after a bearish displacement "
         "candle. Stop 2 ATR, take profit at 1.5R, and get out after 12 bars.",
         (READY,),
         ({"direction": "short",
           "filters": [NY_PM, {"event": {"block": "day_of_week", "params": {"days": ["mon", "wed"]}}}],
           "setup": [{"name": "impulse", "event": {"block": "displacement", "params": {"direction": "bearish"}}}],
           "entry": {"type": "market"}, "stop": {"type": "atr", "multiple": 2},
           "target": {"type": "r_multiple", "r": 1.5}, "limits": {"max_bars_held": 12}},)),
    Case("close_above_prior_week_high", "15m",
         "Go long when a candle closes above the prior week's high during NY AM. Market entry, stop at the most "
         "recent swing low, 2R.",
         (READY,),
         ({"direction": "long", "filters": [NY_AM],
           "setup": [{"name": "breakout", "event": {"left": {"price": "close"}, "op": "crosses_above",
                                                    "right": _window("week", "high")}}],
           "entry": {"type": "market"}, "stop": {"type": "level", "at": {"block": "swing", "output": "low"}},
           "target": {"type": "r_multiple", "r": 2}},)),
    Case("spring_asia", "5m",
         "Spring during the London killzone: price sweeps the Asia low and closes back above it. Buy at the next "
         "open, stop 1 point below the spring's low, target the Asia high.",
         (READY,),
         ({"direction": "long", "filters": [LONDON],
           "setup": [{"name": "sweep", "event": _sweep("low", _window("asia", "low"))}],
           "entry": {"type": "market"},
           "stop": {"type": "level", "at": {"ref": "sweep.extreme"}, "buffer_pts": 1},
           "target": {"type": "level", "at": _window("asia", "high")}},)),
    Case("opening_range_short", "5m",
         "Short a break of the 9:30-9:45 opening range: once the range is set, if a candle closes below its low "
         "within an hour, sell at market. Stop at the range high, target 2R.",
         (READY,),
         ({"direction": "short",
           "setup": [{"name": "range", "event": _window("custom", "updated", start="09:30", end="09:45")},
                     {"name": "breakdown", "within": 12,
                      "event": {"left": {"price": "close"}, "op": "crosses_below",
                                "right": _window("custom", "low", start="09:30", end="09:45")}}],
           "entry": {"type": "market"}, "stop": {"type": "level", "at": {"ref": "range.high"}},
           "target": {"type": "r_multiple", "r": 2}},
          # the range's own high: the same price, since the break comes before the next range is set
          {"direction": "short",
           "setup": [{"name": "range", "event": _window("custom", "updated", start="09:30", end="09:45")},
                     {"name": "breakdown", "within": 12,
                      "event": {"left": {"price": "close"}, "op": "crosses_below",
                                "right": _window("custom", "low", start="09:30", end="09:45")}}],
           "entry": {"type": "market"},
           "stop": {"type": "level", "at": _window("custom", "high", start="09:30", end="09:45")},
           "target": {"type": "r_multiple", "r": 2}})),
    Case("fvg_trend_filter_not_friday", "5m",
         "Buy a bullish FVG during NY AM except on Fridays, only when the close is above the 200 EMA. Limit at the "
         "top of the gap with a 10 candle expiry, stop 1 ATR, 2R.",
         (READY,),
         tuple({"direction": "long",
                "filters": [NY_AM, days, {"left": {"price": "close"}, "op": ">",
                                          "right": {"block": "ema", "params": {"length": 200}}}],
                "setup": [{"name": "gap", "event": {"block": "fvg", "params": {"direction": "bullish"},
                                                    "output": "formed"}}],
                "entry": {"type": "limit", "at": {"ref": "gap.top"}, "expiry_bars": 10},
                "stop": {"type": "atr", "multiple": 1}, "target": {"type": "r_multiple", "r": 2}}
               for days in ({"not": {"event": {"block": "day_of_week", "params": {"days": ["fri"]}}}},
                            {"event": {"block": "day_of_week", "params": {"days": ["mon", "tue", "wed", "thu"]}}}))),
    Case("higher_low_after_asia_sweep", "5m",
         "After price sweeps the Asia low, buy when a higher low is confirmed within 2 hours. Market entry, stop 2 "
         "points below the higher low, target 2R.",
         (READY,),
         ({"direction": "long",
           "setup": [{"name": "sweep", "event": _sweep("low", _window("asia", "low"))},
                     {"name": "hl", "within": 24, "event": {"block": "swing", "output": "higher_low"}}],
           "entry": {"type": "market"},
           "stop": {"type": "level", "at": {"ref": "hl.low"}, "buffer_pts": 2},
           "target": {"type": "r_multiple", "r": 2}},)),
    Case("lower_high_no_wait", "5m",
         "Short when a lower high forms after a sweep of the prior day high. Enter at market, stop above the lower "
         "high, 3R target.",
         (QUESTIONS,), note="no time between the sweep and the lower high: ask, never pick 1 or 500 bars"),
    Case("missing_entry_stop_target", "5m",
         "Buy the fair value gap after a sweep of the Asia low.",
         (QUESTIONS,), note="no entry, stop or target: must ask, never invent"),
    Case("rsi_vwap", "5m",
         "Long when RSI crosses above 30 and price is above VWAP. Stop 10 points, target 20 points.",
         (UNSUPPORTED_STATUS, QUESTIONS), unsupported=("RSI", "VWAP"),
         note="the whole signal is unsupported: nothing may run"),
    Case("stop_in_ticks", "5m",
         "Long on a sweep of the prior day low in NY AM, market entry, stop 40 ticks, target 2R.",
         (QUESTIONS,), note="ticks are ambiguous between futures and CFD: ask"),
    Case("both_directions", "5m",
         "Fade sweeps of the Asia high and low during London with the stop at the sweep extreme and 2R.",
         (QUESTIONS,), note="one direction per strategy: ask which"),
]


def expected_specs(case: Case, instrument: str = "US100") -> list[dict]:
    return [canonical_dict(validate_spec({**s, "instrument": instrument, "timeframe": case.timeframe}))
            for s in case.specs]


def score(case: Case, result: ParseResult, instrument: str = "US100") -> tuple[bool, str]:
    """(passed, why) for one case."""
    if result.status not in case.status:
        detail = f": {result.error}" if result.error else ""
        return False, f"status {result.status}, expected {'/'.join(case.status)}{detail}"
    listed = " ".join(u["concept"] for u in result.unsupported).lower()
    missing = [c for c in case.unsupported if c.lower() not in listed]
    if missing:
        return False, f"didn't list {', '.join(missing)} as unsupported"
    if result.status == READY and case.specs:
        wants = expected_specs(case, instrument)
        if result.spec in wants:
            return True, "exact"
        if unnamed(result.spec) in [unnamed(w) for w in wants]:
            return True, "same rules, other step names"
        return False, "differs in " + ", ".join(_differing(result.spec, wants[0]))
    return True, result.status


def unnamed(spec: dict) -> dict:
    """The spec with its setup steps renamed by position (step1, step2, ...), refs included: a step's
    name is only a label, "spring.extreme" and "sweep.extreme" are the same price."""
    names = {s["name"]: f"step{i}" for i, s in enumerate(spec.get("setup", []), 1)}

    def walk(x):
        if isinstance(x, dict):
            if isinstance(x.get("ref"), str) and "." in x["ref"]:
                step, out = x["ref"].split(".", 1)
                return {**x, "ref": f"{names.get(step, step)}.{out}"}
            return {k: (names.get(v, v) if k == "name" else walk(v)) for k, v in x.items()}
        if isinstance(x, list):
            return [walk(v) for v in x]
        return x

    return walk(spec)


def _differing(got: dict, want: dict) -> list[str]:
    return [k for k in sorted(set(got) | set(want)) if got.get(k) != want.get(k)]
