"""Spec validation, canonical form and hash identity."""

import copy
import json
import re

import pytest

from engine.spec import SpecError, Strategy, canonical_dict, canonical_json, json_schema, spec_hash, validate_spec
from engine.spec.examples import DISPLACEMENT_SHORT, EXAMPLES, FVG_LONG


def h(data: dict) -> str:
    return spec_hash(validate_spec(data))


def edited(data: dict, path: str, value) -> dict:
    """Copy of `data` with the value at a dotted path (list indexes as numbers) replaced."""
    out = copy.deepcopy(data)
    node = out
    *parents, last = [int(p) if p.isdigit() else p for p in path.split(".")]
    for p in parents:
        node = node[p]
    node[last] = value
    return out


# --- examples validate and canonicalize ---------------------------------------------

@pytest.mark.parametrize("name", sorted(EXAMPLES))
def test_examples_validate_and_hash(name):
    spec = validate_spec(EXAMPLES[name])
    assert isinstance(spec, Strategy)
    assert re.fullmatch(r"[0-9a-f]{64}", spec_hash(spec))


@pytest.mark.parametrize("name", sorted(EXAMPLES))
def test_canonical_form_is_a_fixed_point(name):
    canon = canonical_dict(validate_spec(EXAMPLES[name]))
    assert canonical_dict(validate_spec(canon)) == canon
    assert json.loads(canonical_json(validate_spec(canon))) == canon


def test_canonical_form_fills_defaults_and_names():
    canon = canonical_dict(validate_spec(FVG_LONG))
    gap = canon["setup"][1]["event"]
    assert gap == {"block": "fvg", "params": {"direction": "bullish", "min_size_atr": 0.0, "max_age": 100},
                   "inputs": {}, "output": "formed"}
    assert canon["filters"][0]["event"]["output"] == "value"  # single output named explicitly
    assert canon["entry"]["offset_ticks"] == 0 and canon["limits"]["max_bars_held"] is None
    assert canon["slippage_ticks"] == 0 and canon["version"] == 1


# --- hash identity: what is and isn't a new trial ---------------------------------------

def test_hash_ignores_key_order_and_whitespace():
    shuffled = json.loads(json.dumps(FVG_LONG, sort_keys=True, indent=4))
    reordered = dict(reversed(list(shuffled.items())))
    assert h(FVG_LONG) == h(shuffled) == h(reordered)


def test_hash_ignores_spelled_out_defaults():
    explicit = edited(FVG_LONG, "setup.1.event.params", {"direction": "bullish", "min_size_atr": 0, "max_age": 100})
    explicit = edited(explicit, "entry.offset_ticks", 0)
    explicit["slippage_ticks"] = 0
    explicit["limits"] = {"max_trades_per_day": 1, "max_bars_held": None}
    explicit["entry"]["type"] = "limit"
    assert h(explicit) == h(FVG_LONG)


def test_hash_ignores_aliases_and_implicit_output():
    aliased = edited(FVG_LONG, "setup.1.event.block", "Fair Value Gap")
    aliased = edited(aliased, "setup.0.event.block", "turtle soup")
    aliased = edited(aliased, "filters.0.event.output", "value")  # the example leaves the single output implicit
    assert h(aliased) == h(FVG_LONG)


def test_hash_ignores_int_vs_float():
    assert h(edited(FVG_LONG, "target.r", 2.0)) == h(edited(FVG_LONG, "target.r", 2))


@pytest.mark.parametrize("path,value", [
    ("setup.1.event.params.max_age", 50),
    ("setup.1.within", 12),
    ("stop.buffer_ticks", 0),
    ("stop", {"type": "level", "at": {"ref": "sweep.extreme"}}),
    ("target.r", 2.5),
    ("entry.expiry_bars", 10),
    ("entry", {"type": "market"}),
    ("direction", "short"),
    ("timeframe", "15m"),
    ("slippage_ticks", 1),
    ("limits.max_trades_per_day", 2),
    ("filters", []),
    ("filters.0.event.params.session", "ny_pm"),
])
def test_any_rule_change_is_a_new_trial(path, value):
    assert h(edited(FVG_LONG, path, value)) != h(FVG_LONG)


