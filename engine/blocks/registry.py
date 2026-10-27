"""Block registry and catalog.

A block is a function `fn(bars, params, **inputs) -> {output_name: array | Zones}`
registered with @block. The catalog (JSON) is what the LLM prompt is built from.
"""

from collections.abc import Callable
from dataclasses import dataclass

from pydantic import BaseModel, ConfigDict

from engine.blocks.context import Bars


class Params(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


@dataclass(frozen=True)
class BlockSpec:
    name: str
    fn: Callable
    params: type[Params]
    outputs: dict[str, str]  # output name -> kind
    inputs: dict[str, str]  # input name -> kind it must be
    aliases: tuple[str, ...]
    description: str


REGISTRY: dict[str, BlockSpec] = {}

# Shown to users as "can't test yet". Volume tools are out because Dukascopy volume isn't exchange volume.
UNSUPPORTED = {
    "vwap": "needs exchange volume",
    "volume_profile": "needs exchange volume",
    "order_flow": "needs exchange volume and order book data",
    "elliott_wave": "wave counts are subjective, no mechanical definition",
    "harmonic_patterns": "not supported yet",
    "wyckoff_phases": "phases are subjective, no mechanical definition",
    "al_brooks_bar_counts": "not supported yet",
}


def block(name: str, *, params: type[Params], outputs: dict[str, str], description: str,
          inputs: dict[str, str] | None = None, aliases: tuple[str, ...] = ()):
    def register(fn: Callable) -> Callable:
        if name in REGISTRY:
            raise ValueError(f"block {name!r} registered twice")
        REGISTRY[name] = BlockSpec(name, fn, params, outputs, inputs or {}, tuple(aliases), description)
        return fn
    return register


def resolve(name_or_alias: str) -> BlockSpec:
    key = name_or_alias.strip().lower()
    for spec in REGISTRY.values():
        if key == spec.name or key in spec.aliases:
            return spec
    raise KeyError(f"unknown block {name_or_alias!r}")


def compute(name: str, bars: Bars, params: dict | None = None, **inputs) -> dict:
    spec = resolve(name)
    missing = set(spec.inputs) - set(inputs)
    if missing:
        raise TypeError(f"{spec.name} needs inputs {sorted(missing)}")
    return spec.fn(bars, spec.params(**(params or {})), **inputs)


def catalog() -> dict:
    return {
        "blocks": [
            {
                "name": s.name,
                "description": s.description,
                "aliases": list(s.aliases),
                "params": s.params.model_json_schema(),
                "inputs": s.inputs,
                "outputs": s.outputs,
            }
            for s in sorted(REGISTRY.values(), key=lambda s: s.name)
        ],
        "unsupported": UNSUPPORTED,
    }
