# Edge or Noise

**An honest backtester for discretionary traders.** Describe a strategy in plain English, and the app turns it
into exact rules, tests them on 1-minute data from 2020 to 2026, and tells you whether the result is likely an edge
or just noise.

**Live:** https://edge-or-noise-production.up.railway.app — no signup. Click **See a finished project** for the
demo, or describe your own strategy.

**Video Demo:** https://youtu.be/_SN2In4zpCg

Built for the Nebius x NVIDIA Global AI Hackathon. The language model is **NVIDIA Nemotron 3 Super on Nebius
Token Factory**, and web search is **Tavily**, Nebius's agentic search API.

## The problem

Most backtesters make overfitting easy. Tweak a parameter, rerun, keep the best-looking result, repeat. After 40
tries, *something* looks great, and it fails live because the backtest found noise. ICT and price-action traders
are hit hardest: their ideas ("sweep of the Asia low, then a fair value gap in the New York morning") are hard to
code, so they test by eye or not at all.

Edge or Noise does the opposite. It makes the rules exact, then makes overfitting visible and hard:

- **Every try counts.** Any change to the rules is a new trial with a number, and the verdict is deflated for how
  many you've run (Deflated Sharpe Ratio).
- **A sealed holdout.** The most recent 20% of the data is hidden: the engine refuses to run on it and the chart
  never shows it. You get one final exam on it, which locks the rules.
- **A random baseline.** Each trial is compared with 1,000 random-entry runs matched to it on sessions, exits,
  costs, the long/short mix, the trade count and its own stop distances. Beating random is the bar.
- **A verdict, not a pretty curve:** *probably noise*, *can't tell yet* or *promising*, with the expectancy
  interval, the share of random runs beaten, the deflated score and how many more trades it would take to know.

The demo project shows the point: a popular ICT setup beat 68% of random entries in development, its best tweak
went to the sealed holdout, and the edge shrank from +0.07R to +0.006R per trade.

## How NVIDIA Nemotron is used

Model: `nvidia/nemotron-3-super-120b-a12b` through Nebius Token Factory's OpenAI-compatible API
(`engine/llm/client.py`). It has two jobs, and one rule: **the model only ever produces JSON. It never writes
code that runs, never computes a result, and never decides a verdict.**

### 1. Plain English to rules (`engine/llm/parse.py`)

The trader writes *"Long only. In the NY AM killzone, after a sweep of the Asia low, enter on the first bullish
fair value gap at its top. Stop below the sweep's low, target 2R."* Nemotron returns a rule spec in a constrained
JSON schema, inside an envelope:

```json
{"spec": {...}, "assumptions": [...], "questions": [...], "unsupported": [...]}
```

- **The prompt is generated, not hand-written:** the catalog of building blocks (sessions, prior day/week and
  Asia/London highs and lows, swings, fair value gaps, sweeps, displacement, EMA, ATR, with their parameters and
  aliases such as "Turtle Soup" → sweep), the spec's JSON schema, and validated few-shot examples
  (`engine/llm/prompt.py`). Add a block to the registry and the model knows about it.
- **Settings:** `json_object` mode, temperature 0, reasoning (thinking) off, one retry with the validator's error.
- **It asks instead of guessing.** A missing direction, entry, stop or target becomes a question, never a
  default. Optional fields get pinned defaults, listed as assumptions by the engine, not the model.
- **Deterministic checks against the trader's own words** catch what the model gets wrong: an entry, stop or
  target the trader never stated is dropped and asked about; ticks and pips are always asked about (the engine
  works in points); a session or level the trader named but the spec lacks is sent back; a multi-step setup
  with no time limit asks how long each step may wait; concepts the engine can't test (VWAP, order flow,
  Wyckoff, …) are reported as "can't test yet" rather than silently dropped.
- **The rules card the trader approves is rendered from the spec by templates**, so what they read is exactly
  what runs.

### 2. Finding a strategy on the web (`engine/llm/research.py`)