def test_different_examples_differ():
    assert h(FVG_LONG) != h(DISPLACEMENT_SHORT)


# --- semantic errors point at the field --------------------------------------------------

@pytest.mark.parametrize("path,value,where,words", [
    ("setup.1.event.block", "vwap", "setup[1].event.block", "unknown block"),
    ("setup.1.event.params.max_age", 0, "setup[1].event.params", "max_age"),
    ("setup.1.event.params", {"direction": "bullish", "bogus": 1}, "setup[1].event.params", "bogus"),
    ("setup.1.event.output", "top", "setup[1].event.output", "level"),  # a level where an event is needed
    ("setup.1.event.output", "size", "setup[1].event.output", "no output"),
    ("setup.0.event.inputs", {}, "setup[0].event.inputs", "needs inputs"),
    ("setup.0.event.inputs.level.output", "high", "setup[0].event.inputs.level.output", None),  # fine, see below
    ("setup.0.event.inputs.level", {"block": "time_window"}, "setup[0].event.inputs.level.output", "event"),
    ("filters.0.event", {"block": "swing"}, "filters[0].event.output", "say which"),
    ("filters.0", {"left": {"price": "close"}, "op": ">", "right": {"block": "fvg", "output": "formed"}},
     "filters[0].right.output", "event"),
    ("entry.at", {"ref": "gap.size"}, "entry.at.ref", "no output"),
    ("entry.at", {"ref": "nope.top"}, "entry.at.ref", "no setup step"),
    ("entry.at", {"ref": "gap.formed"}, "entry.at.ref", "not a price"),
    ("stop.at", {"block": "displacement"}, "stop.at.output", "event"),
    ("target", {"type": "level", "at": {"ref": "sweep.value"}}, "target.at.ref", "not a price"),
])
def test_semantic_errors(path, value, where, words):
    data = edited(FVG_LONG, path, value)
    if words is None:
        validate_spec(data)
        return
    with pytest.raises(SpecError) as e:
        validate_spec(data)
    assert e.value.path == where
    assert words in e.value.message.lower()


@pytest.mark.parametrize("mutate,words", [
    (lambda d: d.update(direction="both"), "direction"),
    (lambda d: d.update(instrument="SPX"), "instrument"),
    (lambda d: d.update(timeframe="1h"), "timeframe"),
    (lambda d: d.update(setup=[]), "setup"),
    (lambda d: d["setup"][0].update(within=5), "first setup step"),
    (lambda d: d["setup"][1].pop("within"), "needs `within`"),
    (lambda d: d["setup"][1].update(name="sweep"), "unique"),
    (lambda d: d["setup"][1].update(name="Gap"), "pattern"),
    (lambda d: d.update(entry={"type": "stop", "at": {"ref": "gap.top"}}), "entry"),
    (lambda d: d.update(manage={"break_even_r": 1}), "manage"),
    (lambda d: d.update(entry={"type": "limit", "at": {"ref": "gap top"}}), "ref"),
    (lambda d: d["filters"].append({"not": {"all": []}}), "all"),
    (lambda d: d.pop("stop"), "stop"),
])
def test_shape_errors(mutate, words):
    data = copy.deepcopy(FVG_LONG)
    mutate(data)
    with pytest.raises(SpecError) as e:
        validate_spec(data)
    assert words in str(e.value)


# --- schema export ----------------------------------------------------------------------------

def test_json_schema_exports():
    schema = json_schema()
    json.dumps(schema)
    assert set(schema["required"]) == {"instrument", "timeframe", "direction", "setup", "stop", "target"}
    assert "US100" in json.dumps(schema["properties"]["instrument"])
