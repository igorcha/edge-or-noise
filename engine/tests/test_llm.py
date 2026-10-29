"""The LLM layer without the network: prompt, few-shots, envelope handling, retry, defaults, client config."""

import copy
import json
import os

import pytest

from engine.blocks import catalog
from engine.llm import INVALID, QUESTIONS, READY, UNSUPPORTED_STATUS, Completion, Config, LLMNotConfigured, parse
from engine.llm import client as client_mod
from engine.llm.fewshots import FEWSHOTS
from engine.llm.golden import CASES, expected_specs, score
from engine.llm.parse import DECISIONS, extract_json, filled_defaults
from engine.llm.prompt import messages, system_prompt
from engine.spec import validate_spec
from engine.spec.examples import FVG_LONG


class Scripted:
    """A fake model: returns the scripted replies in order and records what it was sent."""

    def __init__(self, *replies):
        self.replies = [r if isinstance(r, str) else json.dumps(r) for r in replies]
        self.sent: list[list[dict]] = []

    def complete(self, messages, schema=None):
        self.sent.append(messages)
        return Completion(self.replies[len(self.sent) - 1], "fake", 0.01, 100, 50)


def strip(spec):
    return {k: v for k, v in spec.items() if k not in ("instrument", "timeframe")}


def envelope(spec=None, assumptions=(), questions=(), unsupported=()):
    return {"spec": spec, "assumptions": list(assumptions), "questions": list(questions),
            "unsupported": list(unsupported)}


FVG = strip(FVG_LONG)
PROMPT = "NY AM, sweep of the Asia low then a bullish FVG, limit at the gap top, stop under the swing low, 2R."


# --- prompt and few-shots ------------------------------------------------------------------------

def test_prompt_is_generated_from_the_catalog():
    sp = system_prompt()
    cat = catalog()
    assert all(f'"name":"{b["name"]}"' in sp for b in cat["blocks"])
    assert all(k.replace("_", " ") in sp for k in cat["unsupported"])
    assert '"title"' not in sp and "Spec JSON schema" in sp


def test_messages_carry_the_few_shots_and_the_project():
    m = messages("buy the dip", "US100", "15m", answers=[{"question": "Stop?", "answer": "20 points"}])
    assert m[0]["role"] == "system" and len(m) == 2 + 2 * len(FEWSHOTS)
    assert m[-1]["content"].startswith("Instrument: US100. Timeframe: 15m bars.")
    assert "Q: Stop?\n  A: 20 points" in m[-1]["content"]
    assert all(json.loads(x["content"]).keys() == {"spec", "assumptions", "questions", "unsupported"}
               for x in m if x["role"] == "assistant")


@pytest.mark.parametrize("i", range(len(FEWSHOTS)))
def test_every_few_shot_is_valid(i):
    ex = FEWSHOTS[i]
    spec, questions = ex["envelope"]["spec"], ex["envelope"]["questions"]
    complete = spec is not None and all(k in spec for k in DECISIONS)
    if complete and not questions:
        validate_spec({**spec, "instrument": "US100", "timeframe": ex["timeframe"]})
    else:  # a few-shot that stops must say why
        assert questions or ex["envelope"]["unsupported"]
    assert spec is None or not {"instrument", "timeframe", "version"} & set(spec)


def test_golden_cases_are_valid_and_not_few_shots():
    shots = {ex["prompt"] for ex in FEWSHOTS}
    for case in CASES:
        assert case.prompt not in shots
        assert len(expected_specs(case)) == len(case.specs)
    assert len(CASES) >= 12


# --- parse --------------------------------------------------------------------------------------------

def test_ready_spec_is_canonical_with_defaults_and_card():
    llm = Scripted(envelope(FVG, assumptions=["Asia low = the Asia session low"]))
    r = parse(PROMPT, "US100", "5m", llm)
    assert r.status == READY and r.spec == expected_spec(FVG, "5m") and r.error is None
    assert r.assumptions == ["Asia low = the Asia session low"] and r.questions == [] and r.unsupported == []
    assert "fvg: min_size_atr = 0, max_age = 100 (default)" in r.defaults
    assert "Long US100 on 5-minute bars." in r.card["text"] and len(r.attempts) == 1


