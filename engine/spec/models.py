"""The rule spec: what a strategy is, as data.

A spec is JSON the LLM produces and the interpreter runs. Sections: instrument/timeframe
(fixed per project), direction, filters, setup, entry, stop, target, limits, slippage.
Every model forbids unknown keys so a typo can't silently become a default.

Blocks are referenced by name (or alias) with their params; `models.py` only checks shape.
Whether a block exists, its params are valid and an output has the right kind is checked
against the registry in `checks.py`, which also fills defaults so two specs that mean the
same thing hash the same.

MVP cuts (roadmap): `manage` (break-even/trailing), `since` state, multi-timeframe, stop
entries, direction "both" (run two trials instead).
"""

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from engine.data.instruments import INSTRUMENTS, TIMEFRAMES

Instrument = Literal[tuple(sorted(INSTRUMENTS))]  # type: ignore[valid-type]
Timeframe = Literal[tuple(sorted(TIMEFRAMES, key=TIMEFRAMES.get))]  # type: ignore[valid-type]
Direction = Literal["long", "short"]
Name = Annotated[str, Field(min_length=1, max_length=64, pattern=r"^[a-z][a-z0-9_]*$")]


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


class StepRef(SpecModel):
    """A price from the setup's own geometry: `<step name>.<level output>`, e.g. "gap.top".

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


Operand = float | Price | BlockRef  # a number, a bar price, or a series/level output


class Compare(SpecModel):
    left: Operand
    op: Literal[">", "<", ">=", "<="]
    right: Operand


class Event(SpecModel):
    event: BlockRef  # an event output: true on the bars where it fires


class All(SpecModel):
    all: list["Condition"] = Field(min_length=1)


class AnyOf(SpecModel):
    any: list["Condition"] = Field(min_length=1)


class Not(SpecModel):
    not_: "Condition" = Field(alias="not")


Condition = Event | Compare | All | AnyOf | Not


# --- setup ----------------------------------------------------------------------------------

class Step(SpecModel):
    """One event in the setup sequence. "A then B within N bars": B's `within` counts bars
    after A's bar (1 = the very next bar). The first step has no `within`."""
    name: Name
    event: BlockRef
    within: Annotated[int, Field(ge=1, le=500)] | None = None


# --- entry / stop / target --------------------------------------------------------------------

class MarketEntry(SpecModel):
    type: Literal["market"] = "market"  # fills at the open of the bar after the signal


class LimitEntry(SpecModel):
    type: Literal["limit"]
    at: PriceRef
    offset_ticks: Annotated[int, Field(ge=-1000, le=1000)] = 0  # +ticks = above the level
    expiry_bars: Annotated[int, Field(ge=1, le=500)] = 20  # cancel if not filled in time


Entry = MarketEntry | LimitEntry


class LevelStop(SpecModel):
    """Stop at a level: a swing, a zone edge ("gap.bottom"), the sweep extreme. `buffer_ticks`
    pushes it further from the entry."""
    type: Literal["level"]
    at: PriceRef
    buffer_ticks: Annotated[int, Field(ge=0, le=1000)] = 0


class AtrStop(SpecModel):
    type: Literal["atr"]
    multiple: Annotated[float, Field(gt=0, le=20)] = 1.5
    length: Annotated[int, Field(ge=2, le=500)] = 14


class TicksStop(SpecModel):
    type: Literal["ticks"]
    ticks: Annotated[int, Field(ge=1, le=100_000)]


Stop = LevelStop | AtrStop | TicksStop


class RTarget(SpecModel):
    type: Literal["r_multiple"]
    r: Annotated[float, Field(gt=0, le=50)] = 2.0


class LevelTarget(SpecModel):
    type: Literal["level"]
    at: PriceRef


Target = RTarget | LevelTarget


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
    entry: Entry = MarketEntry()
    stop: Stop
    target: Target
    limits: Limits = Limits()
    slippage_ticks: Annotated[int, Field(ge=0, le=1000)] = 0  # per side, on top of the spread

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
