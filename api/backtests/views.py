"""The REST API. Runs are synchronous: a trial with its baseline takes a few seconds.

    GET  /api/health                       up, database reachable, which instruments have price data
    GET  /api/catalog                      block catalog, spec schema, examples, markets with price data
    GET  /api/demo                         the shared read-only demo project (manage.py seed_demo)
    POST /api/projects                     {name, instrument, timeframe}
    GET  /api/projects/{id}
    GET  /api/projects/{id}/trials         history (no trade logs)
    POST /api/projects/{id}/parse          {prompt, answers?} -> Nemotron's spec, questions, assumptions
    POST /api/projects/{id}/check          {spec} -> canonical spec, rules card, "trial N" or "already trial K"
    POST /api/projects/{id}/trials         {spec} -> runs it; same spec hash = same trial, not a new one
    GET  /api/trials/{id}                  everything, trade log included
    GET  /api/bars?trial=&trade=&before=&after=   strategy bars around one trade (dev data only)
    POST /api/projects/{id}/exam           {trial?} one-time final exam on the holdout; locks the project
    POST /api/research                     {question, instrument?, timeframe?} -> a strategy found on the web (Tavily + Nemotron),
                                           as rules for the prompt box: summary, rules, the defaults it chose,
                                           what it left out, sources
"""

import logging
import threading

from django.conf import settings
from django.db import DatabaseError, connection, transaction
from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.exceptions import APIException, NotFound, ParseError, ValidationError
from rest_framework.response import Response
from rest_framework.throttling import BaseThrottle, SimpleRateThrottle
from rest_framework.views import APIView
from rest_framework.views import exception_handler as drf_exception_handler

from backtests import services
from backtests.models import Project, Trial
from engine.blocks import catalog
from engine.data import io
from engine.data.instruments import INSTRUMENTS, TIMEFRAMES
from engine.llm import LLMError, LLMNotConfigured
from engine.llm.research import SearchError, SearchNotConfigured, search_configured
from engine.spec import SpecError, json_schema, validate_spec
from engine.spec.examples import EXAMPLES

log = logging.getLogger(__name__)


class Conflict(APIException):
    status_code = status.HTTP_409_CONFLICT
    default_code = "conflict"


def exception_handler(exc, context):
    if isinstance(exc, SpecError):
        return Response({"error": exc.message, "path": exc.path}, status=400)
    return drf_exception_handler(exc, context)


# --- serialization -----------------------------------------------------------------------

def project_json(p: Project) -> dict:
    return {
        "id": p.id, "name": p.name, "instrument": p.instrument, "timeframe": p.timeframe,
        "holdout_boundary": p.holdout_boundary, "trial_count": p.trial_count, "exam_used": p.exam_used,
        "locked_spec_hash": p.locked_spec_hash or None, "is_demo": p.is_demo, "created_at": p.created_at,
    }


def trial_summary(t: Trial) -> dict:
    return {
        "id": t.id, "project": t.project_id, "number": t.number, "stage": t.stage, "spec_hash": t.spec_hash,
        "summary": t.summary, "created_at": t.created_at,
        "n": t.metrics.get("n"), "expectancy_R": t.metrics.get("expectancy_R"),
        "verdict": t.verdict.get("label"), "baseline_beaten": t.verdict.get("baseline_beaten"),
    }


def trial_json(t: Trial) -> dict:
    return {
        **trial_summary(t), "spec": t.spec, "card": t.card, "metrics": t.metrics, "verdict": t.verdict,
        "baseline": t.baseline, "stress": t.stress, "skipped": t.skipped, "trades": t.trades,
    }


# --- spend caps -----------------------------------------------------------------------------------
# Parse and research spend model tokens (and search credits) on the app's own keys. Three caps: one visitor
# (<scope>), everyone together per hour (<scope>_global) and per day (<scope>_global_day), so total spend has a
# ceiling however many IPs call.

