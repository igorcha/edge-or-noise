"""Nemotron turns plain English into a rule spec. The LLM only writes JSON; nothing it writes is executed.

    result = parse(prompt, "US100", "5m", make_llm())   # ready | questions | unsupported | invalid

`make_llm()` returns the configured model (LLM_PROVIDER: nemotron by default, or anthropic).
"""

from engine.llm.client import LLM, Completion, Config, LLMError, LLMNotConfigured, Nemotron, make_llm
from engine.llm.parse import INVALID, QUESTIONS, READY, UNSUPPORTED_STATUS, ParseResult, parse

__all__ = ["INVALID", "LLM", "QUESTIONS", "READY", "UNSUPPORTED_STATUS", "Completion", "Config", "LLMError",
           "LLMNotConfigured", "Nemotron", "ParseResult", "make_llm", "parse"]
