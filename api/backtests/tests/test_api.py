"""API flow on a synthetic dataset: project -> trials (counter rules) -> bars -> exam -> lock."""

import copy

import pandas as pd
import pytest
from rest_framework.test import APIClient

from backtests import services
from engine.blocks import Bars
from engine.data.build import resample, to_close_time
from engine.honest import Dataset
from engine.sim import Costs
from engine.spec.examples import FVG_LONG
from engine.tests.test_data import synth_raw

BOUNDARY = pd.Timestamp("2024-05-01 21:00", tz="UTC")


def _dataset(exam: bool) -> Dataset:
    days = [d.strftime("%Y-%m-%d") for d in pd.bdate_range("2024-01-02", "2024-06-28")]
    m1 = to_close_time(synth_raw(days)[0])
    if not exam:
        m1 = m1[m1.index <= BOUNDARY]
    tf = resample(m1, 5)
    return Dataset("US100", "5m", Bars.from_frame(tf, 5, 0.01), Bars.from_frame(m1, 1, 0.01), Costs(0.5, 1.0),
                   BOUNDARY, exam)


@pytest.fixture(autouse=True)
def synthetic_data(monkeypatch, settings):
    dev, exam = _dataset(False), _dataset(True)
    monkeypatch.setattr(services, "dataset", lambda instrument, timeframe: dev)
    monkeypatch.setattr(services, "exam_dataset", lambda instrument, timeframe: exam)
    settings.BASELINE_RUNS = 30


@pytest.fixture
def client():
    return APIClient()


@pytest.fixture
def project(client):
    r = client.post("/api/projects", {"name": "FVG demo", "instrument": "US100", "timeframe": "5m"}, format="json")
    assert r.status_code == 201, r.content
    return r.json()


def spec_without_fixed_fields():
    s = copy.deepcopy(FVG_LONG)
    del s["instrument"], s["timeframe"]  # the project fixes them
    return s


@pytest.mark.django_db
@pytest.mark.django_db
def test_health_reports_the_database_and_which_instruments_have_data(client, monkeypatch, tmp_path):
    (tmp_path / "bars" / "US100").mkdir(parents=True)
    (tmp_path / "bars" / "US100" / "manifest.json").write_text("{}")
    monkeypatch.setenv("BACKTESTER_DATA_DIR", str(tmp_path))
    r = client.get("/api/health")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] and body["database"] and body["data"]["US100"] is True
    assert not any(v for k, v in body["data"].items() if k != "US100")


def test_database_url_is_read_like_railway_writes_it():
    from config.env import database_from_url

    db = database_from_url("postgresql://postgres:p%40ss@postgres.railway.internal:5432/railway")
    assert (db["HOST"], db["PORT"], db["NAME"], db["USER"], db["PASSWORD"]) == (
        "postgres.railway.internal", "5432", "railway", "postgres", "p@ss")
    assert database_from_url("postgres://u:p@db/x")["PORT"] == "5432"
    with pytest.raises(ValueError):
        database_from_url("mysql://u:p@db/x")


def test_catalog(client):
    r = client.get("/api/catalog").json()
    assert {b["name"] for b in r["catalog"]["blocks"]} >= {"fvg", "sweep", "swing"}
    assert "properties" in r["spec_schema"] and "fvg_long" in r["examples"] and "US100" in r["instruments"]


@pytest.mark.django_db
def test_project_creation_and_validation(client, project):
    assert project["trial_count"] == 0 and not project["exam_used"] and project["holdout_boundary"].startswith("2024-05-01")
    assert client.get(f"/api/projects/{project['id']}").json()["name"] == "FVG demo"
    assert client.post("/api/projects", {"instrument": "SPX"}, format="json").status_code == 400
    assert client.get("/api/projects/999").status_code == 404


