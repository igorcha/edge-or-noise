"""Finding a strategy on the web, without the network: a fake search and a scripted model."""

import json

import pytest

from engine.llm import Completion, LLMError
from engine.llm.research import Result, parser_check, research, unsourced_numbers

RESULTS = [
    Result("ICT Silver Bullet explained", "https://example.com/silver-bullet",
           "The Silver Bullet trades a fair value gap between 10 AM and 11 AM New York time, after liquidity is taken."),
    Result("Silver Bullet strategy guide", "https://example.org/guide",
           "Wait for a sweep of a recent high or low, then enter on the first FVG. Many traders target 2R."),
]


class Search:
    def __init__(self, results=RESULTS):
        self.results, self.queries = results, []

    def search(self, query):
        self.queries.append(query)
        return self.results


class Scripted:
    def __init__(self, *replies):
        self.replies = [r if isinstance(r, str) else json.dumps(r) for r in replies]
        self.sent = []

    def complete(self, messages, schema=None):
        self.sent.append(messages)
        return Completion(self.replies[len(self.sent) - 1], "fake", 0.5, 100, 50)


RULES = ("Long only. Between 10:00 and 11:00 NY, after a sweep of the last swing low, a bullish fair value gap forms "
         "within 1 hour. Limit order at the top of the gap, cancelled if not filled within 12 candles. Stop 1 point "
         "below the sweep's low. Target 2R.")
CHOICES = ["Stop 1 point below the sweep's low: the results don't say.", "Wait within 1 hour, 12 candle expiry."]


def reply(strategy=RULES, choices=CHOICES, left_out=("The short side.",), sources=(1, 2),
          summary="The ICT Silver Bullet trades an FVG."):
    return {"name": "Silver Bullet", "summary": summary, "strategy": strategy, "choices": list(choices),
            "left_out": list(left_out), "sources": list(sources)}


def test_a_found_strategy_comes_back_with_its_choices_sources_and_what_is_left_out():
    search, llm = Search(), Scripted(reply(sources=(2,)))
    r = research("What's the most famous ICT strategy?", "US100", llm, search)
    assert search.queries == ["What's the most famous ICT strategy?"] and r.calls == 1
    assert r.strategy == RULES and r.choices == CHOICES and r.left_out == ["The short side."] and r.name == "Silver Bullet"
    assert r.sources == [{"title": "Silver Bullet strategy guide", "url": "https://example.org/guide"}]
    sent = llm.sent[0]
    assert "[1] ICT Silver Bullet explained" in sent[-1]["content"] and "target: 2R" in sent[0]["content"]


def test_sources_are_the_search_results_never_urls_the_model_wrote():
    r = research("silver bullet", "US100", Scripted(reply(sources=(7, "https://made.up", 1))), Search())
    assert [s["url"] for s in r.sources] == ["https://example.com/silver-bullet"]
    r = research("silver bullet", "US100", Scripted(reply(sources=())), Search())  # none named: show them all
    assert len(r.sources) == 2


def test_a_number_neither_the_results_nor_the_choices_give_is_sent_back_then_flagged():
    made_up = reply(strategy=RULES + " At most 3 trades a day, stop 15 points.")
    llm = Scripted(made_up, reply())
    r = research("silver bullet", "US100", llm, Search())
    assert r.calls == 2 and "3, 15" in llm.sent[1][-1]["content"] and r.choices == CHOICES
    r = research("silver bullet", "US100", Scripted(made_up, made_up, made_up), Search())
    assert r.calls == 3 and r.choices[-2:] == ["The rules use 3, which the results don't give: check it.",
                                               "The rules use 15, which the results don't give: check it."]
    # a house default the model forgot to list is listed for it, and isn't sent back
    unlisted = research("silver bullet", "US100", Scripted(reply(choices=[])), Search())
    assert unlisted.calls == 1 and unlisted.choices == [
        "1 in the rules is one of our house defaults: the results don't give it.",
        "12 in the rules is one of our house defaults: the results don't give it."]


