"""The parse prompt, generated from the block catalog and the spec schema.

Nothing here is hand-maintained per block: add a block to the registry and it's in the prompt.
The few-shots (fewshots.py) are sent as earlier user/assistant turns, so the model sees the exact
envelope shape it must return.
"""

import json
from functools import lru_cache

from engine.blocks import catalog
from engine.blocks.registry import strip_titles
from engine.blocks.timing import SESSIONS
from engine.llm.fewshots import FEWSHOTS
from engine.spec import json_schema

# What the spec itself can't express yet (the block catalog lists unsupported concepts).
SPEC_LIMITS = [
    "trailing stops, moving the stop to break-even, scaling in or out, partial exits",
    "stop-entry orders (buy stop / sell stop above or below price)",
    "conditions on another timeframe than the project's",
    "both directions in one strategy (one direction per strategy; the other can be a separate trial)",
    "simple moving averages (only the EMA exists)",
    "anything based on volume: this CFD data has no exchange volume",
]

RULES = """\
You turn a trader's plain-English strategy into a JSON rule spec for a backtester. You only write
JSON; a deterministic engine runs it, and the trader sees a rules card rendered from your spec.

Reply with exactly one JSON object, no prose, no code fences:
{"spec": {...} or null, "assumptions": [...], "questions": [...], "unsupported": [...]}

The spec:
- Follow the spec JSON schema below. Leave out "instrument", "timeframe" and "version": the project fixes them.
- Use only blocks from the catalog, by their "name". Each block's params must fit its param schema.
  Leave a param out to use its default; the engine fills defaults and tells the trader which ones it used.
- A block with several outputs needs "output". Events (bool per bar) go in setup steps and in
  {"event": ...} filters. Levels (prices) go in comparisons with prices, and in entry/stop/target "at".
  Series (numbers, like ATR) only compare with numbers.
- "setup" is the sequence of events that triggers a trade. Every step after the first has "within":
  how many bars after the previous step it must happen. The trade signal is the last step's bar.
- "filters" must all hold on the signal bar. Use {"all": [...]}, {"any": [...]}, {"not": ...} to combine.
- A price from the setup's own geometry is {"ref": "<step name>.<output>"}, like "gap.top" or
  "sweep.extreme" (the sweep candle's extreme). A block level is the block itself, like the last swing low.
- A comparison is {"left": ..., "op": ..., "right": ...} with op one of > < >= <= crosses_above crosses_below.
  A bar price is {"price": "close"} (or open/high/low). A cross is an event, so it can be a setup step:
  "a candle closes above X" is {"left": {"price": "close"}, "op": "crosses_above", "right": X}.
- Distances (stop points, stop buffer, limit offset, slippage) are price points.
- A target given in points with a stop in points is an R multiple: target points / stop points.
- Times are New York time. Killzones: {sessions}.
- Count "within" and "max_bars_held" in bars of the project's timeframe (1 hour of 5m bars = 12).

Never invent the trading decisions. Direction, entry, stop and target come from the trader: if one
isn't stated, leave it out of the spec and ask. Fill in nothing else that changes results without
saying so in "assumptions".

"assumptions": one short sentence per interpretation of the trader's words ("NY morning session is
the NY AM killzone", "a 30 point target with a 15 point stop is 2R"). Don't list block defaults.

"questions": only when direction, entry, stop or target is missing, or something is genuinely
ambiguous (both directions, distances in ticks or pips, a level that could mean two things). Short,
one per question, offering the options the spec supports. Keep the rest of the spec you're sure of.
Only ask when the answer would change the spec. If the spec can only express the trader's words one
way (filters are checked on the signal bar, not on earlier steps), use it and say so in "assumptions".

"unsupported": {"concept": ..., "reason": ...} for every part of the strategy the catalog and spec
can't express. Never replace it with something similar. If what's left is still the trader's
strategy, return it and say in assumptions what was left out; if nothing meaningful is left, spec is null.
Not supported: {unsupported}; and in the spec: {limits}.
"""


def _sessions() -> str:
    return ", ".join(f"{name} {a}-{b}" for name, (a, b) in SESSIONS.items())


def _unsupported(cat: dict) -> str:
    return "; ".join(f"{k.replace('_', ' ')} ({v})" for k, v in cat["unsupported"].items())


@lru_cache(maxsize=1)
def system_prompt() -> str:
    cat = catalog()
    rules = (RULES.replace("{sessions}", _sessions())
             .replace("{unsupported}", _unsupported(cat))
             .replace("{limits}", "; ".join(SPEC_LIMITS)))
    blocks = json.dumps(cat["blocks"], separators=(",", ":"))
    schema = json.dumps(strip_titles(json_schema()), separators=(",", ":"))
    return f"{rules}\nBlock catalog:\n{blocks}\n\nSpec JSON schema:\n{schema}\n"


def user_message(prompt: str, instrument: str, timeframe: str, answers: list[dict] | None = None) -> str:
    text = f"Instrument: {instrument}. Timeframe: {timeframe} bars.\nStrategy: {prompt.strip()}"
    if answers:
        qa = "\n".join(f"- Q: {a['question'].strip()}\n  A: {a['answer'].strip()}" for a in answers)
        text += f"\nAnswers to your questions:\n{qa}"
    return text


def messages(prompt: str, instrument: str, timeframe: str, answers: list[dict] | None = None) -> list[dict]:
    out = [{"role": "system", "content": system_prompt()}]
    for ex in FEWSHOTS:
        out.append({"role": "user", "content": user_message(ex["prompt"], instrument, ex["timeframe"])})
        out.append({"role": "assistant", "content": json.dumps(ex["envelope"], separators=(",", ":"))})
    out.append({"role": "user", "content": user_message(prompt, instrument, timeframe, answers)})
    return out


ENVELOPE_SCHEMA = {
    "type": "object",
    "properties": {
        "spec": {"type": ["object", "null"]},
        "assumptions": {"type": "array", "items": {"type": "string"}},
        "questions": {"type": "array", "items": {"type": "string"}},
        "unsupported": {"type": "array", "items": {
            "type": "object", "properties": {"concept": {"type": "string"}, "reason": {"type": "string"}},
            "required": ["concept", "reason"]}},
    },
    "required": ["spec", "assumptions", "questions", "unsupported"],
}