@pytest.mark.django_db
def test_trial_counter_and_results(client, project):
    pid = project["id"]
    r = client.post(f"/api/projects/{pid}/trials", {"spec": spec_without_fixed_fields()}, format="json")
    assert r.status_code == 201, r.content
    t1 = r.json()
    assert t1["number"] == 1 and t1["is_new"] and t1["stage"] == "development" and t1["summary"] == "first run"
    assert t1["spec"]["instrument"] == "US100" and t1["metrics"]["n"] == len(t1["trades"]) > 0
    assert t1["verdict"]["label"] in {"probably noise", "can't tell yet", "promising"}
    assert t1["verdict"]["stage"] == "development" and len(t1["baseline"]["curves"]["p50"]) == t1["metrics"]["n"]
    assert t1["stress"]["factor"] == 1.5 and "Long US100 on 5-minute bars" in t1["card"]
    trade = t1["trades"][0]
    assert set(trade) >= {"entry", "exit", "stop", "target", "R", "exit_reason", "signal_bar", "exit_bar", "gap.top",
                          "sweep.bar", "gap.bar"}
    assert trade["sweep.bar"] < trade["gap.bar"] <= trade["signal_bar"] and trade["gap.bar"] - trade["sweep.bar"] <= 10

    # a no-op resave (explicit defaults, alias) is the same trial
    same = spec_without_fixed_fields()
    same["setup"][1]["event"]["block"] = "fair value gap"
    same["slippage_pts"] = 0
    r = client.post(f"/api/projects/{pid}/trials", {"spec": same}, format="json")
    assert r.status_code == 200 and r.json()["number"] == 1 and not r.json()["is_new"]
    assert client.get(f"/api/projects/{pid}").json()["trial_count"] == 1

    # a one-parameter change is trial 2 and the verdict knows it
    changed = spec_without_fixed_fields()
    changed["target"]["r"] = 3
    r = client.post(f"/api/projects/{pid}/trials", {"spec": changed}, format="json")
    t2 = r.json()
    assert r.status_code == 201 and t2["number"] == 2 and t2["summary"] == "target.r: 2.0 -> 3.0"
    assert t2["verdict"]["n_trials"] == 2
    assert client.get(f"/api/projects/{pid}").json()["trial_count"] == 2

    history = client.get(f"/api/projects/{pid}/trials").json()
    assert [h["number"] for h in history] == [1, 2] and "trades" not in history[0]
    full = client.get(f"/api/trials/{t2['id']}").json()
    assert full["trades"] == t2["trades"] and full["spec_hash"] == t2["spec_hash"]


@pytest.mark.django_db
def test_bad_spec_points_at_the_field(client, project):
    bad = spec_without_fixed_fields()
    bad["setup"][1]["event"]["params"]["max_age"] = 0
    r = client.post(f"/api/projects/{project['id']}/trials", {"spec": bad}, format="json")
    assert r.status_code == 400 and r.json()["path"] == "setup[1].event.params" and "max_age" in r.json()["error"]
    assert client.post(f"/api/projects/{project['id']}/trials", {"nope": 1}, format="json").status_code == 400
    assert client.post(f"/api/projects/{project['id']}/trials", {"spec": "fvg"}, format="json").status_code == 400
    assert client.get(f"/api/projects/{project['id']}").json()["trial_count"] == 0


@pytest.mark.django_db
def test_bars_window_around_a_trade(client, project):
    pid = project["id"]
    t = client.post(f"/api/projects/{pid}/trials", {"spec": spec_without_fixed_fields()}, format="json").json()
    r = client.get(f"/api/bars?trial={t['id']}&trade=0&before=10&after=5").json()
    trade = t["trades"][0]
    assert r["first_bar"] == max(0, trade["sweep.bar"] - 10) and r["trade"] == trade and r["timeframe"] == "5m"
    bars = r["bars"]
    assert bars[0]["bar"] == r["first_bar"] and bars[-1]["bar"] == min(trade["exit_bar"] + 5, len(bars) + r["first_bar"] - 1)
    assert all(b["low"] <= min(b["open"], b["close"]) <= max(b["open"], b["close"]) <= b["high"] for b in bars)
    assert bars[1]["time"] - bars[0]["time"] == 300
    assert client.get(f"/api/bars?trial={t['id']}&trade=9999").status_code == 404
    assert client.get("/api/bars?trial=x").status_code == 400