class _Bucket(SimpleRateThrottle):
    """One rate-limited counter, checked and recorded separately."""

    def __init__(self, scope: str, key: str):
        self.scope = scope
        super().__init__()  # reads the rate for `scope` from DEFAULT_THROTTLE_RATES
        self.key = f"throttle_{scope}_{key}"

    def full(self) -> bool:
        self.history, self.now = self.cache.get(self.key, []), self.timer()
        while self.history and self.history[-1] <= self.now - self.duration:
            self.history.pop()
        return len(self.history) >= self.num_requests


class SpendThrottle(BaseThrottle):
    """All three caps as one throttle: a request counts against them only when every one allows it.

    Separate DRF throttle classes don't work for this: DRF runs every class even after one refuses, and each
    records the requests it allows, so a visitor already refused per IP would keep filling the shared buckets
    and lock everyone else out without spending anything. Counters live in the process cache (one Gunicorn
    process): a redeploy resets them, and the lock makes check-and-record exact across its threads."""
    lock = threading.Lock()

    def allow_request(self, request, view):
        scope = view.throttle_scope
        buckets = [_Bucket(scope, self.get_ident(request)), _Bucket(f"{scope}_global", "all"),
                   _Bucket(f"{scope}_global_day", "all")]
        with self.lock:
            self.refused = [b for b in buckets if b.full()]
            if self.refused:
                return False
            for b in buckets:
                b.throttle_success()  # record it in all three
        return True

    def wait(self):
        return max((b.wait() for b in self.refused), default=None)


SPEND_THROTTLES = [SpendThrottle]

# --- views --------------------------------------------------------------------------------------

class Health(APIView):
    """For the host's health check and for a person checking a deploy. Missing price data doesn't fail
    it: the app has to be up for the data to be uploaded to it."""

    def get(self, request):
        try:
            connection.ensure_connection()
        except DatabaseError:
            return Response({"ok": False, "database": False}, status=status.HTTP_503_SERVICE_UNAVAILABLE)
        data = {inst: (io.bars_dir(inst) / "manifest.json").is_file() for inst in sorted(INSTRUMENTS)}
        return Response({"ok": True, "database": True, "data": data})


def has_data(instrument: str) -> bool:
    return (io.bars_dir(instrument) / "manifest.json").is_file()


def check_instrument(instrument) -> str:
    if instrument not in INSTRUMENTS:
        raise ValidationError({"instrument": f"unknown, choose from {sorted(INSTRUMENTS)}"})
    if not has_data(instrument):
        raise ValidationError({"instrument": f"no price data for {instrument} here yet"})
    return instrument


class Catalog(APIView):
    def get(self, request):
        markets = [{"name": i.name, "title": i.title} for i in INSTRUMENTS.values() if has_data(i.name)]
        return Response({"catalog": catalog(), "spec_schema": json_schema(), "examples": EXAMPLES,
                         "instruments": sorted(INSTRUMENTS), "timeframes": sorted(TIMEFRAMES, key=TIMEFRAMES.get),
                         "markets": markets, "research": search_configured()})


class Demo(APIView):
    def get(self, request):
        p = Project.objects.filter(is_demo=True).order_by("-id").first()
        if p is None:
            raise NotFound("no demo project; run `manage.py seed_demo`")
        return Response(project_json(p))


class Projects(APIView):
    def post(self, request):
        data = request.data if isinstance(request.data, dict) else {}
        instrument, timeframe = check_instrument(data.get("instrument", "US100")), data.get("timeframe", "5m")
        if timeframe not in TIMEFRAMES:
            raise ValidationError({"timeframe": f"unknown, choose from {sorted(TIMEFRAMES)}"})
        ds = services.dataset(instrument, timeframe)
        p = Project.objects.create(name=str(data.get("name") or "Untitled strategy")[:200], instrument=instrument,
                                   timeframe=timeframe, holdout_boundary=ds.boundary)
        return Response(project_json(p), status=201)


class ProjectDetail(APIView):
    def get(self, request, pk):
        return Response(project_json(get_object_or_404(Project, pk=pk)))


