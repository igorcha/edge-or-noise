"""The bar context every block receives: numpy OHLC plus precomputed NY-time fields."""

from dataclasses import dataclass

import numpy as np
import pandas as pd

from engine.data.instruments import INSTRUMENTS, TIMEFRAMES
from engine.data.io import load_bars
from engine.data.sessions import minute_of_day_ny, trading_day


@dataclass(frozen=True)
class Bars:
    open: np.ndarray
    high: np.ndarray
    low: np.ndarray
    close: np.ndarray
    close_time: pd.DatetimeIndex  # UTC
    tf_minutes: int
    price_step: float
    open_minute_ny: np.ndarray  # NY minute of day of the bar's open; use for "is this bar in a window"
    close_minute_ny: np.ndarray  # NY minute of day of the bar's close; use for "has a window ended"
    trading_day: np.ndarray  # int day number, rolls at 17:00 NY
    weekday: np.ndarray  # weekday of the trading day, Mon=0

    @classmethod
    def from_frame(cls, df: pd.DataFrame, tf_minutes: int, price_step: float) -> "Bars":
        close_time = df.index
        open_time = close_time - pd.Timedelta(minutes=tf_minutes)
        days = trading_day(close_time)
        return cls(
            open=df["open"].to_numpy(np.float64),
            high=df["high"].to_numpy(np.float64),
            low=df["low"].to_numpy(np.float64),
            close=df["close"].to_numpy(np.float64),
            close_time=close_time,
            tf_minutes=tf_minutes,
            price_step=price_step,
            open_minute_ny=np.asarray(minute_of_day_ny(open_time)),
            close_minute_ny=np.asarray(minute_of_day_ny(close_time)),
            trading_day=days.to_numpy().astype("datetime64[D]").astype(np.int64),
            weekday=np.asarray(days.day_of_week),
        )

    @classmethod
    def load(cls, instrument: str, tf: str, *, include_holdout: bool = False) -> "Bars":
        df = load_bars(instrument, tf, include_holdout=include_holdout)
        return cls.from_frame(df, TIMEFRAMES[tf], INSTRUMENTS[instrument].price_step)

    def __len__(self) -> int:
        return len(self.close)

    def head(self, n: int) -> "Bars":
        """The first n bars, as if the data ended there. Used by truncation tests."""
        return Bars(**{k: (v[:n] if hasattr(v, "__len__") else v) for k, v in self.__dict__.items()})