@pytest.mark.django_db
def test_exam_is_once_and_locks_the_project(client, project):
    pid = project["id"]
    assert client.post(f"/api/projects/{pid}/exam", {}, format="json").status_code == 400  # no trial yet
    t1 = client.post(f"/api/projects/{pid}/trials", {"spec": spec_without_fixed_fields()}, format="json").json()
    r = client.post(f"/api/projects/{pid}/exam", {}, format="json")
    assert r.status_code == 201, r.content
    exam = r.json()
    assert exam["stage"] == "holdout" and exam["number"] == 1 and exam["verdict"]["stage"] == "holdout"
    assert exam["stress"] is None and exam["project"]["exam_used"] and exam["project"]["locked_spec_hash"] == t1["spec_hash"]
    assert all(tr["signal_time"] > "2024-05-01T21:00:00Z" for tr in exam["trades"])
    assert client.get(f"/api/bars?trial={exam['id']}&trade=0").status_code == 403

    assert client.post(f"/api/projects/{pid}/exam", {}, format="json").status_code == 409
    changed = spec_without_fixed_fields()
    changed["target"]["r"] = 3
    assert client.post(f"/api/projects/{pid}/trials", {"spec": changed}, format="json").status_code == 409
    history = client.get(f"/api/projects/{pid}/trials").json()
    assert [h["stage"] for h in history] == ["development", "holdout"]


@pytest.mark.django_db
def test_exam_is_not_a_trial_and_not_deflated(client, project):
    """The exam is one test on unseen data: it adds no trial, and the development trials don't deflate it."""
    pid = project["id"]
    client.post(f"/api/projects/{pid}/trials", {"spec": spec_without_fixed_fields()}, format="json")
    changed = spec_without_fixed_fields()
    changed["target"]["r"] = 3
    t2 = client.post(f"/api/projects/{pid}/trials", {"spec": changed}, format="json").json()
    assert t2["verdict"]["expected_max_sharpe"] > 0  # development trial 2 is deflated
    assert client.post(f"/api/projects/{pid}/exam", {"trial": "abc"}, format="json").status_code == 400
    exam = client.post(f"/api/projects/{pid}/exam", {}, format="json").json()
    assert exam["verdict"]["n_trials"] == 2 and exam["verdict"]["expected_max_sharpe"] == 0.0
    assert exam["project"]["trial_count"] == 2


@pytest.mark.django_db
def test_demo_is_read_only(client, project):
    from backtests.models import Project
    Project.objects.filter(pk=project["id"]).update(is_demo=True)
    r = client.post(f"/api/projects/{project['id']}/trials", {"spec": spec_without_fixed_fields()}, format="json")
    assert r.status_code == 409
    assert client.post(f"/api/projects/{project['id']}/exam", {}, format="json").status_code == 409
    assert client.get(f"/api/projects/{project['id']}").json()["trial_count"] == 0


# --- parse (a scripted model stands in for Nemotron) ----------------------------------------------------

class ScriptedLLM:
    def __init__(self, *replies):
        self.replies = list(replies)

    def complete(self, messages, schema=None):
        from engine.llm import Completion
        import json
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return Completion(json.dumps(reply), "fake", 0.01, 10, 10)


def _reply(spec=None, questions=()):
    return {"spec": spec, "assumptions": ["Asia low = the Asia session low"], "questions": list(questions),
            "unsupported": []}


@pytest.fixture
def use_llm(monkeypatch):
    def install(*replies):
        monkeypatch.setattr(services, "llm", lambda: ScriptedLLM(*replies))
    return install


# states entry, stop and target: the parser only takes decisions the trader's words contain
FVG_PROMPT = "Buy the FVG within 10 bars of the Asia sweep, limit at the gap top, stop under the swing low, 2R."


@pytest.mark.django_db
def test_parse_says_which_trial_the_spec_would_be(client, project, use_llm):
    pid = project["id"]
    use_llm(_reply(spec_without_fixed_fields()))
    r = client.post(f"/api/projects/{pid}/parse", {"prompt": FVG_PROMPT}, format="json")
    assert r.status_code == 200, r.content
    body = r.json()
    assert body["status"] == "ready" and body["spec"]["timeframe"] == "5m" and body["trial"] == {"existing": None, "next": 1}
    assert "Long US100 on 5-minute bars." in body["card"]["text"] and body["defaults"]
    assert body["attempts"] == [{"error": None, "seconds": 0.01, "tokens": 20}]  # no raw model text to the client

    t1 = client.post(f"/api/projects/{pid}/trials", {"spec": body["spec"]}, format="json").json()
    assert t1["spec_hash"] == body["spec_hash"]
    use_llm(_reply(spec_without_fixed_fields()))
    again = client.post(f"/api/projects/{pid}/parse", {"prompt": FVG_PROMPT}, format="json").json()
    assert again["trial"] == {"existing": 1, "next": 2}


