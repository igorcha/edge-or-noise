"""The REST API. Runs are synchronous: a trial with its baseline takes a few seconds.

    GET  /api/catalog                      block catalog, spec schema, examples
    POST /api/projects                     {name, instrument, timeframe}
    GET  /api/projects/{id}
    GET  /api/projects/{id}/trials         history (no trade logs)
    POST /api/projects/{id}/trials         {spec} -> runs it; same spec hash = same trial, not a new one
    GET  /api/trials/{id}                  everything, trade log included
    GET  /api/bars?trial=&trade=&before=&after=   strategy bars around one trade (dev data only)
    POST /api/projects/{id}/exam           {trial?} one-time final exam on the holdout; locks the project
"""

import logging

from django.conf import settings
from django.db import transaction
from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.exceptions import APIException, NotFound, ParseError, ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.views import exception_handler as drf_exception_handler

from backtests import services
from backtests.models import Project, Trial
from engine.blocks import catalog
from engine.data.instruments import INSTRUMENTS, TIMEFRAMES
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


# --- views --------------------------------------------------------------------------------------

class Catalog(APIView):
    def get(self, request):
        return Response({"catalog": catalog(), "spec_schema": json_schema(), "examples": EXAMPLES,
                         "instruments": sorted(INSTRUMENTS), "timeframes": sorted(TIMEFRAMES, key=TIMEFRAMES.get)})


class Projects(APIView):
    def post(self, request):
        data = request.data if isinstance(request.data, dict) else {}
        instrument, timeframe = data.get("instrument", "US100"), data.get("timeframe", "5m")
        if instrument not in INSTRUMENTS:
            raise ValidationError({"instrument": f"unknown, choose from {sorted(INSTRUMENTS)}"})
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
        if "spec" not in data:
            raise ParseError("body must be {spec: ...}")
        with transaction.atomic():
            p = Project.objects.select_for_update().get(pk=pk) if Project.objects.filter(pk=pk).exists() else None
            if p is None:
                raise NotFound("project not found")
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
        window = services.bars_window(ds, trade["signal_bar"], trade["exit_bar"], min(before, 500), min(after, 500))
        return Response({**window, "trade": trade, "timeframe": t.project.timeframe})


class Exam(APIView):
    def post(self, request, pk):
        data = request.data if isinstance(request.data, dict) else {}
        with transaction.atomic():
            p = get_object_or_404(Project.objects.select_for_update(), pk=pk)
            if p.exam_used:
                raise Conflict("the final exam has already been used for this project")
            dev = p.trials.filter(stage=Trial.DEVELOPMENT)
            chosen = get_object_or_404(dev, pk=data["trial"]) if data.get("trial") else dev.last()
            if chosen is None:
                raise ValidationError("run at least one trial before the exam")
            spec = validate_spec(chosen.spec)
            ds = services.exam_dataset(p.instrument, p.timeframe)
            sharpes = [services._sharpe(t.metrics) for t in dev]
            result = services.run(spec, ds, p.trial_count, [s for s in sharpes if s is not None], with_stress=False)
            t = Trial.objects.create(project=p, number=chosen.number, summary=f"final exam of trial {chosen.number}", **result)
            p.exam_used, p.locked_spec_hash = True, chosen.spec_hash
            p.save(update_fields=["exam_used", "locked_spec_hash"])
        log.info("project %s exam on trial %s: %s", p.id, chosen.number, t.verdict.get("label"))
        return Response({**trial_json(t), "project": project_json(p)}, status=201)
