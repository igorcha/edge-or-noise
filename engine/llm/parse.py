"""Plain English -> a validated spec, through the LLM.

    result = parse("Buy the FVG after a sweep of the Asia low ...", "US100", "5m", make_llm())

1. The model returns an envelope {spec, assumptions, questions, unsupported} (prompt.py).
2. A missing trading decision (direction, setup, entry, stop, target) is never filled in: it
   becomes a question, the model's own or a template one if the model didn't ask. (A reply with
   questions and no spec keeps just the model's questions; the answers come back through here.)
3. Otherwise the spec goes through validate_spec. An invalid one gets one retry with the
   validator's error sent back to the model; if that fails too the result is "invalid", with
   the error, and the trader can fix the rules card by hand.
4. A ready spec comes back canonical, with the defaults the engine filled in listed by the
   engine (`defaults`), so no default reaches a backtest without the trader seeing it.

A distance in ticks or pips is always a question too (UNIT_WORDS): the spec counts price points.

Statuses: ready | questions | unsupported (nothing testable left) | invalid.
Strategy parts the engine can't test are listed in `unsupported` even when the rest is ready; the
model is asked to list them, and a keyword check on the trader's words catches the ones it drops.
"""

import json
import re
from dataclasses import asdict, dataclass, field

from pydantic import BaseModel

from engine.blocks.registry import UNSUPPORTED, resolve
from engine.blocks.timing import SESSIONS
from engine.blocks.types import EVENT
from engine.llm.client import LLM
from engine.llm.prompt import ENVELOPE_SCHEMA, messages
from engine.spec import SpecError, Strategy, canonical_dict, validate_spec
from engine.spec.card import render_card
from engine.spec.models import BlockRef

READY, QUESTIONS, UNSUPPORTED_STATUS, INVALID = "ready", "questions", "unsupported", "invalid"

# The decisions only the trader can make, with the question asked when one is missing, and the words
# that show the model already asked about it.
DECISIONS = {
    "direction": ("Long or short? Each strategy tests one direction.", ("long", "short", "direction")),
    "setup": ("What triggers the trade? Describe the events that must happen, in order.",
              ("trigger", "setup", "signal", "event")),
    "entry": ("How do you enter: at market on the next candle's open, or with a limit order at a price (which one)?",
              ("enter", "entry")),
    "stop": ("Where is your stop: at a level (like the last swing low), a fixed number of points, or a multiple of "
             "ATR?", ("stop",)),
    "target": ("Where do you take profit: a multiple of your risk (like 2R), or a level such as the prior day high?",
               ("target", "profit", "take")),
}

# Concepts the trader may mention that no block covers: (pattern, name, words that show it's listed).
UNSUPPORTED_TERMS = [
    (r"\bvwap\b", "VWAP", ("vwap",)),
    (r"\brsi\b|relative strength", "RSI", ("rsi", "relative strength")),
    (r"\bbollinger", "Bollinger Bands", ("bollinger",)),
    (r"\bmacd\b", "MACD", ("macd",)),
    (r"\bstoch(astic)?\b", "Stochastic", ("stoch",)),
    (r"\border ?blocks?\b", "order blocks", ("order block",)),
    (r"\bsmt\b", "SMT divergence", ("smt",)),
    (r"\bfib(onacci|s)?\b|\bote\b|optimal trade entry", "Fibonacci levels", ("fib", "ote", "optimal trade")),
    (r"\bvolume\b", "volume", ("volume", "vwap")),
    (r"\bsma\b|simple moving average", "simple moving average", ("sma", "simple moving")),
    (r"\btrail(ing)?\b|break[- ]?even", "trailing or break-even stops", ("trail", "break")),
    (r"\bmidnight open\b", "NY midnight open", ("midnight",)),
    (r"\bequal (highs|lows)\b", "equal highs/lows", ("equal",)),
]
REGISTRY_REASONS = {"VWAP": UNSUPPORTED["vwap"], "RSI": UNSUPPORTED["rsi"], "SMT divergence": UNSUPPORTED["smt_divergence"],
                    "volume": "this CFD data has no exchange volume"}

# Spec distances are price points. A tick or a pip is a different size on futures and CFDs, so a distance
# given in them is always asked about: a model converting it on its own guess can be off by 25x.
UNIT_WORDS = re.compile(r"\b(tick|pip)s?\b", re.I)
UNIT_QUESTION = "You gave a distance in {unit}s. How many price points is that? Distances here are in points."