@pytest.mark.django_db
def test_parse_questions_and_answers(client, project, use_llm):
    partial = {k: v for k, v in spec_without_fixed_fields().items() if k != "stop"}
    use_llm(_reply(partial, questions=["Where is your stop?"]))
    no_stop = "Buy the FVG within 10 bars of the Asia sweep, limit at the gap top, 2R."
    body = client.post(f"/api/projects/{project['id']}/parse", {"prompt": no_stop}, format="json").json()
    assert body["status"] == "questions" and body["questions"] == ["Where is your stop?"] and "trial" not in body
    use_llm(_reply(spec_without_fixed_fields()))
    r = client.post(f"/api/projects/{project['id']}/parse",
                    {"prompt": no_stop, "answers": [{"question": "Where is your stop?", "answer": "under the swing low"}]},
                    format="json")
    assert r.json()["status"] == "ready"


@pytest.mark.django_db
def test_parse_input_and_model_failures(client, project, use_llm, monkeypatch):
    from engine.llm import LLMError, LLMNotConfigured
    url = f"/api/projects/{project['id']}/parse"
    assert client.post(url, {"prompt": " "}, format="json").status_code == 400
    assert client.post(url, {"prompt": "x" * 4001}, format="json").status_code == 400
    assert client.post(url, {"prompt": "FVG", "answers": ["stop 10"]}, format="json").status_code == 400
    assert client.post("/api/projects/999/parse", {"prompt": "FVG"}, format="json").status_code == 404
    use_llm(LLMError("timeout"))
    assert client.post(url, {"prompt": "FVG"}, format="json").status_code == 502

    def no_key():
        raise LLMNotConfigured("NEBIUS_API_KEY is not set")
    monkeypatch.setattr(services, "llm", no_key)
    r = client.post(url, {"prompt": "FVG"}, format="json")
    assert r.status_code == 503 and "NEBIUS" not in r.json()["error"]  # configuration details stay in the log


@pytest.mark.django_db
def test_check_previews_an_edit_without_running_it(client, project):
    pid = project["id"]
    url = f"/api/projects/{pid}/check"
    r = client.post(url, {"spec": spec_without_fixed_fields()}, format="json")
    assert r.status_code == 200 and r.json()["trial"] == {"existing": None, "next": 1}
    assert r.json()["card"]["sections"]["target"].startswith("2R") and r.json()["spec"]["instrument"] == "US100"
    t1 = client.post(f"/api/projects/{pid}/trials", {"spec": r.json()["spec"]}, format="json").json()
    assert client.post(url, {"spec": t1["spec"]}, format="json").json()["trial"] == {"existing": 1, "next": 2}
    edited = copy.deepcopy(t1["spec"])
    edited["target"]["r"] = 2.5
    assert client.post(url, {"spec": edited}, format="json").json()["trial"] == {"existing": None, "next": 2}
    edited["target"]["r"] = 0
    bad = client.post(url, {"spec": edited}, format="json")
    assert bad.status_code == 400 and bad.json()["path"] == "" and "target" in bad.json()["error"]
    assert client.get(f"/api/projects/{pid}").json()["trial_count"] == 1  # checking never counts


@pytest.mark.django_db
def test_seed_demo_builds_a_read_only_demo(client):
    from django.core.management import call_command
    assert client.get("/api/demo").status_code == 404
    call_command("seed_demo", verbosity=0)
    demo = client.get("/api/demo").json()
    assert demo["is_demo"] and demo["exam_used"] and demo["trial_count"] == 3
    history = client.get(f"/api/projects/{demo['id']}/trials").json()
    assert [h["stage"] for h in history].count("development") == 3 and history[-1]["stage"] == "holdout"
    assert client.post(f"/api/projects/{demo['id']}/trials", {"spec": spec_without_fixed_fields()},
                       format="json").status_code == 409
    call_command("seed_demo", verbosity=0)  # a second run keeps the existing demo
    assert client.get("/api/demo").json()["id"] == demo["id"]
    call_command("seed_demo", replace=True, verbosity=0)
    assert client.get("/api/demo").json()["id"] != demo["id"]