def expected_spec(spec, tf):
    from engine.spec import canonical_dict
    return canonical_dict(validate_spec({**spec, "instrument": "US100", "timeframe": tf}))


def test_project_fixes_instrument_and_timeframe():
    r = parse(PROMPT, "US100", "15m", Scripted(envelope({**FVG, "instrument": "US500", "timeframe": "1m"})))
    assert r.status == READY and r.spec["timeframe"] == "15m" and r.spec["instrument"] == "US100"


def test_invalid_spec_is_retried_once_with_the_validator_error():
    bad = copy.deepcopy(FVG)
    bad["setup"][1]["event"]["params"]["max_age"] = 0
    llm = Scripted(envelope(bad), envelope(FVG))
    r = parse(PROMPT, "US100", "5m", llm)
    assert r.status == READY and len(llm.sent) == 2 and len(r.attempts) == 2
    retry = llm.sent[1][-1]["content"]
    assert "setup[1].event.params" in retry and "max_age" in retry and r.attempts[0].error


def test_second_failure_is_invalid_with_the_error_and_no_third_call():
    bad = copy.deepcopy(FVG)
    bad["setup"][1]["event"]["block"] = "order_block"
    llm = Scripted(envelope(bad), envelope(bad))
    r = parse(PROMPT, "US100", "5m", llm)
    assert r.status == INVALID and len(llm.sent) == 2 and "unknown block" in r.error and r.spec is None
    assert r.draft["setup"][1]["event"]["block"] == "order_block"


def test_broken_json_is_retried():
    llm = Scripted("Sure! Here is the spec: {spec: oops", envelope(FVG))
    r = parse(PROMPT, "US100", "5m", llm)
    assert r.status == READY and "not a valid envelope" in llm.sent[1][-1]["content"]
    assert parse(PROMPT, "US100", "5m", Scripted("nope", "still nope")).status == INVALID


def test_missing_decisions_become_questions_never_defaults():
    partial = {k: v for k, v in FVG.items() if k not in ("stop", "target")}
    r = parse("sweep then FVG, limit at the gap top", "US100", "5m", Scripted(envelope(partial)))
    assert r.status == QUESTIONS and r.spec is None and r.draft == partial
    assert r.questions == [DECISIONS["stop"][0], DECISIONS["target"][0]]


def test_the_models_own_questions_are_kept_and_not_doubled():
    partial = {k: v for k, v in FVG.items() if k != "stop"}
    r = parse(PROMPT, "US100", "5m", Scripted(envelope(partial, questions=["Where's your stop: swing low or ATR?"])))
    assert r.status == QUESTIONS and r.questions == ["Where's your stop: swing low or ATR?"]


def test_questions_block_a_complete_spec():
    r = parse(PROMPT, "US100", "5m", Scripted(envelope(FVG, questions=["Long or short?"])))
    assert r.status == QUESTIONS and r.spec is None


def test_nothing_testable_is_unsupported():
    reply = envelope(None, unsupported=[{"concept": "RSI", "reason": "not available yet"}, "VWAP"])
    r = parse("RSI under 30 and above VWAP", "US100", "5m", Scripted(reply))
    assert r.status == UNSUPPORTED_STATUS and [u["concept"] for u in r.unsupported] == ["RSI", "VWAP"]


def test_an_unsupported_part_the_model_dropped_is_still_listed():
    r = parse(PROMPT + " Only when RSI is under 70.", "US100", "5m", Scripted(envelope(FVG)))
    assert r.status == READY and r.unsupported == [{"concept": "RSI", "reason": "not available yet"}]
    listed = envelope(FVG, unsupported=[{"concept": "Relative Strength Index", "reason": "not available yet"}])
    assert len(parse(PROMPT + " RSI under 70.", "US100", "5m", Scripted(listed)).unsupported) == 1


def test_answers_count_as_the_traders_words():
    r = parse(PROMPT, "US100", "5m", Scripted(envelope(FVG)), answers=[{"question": "Filter?", "answer": "above VWAP"}])
    assert [u["concept"] for u in r.unsupported] == ["VWAP"]