# Words that show the trader stated a decision. An entry, stop or target with none of them in the trader's words
# is the model's guess (Nemotron answered "buy the FVG after a sweep" with a limit at the gap top, a stop at the
# sweep and 2R): it's dropped, and asked like any other missing decision.
STATED = {
    "entry": re.compile(r"\benter|\bentry|\blimit\b|\bmarket\b|\b(at|on) (the|its) |\bnext (candle'?s |bar'?s )?open", re.I),
    "stop": re.compile(r"\bstop(?![- ]?runs?\b)|\bsl\b|\binvalidat", re.I),
    "target": re.compile(r"\btarget|\btp\b|take[- ]?profit|\b\d+(\.\d+)?\s*r\b|\b\d+\s*:\s*\d+\b|\brr\b|\bprofit|\breward"
                         r"|\bexit", re.I),
}
# One direction per strategy. A trader trading both ways, or a model saying it had to pick one, gets the question;
# so does a strategy on both sides of a range ("fade sweeps of the Asia high and low") that never names a side.
BOTH_WAYS = re.compile(r"\b(either|both) (direction|way|side)s?\b|\blongs? and shorts?\b|\bone direction\b", re.I)
BOTH_SIDES = re.compile(r"\bhighs? and (the )?(\w+ )?lows?\b|\blows? and (the )?(\w+ )?highs?\b", re.I)
A_SIDE = re.compile(r"\b(buy|buys|long|longs|bull|bullish|sell|sells|short|shorts|bear|bearish)\b", re.I)

# Conditions the trader named that the spec must then contain: (pattern, text the spec's JSON must have, what it is).
# Nemotron sometimes leaves out the session while its assumptions say "filtered to NY AM", or sweeps the prior day
# low for "the Asia low": the reply goes back once, and if it's still wrong the trader is told.
_HL = r"(high|low)s?\b"
NAMED = [
    (re.compile(r"\b(ny|new york) (am|pm|morning|afternoon)\b|\bkill ?zones?\b"
                r"|\blondon (session|killzone|open)\b(?! (range|high|low))|\bduring (the )?london\b", re.I),
     '"time_window"', "a time_window filter"),
    (re.compile(r"\b(mon|tues|wednes|thurs|fri)days?\b", re.I), '"day_of_week"', "a day_of_week filter"),
    (re.compile(r"\basian? (session |range |killzone )?" + _HL, re.I), '"window": "asia"', "the asia window"),
    (re.compile(r"\blondon (session |range |killzone )?" + _HL, re.I), '"window": "london"', "the london window"),
    (re.compile(r"\b(prior|previous) day'?s? " + _HL + r"|\byesterday'?s? " + _HL + r"|\bpd[hl]s?\b", re.I),
     '"window": "day"', "the day window"),
    (re.compile(r"\b(prior|previous|last) week'?s? " + _HL + r"|\bpw[hl]s?\b", re.I), '"window": "week"',
     "the week window"),
]

# "Stop below the higher low": Nemotron points the stop at the event ({"ref": "hl.higher_low"}) when it means the
# price of the swing that event confirmed, which is the step's own level ("hl.low"). Sent back, it fixed the ref and
# dropped the stop's buffer, so the parser points it there itself.
SWING_PRICE = {"new_high": "high", "higher_high": "high", "lower_high": "high",
               "new_low": "low", "higher_low": "low", "lower_low": "low"}
# A step's candle is only the trader's when they say so: "below the higher low" is the swing, not its candle.
CANDLE_WORDS = re.compile(r"\bcandle|\bbar\b|\bwick", re.I)

# How long a later setup step may wait for the one before it changes the trade count by orders of magnitude, and
# Nemotron fills an unstated one with anything from 1 to 500 bars ("after the sweep, wait for a higher low"). With
# no time in the trader's words, it's asked.
WAIT_WORDS = re.compile(r"\bwithin\b|\b(\d+|an?|one|two|three|four|five|six|ten|twelve|twenty|thirty)[ -]?"
                        r"(bars?|candles?|min(ute)?s?|hours?|hrs?|h)\b|\bnext (bar|candle)\b", re.I)
WAIT_QUESTION = ("How many bars can each step of your setup ({steps}) wait for the one before it? On {tf} bars, "
                 "1 hour is {per_hour} bars (at most 500).")


