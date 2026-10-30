"""Semantic checks against the block registry, and canonicalization.

`validate_spec(dict)` is the one entry point: JSON shape (pydantic) -> semantic checks ->
canonical form. The canonical form is what gets hashed, so it must be the same for every
way of writing the same strategy: aliases become block names, omitted params get their
pinned defaults, a single output is named explicitly, and filters are flattened, de-duplicated
and sorted (their order never matters).

Errors carry a JSON path ("setup[1].event.params.max_age") so the LLM retry and the UI
can point at the field.
"""

import json
from typing import Any

from pydantic import ValidationError

from engine.blocks.registry import BlockSpec, resolve
from engine.blocks.types import EVENT, LEVEL, SERIES
from engine.data.instruments import TIMEFRAMES
from engine.spec.models import (
    CROSS_OPS, All, AnyOf, BlockRef, Compare, Condition, Entry, Event, LevelStop, LevelTarget, LimitEntry, Not,
    CANDLE_PRICES, Price, PriceRef, Stop, StepRef, Strategy, Target,
)

PRICE_KINDS = frozenset({LEVEL})  # what an entry, stop or target can sit at
OPERAND_KINDS = {LEVEL: "price", SERIES: "number"}


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
    """The canonical form of a parsed spec. Only the parts that need normalizing are replaced
    (model_copy), so a field added to the models later can't be silently dropped from the hash."""
    tf = TIMEFRAMES[spec.timeframe]
    steps, step_blocks = [], {}
    for i, s in enumerate(spec.setup):
        path = f"setup[{i}].event"
        if isinstance(s.event, Compare):
            if s.event.op not in CROSS_OPS:
                raise SpecError(f"{path}.op", f"a setup step must be an event; use {' or '.join(CROSS_OPS)}, "
                                              f"or put a plain comparison in filters")
            event, block = _compare(s.event, path, tf), None
        else:
            event, block = _block(s.event, {EVENT}, path, tf)
        steps.append(s.model_copy(update={"event": event}))
        step_blocks[s.name] = block
    filters = [_condition(c, f"filters[{i}]", tf) for i, c in enumerate(spec.filters)]
    return spec.model_copy(update={
        "filters": _group(filters, All),  # the filter list is an implicit `all`
        "setup": steps,
        "entry": _entry(spec.entry, step_blocks, tf),
        "stop": _stop(spec.stop, step_blocks, tf),
        "target": _target(spec.target, step_blocks, tf),
    })


# --- blocks ----------------------------------------------------------------------------------

def _block(ref: BlockRef, need: set[str], path: str, tf: int) -> tuple[BlockRef, BlockSpec]:
    """Resolve a block reference: name, params with defaults, inputs, and an output of kind `need`."""
    try:
        block = resolve(ref.block)
    except KeyError:
        raise SpecError(f"{path}.block", f"unknown block {ref.block!r}") from None
    try:
        params = block.params(**ref.params)
        params.check_timeframe(tf)
    except ValidationError as e:
        raise SpecError(f"{path}.params", _format_validation_error(e)) from None
    except (TypeError, ValueError) as e:  # non-string keys; params that don't fit the timeframe
        raise SpecError(f"{path}.params", str(e)) from None

    extra = set(ref.inputs) - set(block.inputs)
    if extra:
        raise SpecError(f"{path}.inputs", f"{block.name} takes no input {sorted(extra)}")
    missing = set(block.inputs) - set(ref.inputs)
    if missing:
        raise SpecError(f"{path}.inputs", f"{block.name} needs inputs {sorted(missing)}")
    inputs = {k: _block(v, {block.inputs[k]}, f"{path}.inputs.{k}", tf)[0] for k, v in sorted(ref.inputs.items())}

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

def _key(c) -> str:
    return json.dumps(c.model_dump(mode="json", by_alias=True), sort_keys=True)


