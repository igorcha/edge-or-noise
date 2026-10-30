# Deploying to Railway

The whole app is one Docker image. Railway builds it from `Dockerfile` on every push to `main`, runs it next
to a Railway Postgres database, and gives it a free `https://<name>.up.railway.app` address. The strategy
parser calls Nemotron on Nebius Token Factory over the internet.

```
Browser ──https──► Railway's proxy ──► the app container (Gunicorn on $PORT)
                                         ├── /            the React build (web/dist), served by WhiteNoise
                                         ├── /api/...     Django REST
                                         │     ├── Postgres          (Railway database: projects, trials)
                                         │     ├── /data/bars        (a Railway volume: the price data)
                                         │     └── Nebius Token Factory → Nemotron  (strategy parsing)
```

## The files

| File | What it does |
|---|---|
| `Dockerfile` | The recipe for the image, in two stages. Stage 1 starts from a Node image and runs `npm run build`, which turns the React app into plain files. Stage 2 starts from a Python image, installs the dependencies listed in `pyproject.toml`, copies `engine/`, `api/` and the built frontend, and compiles the simulator. Node isn't in the final image. |
| `deploy/start.sh` | What runs every time the container starts. It creates or updates the database tables (`migrate`), builds the demo project if it's missing and the price data is there (`seed_demo`), then starts Gunicorn, the production web server. |
| `.dockerignore` | What never goes into the image: `.env`, `.venv`, `node_modules`, `data/`. |

The image holds code only. The price data lives on a volume, and secrets live in Railway variables, so neither
ends up in the image or on GitHub.

## First deploy

**1. Create the project.** At [railway.com](https://railway.com), choose New Project → Deploy from GitHub repo →
`edge-or-noise`. Railway finds the `Dockerfile` and starts building, which takes a few minutes the first time.
This first deploy **will crash**: it has no secret key or database yet. That's expected.

**2. Add the database.** In the project, choose + Create → Database → PostgreSQL. It appears as a second box named
`Postgres`.

**3. Set the app's variables.** Click the app's box, open Variables, then Raw Editor, and paste:

```
DJANGO_SECRET_KEY=<paste a long random string>
DATABASE_URL=${{Postgres.DATABASE_URL}}
PROXY_HOPS=1
LLM_PROVIDER=nemotron
NEBIUS_API_KEY=<your Token Factory key>
TAVILY_API_KEY=<your Tavily key>
```

- To generate the secret key: `python3 -c "import secrets; print(secrets.token_urlsafe(50))"`.
- `${{Postgres.DATABASE_URL}}` is a reference: Railway fills in the database's address and password itself.
- `PROXY_HOPS=1` tells Django that Railway's proxy sits in front of the app, so it reads each visitor's real
  address. Without it, the per-visitor parse limit would count all visitors together.
- `TAVILY_API_KEY` turns on "Ask the web" on the home page (Tavily search, then Nemotron writes up what it
  found). Without it the home page just doesn't offer it. `RESEARCH_RATE` (default `20/hour` per visitor) caps it.
- Spend caps, all optional. Per visitor: `PARSE_RATE` (default `60/hour`) and `RESEARCH_RATE` (`20/hour`). For
  everyone together, the ceiling on what the app can spend on your keys however many people (or bots) call:
  `PARSE_RATE_GLOBAL` (`300/hour`), `PARSE_RATE_GLOBAL_DAY` (`1500/day`), `RESEARCH_RATE_GLOBAL` (`60/hour`),
  `RESEARCH_RATE_GLOBAL_DAY` (`200/day`). At worst-case cost per call that's about $30 a day of Nemotron and
  400 Tavily credits. The counters live in the app's memory: keep `WEB_CONCURRENCY` at 1 (the default), since each
  extra Gunicorn worker would get caps of its own, and a redeploy starts them from zero. The day is a rolling 24
  hours. Trial runs and final exams (about a second of CPU and a stored trade log each) and new projects are
  capped the same way: `RUN_RATE` (`120/hour` per visitor), `RUN_RATE_GLOBAL` (`600/hour`), `RUN_RATE_GLOBAL_DAY`
  (`2000/day`), `PROJECT_RATE` (`30/hour`), `PROJECT_RATE_GLOBAL` (`200/hour`), `PROJECT_RATE_GLOBAL_DAY`
  (`500/day`). Reading pages and trial history is never capped. Set hard limits on the platforms too: a prepaid Token Factory balance with auto top-up
  off, Tavily's free tier or pay-as-you-go off, and Railway's usage limit.
- The defaults (Nemotron 3 Super in `us-central1`, JSON mode, thinking off) are the tested ones. Optional settings
  are listed in `.env.example`. To use Claude instead, set `LLM_PROVIDER=anthropic` and `ANTHROPIC_API_KEY=<key>`.

**4. Add a volume for the price data.** Go to the app's box → Settings → Volumes (or right-click the box →
Attach volume), with mount path `/data`.

**5. Give it a public address.** Go to Settings → Networking → Generate Domain. If Railway asks for a port, use
the one in the deploy log line `Listening at: http://[::]:<port>`.

Saving variables or a volume triggers a redeploy. The app now starts, and `https://<name>.up.railway.app/api/health`
shows `"database": true` and `"US100": false`, because there's no data yet.

**6. Upload the price data**, once, from this repo on your Mac:

```
brew install railway          # Railway's command-line tool
railway login                 # opens the browser
railway link                  # pick the project and the app service
railway volume files upload data/bars /bars    # add --overwrite to replace files already there
railway redeploy              # so start.sh builds the demo project
```

Then `/api/health` should show `true` for US100, US500, BTCUSD and ETHUSD, and the home page should show
"See a finished project" and a market picker. The upload is about 300MB.

**7. Health check (recommended).** Under Settings → Deploy → Healthcheck Path, enter `/api/health`. Railway then
only switches traffic to a new version once it answers, so a broken push never replaces a working site.

## After that

- **Updating:** `git push` to `main` and Railway rebuilds and redeploys. The database and the price data are kept.
- **Debug is off:** `DEBUG` is only on when `DJANGO_DEBUG=1`, so never add that variable on Railway. To check the
  live site: `curl -s https://<name>.up.railway.app/api/nope | grep -c URLconf` prints `0` (with debug on, Django's
  error page lists the URL patterns). Locally, `cd api && DJANGO_SECRET_KEY=x ../.venv/bin/python manage.py check
  --deploy` runs Django's deployment checklist; the two warnings left are about forcing HTTPS, which is the
  proxy's job here. Check that Railway does it: `curl -sI http://<name>.up.railway.app/` should answer with a
  redirect to `https://`.
- **Logs:** the app's box → Deployments → View logs. Each request shows the visitor's address, the request,
  the status and how long it took.
- **Cost:** each market's price data is loaded into memory the first time someone uses it. With all four in
  use the container held 1.44GB (measured in the Docker test, Oct 30). The app is idle most of the time and Postgres is small.
  Expect roughly $20 to $30 a month on the Hobby plan, which includes $5 of usage.
- **Model spend:** each parse is one or two model calls, a web search up to nine. The per-visitor and global
  caps above bound it; the global daily ones are the ceiling on the bill.

## Running the production setup locally

The same start script works outside Docker:

```
cd web && npm run build && cd ..
PATH="$PWD/.venv/bin:$PATH" DJANGO_SECRET_KEY=local PORT=8001 sh deploy/start.sh
```

Then open http://localhost:8001. Django serves the built frontend, as on Railway.
