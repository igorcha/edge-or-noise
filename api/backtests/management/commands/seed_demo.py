"""Create the shared demo project: the FVG strategy, a few honest iterations, and its final exam.

    python manage.py seed_demo            # once; does nothing if a demo exists
    python manage.py seed_demo --replace  # delete the old demo and make a new one

It goes through the real API views (same counter, same exam lock as any project), then marks the
project as the read-only demo. The exam is taken on the best-looking development trial, which is
what a trader would pick and exactly what the holdout is there to check.
"""

import copy

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.test import Client

from backtests.models import Project
from engine.spec.examples import FVG_LONG

NAME = "Demo: Asia sweep, then FVG"


def _iterations() -> list[tuple[str, dict]]:
    """What a trader might try: the base idea, a bigger target, a stricter gap."""
    base = {k: v for k, v in FVG_LONG.items() if k not in ("instrument", "timeframe")}
    bigger_target = copy.deepcopy(base)
    bigger_target["target"]["r"] = 3
    bigger_gaps = copy.deepcopy(base)
    bigger_gaps["setup"][1]["event"]["params"]["min_size_atr"] = 0.3
    return [("base", base), ("3R target", bigger_target), ("gaps of at least 0.3 ATR", bigger_gaps)]


class Command(BaseCommand):
    help = "Create the read-only demo project (FVG strategy, three trials, final exam)."

    def add_arguments(self, parser):
        parser.add_argument("--replace", action="store_true", help="delete existing demo projects first")
        parser.add_argument("--no-exam", action="store_true", help="leave the holdout sealed")

    def handle(self, *args, replace=False, no_exam=False, **options):
        existing = Project.objects.filter(is_demo=True)
        if existing.exists() and not replace:
            self.stdout.write(f"demo project {existing.latest('id').id} exists; use --replace to rebuild it")
            return
        existing.delete()

        host = next((h for h in settings.ALLOWED_HOSTS if h and h != "*" and not h.startswith(".")), "localhost")
        client = Client(HTTP_HOST=host)

        def post(url: str, body: dict) -> dict:
            r = client.post(f"/api{url}", body, content_type="application/json")
            if r.status_code not in (200, 201):
                raise CommandError(f"POST {url} -> {r.status_code}: {r.content[:500]!r}")
            return r.json()

        project = post("/projects", {"name": NAME, "instrument": "US100", "timeframe": FVG_LONG["timeframe"]})
        trials = []
        for label, spec in _iterations():
            t = post(f"/projects/{project['id']}/trials", {"spec": spec})
            trials.append(t)
            self.stdout.write(f"trial {t['number']} ({label}): {t['metrics']['n']} trades, "
                              f"{t['metrics']['expectancy_R']:+.3f}R, {t['verdict']['label']}")
        if not no_exam:
            best = max(trials, key=lambda t: t["metrics"]["expectancy_R"] or float("-inf"))
            exam = post(f"/projects/{project['id']}/exam", {"trial": best["id"]})
            self.stdout.write(f"exam on trial {best['number']}: {exam['metrics']['n']} trades, "
                              f"{exam['metrics']['expectancy_R']:+.3f}R, {exam['verdict']['label']}")
        Project.objects.filter(pk=project["id"]).update(is_demo=True)
        self.stdout.write(self.style.SUCCESS(f"demo project {project['id']} ready"))
