"""Spec validation, canonical form and hash identity."""

import copy
import json
import re

import pytest

from engine.spec.card import render_card
from engine.spec import SpecError, Strategy, canonical_dict, canonical_json, json_schema, spec_hash, validate_spec
from engine.spec import hashing
from engine.spec.examples import DISPLACEMENT_SHORT, EMA_CROSS_LONG, EXAMPLES, FVG_LONG, OPENING_RANGE_LONG


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
    assert canon["entry"]["offset_pts"] == 0 and canon["limits"]["max_bars_held"] is None
    assert canon["slippage_pts"] == 0 and canon["version"] == 1


# --- hash identity: what is and isn't a new trial ---------------------------------------

def test_hash_ignores_key_order_and_whitespace():
    shuffled = json.loads(json.dumps(FVG_LONG, sort_keys=True, indent=4))
    reordered = dict(reversed(list(shuffled.items())))
    assert h(FVG_LONG) == h(shuffled) == h(reordered)


def test_hash_ignores_spelled_out_defaults():
    explicit = edited(FVG_LONG, "setup.1.event.params", {"direction": "bullish", "min_size_atr": 0, "max_age": 100})
    explicit = edited(explicit, "entry.offset_pts", 0)
    explicit["slippage_pts"] = 0
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
    ("stop.buffer_pts", 0),
    ("stop", {"type": "level", "at": {"ref": "sweep.extreme"}}),
    ("target.r", 2.5),
    ("entry.expiry_bars", 10),
    ("entry", {"type": "market"}),
    ("direction", "short"),
    ("timeframe", "15m"),
    ("slippage_pts", 0.25),
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
    (lambda d: d.pop("entry"), "entry"),  # a missing entry is a question for the user, never assumed
    (lambda d: d["stop"].update(buffer_ticks=10), "buffer_ticks"),  # distances are in points
    (lambda d: d.update(stop={"type": "ticks", "ticks": 20}), "stop"),
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
    assert set(schema["required"]) == {"instrument", "timeframe", "direction", "setup", "entry", "stop", "target"}
    assert "US100" in json.dumps(schema["properties"]["instrument"])


# --- crosses ------------------------------------------------------------------------------------

def test_cross_examples_canonicalize():
    canon = canonical_dict(validate_spec(EMA_CROSS_LONG))
    assert canon["setup"][0]["event"]["op"] == "crosses_above"
    assert canon["setup"][0]["event"]["left"]["params"] == {"length": 9, "source": "close"}
    orb = canonical_dict(validate_spec(OPENING_RANGE_LONG))
    assert orb["stop"]["at"] == {"ref": "range.low"}


def test_cross_works_as_a_filter_too():
    d = edited(FVG_LONG, "filters.0", {"left": {"price": "close"}, "op": "crosses_below",
                                       "right": {"block": "ema", "params": {"length": 50}}})
    validate_spec(d)


@pytest.mark.parametrize("path,value,where,words", [
    ("setup.0.event.op", ">", "setup[0].event.op", "crosses_above"),  # a plain comparison isn't an event
    ("stop", {"type": "level", "at": {"ref": "cross.left"}}, "stop.at.ref", "cross"),
])
def test_cross_errors(path, value, where, words):
    with pytest.raises(SpecError) as e:
        validate_spec(edited(EMA_CROSS_LONG, path, value))
    assert (e.value.path, words in e.value.message) == (where, True)


def test_a_cross_step_has_its_candle_prices():
    spec = validate_spec(edited(EMA_CROSS_LONG, "stop", {"type": "level", "at": {"ref": "cross.candle_low"}, "buffer_pts": 1}))
    assert render_card(spec)["sections"]["stop"].startswith("1 point below the low of the signal candle")


# --- prices vs numbers ------------------------------------------------------------------------------

CLOSE, EMA, ATR = {"price": "close"}, {"block": "ema", "params": {"length": 50}}, {"block": "atr"}


