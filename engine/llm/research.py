"""Find a strategy on the web: Tavily searches, Nemotron writes what it found as rules the backtester can run.

    r = research("the most famous ICT strategy", "US100", make_llm(), make_search(), "5m",
                 check=parser_check(make_llm(), "US100", "5m"))
    r.summary    what the results describe, in a few sentences for someone new
    r.strategy   complete rules in the backtester's own terms: direction, when, setup, entry, stop, target
    r.choices    every gap the results left that the rules fill with a house default ("Target 2R: the results
                 only say 'the next liquidity'"), shown to the trader before they use the rules
    r.left_out   what the rules leave out and why: the short side, concepts the backtester can't test
    r.sources    the results it used, as Tavily returned them: the model picks result numbers, never writes a URL

The rules should parse without follow-up questions: the site found the strategy, so the site fills its gaps.
That's only honest because each fill is a fixed house default (DEFAULTS, chosen before any result is seen)
listed in `choices`, and nothing runs until the trader has read them and clicked "Use these rules". The parser
itself still never invents a decision.

Checks before the trader sees anything, sent back to the model together:
- every number in the rules appears in the results, the question or the choices (else it's flagged in choices)
- one direction
- `check`: the real parser reads the rules; its questions and the concepts it can't test go back to the model

Up to MAX_WRITEUPS write-ups: each one after the first fixes what the checks found, and the final rules are
checked too, so `parses` says whether the trader will get a question.

Search needs TAVILY_API_KEY (environment or the repo-root .env). Each search is one Tavily call at "advanced"
depth (2 credits) and usually two to four model calls (write-ups and parser checks), at most about nine.
"""

import json
import os
import re
import time
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass, field
from typing import Callable, Protocol

from engine.blocks import catalog
from engine.blocks.timing import SESSIONS
from engine.llm.client import LLM, LLMError, load_env_file
from engine.llm.parse import INVALID, READY, extract_json, parse
from engine.llm.prompt import SPEC_LIMITS
from engine.spec.card import SESSION_NAMES

TAVILY_URL = "https://api.tavily.com/search"
MAX_RESULTS = 5
MAX_QUERY = 400  # Tavily's advice for query length
MAX_CONTENT = 3000  # characters per result sent to the model
MAX_STRATEGY = 2000


class SearchError(RuntimeError):
    """The search service couldn't be reached or refused the request."""


class SearchNotConfigured(SearchError):
    pass


@dataclass(frozen=True)
class Result:
    title: str
    url: str
    content: str


class Search(Protocol):
    def search(self, query: str) -> list[Result]: ...


def search_configured() -> bool:
    load_env_file()
    return bool(os.environ.get("TAVILY_API_KEY"))


def make_search() -> "Tavily":
    return Tavily()


