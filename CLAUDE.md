# CLAUDE.md

## What this is

[App name] is an "honest" backtesting web app for discretionary traders, especially ICT/price-action and prop-firm traders. It's a solo entry for the Nebius x NVIDIA Global AI Hackathon, due **Fri Oct 30, 2026, 10:00 AM PDT**. It must run on Nebius (Token Factory / AI Cloud) and use an NVIDIA open model (Nemotron).

The user describes a strategy in plain English. Nemotron turns it into a constrained JSON rule spec, and a deterministic Python engine runs it. The app then says honestly whether the result is likely noise: it counts every trial, seals a holdout, compares against random entries, and deflates the verdict for the number of trials.

**Core principle:** make overfitting visible and hard. Anything that makes it easier (optimizers, unconstrained "improve my strategy") is out.

## Architecture

```
Parquet bars -> building blocks -> interpreter -> Numba simulator -> trade log
                                                                       |
                         metrics / validation / prop sim / chart / coach
```

The trade log is the contract. Everything downstream only reads it.

- `engine/`: pure Python package, no Django imports, tested with pytest. Covers data, blocks, spec, interpreter, simulator, validation, prop sim and diagnostics.
- `api/`: Django REST. Postgres models: Project, Trial, trade logs.
- `web/`: Vite + React + TypeScript, Lightweight Charts v5. API types are generated with drf-spectacular → openapi-typescript.
- **Deploy:** one Docker image on Railway (`Dockerfile`, `deploy/start.sh`, guide in `DEPLOY.md`): Gunicorn runs Django, which serves `/api` and, through WhiteNoise, the React build. Railway Postgres; the bars on a volume at `/data`. The model is Nemotron on Nebius Token Factory, which is the hackathon's Nebius requirement.

**Data:** Dukascopy 1m bid and ask CFD bars for US100, US500, JP225 and XAUUSD.
- `data/raw/<INST>/{bid,ask}/YYYY-MM.parquet`: as downloaded, indexed by bar **open** time. Never modified, and the engine never reads it.
- `scripts/build_bars.py` validates raw and writes `data/bars/<INST>/{1m,5m,15m}.parquet` (bid OHLC only, indexed by **bar close time** in UTC) plus `manifest.json` (holdout boundary, spread schedule, file hashes). 2m and 1h come later.
- **The MVP dataset is frozen:** Jan 1 2020 to Oct 23 2026 (`DATA_END` in `engine/data/instruments.py`). Don't refetch, because new data would move the holdout boundary.
- Read bars only through `engine.data.io.load_bars`. It cuts the holdout unless `include_holdout=True`, which only the final exam passes.
- US100 trades 18:00–16:15 NY. The daily break is 16:15–18:00, so the 17:00 roll falls inside it.
- Dukascopy volume isn't exchange volume, so there are no volume-based tools.

## Building blocks

Each block has:
- a function
- a param schema with pinned defaults
- aliases
- a registry entry
- a truncation lookahead test

A block returns one of four types:
- **series:** a number per bar that isn't a price (ATR in points, RSI)
- **level:** a price per bar (prior day high, swing low, EMA)
- **event:** a bool per bar
- **zone:** a price range with a lifetime

The registry exports the catalog as JSON, and the LLM prompt is generated from it. To add a concept, write the block, its registry entry and its test. The simulator never changes.

**Where things live:**
- Blocks are in `engine/blocks/`, grouped by family: `timing.py`, `levels.py`, `structure.py`, `series.py`.
- Each block has a comment above it saying what it is, how strategies use it, and its lookahead rule.
- Every block also needs a `CASES` entry in `engine/tests/test_truncation.py` (`test_every_block_has_a_case` enforces this) and hand-built cases in `engine/tests/test_blocks.py`.

**Must-have blocks:**
- **Time:** sessions/killzones, day of week, time windows.
- **Levels:** prior day/week high/low, Asia/London high/low, NY midnight open, opening range, equal highs/lows.
- **Structure and zones:** confirmed swings, FVG, sweep, candle properties/displacement.
- **Series:** EMA, ATR, RSI, Bollinger Bands.

**Aliases:** Turtle Soup, spring and stop run all map to sweep.

**Unsupported** (shown to users as "can't test yet"): VWAP, volume profile, order flow, Elliott, harmonics, Wyckoff phases, Al Brooks bar counts.

## Rule spec

The spec is a set of Pydantic models with JSON schema export. Its sections are direction, filters, setup (a sequence), entry, stop, target, manage and limits. Instrument and timeframe are fixed per project.

It supports:
- all/any/not filters
- comparisons (`>`, `<`, `>=`, `<=`) and crosses (`crosses_above`, `crosses_below`), prices with prices and numbers with numbers. A cross is an event, so it can be a setup step.
- "A then B within N bars"
- persistent state ("since")
- references to setup geometry (`gap.top`)
- entry expiry
- multi-timeframe evaluation, joined on close time

