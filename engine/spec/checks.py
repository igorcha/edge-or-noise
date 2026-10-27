"""Semantic checks against the block registry, and canonicalization.

`validate_spec(dict)` is the one entry point: JSON shape (pydantic) -> semantic checks ->
canonical form. The canonical form is what gets hashed, so it must be the same for every
way of writing the same strategy: aliases become block names, omitted params get their
pinned defaults, a single output is named explicitly.

Errors carry a JSON path ("setup[1].event.params.max_age") so the LLM retry and the UI
can point at the field.
"""

from typing import Any

from pydantic import ValidationError

from engine.blocks.registry import BlockSpec, resolve
from engine.blocks.types import EVENT, LEVEL, SERIES
from engine.spec.models import (
    All, AnyOf, AtrStop, BlockRef, Compare, Condition, Entry, Event, LevelStop, LevelTarget, LimitEntry,
    MarketEntry, Not, Price, PriceRef, RTarget, Step, Stop, StepRef, Strategy, Target, TicksStop,
)

PRICE_KINDS = frozenset({LEVEL, SERIES})  # anything that is a price per bar can be an entry/stop/target


class SpecError(ValueError):
    def __init__(self, path: str, message: str):
        self.path, self.message = path, message
        super().__init__(f"{path}: {message}" if path else message)


def _format_validation_error(e: ValidationError, prefix: str = "") -> str:
    lines = []
    for err in e.errors(include_url=False):
        loc = ".".join(str(p) for p in err["loc"])
        lines.append(f"{prefix + '.' if prefix and loc else prefix}{loc}: {err['msg']}")
    return "; ".join(lines)


def validate_spec(data: dict[str, Any]) -> Strategy:
    """Parse a spec dict, check it against the registry and return its canonical form."""
    try:
        spec = Strategy.model_validate(data)
    except ValidationError as e:
        raise SpecError("", _format_validation_error(e)) from None
    return canonicalize(spec)


def canonicalize(spec: Strategy) -> Strategy:
    steps, step_blocks = [], {}
    for i, s in enumerate(spec.setup):
        ref, block = _block(s.event, {EVENT}, f"setup[{i}].event")
        steps.append(Step(name=s.name, event=ref, within=s.within))
        step_blocks[s.name] = block
    return Strategy(
        version=spec.version,
        instrument=spec.instrument,
        timeframe=spec.timeframe,
        direction=spec.direction,
        filters=[_condition(c, f"filters[{i}]") for i, c in enumerate(spec.filters)],
        setup=steps,
        entry=_entry(spec.entry, step_blocks),
        stop=_stop(spec.stop, step_blocks),
        target=_target(spec.target, step_blocks),
        limits=spec.limits,
        slippage_ticks=spec.slippage_ticks,
    )


# --- blocks ----------------------------------------------------------------------------------

def _block(ref: BlockRef, need: set[str], path: str) -> tuple[BlockRef, BlockSpec]:
    """Resolve a block reference: name, params with defaults, inputs, and an output of kind `need`."""
    try:
        block = resolve(ref.block)
    except KeyError:
        raise SpecError(f"{path}.block", f"unknown block {ref.block!r}") from None
    try:
        params = block.params(**ref.params)
    except ValidationError as e:
        raise SpecError(f"{path}.params", _format_validation_error(e)) from None
    except TypeError as e:  # non-string keys
        raise SpecError(f"{path}.params", str(e)) from None

    extra = set(ref.inputs) - set(block.inputs)
    if extra:
        raise SpecError(f"{path}.inputs", f"{block.name} takes no input {sorted(extra)}")
    missing = set(block.inputs) - set(ref.inputs)
    if missing:
        raise SpecError(f"{path}.inputs", f"{block.name} needs inputs {sorted(missing)}")
    inputs = {k: _block(v, {block.inputs[k]}, f"{path}.inputs.{k}")[0] for k, v in sorted(ref.inputs.items())}

    output = ref.output
    if output is None:
        if len(block.outputs) != 1:
            raise SpecError(f"{path}.output", f"{block.name} has outputs {sorted(block.outputs)}, say which one")
        output = next(iter(block.outputs))
    elif output not in block.outputs:
        raise SpecError(f"{path}.output", f"{block.name} has no output {output!r}, only {sorted(block.outputs)}")
    kind = block.outputs[output]
    if kind not in need:
        raise SpecError(f"{path}.output", f"{block.name}.{output} is a {kind}, needed {' or '.join(sorted(need))}")

    canon = BlockRef(block=block.name, params=params.model_dump(mode="json"), inputs=inputs, output=output)
    return canon, block


# --- conditions ----------------------------------------------------------------------------------

def _condition(c: Condition, path: str) -> Condition:
    if isinstance(c, Event):
        return Event(event=_block(c.event, {EVENT}, f"{path}.event")[0])
    if isinstance(c, Compare):
        return Compare(left=_operand(c.left, f"{path}.left"), op=c.op, right=_operand(c.right, f"{path}.right"))
    if isinstance(c, All):
        return All(all=[_condition(x, f"{path}.all[{i}]") for i, x in enumerate(c.all)])
    if isinstance(c, AnyOf):
        return AnyOf(any=[_condition(x, f"{path}.any[{i}]") for i, x in enumerate(c.any)])
    if isinstance(c, Not):
        return Not(**{"not": _condition(c.not_, f"{path}.not")})
    raise SpecError(path, f"unknown condition {type(c).__name__}")


def _operand(o: float | Price | BlockRef, path: str):
    if isinstance(o, BlockRef):
        return _block(o, PRICE_KINDS, path)[0]
    return o


# --- prices: entry, stop, target -------------------------------------------------------------------

def _price(p: PriceRef, step_blocks: dict[str, BlockSpec], path: str) -> PriceRef:
    if isinstance(p, BlockRef):
        return _block(p, PRICE_KINDS, path)[0]
    block = step_blocks.get(p.step)
    if block is None:
        raise SpecError(f"{path}.ref", f"no setup step named {p.step!r}, steps are {sorted(step_blocks)}")
    kind = block.outputs.get(p.output)
    if kind is None:
        raise SpecError(f"{path}.ref", f"step {p.step!r} ({block.name}) has no output {p.output!r}, "
                                       f"only {sorted(block.outputs)}")
    if kind not in PRICE_KINDS:
        raise SpecError(f"{path}.ref", f"{p.ref} is a {kind}, not a price")
    return p


def _entry(e: Entry, step_blocks) -> Entry:
    if isinstance(e, LimitEntry):
        return LimitEntry(type="limit", at=_price(e.at, step_blocks, "entry.at"),
                          offset_ticks=e.offset_ticks, expiry_bars=e.expiry_bars)
    return MarketEntry()


def _stop(s: Stop, step_blocks) -> Stop:
    if isinstance(s, LevelStop):
        return LevelStop(type="level", at=_price(s.at, step_blocks, "stop.at"), buffer_ticks=s.buffer_ticks)
    assert isinstance(s, (AtrStop, TicksStop))
    return s


def _target(t: Target, step_blocks) -> Target:
    if isinstance(t, LevelTarget):
        return LevelTarget(type="level", at=_price(t.at, step_blocks, "target.at"))
    assert isinstance(t, RTarget)
    return t
