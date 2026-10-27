"""Example specs: the demo strategy, and material for tests and LLM few-shots."""

# The canonical ICT demo: NY AM killzone, sweep of the Asia low, then a bullish FVG within
# 10 bars; limit at the gap top, stop under the last swing low, 2R target, one trade a day.
FVG_LONG = {
    "instrument": "US100",
    "timeframe": "5m",
    "direction": "long",
    "filters": [
        {"event": {"block": "time_window", "params": {"session": "ny_am"}}},
    ],
    "setup": [
        {
            "name": "sweep",
            "event": {
                "block": "sweep",
                "params": {"side": "low"},
                "inputs": {"level": {"block": "window_high_low", "params": {"window": "asia"}, "output": "low"}},
                "output": "value",
            },
        },
        {
            "name": "gap",
            "event": {"block": "fvg", "params": {"direction": "bullish"}, "output": "formed"},
            "within": 10,
        },
    ],
    "entry": {"type": "limit", "at": {"ref": "gap.top"}, "expiry_bars": 20},
    "stop": {"type": "level", "at": {"block": "swing", "output": "low"}, "buffer_ticks": 10},
    "target": {"type": "r_multiple", "r": 2},
    "limits": {"max_trades_per_day": 1},
}

# A plain indicator strategy: short a bearish displacement candle below the 50 EMA in London,
# Tuesday to Thursday, stop 1.5 ATR, target the prior day low.
DISPLACEMENT_SHORT = {
    "instrument": "US100",
    "timeframe": "15m",
    "direction": "short",
    "filters": [
        {"all": [
            {"event": {"block": "time_window", "params": {"session": "london"}}},
            {"event": {"block": "day_of_week", "params": {"days": ["tue", "wed", "thu"]}}},
            {"left": {"price": "close"}, "op": "<", "right": {"block": "ema", "params": {"length": 50}}},
        ]},
    ],
    "setup": [
        {"name": "impulse", "event": {"block": "displacement", "params": {"direction": "bearish"}}},
    ],
    "entry": {"type": "market"},
    "stop": {"type": "atr", "multiple": 1.5},
    "target": {"type": "level", "at": {"block": "window_high_low", "params": {"window": "day"}, "output": "low"}},
    "limits": {"max_trades_per_day": 2, "max_bars_held": 40},
    "slippage_ticks": 2,
}

EXAMPLES = {"fvg_long": FVG_LONG, "displacement_short": DISPLACEMENT_SHORT}