def test_reasoning_and_fences_around_the_json_are_ignored():
    body = json.dumps(envelope(FVG))
    for text in (f"<think>long thoughts {{not json}}</think>\n{body}", f"```json\n{body}\n```",
                 f"reasoning... </think>{body}", json.dumps(FVG)):  # the last: a bare spec
        assert parse(PROMPT, "US100", "5m", Scripted(text)).status == READY, text[:40]
    with pytest.raises(ValueError):
        extract_json("[1, 2]")


def test_defaults_list_only_what_the_model_left_out():
    spec = copy.deepcopy(FVG)
    spec["setup"][1]["event"]["params"].update(min_size_atr=0.5, max_age=50)
    spec["slippage_pts"] = 0.25
    out = filled_defaults(spec, "US100", "5m")
    assert not any(line.startswith("fvg") for line in out) and not any("slippage" in line for line in out)
    assert "swing: left = 2, right = 2 (default)" in out and "sweep: reclaim_bars = 1 (default)" in out
    minimal = {"direction": "long", "setup": [{"name": "s", "event": {"block": "displacement"}}],
               "entry": {"type": "limit", "at": {"block": "swing", "output": "low"}},
               "stop": {"type": "atr"}, "target": {"type": "r_multiple"}}
    out = filled_defaults(minimal, "US100", "5m")
    for line in ("at most 1 trade per trading day (default)", "the limit order is cancelled if not filled within 20 "
                 "bars (default)", "stop at 1.5 x ATR (default)", "target 2R (default)",
                 "0 points of slippage per side, on top of the spread (default)"):
        assert line in out


# --- golden scoring (offline: a perfect fake model passes every case) -------------------------------------

@pytest.mark.parametrize("case", CASES, ids=lambda c: c.name)
def test_golden_scoring_accepts_the_expected_answer(case):
    if case.specs:
        for spec in case.specs:
            r = parse(case.prompt, "US100", case.timeframe, Scripted(envelope(spec)))
            assert score(case, r) == (True, "exact")
    elif QUESTIONS in case.status:
        r = parse(case.prompt, "US100", case.timeframe, Scripted(envelope(None, questions=["Which direction?"])))
        assert score(case, r)[0]


def test_golden_scoring_rejects_a_wrong_answer():
    case = CASES[0]
    wrong = copy.deepcopy(case.specs[0])
    wrong["target"]["r"] = 2
    passed, why = score(case, parse(case.prompt, "US100", case.timeframe, Scripted(envelope(wrong))))
    assert not passed and why == "differs in target"
    runs_anyway = next(c for c in CASES if c.name == "missing_entry_stop_target")
    passed, why = score(runs_anyway, parse(runs_anyway.prompt, "US100", "5m", Scripted(envelope(FVG))))
    assert not passed and why.startswith("status ready")


def test_golden_scoring_ignores_step_names_but_not_what_refs_point_at():
    case = CASES[0]  # steps "sweep" then "gap", entry at gap.top, stop at gap.bottom
    renamed = json.loads(json.dumps(case.specs[0]).replace('"gap"', '"fvg"').replace('"gap.', '"fvg.'))
    r = parse(case.prompt, "US100", case.timeframe, Scripted(envelope(renamed)))
    assert r.spec["setup"][1]["name"] == "fvg"
    assert score(case, r) == (True, "same rules, other step names")
    swapped = copy.deepcopy(case.specs[0])
    swapped["stop"]["at"] = {"ref": "sweep.extreme"}
    assert not score(case, parse(case.prompt, "US100", case.timeframe, Scripted(envelope(swapped))))[0]


# --- client configuration --------------------------------------------------------------------------------

def test_config_needs_a_key_and_reads_dot_env_without_overriding(tmp_path, monkeypatch):
    env = {k: v for k, v in os.environ.items() if not k.startswith("NEBIUS_")}
    monkeypatch.setattr(os, "environ", env)  # the .env loader writes here; the real environment stays untouched
    monkeypatch.setattr(client_mod, "ENV_FILE", tmp_path / ".env")
    with pytest.raises(LLMNotConfigured):
        Config.from_env()
    (tmp_path / ".env").write_text("# local\nNEBIUS_API_KEY='abc'\nexport NEBIUS_MODEL=nvidia/other\n")
    monkeypatch.setenv("NEBIUS_MODEL", "nvidia/already-set")
    cfg = Config.from_env()
    assert cfg.api_key == "abc" and cfg.model == "nvidia/already-set" and cfg.json_mode == "json_object"
    monkeypatch.setenv("NEBIUS_JSON_MODE", "yaml")
    with pytest.raises(LLMNotConfigured):
        Config.from_env()