class ProjectTrials(APIView):
    def get(self, request, pk):
        p = get_object_or_404(Project, pk=pk)
        return Response([trial_summary(t) for t in p.trials.all()])

    def post(self, request, pk):
        data = request.data if isinstance(request.data, dict) else {}
        if not isinstance(data.get("spec"), dict):
            raise ParseError("body must be {spec: {...}}")
        with transaction.atomic():
            p = get_object_or_404(Project.objects.select_for_update(), pk=pk)
            _refuse_demo(p)
            if p.exam_used:
                raise Conflict("this project took its final exam; the spec is locked. Start a new project to keep developing.")
            spec = validate_spec({**data["spec"], "instrument": p.instrument, "timeframe": p.timeframe})
            prior = list(p.trials.filter(stage=Trial.DEVELOPMENT))
            existing = next((t for t in prior if t.spec_hash == _hash(spec)), None)
            if existing is not None:
                return Response({**trial_json(existing), "is_new": False})

            ds = services.dataset(p.instrument, p.timeframe)
            n_trials = p.trial_count + 1
            sharpes = [services._sharpe(t.metrics) for t in prior]
            result = services.run(spec, ds, n_trials, [s for s in sharpes if s is not None])
            t = Trial.objects.create(project=p, number=n_trials, summary=services.spec_diff(
                prior[-1].spec if prior else None, result["spec"]), **result)
            p.trial_count = n_trials
            p.save(update_fields=["trial_count"])
        log.info("project %s trial %s: %s trades, %s", p.id, t.number, t.metrics.get("n"), t.verdict.get("label"))
        return Response({**trial_json(t), "is_new": True}, status=201)


def _hash(spec) -> str:
    from engine.spec import spec_hash
    return spec_hash(spec)


def _refuse_demo(p: Project) -> None:
    """The demo is shared by every visitor: trials or an exam on it would count for all of them."""
    if p.is_demo:
        raise Conflict("the demo project is read-only. Start your own project to run trials.")


def _known(p: Project) -> dict[str, int]:
    return dict(p.trials.filter(stage=Trial.DEVELOPMENT).values_list("spec_hash", "number"))


class Check(APIView):
    """Validate an edited spec without running it: the rules card form calls this on every edit."""

    def post(self, request, pk):
        p = get_object_or_404(Project, pk=pk)
        data = request.data if isinstance(request.data, dict) else {}
        if not isinstance(data.get("spec"), dict):
            raise ParseError("body must be {spec: {...}}")
        spec = validate_spec({**data["spec"], "instrument": p.instrument, "timeframe": p.timeframe})
        return Response(services.preview(spec, _known(p), p.trial_count + 1))


MAX_PROMPT = 4000


class Parse(APIView):
    throttle_classes = SPEND_THROTTLES  # every call spends model tokens on our key
    throttle_scope = "parse"

    def post(self, request, pk):
        p = get_object_or_404(Project, pk=pk)
        data = request.data if isinstance(request.data, dict) else {}
        prompt, answers = data.get("prompt"), data.get("answers") or []
        if not isinstance(prompt, str) or not prompt.strip():
            raise ValidationError({"prompt": "describe the strategy"})
        if len(prompt) > MAX_PROMPT:
            raise ValidationError({"prompt": f"keep it under {MAX_PROMPT} characters"})
        if not isinstance(answers, list) or len(answers) > 20 or not all(
                isinstance(a, dict) and isinstance(a.get("question"), str) and isinstance(a.get("answer"), str)
                and len(a["question"]) + len(a["answer"]) <= MAX_PROMPT for a in answers):
            raise ValidationError({"answers": "a list of {question, answer}"})
        try:
            result = services.parse(prompt, p.instrument, p.timeframe, answers, _known(p), p.trial_count + 1)
        except LLMNotConfigured as e:
            log.error("parse unavailable: %s", e)
            return Response({"error": "the strategy parser isn't configured; write the rules in the form instead"},
                            status=503)
        except LLMError as e:
            log.warning("parse failed: %s", e)
            return Response({"error": "the language model didn't answer; try again, or write the rules in the form"},
                            status=502)
        log.info("project %s parse: %s after %s call(s)", p.id, result["status"], len(result["attempts"]))
        return Response(result)


MAX_QUESTION = 500