def test_numbers_count_as_sourced_in_any_form():
    assert unsourced_numbers("From 10:00 to 11:00, 2R, 1.5 ATR", "1.5 ATR stop", RESULTS) == []
    assert unsourced_numbers("1. Long. 2. Target 2R. 3) Done", "", RESULTS) == []  # list numbering isn't a rule
    assert unsourced_numbers("Target 3R", "", RESULTS) == ["3"]
    pm = [Result("NY PM", "https://example.com/pm", "The PM window runs from 2 PM to 3 PM, or 9:30 to 10.")]
    assert unsourced_numbers("14:00 to 15:00, or 09:30 to 10:00", "", pm) == []
    assert unsourced_numbers("16:00 to 09:45", "", pm) == ["16:00", "09:45"]


def test_rules_trading_both_ways_go_back_once():
    both = reply(strategy="After a sweep, buy the bullish FVG or sell the bearish one, 2R.")
    llm = Scripted(both, reply())
    r = research("silver bullet", "US100", llm, Search())
    assert r.calls == 2 and "only the long side" in llm.sent[1][-1]["content"]
    assert research("x", "US100", Scripted(reply(strategy="Buy it for a short-term move, 2R.")), Search()).calls == 1


def test_what_the_parser_would_ask_goes_back_until_the_rules_parse():
    checked = []

    def check(rules):
        checked.append(rules)
        return (["It would ask: Where is your stop?"], None) if len(checked) == 1 else ([], {"status": "ready"})
    llm = Scripted(reply(strategy="Long. Buy the FVG at its top, 2R."), reply())
    r = research("silver bullet", "US100", llm, Search(), check=check)
    assert r.strategy == RULES and r.calls == 2 and r.parses is True and r.parsed == {"status": "ready"}
    assert checked == ["Long. Buy the FVG at its top, 2R.", RULES]  # the rewrite is checked too
    assert "It would ask: Where is your stop?" in llm.sent[1][-1]["content"]
    clean = research("x", "US100", Scripted(reply()), Search(), check=lambda rules: ([], {"status": "ready"}))
    assert (clean.calls, clean.parses) == (1, True)
    stuck = research("x", "US100", Scripted(reply(), reply(), reply()), Search(),
                     check=lambda rules: (["It would ask: ?"], None))
    assert (stuck.calls, stuck.parses, stuck.parsed) == (3, False, None)  # the trader will be asked
    assert research("x", "US100", Scripted(reply()), Search()).parses is None  # not checked


def test_the_parser_check_reports_questions_and_untestable_concepts():
    spec_reply = {"spec": None, "assumptions": [], "questions": ["Long or short?"],
                  "unsupported": [{"concept": "order blocks", "reason": "not available yet"}]}
    problems, parsed = parser_check(Scripted(spec_reply), "US100", "5m")("Trade the order block.")
    assert parsed is None and problems == ["It would ask: Long or short?",
                        "It can't test order blocks: take it out of the rules and list it in \"left_out\"."]


def test_lists_as_one_string_are_split_not_spelled_out():
    r = research("x", "US100", Scripted({**reply(), "choices": "Target 2R; stop 1 point\nwait 1 hour, 12 candles"}), Search())
    assert r.choices == ["Target 2R", "stop 1 point", "wait 1 hour, 12 candles"]


def test_an_unreadable_reply_is_retried_once():
    r = research("silver bullet", "US100", Scripted("not json", reply()), Search())
    assert r.calls == 2 and r.strategy
    with pytest.raises(LLMError):
        research("silver bullet", "US100", Scripted("not json", "{}", "{}"), Search())
    # a valid first reply stands, flagged, when the retry comes back broken
    made_up = reply(strategy="Buy the FVG, stop 15 points.")
    r = research("silver bullet", "US100", Scripted(made_up, "not json"), Search())
    assert r.strategy == "Buy the FVG, stop 15 points." and "15" in r.choices[-1]


def test_no_results_means_no_model_call():
    llm = Scripted()
    r = research("asdfgh", "US100", llm, Search(results=[]))
    assert r.strategy == "" and llm.sent == [] and "nothing" in r.summary