def test_client_sends_temperature_zero_json_mode_and_thinking_switch():
    pytest.importorskip("openai")
    sent = {}

    class FakeCompletions:
        def create(self, **kwargs):
            sent.update(kwargs)
            msg = type("M", (), {"content": "{}"})()
            return type("R", (), {"choices": [type("C", (), {"message": msg})()], "model": "m", "usage": None})()

    for mode, thinking, fmt, extra in (
            ("json_object", "off", {"type": "json_object"}, {"chat_template_kwargs": {"enable_thinking": False}}),
            ("off", "on", None, {"chat_template_kwargs": {"enable_thinking": True}}),
            ("json_schema", "default", "json_schema", None)):
        sent.clear()
        llm = client_mod.Nemotron(Config(api_key="k", json_mode=mode, thinking=thinking))
        llm._client = type("C", (), {"chat": type("Ch", (), {"completions": FakeCompletions()})()})()
        assert llm.complete([{"role": "user", "content": "x"}], {"type": "object"}).text == "{}"
        assert sent["temperature"] == 0 and sent.get("extra_body") == extra
        if fmt == "json_schema":
            assert sent["response_format"]["type"] == "json_schema"
        else:
            assert sent.get("response_format") == fmt


# --- swappable providers --------------------------------------------------------------------------------

def test_provider_is_picked_from_the_environment(tmp_path, monkeypatch):
    pytest.importorskip("anthropic")
    from engine.llm import make_llm
    from engine.llm.claude import Claude
    env = {k: v for k, v in os.environ.items() if not k.startswith(("NEBIUS_", "ANTHROPIC_", "LLM_"))}
    monkeypatch.setattr(os, "environ", env)
    monkeypatch.setattr(client_mod, "ENV_FILE", tmp_path / ".env")
    with pytest.raises(LLMNotConfigured, match="NEBIUS_API_KEY"):  # nemotron is the default
        make_llm()
    env.update(NEBIUS_API_KEY="k")
    assert isinstance(make_llm(), client_mod.Nemotron)
    env.update(LLM_PROVIDER="anthropic")
    with pytest.raises(LLMNotConfigured, match="ANTHROPIC_API_KEY"):
        make_llm()
    env.update(ANTHROPIC_API_KEY="k", ANTHROPIC_MODEL="claude-sonnet-5-5")
    llm = make_llm()
    assert isinstance(llm, Claude) and llm.describe() == "anthropic claude-sonnet-5-5"
    env.update(LLM_PROVIDER="gpt")
    with pytest.raises(LLMNotConfigured, match="LLM_PROVIDER"):
        make_llm()


def test_claude_gets_a_system_prompt_cached_few_shots_and_no_sampling_settings():
    pytest.importorskip("anthropic")
    from engine.llm.claude import Claude, ClaudeConfig
    sent = {}

    class FakeMessages:
        def create(self, **kwargs):
            sent.update(kwargs)
            block = type("B", (), {"type": "text", "text": '{"spec": null}'})()
            usage = type("U", (), {"input_tokens": 20, "cache_read_input_tokens": 6000, "cache_creation_input_tokens": 0,
                                   "output_tokens": 300})()
            return type("R", (), {"content": [block], "model": "claude-haiku-5-5", "usage": usage})()

    llm = Claude(ClaudeConfig(api_key="k"))
    llm._client = type("C", (), {"messages": FakeMessages()})()
    msgs = messages("buy the dip", "US100", "5m")
    c = llm.complete(msgs)
    assert c.text == '{"spec": null}' and c.prompt_tokens == 6020 and c.completion_tokens == 300
    assert sent["system"][0]["text"] == msgs[0]["content"] and "temperature" not in sent
    turns = sent["messages"]
    assert [t["role"] for t in turns] == [m["role"] for m in msgs[1:]]
    cached = [i for i, t in enumerate(turns) if "cache_control" in t["content"][0]]
    assert cached == [len(turns) - 2]  # the last few-shot: everything before the trader's message is cached
