"""Block output types.

Every block returns a dict of named outputs. Each output is one of:
    series  a number per bar (float array, NaN while warming up)
    level   a price per bar (float array, NaN while unknown)
    event   True on the bars where something happens (bool array)
    zone    price ranges with a lifetime (a Zones table)
"""

from dataclasses import dataclass

import numpy as np

SERIES, LEVEL, EVENT, ZONE = "series", "level", "event", "zone"


@dataclass(frozen=True)
class Zones:
    created: np.ndarray  # bar index where the zone becomes known
    ended: np.ndarray  # bar index where it stops being active, -1 if still active at the last bar
    top: np.ndarray
    bottom: np.ndarray
    direction: np.ndarray  # +1 bullish, -1 bearish

    def __len__(self) -> int:
        return len(self.created)

    def take(self, mask: np.ndarray) -> "Zones":
        return Zones(self.created[mask], self.ended[mask], self.top[mask], self.bottom[mask], self.direction[mask])
