"""Plain English -> a validated spec, through the LLM.

    result = parse("Buy the FVG after a sweep of the Asia low ...", "US100", "5m", make_llm())

1. The model returns an envelope {spec, assumptions, questions, unsupported} (prompt.py).
2. A missing trading decision (direction, setup, entry, stop, target) is never filled in: it
   becomes a question, the model's own or a template one if the model didn't ask.
3. Otherwise the spec goes through validate_spec. An invalid one gets one retry with the
   validator's error sent back to the model; if that fails too the result is "invalid", with
   the error, and the trader can fix the rules card by hand.
4. A ready spec comes back canonical, with the defaults the engine filled in listed by the
   engine (`defaults`), so no default reaches a backtest without the trader seeing it.

Statuses: ready | questions | unsupported (nothing testable left) | invalid.
Strategy parts the engine can't test are listed in `unsupported` even when the rest is ready; the
model is asked to list them, and a keyword check on the trader's words catches the ones it drops.
"""

import json
import re
from dataclasses import asdict, dataclass, field

from pydantic import BaseModel

from engine.blocks.registry import UNSUPPORTED, resolve
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
        base = dict(draft=spec, assumptions=assumptions, unsupported=unsupported, attempts=attempts)
        if spec is None and unsupported and not questions:
            return ParseResult(UNSUPPORTED_STATUS, **base)
        missing = [k for k in DECISIONS if not (spec or {}).get(k)]
        questions += [DECISIONS[k][0] for k in missing if not _asked(questions, DECISIONS[k][1])]
        if questions:
            return ParseResult(QUESTIONS, questions=questions, **base)

        try:
            canon = validate_spec({**spec, "instrument": instrument, "timeframe": timeframe})
        except SpecError as e:
            attempts[-1].error = str(e)
            if attempt == 1:
                msgs = [*msgs, {"role": "assistant", "content": c.text},
                        {"role": "user", "content": f"The spec failed validation: {e}. Fix that and reply with the "
                                                    f"whole envelope again."}]
                continue
            return ParseResult(INVALID, error=str(e), **base)
        return ParseResult(READY, spec=canonical_dict(canon), defaults=filled_defaults(spec, instrument, timeframe),
                           card=render_card(canon), **base)
    raise AssertionError("unreachable")


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