class Research(APIView):
    """Find a strategy on the web. It only returns text for the prompt box: nothing is parsed or run."""
    throttle_classes = SPEND_THROTTLES  # every call spends search credits and model tokens
    throttle_scope = "research"

    def post(self, request):
        data = request.data if isinstance(request.data, dict) else {}
        question = data.get("question")
        if not isinstance(question, str) or not question.strip():
            raise ValidationError({"question": "ask about a strategy"})
        if len(question) > MAX_QUESTION:
            raise ValidationError({"question": f"keep it under {MAX_QUESTION} characters"})
        instrument, timeframe = check_instrument(data.get("instrument", "US100")), data.get("timeframe", "5m")
        if timeframe not in TIMEFRAMES:
            raise ValidationError({"timeframe": f"one of {', '.join(TIMEFRAMES)}"})
        try:
            r = services.research(question.strip(), instrument, timeframe)
        except (SearchNotConfigured, LLMNotConfigured) as e:
            log.error("research unavailable: %s", e)
            return Response({"error": "web search isn't configured here"}, status=503)
        except SearchError as e:
            log.warning("research search failed: %s", e)
            return Response({"error": "the web search didn't answer; try again"}, status=502)
        except LLMError as e:
            log.warning("research model failed: %s", e)
            return Response({"error": "the language model didn't answer; try again"}, status=502)
        log.info("research: %s source(s), %s call(s), search %.1fs, model %.1fs", len(r.sources), r.calls,
                 r.search_seconds, r.model_seconds)
        return Response(r.to_json())


class TrialDetail(APIView):
    def get(self, request, pk):
        return Response(trial_json(get_object_or_404(Trial, pk=pk)))


class BarsWindow(APIView):
    def get(self, request):
        try:
            trial_id, trade_i = int(request.query_params["trial"]), int(request.query_params["trade"])
            before, after = int(request.query_params.get("before", 60)), int(request.query_params.get("after", 20))
        except (KeyError, ValueError):
            raise ValidationError("query needs integer trial and trade, optional before/after")
        t = get_object_or_404(Trial, pk=trial_id)
        if t.stage == Trial.HOLDOUT:
            return Response({"error": "holdout bars are hidden"}, status=403)
        if not 0 <= trade_i < len(t.trades):
            raise NotFound(f"trade index out of range, trial has {len(t.trades)} trades")
        trade = t.trades[trade_i]
        ds = services.dataset(t.project.instrument, t.project.timeframe)
        first = min([trade["signal_bar"], *(v for k, v in trade.items() if k.endswith(".bar") and v is not None)])
        window = services.bars_window(ds, first, trade["exit_bar"], min(before, 500), min(after, 500))
        return Response({**window, "trade": trade, "timeframe": t.project.timeframe})


class Exam(APIView):
    def post(self, request, pk):
        data = request.data if isinstance(request.data, dict) else {}
        with transaction.atomic():
            p = get_object_or_404(Project.objects.select_for_update(), pk=pk)
            _refuse_demo(p)
            if p.exam_used:
                raise Conflict("the final exam has already been used for this project")
            dev = p.trials.filter(stage=Trial.DEVELOPMENT)
            trial_id = data.get("trial")
            if trial_id is not None and (not isinstance(trial_id, int) or isinstance(trial_id, bool)):
                raise ValidationError({"trial": "must be a trial id"})
            chosen = get_object_or_404(dev, pk=trial_id) if trial_id is not None else dev.last()
            if chosen is None:
                raise ValidationError("run at least one trial before the exam")
            spec = validate_spec(chosen.spec)
            ds = services.exam_dataset(p.instrument, p.timeframe)
            result = services.run(spec, ds, p.trial_count, [])  # one test on unseen data: not deflated
            t = Trial.objects.create(project=p, number=chosen.number, summary=f"final exam of trial {chosen.number}", **result)
            p.exam_used, p.locked_spec_hash = True, chosen.spec_hash
            p.save(update_fields=["exam_used", "locked_spec_hash"])
        log.info("project %s exam on trial %s: %s", p.id, chosen.number, t.verdict.get("label"))
        return Response({**trial_json(t), "project": project_json(p)}, status=201)
