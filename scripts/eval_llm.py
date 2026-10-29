"""Score the parse prompt on the golden eval against the live model.

    .venv/bin/python scripts/eval_llm.py
    .venv/bin/python scripts/eval_llm.py --case spring_asia --show
    NEBIUS_JSON_MODE=json_schema NEBIUS_THINKING=on .venv/bin/python scripts/eval_llm.py
    LLM_PROVIDER=anthropic ANTHROPIC_MODEL=claude-haiku-5-5 .venv/bin/python scripts/eval_llm.py

Needs the provider's key (environment or the repo-root .env). Run it after every prompt, few-shot
or model change, and compare scores only between runs on the same model.
"""

import argparse
import json
import sys
import time

from engine.llm import LLMError, LLMNotConfigured, make_llm, parse
from engine.llm.golden import CASES, score


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--case", action="append", help="run only these cases (repeatable)")
    ap.add_argument("--show", action="store_true", help="print the model's replies for failed cases")
    args = ap.parse_args()
    cases = [c for c in CASES if not args.case or c.name in args.case]
    if not cases:
        print(f"no such case; cases are {[c.name for c in CASES]}", file=sys.stderr)
        return 2

    try:
        llm = make_llm()
    except LLMNotConfigured as e:
        print(f"{e}. Put the key in .env at the repo root.", file=sys.stderr)
        return 2
    print(f"{llm.describe()}\n")
    passed, start = 0, time.monotonic()
    for case in cases:
        try:
            result = parse(case.prompt, "US100", case.timeframe, llm)
        except LLMError as e:
            print(f"ERROR {case.name:30s} {e}")
            continue
        ok, why = score(case, result)
        passed += ok
        secs = sum(a.seconds for a in result.attempts)
        tokens = sum(a.tokens or 0 for a in result.attempts)
        print(f"{'pass' if ok else 'FAIL'}  {case.name:30s} {result.status:11s} {len(result.attempts)} call(s) "
              f"{secs:5.1f}s {tokens:6d} tok  {why}")
        if not ok and args.show:
            for i, a in enumerate(result.attempts, 1):
                print(f"      reply {i}{' (sent back: ' + a.error + ')' if a.error else ''}:\n      {a.text}")
            print(f"      questions: {json.dumps(result.questions)}\n      unsupported: {json.dumps(result.unsupported)}")
    print(f"\n{passed}/{len(cases)} passed in {time.monotonic() - start:.0f}s")
    return 0 if passed == len(cases) else 1


if __name__ == "__main__":
    sys.exit(main())