New traders often don't know how to write rules. Ask *"find the most famous ICT strategy and backtest it"*:
[Tavily](https://tavily.com) searches the web, and Nemotron writes the strategy as complete rules in the
backtester's own terms. Before the trader sees them:

- Every number must appear in the search results or the question. Gaps are filled with fixed, disclosed house
  defaults, listed under "Our choices".
- **The real parser (Nemotron again) reads the rules**, and anything it would ask goes back for a rewrite, up to
  three write-ups. Parts that can't be tested (order blocks, the short side) are listed under "Left out".
- Sources are the search results, picked by number: the model never writes a URL.

Nothing runs until the trader clicks "Run as trial 1", and it counts like any other trial.

### What stays deterministic

Signals, fills, costs, the random baseline, the verdict math, the rules card and every number on screen come
from the Python engine. The model turns words into a spec; the engine is the only thing that judges it.

## Nebius services used

- **Nebius Token Factory** runs Nemotron for both jobs above: every parse, every strategy write-up and every
  check of a write-up.
- **Tavily**, the agentic search API Nebius acquired in 2026, finds strategies on the web
  (`engine/llm/research.py`). One advanced search per question returns the top 5 pages with the 3 most relevant
  passages from each. Those passages are what Nemotron writes the rules from, and what makes the checks
  possible: a number in the rules has to appear in them or be listed under "Our choices", and the trader
  sees the pages as sources.

The web app and its Postgres database are hosted on Railway. Both services have spend caps on the live site, per
visitor and for everyone together, so a busy day can't run up the bill.

## Where Token Factory accelerated the work

- **Measuring instead of guessing.** A golden eval of 14 trader prompts with expected specs
  (`engine/llm/golden.py`) runs against the live model in about 15 seconds: ~1 second and ~8k tokens per parse.
  That made it practical to score every prompt, few-shot and parser change several times. Temperature 0 isn't
  fully deterministic on a hosted model, so one run isn't enough.
- **Choosing settings with data.** Because switching options is an environment variable, the same eval compared
  them in minutes:

  | Settings | Golden eval | Time per parse |
  |---|---|---|
  | `json_object`, thinking off (shipped) | 12/12 in 3 runs in a row; **14/14** in most runs with 2 more cases | ~1s |
  | `json_schema`, thinking off | 10/12: it picked a direction instead of asking | |
  | `json_object`, thinking on | 9/12 | ~9s |

- **Drop-in API.** Token Factory speaks the OpenAI chat completions API, so the client is the standard `openai`
  SDK with a different base URL, and the model ID is configuration.
- **No GPUs to run.** Hosted inference means the app itself is a single CPU container. On the live site a parse
  takes about 2 seconds, and finding a strategy (Tavily search, write-up and checks) about 4–17 seconds.

## How it works

```
Parquet bars ─► building blocks ─► interpreter ─► Numba simulator ─► trade log
                                                                        │
                      verdict · random baseline · cost stress test · chart
```

- **`engine/`** — a standalone Python package (no Django), tested with pytest.
  - **Building blocks** (`engine/blocks/`): 9 functions. Trader vocabulary maps onto them through parameters and
    aliases (Turtle Soup, stop run and spring are all a sweep; a killzone is a time window). Each returns a series,
    a price level, an event or a zone.
  - **Rule spec** (`engine/spec/`): Pydantic models with all/any/not filters, comparisons and crosses, "A then B
    within N bars" setups, references to setup geometry (`gap.top`, the signal candle's low), market and limit
    entries with expiry, level/ATR/point stops, R-multiple or level targets, a daily trade cap and a time stop.
    Canonicalized and hashed (SHA-256): the hash is a trial's identity.
  - **Simulator** (`engine/sim/`): vectorized signals, then a Numba loop resolves each trade on the 1-minute bars
    inside the strategy's timeframe. Stop wins when stop and target hit in the same bar, gaps fill at the open,
    limits need a trade-through, and the spread is charged on the buy side.
  - **Honest core** (`engine/honest/`): trial ledger, holdout, matched random baseline, verdict with the Deflated
    Sharpe Ratio, costs ×1.5 stress test.
- **`api/`** — Django REST: projects, trials, the final exam, parse and research endpoints.
- **`web/`** — Vite, React and TypeScript, with TradingView Lightweight Charts.

**No lookahead, enforced by tests.** A decision at bar *t* uses only bars closed by *t* and acts from the next
bar. Swings count only from confirmation, a fair value gap from its third candle's close, session highs and lows
only once the session ends. Every block has a truncation test: computed on the data up to a random bar *t*, it
must equal the full-data value at *t*, on every market.

**Data.** Dukascopy 1-minute bid and ask bars for the Nasdaq 100, S&P 500, Bitcoin and Ether CFDs, January 2020
to October 23, 2026 (frozen, so the holdout boundary never moves; it starts June 16, 2025 for all four), built to
1m, 5m and 15m Parquet files indexed by bar close time. Spreads are a fixed schedule from the last six months'
mean (RTH and overnight), charged in points for the indices and as a share of price for crypto. The data isn't in
the repository; the scripts below download and build it.

## Run it locally

**Needs:** Python 3.12 or newer (check with `python3 --version`), Node 22, about 1 GB of disk for four markets,
and a Nebius Token Factory API key for parsing. A Tavily key is optional and enables "find a strategy".

```sh
git clone https://github.com/igorcha/edge-or-noise.git && cd edge-or-noise
python3 -m venv .venv && .venv/bin/pip install -e ".[dev,api,fetch]"

cp .env.example .env        # then set NEBIUS_API_KEY (and TAVILY_API_KEY); .env is gitignored
```

**Price data.** Download Dukascopy's raw bars and build the files the engine reads: about 6 minutes and 160 MB
per market. US100 is enough to start, and it's the market the demo project uses. The build checks the data and
cuts it to the frozen window: a fresh US100 download builds files identical to the live site's.

```sh
for i in US100 US500 BTCUSD ETHUSD; do
  .venv/bin/python scripts/fetch_data.py --instrument $i --start 2020-01
  .venv/bin/python scripts/build_bars.py --instrument $i
done
```

**The app** (two terminals):

```sh
cd api
../.venv/bin/python manage.py migrate
../.venv/bin/python manage.py seed_demo     # the demo project: 3 trials and its final exam
../.venv/bin/python manage.py runserver     # API on :8000

# second terminal, from the repo root
cd web && npm install && npm run dev        # open http://localhost:5173 (it proxies /api to :8000)
```

**Without the web app:**

```sh
.venv/bin/python scripts/run_trial.py --example fvg_long    # one trial with its baseline and verdict, in the terminal
```

### Tests and evals

```sh
.venv/bin/python -m pytest                  # engine and API tests, offline, ~20s (data tests skip until it's built)
.venv/bin/python scripts/eval_llm.py        # the golden eval against live Nemotron: scores and timings
cd web && npx tsc -b && npx oxlint && npm run build
```

### Deploy

One Docker image (`Dockerfile`): Gunicorn runs Django, which serves the API and the React build. The live site
runs on Railway with Postgres and the price data on a volume, and spend caps on the model and search calls per
visitor and for everyone together. Step-by-step guide, including running the production setup locally:
[`DEPLOY.md`](DEPLOY.md).

## Status and roadmap

Built in the hackathon week, live now:

- English to rules with Nemotron, clarifying questions, an editable rules card; finding strategies on the web
- Four markets (Nasdaq 100, S&P 500, Bitcoin, Ether) on 1m, 5m and 15m
- The honest core: trial counter, sealed holdout and one-time final exam, matched random baseline, deflated
  verdict, costs ×1.5 stress test
- Trade chart (a window of bars around each trade, with entry, exit, stop, target and the fair value gap), equity
  curve over the random-baseline band, trial history, demo project

Next, in order:

1. **Prop-firm simulator:** bootstrapped account paths with daily and max loss limits checked against each trade's
   worst point: pass and breach probabilities, days to pass, cost per expected pass.
2. **More of the rule language:** break-even and trailing stops, "since" state, multi-timeframe conditions, stop
   entries; RSI, Bollinger Bands, equal highs/lows, NY midnight open.
3. **More stress tests:** entry one bar late, the same rules on a second market.
4. **A coach:** the engine computes diagnostics (cost share, fast stop-outs, give-backs, dependence on the top
   five trades) and Nemotron explains them, with a checker that rejects any number not in the diagnostics.
   Every suggestion shows "this will be trial N".
5. **Accounts**, so trials are counted per trader across projects, not per project.

Known limits, stated plainly:

- The trial count and the one final exam are **per project**. A trader can start a new project and take another
  exam on the same holdout. Counting across projects needs accounts (point 5).
- The holdout is hidden in the app, but nothing stops someone from looking at a chart elsewhere.
- Prices are Dukascopy CFD quotes, not exchange data, and Dukascopy's volume isn't exchange volume, so there are
  no volume-based tools.
- The verdict judges the mechanical rules, not the trader.
- Not built for phones yet.

## License

[AGPL-3.0](LICENSE). Market data comes from Dukascopy and isn't redistributed here.
