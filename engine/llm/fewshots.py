"""Few-shot examples for the parse prompt: a trader's words -> the envelope the model must return.

Each one is validated by the tests (a "ready" example must pass validate_spec), so the prompt
never teaches the model an invalid spec. They're deliberately different from the golden eval
prompts (golden.py), so the eval measures generalisation, not recall.
"""

from engine.spec.examples import DISPLACEMENT_SHORT, EMA_CROSS_LONG, FVG_LONG, OPENING_RANGE_LONG


def _strip(spec: dict) -> dict:
    """The project fixes instrument and timeframe, so the model never writes them."""
    return {k: v for k, v in spec.items() if k not in ("instrument", "timeframe")}


def _envelope(spec=None, assumptions=(), questions=(), unsupported=()) -> dict:
    return {"spec": spec, "assumptions": list(assumptions), "questions": list(questions),
            "unsupported": list(unsupported)}


PRIOR_DAY = {"block": "window_high_low", "params": {"window": "day"}}

_displacement_short = _strip(DISPLACEMENT_SHORT)
_displacement_short["filters"] = DISPLACEMENT_SHORT["filters"][0]["all"]  # the plain list form reads better

FEWSHOTS: list[dict] = [
    {
        "timeframe": "5m",
        "prompt": "In the New York AM killzone I wait for price to take out the Asia session low and close back "
                  "above it. Then within 10 candles I want a bullish fair value gap. I buy with a limit at the top "
                  "of the gap and cancel it if it doesn't fill within 20 candles. Stop goes 2 points under the most "
                  "recent swing low, target is 2R. Only one trade per day.",
        "envelope": _envelope(
            _strip(FVG_LONG),
            assumptions=["Taking out the Asia low and closing back above it is a sweep of the Asia session low, "
                         "reclaimed on the same candle",
                         "The most recent swing low is the last confirmed swing low"]),
    },
    {
        "timeframe": "15m",
        "prompt": "Tuesday to Thursday only, London session, and only when price is below the 50 EMA. Short a big "
                  "bearish displacement candle at the next open. Stop 1.5 ATR, target the previous day's low. Max 2 "
                  "trades a day, and close the trade after 40 bars if neither stop nor target is hit. Assume 0.5 "
                  "points of slippage.",
        "envelope": _envelope(
            _displacement_short,
            assumptions=["'Below the 50 EMA' means the signal candle closes below the 50 EMA",
                         "The London session is the London killzone"]),
    },
    {
        "timeframe": "15m",
        "prompt": "Go long when the 9 EMA crosses above the 21 EMA, but only during the NY morning or afternoon "
                  "sessions. Market entry, 30 point stop, 2R target.",
        "envelope": _envelope(
            _strip(EMA_CROSS_LONG),
            assumptions=["The NY morning and afternoon sessions are the NY AM and NY PM killzones"]),
    },
    {
        "timeframe": "5m",
        "prompt": "Opening range breakout: take the 9:30 to 10:00 range. If a candle closes above the range high "
                  "within two hours after that, buy at market. Stop at the range low, take profit at 1.5 times risk.",
        "envelope": _envelope(
            _strip(OPENING_RANGE_LONG),
            assumptions=["Two hours of 5-minute candles is 24 bars"]),
    },
    {
        "timeframe": "5m",
        "prompt": "Short when price sweeps the previous day high during London.",
        "envelope": _envelope(
            {"direction": "short",
             "filters": [{"event": {"block": "time_window", "params": {"session": "london"}}}],
             "setup": [{"name": "sweep", "event": {"block": "sweep", "params": {"side": "high"},
                                                   "inputs": {"level": {**PRIOR_DAY, "output": "high"}},
                                                   "output": "value"}}]},
            assumptions=["A sweep of the previous day high: price trades above it and closes back below it on the "
                         "same candle"],
            questions=["How do you enter: at market on the next candle's open, or with a limit order at a price "
                       "(which one)?",
                       "Where is your stop: above the sweep's high, a fixed number of points, or a multiple of ATR?",
                       "Where do you take profit: a multiple of your risk (for example 2R), or a level such as the "
                       "previous day low?"]),
    },
    {
        "timeframe": "5m",
        "prompt": "Buy when RSI drops below 30 and price is above VWAP. 20 point stop, 40 point target.",
        "envelope": _envelope(
            None,
            unsupported=[{"concept": "RSI", "reason": "not available yet"},
                         {"concept": "VWAP", "reason": "needs exchange volume, which CFD data doesn't have"}]),
    },
    {
        "timeframe": "5m",
        "prompt": "Turtle soup long on the prior day low during the NY AM killzone: once the low is swept, buy at "
                  "the next open. Stop 1 point below the low of the sweep, target the prior day high.",
        "envelope": _envelope(
            {"direction": "long",
             "filters": [{"event": {"block": "time_window", "params": {"session": "ny_am"}}}],
             "setup": [{"name": "sweep", "event": {"block": "sweep", "params": {"side": "low"},
                                                   "inputs": {"level": {**PRIOR_DAY, "output": "low"}},
                                                   "output": "value"}}],
             "entry": {"type": "market"},
             "stop": {"type": "level", "at": {"ref": "sweep.extreme"}, "buffer_pts": 1},
             "target": {"type": "level", "at": {**PRIOR_DAY, "output": "high"}}},
            assumptions=["Turtle soup is a sweep: price trades below the prior day low and closes back above it on "
                         "the same candle",
                         "The low of the sweep is the lowest price of the sweep candle"]),
    },
    {
        "timeframe": "5m",
        "prompt": "After a bullish displacement candle, buy when a candle closes above the last swing high within 6 "
                  "bars. Not on Mondays or Fridays. Market entry, stop 1 ATR, target 3R.",
        "envelope": _envelope(
            {"direction": "long",
             "filters": [{"not": {"event": {"block": "day_of_week", "params": {"days": ["mon", "fri"]}}}}],
             "setup": [{"name": "impulse", "event": {"block": "displacement", "params": {"direction": "bullish"}}},
                       {"name": "shift", "within": 6,
                        "event": {"left": {"price": "close"}, "op": "crosses_above",
                                  "right": {"block": "swing", "output": "high"}}}],
             "entry": {"type": "market"},
             "stop": {"type": "atr", "multiple": 1},
             "target": {"type": "r_multiple", "r": 3}},
            assumptions=["A candle closing above the last swing high is the close crossing above the last confirmed "
                         "swing high"]),
    },
    {
        "timeframe": "15m",
        "prompt": "Short when the close crosses below the 20 EMA in the NY afternoon. Market entry, 15 point stop "
                  "and 30 point target, up to 3 trades a day.",
        "envelope": _envelope(
            {"direction": "short",
             "filters": [{"event": {"block": "time_window", "params": {"session": "ny_pm"}}}],
             "setup": [{"name": "cross", "event": {"left": {"price": "close"}, "op": "crosses_below",
                                                   "right": {"block": "ema", "params": {"length": 20}}}}],
             "entry": {"type": "market"},
             "stop": {"type": "points", "points": 15},
             "target": {"type": "r_multiple", "r": 2},
             "limits": {"max_trades_per_day": 3}},
            assumptions=["A 30 point target with a 15 point stop is a 2R target",
                         "The NY afternoon is the NY PM killzone"]),
    },
    {
        "timeframe": "5m",
        "prompt": "Trade breakouts of the Asia range in both directions during London. Market entry when a candle "
                  "closes outside the range, stop 40 ticks, 2R.",
        "envelope": _envelope(
            {"filters": [{"event": {"block": "time_window", "params": {"session": "london"}}}],
             "entry": {"type": "market"},
             "target": {"type": "r_multiple", "r": 2}},
            assumptions=["The Asia range is the high and low of the Asia session"],
            questions=["Each strategy tests one direction. Which first: long when a candle closes above the Asia "
                       "high, or short when one closes below the Asia low? The other can be a separate trial.",
                       "Your stop is in ticks. How many price points is that? Ticks differ between instruments "
                       "(0.25 points on Nasdaq futures, 0.01 on this CFD)."]),
    },
]