@dataclass
class Attempt:
    text: str  # the model's raw reply
    error: str | None  # why it was sent back, if it was
    seconds: float
    tokens: int | None


@dataclass
class ParseResult:
    status: str
    spec: dict | None = None  # canonical, only when ready
    draft: dict | None = None  # the model's spec as written, for questions/invalid
    assumptions: list[str] = field(default_factory=list)  # the model's reading of the trader's words
    defaults: list[str] = field(default_factory=list)  # defaults the engine filled in
    questions: list[str] = field(default_factory=list)
    unsupported: list[dict] = field(default_factory=list)
    error: str | None = None
    card: dict | None = None
    attempts: list[Attempt] = field(default_factory=list)

    def to_json(self) -> dict:
        return asdict(self)


def parse(prompt: str, instrument: str, timeframe: str, llm: LLM, answers: list[dict] | None = None) -> ParseResult:
    msgs = messages(prompt, instrument, timeframe, answers)
    words = " ".join([prompt, *(a.get("answer", "") for a in answers or [])])
    attempts: list[Attempt] = []
    for attempt in (1, 2):
        c = llm.complete(msgs, ENVELOPE_SCHEMA)
        tokens = (c.prompt_tokens or 0) + (c.completion_tokens or 0) if c.prompt_tokens is not None else None
        try:
            spec, assumptions, questions, unsupported = read_envelope(c.text)
        except ValueError as e:
            attempts.append(Attempt(c.text, f"not a valid envelope: {e}", c.seconds, tokens))
            if attempt == 1:
                msgs = [*msgs, {"role": "assistant", "content": c.text},
                        {"role": "user", "content": f"That reply was not a valid envelope ({e}). Reply with only the "
                                                    f"JSON object {{spec, assumptions, questions, unsupported}}."}]
                continue
            return ParseResult(INVALID, error=f"the model's reply was not a valid envelope: {e}", attempts=attempts)

        attempts.append(Attempt(c.text, None, c.seconds, tokens))
        unsupported = flag_unsupported(words, unsupported)
        if spec:
            spec, assumptions = keep_to_the_traders_words(spec, assumptions, words, answers)
        base = dict(draft=spec, assumptions=assumptions, unsupported=unsupported, attempts=attempts)
        if spec is None and unsupported and not questions:
            return ParseResult(UNSUPPORTED_STATUS, **base)
        # Template questions fill gaps in a spec the model wrote. With no spec but questions of its own, the
        # model's questions stand: asking "long or short?" after "buy ..." would read as not listening, and
        # nothing runs before the answers come back through here anyway.
        missing = [k for k in DECISIONS if not (spec or {}).get(k)] if spec or not questions else []
        questions += [DECISIONS[k][0] for k in missing if not _asked(questions, DECISIONS[k][1])]
        unit = UNIT_WORDS.search(prompt)
        if unit and not _asked([*questions, *(a.get("question", "") for a in answers or [])], ("tick", "pip")):
            questions.append(UNIT_QUESTION.format(unit=unit.group(1).lower()))
        asked = [*questions, *(a.get("question", "") for a in answers or [])]
        if not _asked(asked, ("how many bars", "within")) and (wait := unstated_wait(spec, words, timeframe)):
            questions.append(wait)
            base["assumptions"] = [a for a in assumptions if not re.search(r"within|number of bars", a, re.I)]
        if questions:
            return ParseResult(QUESTIONS, questions=questions, **base)
        gaps = left_out(words, spec)
        if gaps and attempt == 1:
            attempts[-1].error = "; ".join(gaps)
            msgs = [*msgs, {"role": "assistant", "content": c.text},
                    {"role": "user", "content": f"The spec leaves out what the trader said: {'; '.join(gaps)}. Put it "
                                                f"where the schema allows it and reply with the whole envelope again."}]
            continue
        assumptions += [f"Your words mention {g}, but these rules don't. Check the rules card." for g in gaps]

        try:
            canon = validate_spec({**spec, "instrument": instrument, "timeframe": timeframe})
        except SpecError as e:
            attempts[-1].error = str(e)
            if attempt == 1:
                msgs = [*msgs, {"role": "assistant", "content": c.text},
                        {"role": "user", "content": f"The spec failed validation: {e}. Fix that and reply with the "
                                                    f"whole envelope again. Keep every condition the trader stated: "
                                                    f"move it to where the schema allows it, don't drop it."}]
                continue
            return ParseResult(INVALID, error=str(e), **base)
        return ParseResult(READY, spec=canonical_dict(canon), defaults=filled_defaults(spec, instrument, timeframe),
                           card=render_card(canon), **base)
    raise AssertionError("unreachable")


