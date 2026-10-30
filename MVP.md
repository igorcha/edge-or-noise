# 6-day MVP plan: honest backtester for the Nebius x NVIDIA hackathon

## Context

The repo is empty except for `CLAUDE.md`. Deadline is **Fri Oct 30, 10:00 PDT**. Today is Sat Oct 24, so there are 6 working days (Sat–Thu) plus Friday morning as buffer. Solo builder, no data downloaded yet, no Nebius account yet.

The full spec in `CLAUDE.md` is a 3–4 week product. Judges will see a 3-minute demo plus a README, so the MVP must demonstrate the *one* idea that differentiates it: **"describe a strategy in English → run it → the app tells you honestly whether it's noise"**, with the honest core visibly working (trial counter, sealed holdout, random baseline band, deflated verdict). Everything else is dressing.

## The MVP cut

### Keep (the demo path)

| Area | MVP scope |
|---|---|
| Data | **US100 only** (add US500 on day 5 only if ahead). 1m bid+ask → Parquet at 1m, 5m, 15m, indexed by close time UTC. **Done Oct 24:** full Jan 2020 – Oct 23 2026, frozen; holdout from trading day Jun 16 2025. |
| Blocks | Session/killzone, time window, day-of-week, prior day H/L, Asia & London H/L (same function as PDH/PDL with a different window), confirmed swing H/L, FVG (zone), sweep (+ aliases Turtle Soup/spring/stop run), displacement candle, EMA, ATR. Each with a truncation test. |
| Spec | direction, filters (all/any/not, compare, crosses), setup = sequence of events or crosses with "then B within N bars", entry (market / limit at a level or zone edge, with expiry), stop (swing / ATR / fixed points / zone edge), target (R multiple / level), limits (max trades per day, one position at a time). Pydantic → JSON schema → canonicalize → SHA-256. **Done Oct 28** (`engine/spec/`). |
| Simulator | Vectorized signals → Numba scan, one position at a time, exits resolved on 1m bars, stop wins ties, gaps fill at open, costs = spread + commission/slippage from instrument config. Output = trade log DataFrame with the row fields from `CLAUDE.md`. |
| Honest core | Trial counter keyed by spec hash. Holdout = last 20% (min 6 months) sealed; one-time final exam locks the spec. Random baseline ~1,000 runs matched on session, exit rules, costs, daily cap, L/S mix, trade count, stop distances sampled from the strategy. Verdict = expectancy ± SE, % of baseline beaten, Deflated Sharpe for trial count, trades still needed, label "development verdict" until exam. One stress test: **costs ×1.5** (free, it's a rerun). |
| LLM | Nemotron on Token Factory, `json_object` mode, temp 0, prompt generated from block catalog + spec schema + ~10 few-shots, envelope `{spec, assumptions, questions, unsupported}`, one retry with validator error. Golden eval: **12 prompts**, run as a pytest. **Built Oct 28** (`engine/llm/`, `POST /projects/{id}/parse`); live eval on Nemotron still to run. |
| API | Django + DRF, Postgres via Compose. Endpoints: `POST /projects`, `POST /projects/{id}/parse`, `POST /projects/{id}/trials` (run, **synchronous**, no Celery), `GET /trials/{id}`, `GET /bars?trial&trade`, `POST /projects/{id}/exam`. Rules card text rendered server-side from the spec via templates. |
| Frontend | **Built Oct 28** (`web/`): workspace, rules card + parameter editor (live check, "Run as trial N"), verdict card, metrics, equity vs baseline band, trade list, trade chart with FVG box, exam modal + dev/holdout comparison, demo (`manage.py seed_demo`). Untested: the prompt flow against live Nemotron. One workspace page: prompt → questions/assumptions → rules card (editable as a form over the spec; re-running creates trial N+1) → "Run as trial N" → results: verdict card, equity curve over baseline 5/50/95 band, trade list, chart window with entry/exit markers, stop/target lines, FVG box. Trial history drawer, final exam modal, demo mode (pre-loaded FVG project, no signup). |
| Deploy | **Changed Oct 29:** one Docker image on Railway (Django + Gunicorn serving the API and, via WhiteNoise, the Vite build), Railway Postgres, bars on a volume. Free HTTPS `*.up.railway.app` URL. Nemotron stays on Nebius Token Factory, which meets the "runs on Nebius" rule. See `DEPLOY.md`. |
| Stretch (day 5 only) | **Prop sim**: 10k bootstrapped daily paths, daily loss vs MAE, pass/breach probability, median days to pass. Table output, no distribution chart. |

### Cut (say so in the README as "roadmap")

RSI, Bollinger, equal highs/lows, opening range, NY midnight open, `manage` (break-even/trailing), `since` persistent state, multi-timeframe evaluation, stop-entry orders, JP225/XAUUSD, coach card, chat edits as JSON patch, late-entry and second-instrument stress tests, prop-sim chart, backtesting.py cross-check (do one manual sanity check on an EMA cross instead if time allows), drf-spectacular → openapi-typescript codegen (hand-write ~6 TS types), accounts.

**Why these cuts:** `manage`, `since` and MTF are the three hardest interpreter features and none are needed for the canonical ICT demo (NY killzone + sweep of Asia low + FVG entry + swing stop + 2R target). Coach and chat-edit are second LLM surfaces; one LLM surface done well beats two done badly. Codegen and Celery are infrastructure that saves time only after week 2.

## Repo layout

```
backtester/
  engine/            # pure Python, no Django; pytest
    data/            # dukascopy download, resample, parquet io, sessions/DST
    blocks/          # one module per block + registry.py (catalog JSON export)
    spec/            # pydantic models, semantic checks, canonicalize, hash, card templates
    interpreter/     # spec -> signal arrays (entries, stops, targets, expiry)
    sim/             # numba scan, costs, trade log
    honest/          # holdout, trials, baseline, verdict (DSR), stress, propsim
    llm/             # prompt builder, nemotron client, envelope validation, golden eval
    tests/
  api/               # Django project + app: Project, Trial models; DRF views
  web/               # Vite + React + TS + Lightweight Charts v5
  deploy/            # start.sh (the Dockerfile is at the root, where Railway finds it)
  scripts/           # fetch_data.py, warm_numba.py, seed_demo.py
```

## Day-by-day

### Day 1 — Sat Oct 24: data + blocks + spec (and unblock Nebius)
- **First thing, in parallel with coding:** sign up for Nebius, get Token Factory API key, request AI Cloud VM quota/credits. Confirm a Nemotron model id answers a trivial JSON prompt. If provisioning lags, it lags while you code.
- `scripts/fetch_data.py`: download US100 1m bid+ask via `dukascopy-python` (Dukascopy symbol for US100 CFD, check availability of ask side). Resample to 5m/15m, label by **close time**, write Parquet. Run in background; it may take an hour.
- `engine/data/sessions.py`: NY-time session table with DST via `zoneinfo`, trading day roll at 17:00 NY. Unit tests on a DST boundary.
- Blocks + registry + truncation test harness (`tests/test_truncation.py` parametrized over all registered blocks and random `t`). Ship session, time window, DOW, PDH/PDL, Asia/London H/L, swings, FVG, sweep, displacement, EMA, ATR.
- `engine/spec/`: Pydantic models, canonicalize, hash. Test: reordering keys or changing a default doesn't change the hash; changing a param does.

### Day 2 — Sun Oct 25: interpreter + simulator + trade log
- Interpreter: filters (all/any/not) → bool mask; setup sequence "A then B within N" → signal bar + geometry (gap top/bottom, sweep level, swing price); entry/stop/target → per-signal price arrays; expiry.
- Numba simulator: per-signal forward scan on 1m bars inside the strategy TF, stop-wins-tie, gap fill at open, limit needs 1-tick trade-through, one position at a time, daily cap. Costs on bid with spread added on buy side. `warm_numba()` at import.
- Trade log schema + metrics (expectancy R, SD, n, win rate, max DD in R, profit factor, exit mix, MAE/MFE).
- Tests: synthetic bars with known outcomes (stop and target same bar → stop; gap through stop → loss > 1R; limit not touched → no fill).
- End of day: run the canonical FVG spec end-to-end from a script and eyeball 5 trades on a matplotlib chart.

### Day 3 — Mon Oct 26: honest core + Nemotron parse
- `honest/holdout.py`: split, refusal outside exam.
- `honest/baseline.py`: generate 1,000 matched random signal sets, run through the same simulator in a batch, return 5/50/95 equity percentiles + fraction beaten. Target < 15s for 3 years of 5m US100.
- `honest/verdict.py`: expectancy CI, edge over baseline, DSR (Bailey & López de Prado formula, effective trials = project trial count), trades needed, three-way label.
- `honest/stress.py`: costs ×1.5 rerun.
- `llm/`: catalog JSON → prompt; Nemotron client (OpenAI-compatible endpoint on Token Factory), `json_object`, temp 0, validate + one retry; 12-prompt golden eval as pytest (mark as `slow`/network).
- Rules card templates (Jinja2) from spec.

### Day 4 — Tue Oct 27: Django API + React workspace
- Models: Project (instrument, timeframe, holdout_end, exam_used, trial_count), Trial (spec_json, spec_hash, number, summary, metrics_json, verdict_json, baseline_json, trade_log parquet path or JSON column).
- Endpoints listed above; run is synchronous (Gunicorn timeout 180s). `GET /bars` reads a Parquet slice around one trade only.
- React: workspace layout, prompt box → parse result (questions/assumptions/unsupported) → rules card form → run button with "Run as trial N" → results: verdict card, equity vs band (Lightweight Charts area + lines), trade list.
- Hand-written TS types for the 6 payloads.

### Day 5 — Wed Oct 28: chart + exam + demo mode + deploy (+ prop sim if ahead)
- Trade chart: candlesticks for the window, markers, stop/target price lines bounded to the trade, FVG box via series primitive. Holdout region never requested.
- Final exam modal + badge; trial history drawer.
- `scripts/seed_demo.py`: create the demo project with 3 pre-run trials; demo mode route loads it with no signup.
- Docker Compose + Nginx; deploy to Nebius VM; copy Parquet to the VM; smoke test the full flow on the public URL.
- If ahead by noon: prop sim (`honest/propsim.py`) + a results tab with the three numbers.

### Day 6 — Thu Oct 29: hardening, README, video
- Fix what the deploy exposed. Run golden eval and truncation tests on the VM.
- README: what it is, the honest core, architecture diagram, how Nemotron/Nebius are used, roadmap (the cut list).
- 3-minute demo video on the live URL: prompt → questions → rules card → trial 1 → verdict "can't tell yet" → tweak a parameter → "this is trial 2" → verdict deflates → final exam. That story *is* the product.
- Submit Thursday night. Friday morning is buffer only.

## Risk controls
- **Data download fails or lacks ask side:** fall back to bid-only with a fixed spread from instrument config (documented as an assumption). Decide by end of day 1.
- **Nebius VM delay:** build with Compose locally from day 4; deploy is a copy, not a build. If the VM never comes, deploy anywhere else that's reachable and document the Token Factory usage as the Nebius dependency.
- **Nemotron JSON flakiness:** `json_object` first; keep few-shots short; retry once; if parse fails twice, show the validator message and let the user edit the card form directly. The app still works without the LLM.
- **Baseline too slow:** reduce to 300 runs and say so in the UI; correctness over count.
- **Scope pressure:** the cut order from `CLAUDE.md` applies. Never cut trial counter, holdout, baseline, verdict, truncation tests, demo mode.

## Verification
- `pytest engine/` green: truncation tests for every block, hash stability, simulator edge cases, verdict math on synthetic trade logs, golden eval (network-marked).
- Manual: canonical FVG spec runs end-to-end locally; 5 trades eyeballed against the chart; baseline band renders; trial counter increments on a one-parameter change and not on a no-op resave; holdout request outside exam returns 403; exam runs once then locks.
- Deployed: full flow on the public Nebius URL in a fresh browser, demo mode loads without signup.
