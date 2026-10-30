"""The rule spec: what a strategy is, as data.

A spec is JSON the LLM produces and the interpreter runs. Sections: instrument/timeframe
(fixed per project), direction, filters, setup, entry, stop, target, limits, slippage.
Every model forbids unknown keys so a typo can't silently become a default.

Distances (slippage, stop buffer, limit offset, fixed stops) are in price points, never ticks:
a US100 tick is 0.01 points, so "a 20 tick stop" would mean 0.2 points.

Blocks are referenced by name (or alias) with their params; `models.py` only checks shape.
Whether a block exists, its params are valid and an output has the right kind is checked
against the registry in `checks.py`, which also fills defaults so two specs that mean the
same thing hash the same.

MVP cuts (roadmap): `manage` (break-even/trailing), `since` state, multi-timeframe, stop
entries, direction "both" (run two trials instead).
"""

from typing import Annotated, Any, Literal, Union

from pydantic import BaseModel, ConfigDict, Discriminator, Field, Tag, model_validator

from engine.data.instruments import INSTRUMENTS, TIMEFRAMES

Instrument = Literal[tuple(sorted(INSTRUMENTS))]  # type: ignore[valid-type]
Timeframe = Literal[tuple(sorted(TIMEFRAMES, key=TIMEFRAMES.get))]  # type: ignore[valid-type]
Direction = Literal["long", "short"]
Name = Annotated[str, Field(min_length=1, max_length=64, pattern=r"^[a-z][a-z0-9_]*$")]
Points = Annotated[float, Field(ge=0, le=10_000)]  # a distance in price points


class SpecModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


# --- block references ---------------------------------------------------------------

class BlockRef(SpecModel):
    """A block with its params, and which of its outputs is meant.

    `output` may be omitted when the block has exactly one output. `inputs` feed blocks that
    take another block's output (sweep needs a `level`).
    """
    block: Annotated[str, Field(min_length=1, max_length=64)]
    params: dict[str, Any] = {}
    inputs: dict[Name, "BlockRef"] = {}
    output: Name | None = None


# The candle a setup step fired on, for any step (a cross too): "stop below the signal candle's low" is the
# last step's candle_low. Known when that candle closes, which is at or before the signal.
CANDLE_PRICES = {"candle_open": "open", "candle_high": "high", "candle_low": "low", "candle_close": "close"}


class StepRef(SpecModel):
    """A price from the setup's own geometry: `<step name>.<level output>`, e.g. "gap.top", or the
    candle the step fired on: `<step name>.candle_low` (CANDLE_PRICES).

    It's the output as it was on the bar that step fired, so an entry "at gap.top" doesn't
    drift to a newer gap.
    """
    ref: Annotated[str, Field(pattern=r"^[a-z][a-z0-9_]*\.[a-z][a-z0-9_]*$")]

    @property
    def step(self) -> str:
        return self.ref.split(".")[0]

    @property
    def output(self) -> str:
        return self.ref.split(".")[1]


PriceRef = StepRef | BlockRef  # a level: setup geometry or a level output (swing low, PDH, EMA)


# --- conditions (filters) ---------------------------------------------------------------

class Price(SpecModel):
    price: Literal["open", "high", "low", "close"]


Operand = float | Price | BlockRef  # a constant, a bar price, or a level (price) / series (number) output
CROSS_OPS = ("crosses_above", "crosses_below")


class Compare(SpecModel):
    """`left op right` on each bar. Both sides must be prices (bar prices, levels) or both numbers
    (series); a constant fits either.

    `crosses_above`: left > right on this bar and left <= right on the previous one, both closed
    bars. `crosses_below` mirrors it. A cross is an event, so it can also be a setup step
    ("EMA 9 crosses above EMA 21", "close crosses above the opening range high")."""
    left: Operand
    op: Literal[">", "<", ">=", "<=", "crosses_above", "crosses_below"]
    right: Operand


class Event(SpecModel):
    event: BlockRef  # an event output: true on the bars where it fires


class All(SpecModel):
    all: list["Condition"] = Field(min_length=1)


class AnyOf(SpecModel):
    any: list["Condition"] = Field(min_length=1)


class Not(SpecModel):
    not_: "Condition" = Field(alias="not")