@pytest.mark.parametrize("cond,ok", [
    ({"left": CLOSE, "op": ">", "right": EMA}, True),
    ({"left": ATR, "op": ">", "right": 20}, True),        # volatility filter
    ({"left": CLOSE, "op": ">", "right": 18000}, True),   # a constant fits either side
    ({"left": CLOSE, "op": ">", "right": ATR}, False),    # price vs points
    ({"left": EMA, "op": "crosses_above", "right": ATR}, False),
    ({"left": 1, "op": ">", "right": 2}, False),
])
def test_compare_needs_like_with_like(cond, ok):
    data = edited(FVG_LONG, "filters.0", cond)
    if ok:
        validate_spec(data)
    else:
        with pytest.raises(SpecError) as e:
            validate_spec(data)
        assert e.value.path == "filters[0]"


def test_atr_is_not_a_price_but_ema_is():
    with pytest.raises(SpecError) as e:
        validate_spec(edited(FVG_LONG, "stop.at", ATR))
    assert e.value.path == "stop.at.output" and "series" in e.value.message
    validate_spec(edited(FVG_LONG, "stop.at", EMA))


# --- same strategy, same hash --------------------------------------------------------------------

def test_filter_order_and_grouping_dont_matter():
    base = h(DISPLACEMENT_SHORT)
    reordered = copy.deepcopy(DISPLACEMENT_SHORT)
    reordered["filters"][0]["all"].reverse()
    as_list = edited(DISPLACEMENT_SHORT, "filters", DISPLACEMENT_SHORT["filters"][0]["all"])
    nested = edited(DISPLACEMENT_SHORT, "filters", [{"all": DISPLACEMENT_SHORT["filters"][0]["all"][:2]},
                                                    DISPLACEMENT_SHORT["filters"][0]["all"][2]])
    duplicated = edited(as_list, "filters", as_list["filters"] + as_list["filters"][:1])
    assert h(reordered) == h(as_list) == h(nested) == h(duplicated) == base
    anys = copy.deepcopy(EMA_CROSS_LONG)
    anys["filters"][0]["any"].reverse()
    assert h(anys) == h(EMA_CROSS_LONG)


def test_double_not_and_single_item_groups_unwrap():
    f = FVG_LONG["filters"][0]
    assert h(edited(FVG_LONG, "filters.0", {"not": {"not": f}})) == h(FVG_LONG)
    assert h(edited(FVG_LONG, "filters.0", {"any": [f]})) == h(FVG_LONG)
    assert h(edited(FVG_LONG, "filters.0", {"not": f})) != h(FVG_LONG)


def test_param_spellings_dont_matter():
    days = "filters.0.all.1.event.params.days"
    assert h(edited(DISPLACEMENT_SHORT, days, ["thu", "tue", "wed", "tue"])) == h(DISPLACEMENT_SHORT)
    custom = {"block": "time_window", "params": {"session": "custom", "start": "9:30", "end": "10:00"}}
    padded = {"block": "time_window", "params": {"session": "custom", "start": "09:30", "end": "10:00"}}
    assert h(edited(FVG_LONG, "filters.0.event", custom)) == h(edited(FVG_LONG, "filters.0.event", padded))


def test_engine_version_is_part_of_the_hash(monkeypatch):
    before = h(FVG_LONG)
    monkeypatch.setattr(hashing, "ENGINE_VERSION", hashing.ENGINE_VERSION + 1)
    assert h(FVG_LONG) != before


# --- timeframe fit and error quality ------------------------------------------------------------

def test_custom_window_must_sit_on_the_bar_grid():
    off_grid = {"block": "time_window", "params": {"session": "custom", "start": "09:30", "end": "09:50"}}
    validate_spec(edited(FVG_LONG, "filters.0.event", off_grid))  # fine on 5m bars
    with pytest.raises(SpecError) as e:
        validate_spec(edited(edited(FVG_LONG, "filters.0.event", off_grid), "timeframe", "15m"))
    assert e.value.path == "filters[0].event.params" and "grid" in e.value.message


def test_shape_errors_are_short_and_specific():
    with pytest.raises(SpecError) as e:
        validate_spec(edited(FVG_LONG, "entry", {"type": "limit"}))
    assert "at" in str(e.value) and "market" not in str(e.value).lower()
    with pytest.raises(SpecError) as e:
        validate_spec(edited(FVG_LONG, "filters.0", {"evnt": {"block": "time_window"}}))
    assert "a condition is one of" in str(e.value) and len(str(e.value)) < 200