**Pipeline:** JSON schema → semantic checks → canonicalize (fill defaults, resolve aliases, flatten and sort filters, sort keys) → SHA-256 hash of the canonical JSON plus `ENGINE_VERSION` (`engine/__init__.py`). The hash is the trial's identity. Bump `ENGINE_VERSION` whenever a block, interpreter or simulator change alters results for an unchanged spec.

**Units:** distances in the spec (slippage, stop buffer, limit offset, fixed stops) are price points, never ticks. A US100 tick is 0.01 points.

## Engine rules (non-negotiable)

**No lookahead:**
- A decision at bar t uses only bars closed by t, and acts from the next bar.
- Join resampled bars by close time, never by label (pandas labels by start).
- Swings are usable only from confirmation. An FVG is known at its third candle's close. Session highs/lows are final only at session end. Never normalize over the full sample.
- Sessions are defined in New York time with DST. Everything is UTC internally, and the trading day rolls at 17:00 NY.

**Fills:**
- Market entries fill at the next open. Limits need trade-through by 1 tick. Stop entries are supported.
- When stop and target hit in the same bar, the stop wins. Resolve exits on the 1m bars inside the strategy timeframe, and assume the worst on the fill bar.
- Gaps fill at the open, so losses can exceed 1R.
- Costs: run on bid candles and add the spread on the buy side.
  - Spread is a fixed schedule per instrument, with one value for RTH (9:30–16:00 NY) and one for overnight. Each value is the mean ask − bid over the last 6 months of the dataset, stored in the manifest and applied to all history so dev and holdout are charged the same.
  - Per-bar ask − bid is not used: Dukascopy's quoted spread changed regime during 2020–2026 and never widens on news.
  - Slippage is user-set in the UI (default 0). Commission comes from the instrument config.

**Output and implementation:**
- **R only:** `R = signed (exit - entry) / (entry - stop) - cost_R`. No dollars or lots in the engine; sizing lives in the prop sim.
- **Simulator:** signals are vectorized. A Numba loop scans each signal forward to its exit, one position at a time. Warm Numba at startup.
- **Our own engine:** backtesting.py (AGPL) is used only as a local cross-check. Don't add vectorbt (Commons Clause).
- **Trade log row:** direction, signal/entry/exit times and prices, stop, target, exit reason, R, cost_R, MAE_R, MFE_R, bars held, setup geometry.
- **Truncation test:** every new block or engine change gets one. The feature computed on `data[:t+1]` must equal its full-data value at t, at random t, on all instruments.

## Honest core

- **Trial:** a run with a new spec hash. Any change to a block parameter or definition is a new spec. Slippage is part of the spec, so changing it is a new trial too. Stress tests and the final exam don't count.
- **Holdout:** the most recent 20% of data (minimum 6 months), sealed. The engine refuses it outside a one-time final exam. The exam locks the spec and badges the project "holdout used".
- **Random baseline:** about 1,000 runs, matched to the strategy on:
  - sessions, exits, costs and the daily cap
  - long/short mix and roughly the same trade count
  - stop distances, sampled from the strategy's own stops

  It returns the 5/50/95 percentile equity curves and the share of runs the strategy beat.
- **Verdict:** probably noise / can't tell yet / promising. It reports:
  - the expectancy interval (SE = SD/√n)
  - edge over the baseline
  - a deflated score for the effective trial count (Deflated Sharpe Ratio). Development verdicts only: the holdout exam is one test of one locked spec, so it is judged undeflated (benchmark Sharpe 0) and only reports the trial count
  - how many more trades are needed

  It's labeled "development verdict" until the holdout is used. It judges the mechanical rules, not the trader.
- **Stress tests:** costs ×1.5, entry one bar late, second instrument.
- **Prop sim:** 10,000 bootstrapped daily account paths. The daily loss limit is checked against MAE. It outputs:
  - pass vs breach probability (daily vs max loss)
  - median days to pass
  - cost per expected pass

  Expectancy can optionally be shrunk first, and holdout trades are preferred.

## LLM (Nemotron on Nebius Token Factory)

**Parsing:**
- The LLM only produces JSON. It never writes code that gets executed.
- The prompt is the generated catalog + spec schema + 10–20 few-shot examples. Temperature 0 for parsing. No fine-tuning.
- The output envelope is `{spec, assumptions, questions, unsupported}`.
  - A missing required field (direction, entry, stop, target) becomes a question.
  - A missing optional field gets its pinned default, listed as an assumption.
