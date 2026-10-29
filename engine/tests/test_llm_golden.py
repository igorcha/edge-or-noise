"""The golden eval against the live model. Opt in: `pytest -m network` (needs the provider's key).

For a scored table with timings and the model's raw replies, use scripts/eval_llm.py instead.
"""

import pytest

from engine.llm import LLMNotConfigured, make_llm, parse
from engine.llm.golden import CASES, score

pytestmark = pytest.mark.network


@pytest.fixture(scope="module")
def llm():
    try:
        return make_llm()
    except LLMNotConfigured as e:
        pytest.skip(str(e))


@pytest.mark.parametrize("case", CASES, ids=lambda c: c.name)
def test_golden(case, llm):
    result = parse(case.prompt, "US100", case.timeframe, llm)
    passed, why = score(case, result)
    assert passed, f"{why}\nassumptions: {result.assumptions}\nquestions: {result.questions}\nreply: {result.attempts[-1].text[:2000]}"