def _group(children: list, cls: type[All] | type[AnyOf]) -> list:
    """Children of an all/any in canonical order: same-kind groups merged in, duplicates dropped, sorted."""
    flat = []
    for c in children:
        flat.extend((c.all if cls is All else c.any) if isinstance(c, cls) else [c])
    return sorted({_key(c): c for c in flat}.values(), key=_key)


def _condition(c: Condition, path: str, tf: int) -> Condition:
    if isinstance(c, Event):
        return Event(event=_block(c.event, {EVENT}, f"{path}.event", tf)[0])
    if isinstance(c, Compare):
        return _compare(c, path, tf)
    if isinstance(c, (All, AnyOf)):
        field = "all" if isinstance(c, All) else "any"
        kids = _group([_condition(x, f"{path}.{field}[{i}]", tf) for i, x in enumerate(getattr(c, field))], type(c))
        return kids[0] if len(kids) == 1 else type(c)(**{field: kids})
    if isinstance(c, Not):
        inner = _condition(c.not_, f"{path}.not", tf)
        return inner.not_ if isinstance(inner, Not) else Not(**{"not": inner})
    raise SpecError(path, f"unknown condition {type(c).__name__}")


def _compare(c: Compare, path: str, tf: int) -> Compare:
    left, lu = _operand(c.left, f"{path}.left", tf)
    right, ru = _operand(c.right, f"{path}.right", tf)
    if lu is None and ru is None:
        raise SpecError(path, "compares two constants")
    if lu and ru and lu != ru:
        raise SpecError(path, f"compares a {lu} with a {ru}; compare prices with prices (close, levels, EMA) "
                              f"and numbers with numbers (ATR, constants)")
    return c.model_copy(update={"left": left, "right": right})


def _operand(o: float | Price | BlockRef, path: str, tf: int) -> tuple[Any, str | None]:
    """The canonical operand and what it measures: "price", "number", or None for a constant."""
    if isinstance(o, BlockRef):
        ref, block = _block(o, set(OPERAND_KINDS), path, tf)
        return ref, OPERAND_KINDS[block.outputs[ref.output]]
    if isinstance(o, Price):
        return o, "price"
    return o, None


# --- prices: entry, stop, target -------------------------------------------------------------------

def _price(p: PriceRef, step_blocks: dict[str, BlockSpec | None], path: str, tf: int) -> PriceRef:
    if isinstance(p, BlockRef):
        return _block(p, PRICE_KINDS, path, tf)[0]
    if p.step not in step_blocks:
        raise SpecError(f"{path}.ref", f"no setup step named {p.step!r}, steps are {sorted(step_blocks)}")
    if p.output in CANDLE_PRICES:  # the step's own candle: every step has one
        return p
    block = step_blocks[p.step]
    if block is None:
        raise SpecError(f"{path}.ref", f"step {p.step!r} is a cross: its only prices are its candle's "
                                       f"({', '.join(CANDLE_PRICES)})")
    kind = block.outputs.get(p.output)
    if kind is None:
        raise SpecError(f"{path}.ref", f"step {p.step!r} ({block.name}) has no output {p.output!r}, "
                                       f"only {sorted(block.outputs)}")
    if kind not in PRICE_KINDS:
        raise SpecError(f"{path}.ref", f"{p.ref} is a {kind}, not a price")
    return p


def _entry(e: Entry, step_blocks, tf: int) -> Entry:
    if isinstance(e, LimitEntry):
        return e.model_copy(update={"at": _price(e.at, step_blocks, "entry.at", tf)})
    return e


def _stop(s: Stop, step_blocks, tf: int) -> Stop:
    if isinstance(s, LevelStop):
        return s.model_copy(update={"at": _price(s.at, step_blocks, "stop.at", tf)})
    return s


def _target(t: Target, step_blocks, tf: int) -> Target:
    if isinstance(t, LevelTarget):
        return t.model_copy(update={"at": _price(t.at, step_blocks, "target.at", tf)})
    return t