def _condition_tag(v: Any) -> str | None:
    """Which kind of condition this is, from its keys, so a malformed one gets one precise error
    instead of an error for every kind it might have been."""
    if isinstance(v, dict):
        return next((tag for key, tag in (("event", "event"), ("left", "compare"), ("all", "all"),
                                          ("any", "any"), ("not", "not")) if key in v), None)
    return {Event: "event", Compare: "compare", All: "all", AnyOf: "any", Not: "not"}.get(type(v))


Condition = Annotated[
    Union[Annotated[Event, Tag("event")], Annotated[Compare, Tag("compare")], Annotated[All, Tag("all")],
          Annotated[AnyOf, Tag("any")], Annotated[Not, Tag("not")]],
    Discriminator(_condition_tag, custom_error_type="condition",
                  custom_error_message="a condition is one of {event}, {left, op, right}, {all: [...]}, "
                                       "{any: [...]}, {not: ...}"),
]


# --- setup ----------------------------------------------------------------------------------

class Step(SpecModel):
    """One event in the setup sequence. "A then B within N bars": B's `within` counts bars
    after A's bar (1 = the very next bar). The first step has no `within`.

    `event` is a block's event output, or a cross (a Compare with crosses_above/crosses_below)."""
    name: Name
    event: BlockRef | Compare
    within: Annotated[int, Field(ge=1, le=500)] | None = None


# --- entry / stop / target --------------------------------------------------------------------

class MarketEntry(SpecModel):
    type: Literal["market"] = "market"  # fills at the open of the bar after the signal


class LimitEntry(SpecModel):
    type: Literal["limit"]
    at: PriceRef
    offset_pts: Annotated[float, Field(ge=-10_000, le=10_000)] = 0.0  # + = above the level
    expiry_bars: Annotated[int, Field(ge=1, le=500)] = 20  # cancel if not filled in time


Entry = Annotated[MarketEntry | LimitEntry, Field(discriminator="type")]


class LevelStop(SpecModel):
    """Stop at a level: a swing, a zone edge ("gap.bottom"), the sweep extreme. `buffer_pts`
    pushes it further from the entry."""
    type: Literal["level"]
    at: PriceRef
    buffer_pts: Points = 0.0


class AtrStop(SpecModel):
    type: Literal["atr"]
    multiple: Annotated[float, Field(gt=0, le=20)] = 1.5
    length: Annotated[int, Field(ge=2, le=500)] = 14


class PointsStop(SpecModel):
    type: Literal["points"]
    points: Annotated[float, Field(gt=0, le=10_000)]  # fixed distance from the entry


Stop = Annotated[LevelStop | AtrStop | PointsStop, Field(discriminator="type")]


class RTarget(SpecModel):
    type: Literal["r_multiple"]
    r: Annotated[float, Field(gt=0, le=50)] = 2.0


class LevelTarget(SpecModel):
    type: Literal["level"]
    at: PriceRef


Target = Annotated[RTarget | LevelTarget, Field(discriminator="type")]


class Limits(SpecModel):
    max_trades_per_day: Annotated[int, Field(ge=1, le=50)] = 1
    max_bars_held: Annotated[int, Field(ge=1, le=5000)] | None = None  # time stop, None = until stop/target
    # One position at a time is always on; the simulator doesn't support anything else.


# --- the whole thing ------------------------------------------------------------------------------

class Strategy(SpecModel):
    version: Literal[1] = 1
    instrument: Instrument
    timeframe: Timeframe
    direction: Direction
    filters: list[Condition] = []  # all must hold on the signal bar (an implicit `all`)
    setup: list[Step] = Field(min_length=1, max_length=8)
    entry: Entry  # required: market vs a limit at the gap changes results too much to assume
    stop: Stop
    target: Target
    limits: Limits = Limits()
    slippage_pts: Annotated[float, Field(ge=0, le=1000)] = 0.0  # per side, on top of the spread

    @model_validator(mode="after")
    def _steps(self):
        names = [s.name for s in self.setup]
        if len(names) != len(set(names)):
            raise ValueError("setup step names must be unique")
        if self.setup[0].within is not None:
            raise ValueError("the first setup step can't have `within`")
        for s in self.setup[1:]:
            if s.within is None:
                raise ValueError(f"setup step {s.name!r} needs `within` (bars after the previous step)")
        return self


def json_schema() -> dict:
    """JSON schema of the spec, for the LLM prompt and for API docs."""
    return Strategy.model_json_schema()
