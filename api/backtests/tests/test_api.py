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
    assert set(trade) >= {"entry", "exit", "stop", "target", "R", "exit_reason", "signal_bar", "exit_bar", "gap.top"}

    # a no-op resave (explicit defaults, alias) is the same trial
    same = spec_without_fixed_fields()
    same["setup"][1]["event"]["block"] = "fair value gap"
    same["slippage_ticks"] = 0
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
    assert client.get(f"/api/projects/{project['id']}").json()["trial_count"] == 0


@pytest.mark.django_db
def test_bars_window_around_a_trade(client, project):
    pid = project["id"]
    t = client.post(f"/api/projects/{pid}/trials", {"spec": spec_without_fixed_fields()}, format="json").json()
    r = client.get(f"/api/bars?trial={t['id']}&trade=0&before=10&after=5").json()
    trade = t["trades"][0]
    assert r["first_bar"] == max(0, trade["signal_bar"] - 10) and r["trade"] == trade and r["timeframe"] == "5m"
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
