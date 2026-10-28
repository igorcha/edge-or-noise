"""New York time and trading-day helpers. Everything is UTC internally."""

import pandas as pd

NY = "America/New_York"


def ny_time(index: pd.DatetimeIndex) -> pd.DatetimeIndex:
    """Naive NY wall-clock time for a UTC index (DST handled by zoneinfo)."""
    return index.tz_convert(NY).tz_localize(None)


def trading_day(close_time: pd.DatetimeIndex) -> pd.DatetimeIndex:
    """Trading day of each bar, rolling at 17:00 NY.

    A bar belongs to the day in which it closes; a bar closing exactly at 17:00 NY
    is the last bar of that day. Returns naive midnight timestamps.
    """
    shifted = ny_time(close_time) - pd.Timedelta(1, "us") + pd.Timedelta(hours=7)
    return shifted.normalize()


def trading_day_start(day: pd.Timestamp) -> pd.Timestamp:
    """UTC instant a trading day starts: 17:00 NY on the previous calendar day."""
    return (pd.Timestamp(day).normalize() - pd.Timedelta(hours=7)).tz_localize(NY).tz_convert("UTC")


def minute_of_day_ny(index: pd.DatetimeIndex):
    t = ny_time(index)
    return t.hour * 60 + t.minute