class Tavily:
    def __init__(self, api_key: str | None = None, timeout_s: float = 30.0):
        load_env_file()
        self.api_key = api_key or os.environ.get("TAVILY_API_KEY", "")
        if not self.api_key:
            raise SearchNotConfigured("TAVILY_API_KEY is not set (environment or .env)")
        self.timeout_s = timeout_s

    def search(self, query: str) -> list[Result]:
        body = {"query": query[:MAX_QUERY], "search_depth": "advanced", "chunks_per_source": 3,
                "max_results": MAX_RESULTS}
        req = urllib.request.Request(TAVILY_URL, data=json.dumps(body).encode(), method="POST",
                                     headers={"Authorization": f"Bearer {self.api_key}",
                                              "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout_s) as r:
                results = json.loads(r.read()).get("results") or []
        except urllib.error.HTTPError as e:
            raise SearchError(f"Tavily search failed: HTTP {e.code} {e.read()[:200]!r}") from None
        except (OSError, ValueError, AttributeError) as e:  # URLError and timeouts are OSErrors
            raise SearchError(f"Tavily search failed: {e}") from None
        return [Result(str(x.get("title") or x["url"]), str(x["url"]), str(x.get("content") or ""))
                for x in results if isinstance(x, dict) and x.get("url")]


# The house defaults for what the results leave out. Fixed here, before any result is seen, and every use is
# listed in `choices`.
DEFAULTS = """\
  entry at a fair value gap: a limit order at its near edge (the top for a long), cancelled if not filled within
    12 candles; any other entry: a market order at the next candle's open
  stop: 1 point below the sweep's low; with no sweep, 1 point below the last swing low
  target: 2R
  time between setup steps: within 1 hour
  "the New York killzone" or "New York session": the NY AM killzone"""

RULES = """\
A trader asked about trading strategies. Below are web search results. Write the strategy they describe as
complete rules a backtester can run on {instrument} {timeframe} bars, so the trader can test it without
filling any gaps. Reply with exactly one JSON object, no prose:
{{"name": "...", "summary": "...", "strategy": "...", "choices": ["..."], "left_out": ["..."], "sources": [1, 2]}}

"name": the strategy's name in 2 to 5 words, like "ICT Silver Bullet".
"summary": 2 to 4 plain sentences for someone new: what it's called, its idea, when it trades. Only what the
results say; if they disagree, say so. Asked for "the most famous" or "the best", pick the one most results
describe, and never claim it works: the backtest will tell.

"strategy": plain sentences, no numbering or headings, each rule exact, in this order:
- Direction: long. A short side goes in "left_out", to be tested as its own strategy. For a long, the sweep
  takes a low, the fair value gap is bullish, a structure shift closes above a swing high, the stop is below.
- When: name a killzone only when the results mean it with its standard times ({sessions}, New York time).
  Any other window is exact New York times: "between 10:00 and 11:00", or several: "between 03:00 and 04:00,
  10:00 and 11:00, or 14:00 and 15:00".
- Setup: the strategy's own events in order: a breakout stays a breakout (a candle closes above the range high),
  never turned into a sweep, and no event the results don't describe is added. Every event after the first
  says how long it may take: "then, within 1 hour, a bullish fair value gap forms". Each event names one level. Events: a sweep of a named level (the prior day or week low, the Asia or
  London session low, the last swing low); a candle closing above a level (a market structure shift or break
  of structure is "a candle closes above the last swing high"); a bullish fair value gap forming; a bullish
  displacement candle; a higher low; an EMA cross. An entry at a fair value gap needs the gap as a setup event.
- Entry: "a market order at the next candle's open", or "a limit order at the top of the fair value gap,
  cancelled if not filled within N candles".
- Stop: at a level with its distance in points ("1 point below the sweep's low", "at the bottom of the gap"),
  a number of points, or a multiple of ATR. Never ticks or pips.
- Target: an R multiple ("2R") or a named level ("the prior day high").
Use a rule from the results wherever they state one. Where they don't, or only vaguely ("target the next
liquidity", "stop beyond the swing"), use these house defaults:
{defaults}
Only these concepts: {concepts}. Anything the backtester can't test ({unsupported}; {limits}; conditions on a
higher timeframe) goes in "left_out", and the rules must work without it.

"choices": one line for each default you used, saying what the results said instead: "Target 2R: the results
only say 'the next liquidity pool'". Leave out defaults you didn't use.
"left_out": one line for each part of the strategy the results describe that the rules leave out (the short side,
order blocks, a trailing stop...), and why. Not concepts the results never mention.
"sources": the numbers of the results you used.
If the results don't describe a strategy with rules, set "strategy" to "" and say why in "summary".
"""

SCHEMA = {
    "type": "object",
    "properties": {"name": {"type": "string"}, "summary": {"type": "string"}, "strategy": {"type": "string"},
                   "choices": {"type": "array", "items": {"type": "string"}},
                   "left_out": {"type": "array", "items": {"type": "string"}},
                   "sources": {"type": "array", "items": {"type": "integer"}}},
    "required": ["summary", "strategy", "choices", "left_out", "sources"],
}

# The parser's verdict on a set of rules: what it would ask or can't test (empty: they parse), and the parse
# itself when it's ready, so the trader's new project starts from exactly what was checked.
Check = Callable[[str], tuple[list[str], dict | None]]

NUMBER = re.compile(r"(?<![\w.])\d+(?:\.\d+)?")
CLOCK = re.compile(r"\b(\d{1,2}):(\d{2})\b")
LIST_MARK = re.compile(r"(?:^|(?<=\s))\d{1,2}[.)](?=\s)")  # "1. Direction": numbering, not a rule's number
MAX_WRITEUPS = 3  # each one after the first fixes what the checks found in the one before
DEFAULT_NUMBERS = {1.0, 2.0, 12.0}  # the numbers DEFAULTS uses: 1 point, 2R, 12 candles, 1 hour
# One direction per strategy: rules that buy and sell go back once (the parser would ask anyway)
LONG = re.compile(r"\b(long|longs|buy|buys)\b", re.I)
SHORT = re.compile(r"\b(short(?![- ]term)|shorts|sell|sells)\b", re.I)


@dataclass
class Research:
    summary: str
    strategy: str = ""
    name: str = ""  # the strategy's name, for the new project
    choices: list[str] = field(default_factory=list)
    left_out: list[str] = field(default_factory=list)
    sources: list[dict] = field(default_factory=list)  # {title, url}
    search_seconds: float = 0.0
    model_seconds: float = 0.0  # the write-ups and the parser checks
    calls: int = 0  # write-ups (1 to MAX_WRITEUPS)
    parses: bool | None = None  # the parser read the final rules without a question (None: not checked)
    parsed: dict | None = None  # that parse (ParseResult.to_json()), when it's ready

    def to_json(self) -> dict:
        return asdict(self)


def system_prompt(instrument: str, timeframe: str) -> str:
    cat = catalog()
    concepts = "; ".join(f"{b['name']} ({', '.join(b['aliases'][:5])})" if b["aliases"] else b["name"]
                         for b in cat["blocks"])
    return RULES.format(instrument=instrument, timeframe=timeframe, defaults=DEFAULTS, concepts=concepts,
                        sessions=", ".join(f"{SESSION_NAMES[k]} {a}-{b}" for k, (a, b) in SESSIONS.items()),
                        unsupported=", ".join(k.replace("_", " ") for k in cat["unsupported"]),
                        limits="; ".join(SPEC_LIMITS))


def user_message(question: str, results: list[Result]) -> str:
    found = "\n\n".join(f"[{i}] {r.title}\n{r.url}\n{r.content[:MAX_CONTENT]}" for i, r in enumerate(results, 1))
    return f"Question: {question.strip()}\n\nSearch results:\n{found}"


def parser_check(llm: LLM, instrument: str, timeframe: str) -> Check:
    """Run the backtester's own parser on the rules: whatever it would ask the trader, or can't test, is feedback."""
    def check(rules: str) -> tuple[list[str], dict | None]:
        r = parse(rules, instrument, timeframe, llm)
        out = [f"It would ask: {q}" for q in r.questions]
        out += [f"It can't test {u['concept']}: take it out of the rules and list it in \"left_out\"."
                for u in r.unsupported]
        if r.status == INVALID:
            out.append("It couldn't turn the rules into a spec: make each rule simpler and more exact, one level per "
                       "event, in the backtester's own terms.")
        return out, r.to_json() if not out and r.status == READY else None
    return check


def research(question: str, instrument: str, llm: LLM, search: Search, timeframe: str = "5m",
             check: Check | None = None) -> Research:
    start = time.monotonic()
    results = search.search(question)
    searched = time.monotonic() - start
    if not results:
        return Research("The search found nothing to go on. Try naming a strategy, like the ICT Silver Bullet.",
                        search_seconds=searched)
    msgs = [{"role": "system", "content": system_prompt(instrument, timeframe)},
            {"role": "user", "content": user_message(question, results)}]
    # numbers with a known origin: the question, the killzone times (the backtester's own) and the listed choices
    known = " ".join([question, *(f"{a} {b}" for a, b in SESSIONS.values())])
    best, calls, parses, parsed, started = None, 0, None, None, time.monotonic()
    for attempt in range(1, MAX_WRITEUPS + 1):
        c = llm.complete(msgs, SCHEMA)
        calls = attempt
        try:
            name, summary, strategy, choices, left_out, used = read_reply(c.text, len(results))
        except ValueError as e:
            if best is None and attempt < MAX_WRITEUPS:
                msgs = [*msgs, {"role": "assistant", "content": c.text},
                        {"role": "user", "content": f"That reply wasn't valid ({e}). Reply with only the JSON object "
                                                    f"{{summary, strategy, choices, left_out, sources}}."}]
                continue
            if best is None:
                raise LLMError(f"the model's reply wasn't valid: {e}") from None
            break  # the last valid write-up stands
        unsourced = unsourced_numbers(strategy, " ".join([known, *choices]), results)
        house = [n for n in unsourced if _float(n) in DEFAULT_NUMBERS]  # a house default the model didn't list: list it
        unsourced = [n for n in unsourced if _float(n) not in DEFAULT_NUMBERS]
        best = Research(summary, strategy, name=name, left_out=left_out,
                        choices=choices + [f"{n} in the rules is one of our house defaults: the results don't give it."
                                           for n in house]
                        + [f"The rules use {n}, which the results don't give: check it." for n in unsourced],
                        sources=[{"title": r.title, "url": r.url} for r in [results[i] for i in used] or results])
        parses = parsed = None
        if not strategy:
            break
        problems = []
        if unsourced:
            problems.append(f"These numbers in \"strategy\" aren't in the search results or \"choices\": "
                            f"{', '.join(unsourced)}. Use the number the results give, or a default listed in "
                            f"\"choices\".")
        if LONG.search(strategy) and SHORT.search(strategy):
            problems.append("\"strategy\" trades both directions. Write only the long side, and put the short "
                            "side in \"left_out\".")
        if check and (not problems or attempt == MAX_WRITEUPS):  # the final rules are always checked
            asked, parsed = check(strategy)
            parses = not asked
            if asked:
                problems.append("The backtester read the rules. " + " ".join(asked) + " Make the rules answer each "
                                "one: from the results where they say, otherwise with a house default, added to "
                                "\"choices\".")
        if not problems or attempt == MAX_WRITEUPS:
            break
        msgs = [*msgs, {"role": "assistant", "content": c.text},
                {"role": "user", "content": " ".join(problems) + " Reply with the whole JSON object again."}]
    best.search_seconds, best.model_seconds, best.calls = searched, time.monotonic() - started, calls
    best.parses, best.parsed = parses, parsed
    return best


def read_reply(text: str, n_results: int) -> tuple[str, str, str, list[str], list[str], list[int]]:
    obj = extract_json(text)
    summary, strategy = obj.get("summary"), obj.get("strategy") or ""
    if not isinstance(summary, str) or not summary.strip():
        raise ValueError("summary must be a non-empty string")
    if not isinstance(strategy, str):
        raise ValueError("strategy must be a string")
    used: list[int] = []
    for i in obj.get("sources") or []:
        if isinstance(i, int) and 1 <= i <= n_results and i - 1 not in used:
            used.append(i - 1)
    name = obj.get("name") if isinstance(obj.get("name"), str) else ""
    return (name.strip()[:60], summary.strip(), strategy.strip()[:MAX_STRATEGY], _lines(obj.get("choices")),
            _lines(obj.get("left_out")), used)


def _float(n: str) -> float | None:
    try:
        return float(n)
    except ValueError:  # a clock time
        return None


def _lines(v) -> list[str]:
    """A list of strings; one string instead of a list is split into its lines or "; " parts."""
    if isinstance(v, str):
        v = re.split(r"\s*[;\n]\s*", v)
    return [x.strip() for x in v or [] if isinstance(x, str) and x.strip()]


def unsourced_numbers(strategy: str, question: str, results: list[Result]) -> list[str]:
    """Numbers in the strategy that neither the question nor any result contains. A clock time counts in
    either form: "14:00" is sourced by "2 PM", and "10:00" by "10 AM"."""
    have = {float(n) for n in NUMBER.findall(" ".join([question, *(f"{r.title} {r.content}" for r in results)]))}
    out: list[str] = []
    for m in CLOCK.finditer(strategy):
        hour, minute = int(m.group(1)), int(m.group(2))
        if not {hour, hour % 12 or 12} & have or (minute and minute not in have):
            out.append(m.group(0))
    for n in NUMBER.findall(CLOCK.sub(" ", LIST_MARK.sub(" ", strategy))):
        if float(n) != 0 and float(n) not in have and n not in out:
            out.append(n)
    return out