def keep_to_the_traders_words(spec: dict, assumptions: list[str], words: str,
                              answers: list[dict] | None) -> tuple[dict, list[str]]:
    """What the parser checks itself instead of trusting the model: decisions the trader never stated (in
    the prompt, or by answering a question about them) are dropped, so they become questions, and
    conditions written on a setup step move to the filters."""
    asked = [a.get("question", "") for a in answers or []]
    dropped = [k for k, stated in STATED.items()
               if k in spec and not stated.search(words) and not _asked(asked, DECISIONS[k][1])]
    both = BOTH_WAYS.search(" ".join([words, *assumptions])) or (BOTH_SIDES.search(words) and not A_SIDE.search(words))
    if both and "direction" in spec and not _asked(asked, DECISIONS["direction"][1]):
        dropped.append("direction")
    spec = {k: v for k, v in spec.items() if k not in dropped}
    # the model's reasons for what it filled in go with it
    said = [w for k in dropped for w in DECISIONS[k][1]]
    assumptions = [a for a in assumptions if not any(w in a.lower() for w in said)]
    point_swing_refs_at_prices(spec, words)
    name_the_event_outputs(spec)
    candle_prices_are_the_signal_candle(spec)
    times_make_a_custom_window(spec)
    steps = spec.get("setup")
    if isinstance(steps, list) and steps and isinstance(steps[0], dict):
        steps[0].pop("within", None)  # the first step has no step before it to wait for
    return spec, assumptions + lift_step_filters(spec)


def left_out(words: str, spec: dict) -> list[str]:
    """Sessions, weekdays and session levels the trader named that the spec doesn't use."""
    text = json.dumps(spec)
    return [f'"{m.group(0)}" ({what})' for pattern, needle, what in NAMED
            if (m := pattern.search(words)) and needle not in text]