- Validate the output and retry once with the validator error.
- Settings, tested Oct 29: `nvidia/nemotron-3-super-120b-a12b`, `json_object` mode, thinking off. The golden eval scored 12/12 in three runs in a row, at about 1s per parse; with the two swing-structure cases added it scores 14/14 in most runs (one miss in seven runs of a case was the model asking a question of its own). `json_schema` scored 10/12 (it picked a direction instead of asking), and thinking on scored 9/12 at about 9s. Temperature 0 isn't deterministic on Token Factory, so judge a prompt change on several eval runs.

**Rules card and edits:**
- Rules card text is rendered server-side from the spec with templates. The LLM never writes it.
- Chat edits become a JSON patch against the current spec. The diff becomes the trial's summary line.

**Evaluation:** a golden eval set of 30–50 prompts with expected specs (MVP: 14, `engine/llm/golden.py`). Score every prompt change against it.

**Where it lives:** `engine/llm/`.
- `prompt.py` builds the prompt from the catalog and spec schema.
- `fewshots.py` holds the few-shots, each validated by tests.
- `parse.py` holds the envelope handling:
  - one retry with the validator error
  - a missing direction, setup, entry, stop or target always becomes a question, never a default
  - the engine, not the model, lists the defaults it filled in
  - a keyword check catches unsupported concepts the model dropped
  - checks against the trader's own words, because Nemotron does all of these: an entry, stop or target the trader never stated is dropped and asked; ticks/pips are always asked; both directions are asked; a multi-step setup with no time given ("then a higher low") asks how many bars each step may wait; a session, weekday or session level (Asia/London/prior day/week high or low) the trader named but the spec lacks is sent back once, then flagged; filters written on a setup step move to the top level; a price ref to a swing event (`hl.higher_low`) becomes the swing's level (`hl.low`)
- `client.py` is the Nemotron client. It's configured by `NEBIUS_*` env vars or a gitignored `.env`.

**Coach:**
- The engine computes the diagnostics: cost share, exit mix, fast stop-outs, give-backs after +1.5R, and top-5-trade dependence. The LLM only interprets them.
- A number checker rejects any figure not in the diagnostics JSON.
- Every suggestion shows "this will be trial N".
- Diagnostics accept any trade log, not just engine output.

## Frontend

- **Workspace:** a strategy panel (prompt → clarifying questions → editable rules card → "Run as trial N"), the chart, and results.
- **Chart:**
  - Shows only a window of bars around the selected trade, served by `GET /bars` from Parquet slices. Never load full history.
  - Draws entry/exit markers, stop/target lines bounded to the trade, and the FVG box via a series primitive.
  - The holdout region is hidden.
- **Navigation:** the trade list is the index. The equity curve over the baseline band is the overview; click a point to jump to that trade.
- **Other screens:** trial history drawer, stress tests tab, final exam modal, prop sim tab, and a demo mode (a pre-loaded FVG project, no signup).

## Scope

**Out until after submission:** accounts, broker sync, trade-history analysis, live data, optimizer, mobile.

**If behind, cut in this order:**
1. Stretch blocks
2. Second-instrument stress test
3. Instruments 3–4
4. Chat edits as patches
5. Coach card
6. Prop sim distribution chart

**Never cut:** trial counter, holdout, baseline, verdict, lookahead tests, demo mode.

## Working rules

- Prefer working code over explanation.
- Flag any lookahead risk in indicator or engine code.
- Push back on scope creep and on anything that makes overfitting easier.
- Keep `engine/` free of Django; it must run and test standalone.

## Commands

- Setup: `python3 -m venv .venv && .venv/bin/pip install -e ".[dev,api,fetch]"`
- LLM keys: `cp .env.example .env` at the repo root, then fill in a key (`.env` is gitignored)
- Fetch raw data (already done for the frozen MVP dataset): `.venv/bin/python scripts/fetch_data.py --instrument US100 --start 2020-01`
- Build bars from raw: `.venv/bin/python scripts/build_bars.py --instrument US100`
- Tests: `.venv/bin/python -m pytest` (offline; the live LLM eval is opt-in: `pytest -m network`)
- LLM golden eval with scores and timings: `.venv/bin/python scripts/eval_llm.py [--case NAME] [--show]`
- Local app:
  - API: `cd api && ../.venv/bin/python manage.py migrate && ../.venv/bin/python manage.py runserver`
  - Web: `cd web && npm install && npm run dev`, then open http://localhost:5173; Vite proxies `/api` to :8000.
- Demo project: `cd api && ../.venv/bin/python manage.py seed_demo [--replace]`
- Frontend checks: `cd web && npx tsc -b && npx oxlint && npm run build`
- Deploy: `DEPLOY.md`. A push to main redeploys on Railway; check `/api/health` afterwards.

<!-- TODO: fill in real commands (tests, dev servers, data scripts, deploy) -->
