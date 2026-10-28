"""Block output types.

Every block returns a dict of named outputs. Each output is one of:
    series  a number per bar that is not a price, e.g. ATR in points (float array, NaN while warming up)
    level   a price per bar, e.g. prior day high or EMA (float array, NaN while unknown)
    event   True on the bars where something happens (bool array)
    zone    price ranges with a lifetime (a Zones table)
"""

from dataclasses import dataclass

import numpy as np

SERIES, LEVEL, EVENT, ZONE = "series", "level", "event", "zone"


@dataclass(frozen=True)
class Zones:
    """A zone is active for decisions made at the close of bar t when created <= t < ended
    (or ended == -1). `ended` is the bar that traded through the far edge, so the zone is already
    dead at its close. But an order placed while the zone was active rests during bar `ended`
    and must still be checked for a fill there before it's cancelled. Cancelling first would drop
    exactly the trades that fill and then blow through the zone: the losers."""
    created: np.ndarray  # bar index where the zone becomes known
    ended: np.ndarray  # first bar it's no longer active (trade-through or expiry), -1 if still active at the last bar
    top: np.ndarray
    bottom: np.ndarray
    direction: np.ndarray  # +1 bullish, -1 bearish

    def __len__(self) -> int:
        return len(self.created)

    def take(self, mask: np.ndarray) -> "Zones":
        return Zones(self.created[mask], self.ended[mask], self.top[mask], self.bottom[mask], self.direction[mask])