def unstated_wait(spec: dict | None, words: str, timeframe: str) -> str | None:
    """The question for a setup of several steps when the trader gave no time at all."""
    steps = (spec or {}).get("setup")
    if not isinstance(steps, list) or len(steps) < 2 or WAIT_WORDS.search(words):
        return None
    minutes = int(timeframe[:-1]) * (60 if timeframe.endswith("h") else 1)
    names = [str(s.get("name")) if isinstance(s, dict) and s.get("name") else f"step {i + 1}"
             for i, s in enumerate(steps)]
    return WAIT_QUESTION.format(steps=", then ".join(names), tf=timeframe, per_hour=max(1, 60 // minutes))


# The blocks with a custom NY window, and the param that names it.
WINDOW_PARAM = {"time_window": "session", "window_high_low": "window"}


def times_make_a_custom_window(spec) -> None:
    """"Between 08:30 and 11:00": Nemotron writes time_window {"start", "end"} without "session": "custom",
    every time, and the retry doesn't always fix it. Times are the trader's own words, so they win: a named
    session when they're exactly its times, otherwise a custom window."""
    if isinstance(spec, list):
        for x in spec:
            times_make_a_custom_window(x)
        return
    if not isinstance(spec, dict):
        return
    key = WINDOW_PARAM.get(spec.get("block")) if isinstance(spec.get("block"), str) else None
    params = spec.get("params")
    if key and isinstance(params, dict) and params.get("start") and params.get("end"):
        named = next((name for name, (a, b) in SESSIONS.items() if (a, b) == (params["start"], params["end"])), None)
        if named:
            spec["params"] = {k: v for k, v in params.items() if k not in ("start", "end")} | {key: named}
        else:
            params[key] = "custom"
    for v in spec.values():
        times_make_a_custom_window(v)


def candle_prices_are_the_signal_candle(spec: dict) -> None:
    """"Stop below the low of the signal candle": Nemotron writes the stop at {"price": "low"}, a condition's
    bar price. As an entry, stop or target level it can only mean the signal candle's, the last step's."""
    steps = spec.get("setup")
    last = steps[-1].get("name") if isinstance(steps, list) and steps and isinstance(steps[-1], dict) else None
    if not isinstance(last, str):
        return
    for key in ("entry", "stop", "target"):
        part = spec.get(key)
        at = part.get("at") if isinstance(part, dict) else None
        if isinstance(at, dict) and set(at) == {"price"} and at["price"] in ("open", "high", "low", "close"):
            part["at"] = {"ref": f"{last}.candle_{at['price']}"}


def name_the_event_outputs(spec: dict) -> None:
    """A setup step is an event. Its block written without "output" ("sweep") means the block's one event
    output when it has exactly one (sweep "value", fvg "formed"). Nemotron leaves it out, then fails the
    retry; with several event outputs (swing) it stays a validation error."""
    for step in spec.get("setup") or []:
        ev = step.get("event") if isinstance(step, dict) else None
        if not isinstance(ev, dict) or ev.get("output") or not isinstance(ev.get("block"), str):
            continue
        try:
            outputs = resolve(ev["block"]).outputs
        except KeyError:
            continue
        events = [k for k, kind in outputs.items() if kind == EVENT]
        if len(outputs) > 1 and len(events) == 1:
            ev["output"] = events[0]


def point_swing_refs_at_prices(spec: dict, words: str = "") -> None:
    """A swing step used as a price means that swing's price ("hl.low"), however Nemotron writes it: its event
    ("hl.higher_low"), the bare step ("hl"), or, when the trader never said "candle" or "bar", the candle that
    confirmed it ("hl.candle_low": bars after the swing, not the higher low)."""
    swings = {}  # step name -> the swing's event output
    for step in spec.get("setup") or []:
        try:
            if resolve(step["event"]["block"]).name == "swing":
                swings[step["name"]] = step["event"].get("output")
        except (KeyError, TypeError, AttributeError):
            pass
    said_candle = bool(CANDLE_WORDS.search(words))

    def walk(x):
        if isinstance(x, dict):
            step, dot, out = str(x.get("ref", "")).partition(".")
            if step in swings and out in SWING_PRICE:
                x["ref"] = f"{step}.{SWING_PRICE[out]}"
            elif step in swings and not dot and swings[step] in SWING_PRICE:
                x["ref"] = f"{step}.{SWING_PRICE[swings[step]]}"
            elif step in swings and out in ("candle_low", "candle_high") and not said_candle:
                x["ref"] = f"{step}.{out.removeprefix('candle_')}"
            for v in x.values():
                walk(v)
        elif isinstance(x, list):
            for v in x:
                walk(v)
    walk({k: v for k, v in spec.items() if k != "setup"})


def lift_step_filters(spec: dict) -> list[str]:
    """Models put a session or day on the setup step it describes ("an FVG during NY AM"). Steps have no
    filters: the spec checks filters on the signal bar. Move them there rather than lose them, and say so
    when the step isn't the last one. Returns the assumptions to add."""
    steps = spec.get("setup")
    if not isinstance(steps, list):
        return []
    moved, notes = [], []
    for i, step in enumerate(steps):
        if isinstance(step, dict) and step.get("filters"):
            f = step.pop("filters")
            moved += f if isinstance(f, list) else [f]
            if i < len(steps) - 1:
                notes.append(f"The conditions on \"{step.get('name', i + 1)}\" are checked on the signal bar, the "
                             f"last step: filters apply there.")
        elif isinstance(step, dict):
            step.pop("filters", None)
    if moved:
        top = spec.get("filters") or []
        spec["filters"] = (top if isinstance(top, list) else [top]) + moved
    return notes


# --- reading the reply ---------------------------------------------------------------------------

_THINK = re.compile(r"<think>.*?</think>", re.S)


def extract_json(text: str) -> dict:
    """The JSON object in a reply, ignoring reasoning blocks and code fences around it."""
    t = _THINK.sub("", text)
    if "</think>" in t:  # the opening tag was in the chat template, not the reply
        t = t.split("</think>", 1)[1]
    start, end = t.find("{"), t.rfind("}")
    if start < 0 or end < start:
        raise ValueError("no JSON object in the reply")
    try:
        obj = json.loads(t[start:end + 1])
    except json.JSONDecodeError as e:
        raise ValueError(f"invalid JSON: {e}") from None
    if not isinstance(obj, dict):
        raise ValueError("the reply is not a JSON object")
    return obj


def read_envelope(text: str) -> tuple[dict | None, list[str], list[str], list[dict]]:
    obj = extract_json(text)
    if "spec" not in obj and ("setup" in obj or "direction" in obj):
        obj = {"spec": obj}  # a bare spec: tolerate it, there's nothing else to say
    spec = obj.get("spec")
    if spec is not None and not isinstance(spec, dict):
        raise ValueError("spec must be an object or null")
    for key in ("instrument", "timeframe", "version"):  # the project fixes these
        (spec or {}).pop(key, None)
    return (spec or None, _strings(obj.get("assumptions"), "assumptions"), _strings(obj.get("questions"), "questions"),
            [_concept(u) for u in _list(obj.get("unsupported"), "unsupported")])


def _list(v, name: str) -> list:
    if v is None:
        return []
    if not isinstance(v, list):
        raise ValueError(f"{name} must be a list")
    return v


def _strings(v, name: str) -> list[str]:
    return [s.strip() for s in _list(v, name) if isinstance(s, str) and s.strip()]


def _concept(u) -> dict:
    if isinstance(u, str):
        return {"concept": u, "reason": "not supported"}
    if isinstance(u, dict) and u.get("concept"):
        return {"concept": str(u["concept"]), "reason": str(u.get("reason") or "not supported")}
    raise ValueError("unsupported items are {concept, reason}")


def _asked(questions: list[str], words: tuple[str, ...]) -> bool:
    text = " ".join(questions).lower()
    return any(w in text for w in words)


def flag_unsupported(words: str, unsupported: list[dict]) -> list[dict]:
    """Add unsupported concepts the trader mentioned but the model didn't list."""
    listed = " ".join(f"{u['concept']} {u['reason']}" for u in unsupported).lower()
    out = list(unsupported)
    for pattern, name, markers in UNSUPPORTED_TERMS:
        if re.search(pattern, words, re.I) and not any(m in listed for m in markers):
            out.append({"concept": name, "reason": REGISTRY_REASONS.get(name, "not available yet")})
    return out


# --- defaults the engine filled ----------------------------------------------------------------------

LABELS = {
    "limits.max_trades_per_day": lambda v: f"at most {v} trade{'s' if v != 1 else ''} per trading day",
    "slippage_pts": lambda v: f"{v:g} points of slippage per side, on top of the spread",
    "entry.expiry_bars": lambda v: f"the limit order is cancelled if not filled within {v} bars",
    "stop.multiple": lambda v: f"stop at {v:g} x ATR",
    "stop.length": lambda v: f"ATR length {v} for the stop",
    "target.r": lambda v: f"target {v:g}R",
}
QUIET = {"version", "instrument", "timeframe", "type", "offset_pts", "buffer_pts", "filters"}  # nothing to tell


def filled_defaults(spec: dict, instrument: str, timeframe: str) -> list[str]:
    """Every default the engine filled into the model's spec, as one line each, in spec order."""
    out: list[str] = []
    _walk(Strategy.model_validate({**spec, "instrument": instrument, "timeframe": timeframe}), "", out)
    return list(dict.fromkeys(out))


def _walk(m, path: str, out: list[str]) -> None:
    if isinstance(m, BlockRef):
        out.extend(_block_defaults(m))
        for v in m.inputs.values():
            _walk(v, path, out)
        return
    if isinstance(m, list):
        for x in m:
            _walk(x, path, out)
        return
    if not isinstance(m, BaseModel):
        return
    for name in type(m).model_fields:
        key, value = f"{path}.{name}" if path else name, getattr(m, name)
        if name in m.model_fields_set or isinstance(value, BaseModel):
            _walk(value, key, out)  # a default sub-model (limits) still has defaults inside to list
        elif name not in QUIET and value is not None:
            out.append(LABELS.get(key, lambda v: f"{key} = {v}")(value) + " (default)")


def _block_defaults(ref: BlockRef) -> list[str]:
    try:
        block = resolve(ref.block)
        params = block.params(**ref.params)
    except (KeyError, ValueError, TypeError):
        return []
    filled = {k: getattr(params, k) for k in type(params).model_fields
              if k not in params.model_fields_set and getattr(params, k) is not None}
    if not filled:
        return []
    return [f"{block.name}: " + ", ".join(f"{k} = {_fmt(v)}" for k, v in filled.items()) + " (default)"]


def _fmt(v) -> str:
    if isinstance(v, (list, tuple)):
        return ", ".join(map(str, v))
    if isinstance(v, float):
        return f"{v:g}"
    return str(v)
