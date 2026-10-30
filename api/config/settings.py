"""Django settings. Everything configurable comes from the environment.

    DJANGO_SECRET_KEY     required in production
    DJANGO_DEBUG=1        debug mode
    DJANGO_ALLOWED_HOSTS  comma separated, default *
    DJANGO_CORS=1         allow any origin (the Vite dev server); off in production, where the app is same-origin
    DATABASE_URL          postgres://user:password@host:port/name (Railway's Postgres provides it)
    POSTGRES_HOST/DB/USER/PASSWORD/PORT   the same, as separate variables
                          with neither, a SQLite file in data/
    BACKTESTER_DATA_DIR   where data/bars lives (engine.data.io)
    LLM_PROVIDER, NEBIUS_*, ANTHROPIC_*   the strategy parser's model (engine/llm/client.py)
    PARSE_RATE            parse calls per client IP, default 60/hour (each one spends model tokens)
    TAVILY_API_KEY        web search for "find a strategy" (engine/llm/research.py); without it the UI hides it
    RESEARCH_RATE         web searches per client IP, default 20/hour (search credits plus model tokens)
    PARSE_RATE_GLOBAL, PARSE_RATE_GLOBAL_DAY, RESEARCH_RATE_GLOBAL, RESEARCH_RATE_GLOBAL_DAY
                          the same for all visitors together, default 300/hour, 1500/day, 60/hour, 200/day:
                          the ceiling on total spend (about $30/day of Nemotron and 400 Tavily credits at
                          worst-case cost per call), however many IPs call. Counters reset on redeploy.
    RUN_RATE(_GLOBAL, _GLOBAL_DAY)        trial runs and final exams: per IP 120/hour, everyone 600/hour, 2000/day
    PROJECT_RATE(_GLOBAL, _GLOBAL_DAY)    new projects: per IP 30/hour, everyone 200/hour, 500/day
    PROXY_HOPS            proxies in front of the app (Railway: 1), so client IPs and https are read
                          from X-Forwarded-For/-Proto; 0 (default) trusts no forwarded headers
    WEB_DIST              the built frontend (default web/dist), served at / when it exists

In production one process serves both: Django answers /api/..., WhiteNoise serves the React build
for everything else. The app uses hash routes (#/p/12), so the server only ever sees "/" for pages.

No auth: projects are reachable by id. Accounts are out of scope for the MVP.
"""

import os
from pathlib import Path

from config.env import database_from_url

API_DIR = Path(__file__).resolve().parent.parent
ROOT_DIR = API_DIR.parent

SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY", "dev-only-not-a-secret")
DEBUG = os.environ.get("DJANGO_DEBUG", "0") == "1"
ALLOWED_HOSTS = os.environ.get("DJANGO_ALLOWED_HOSTS", "*").split(",")

INSTALLED_APPS = [
    "rest_framework",
    "backtests",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",  # nosniff, Referrer-Policy and COOP headers
    "backtests.middleware.cors",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",  # the app isn't meant to load inside other sites
]
# No CSRF middleware: there are no logins, sessions or cookies, so there's nothing for a forged request to ride on.
SILENCED_SYSTEM_CHECKS = ["security.W003"]

WEB_DIST = Path(os.environ.get("WEB_DIST", ROOT_DIR / "web" / "dist"))
if (WEB_DIST / "index.html").is_file():
    MIDDLEWARE.insert(1, "whitenoise.middleware.WhiteNoiseMiddleware")  # right after SecurityMiddleware, per WhiteNoise
    WHITENOISE_ROOT = WEB_DIST
    WHITENOISE_INDEX_FILE = True  # "/" -> index.html


def WHITENOISE_IMMUTABLE_FILE_TEST(path: str, url: str) -> bool:
    return url.startswith("/assets/")  # Vite puts a content hash in these names: cache them forever


PROXY_HOPS = int(os.environ.get("PROXY_HOPS", "0"))
if PROXY_HOPS:
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")

ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"
TEMPLATES = []

if os.environ.get("DATABASE_URL"):
    DATABASES = {"default": database_from_url(os.environ["DATABASE_URL"])}
elif os.environ.get("POSTGRES_HOST"):
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.postgresql",
            "HOST": os.environ["POSTGRES_HOST"],
            "PORT": os.environ.get("POSTGRES_PORT", "5432"),
            "NAME": os.environ.get("POSTGRES_DB", "backtester"),
            "USER": os.environ.get("POSTGRES_USER", "backtester"),
            "PASSWORD": os.environ.get("POSTGRES_PASSWORD", ""),
            "CONN_MAX_AGE": 60,
            "CONN_HEALTH_CHECKS": True,
        }
    }
else:
    _data = Path(os.environ.get("BACKTESTER_DATA_DIR", ROOT_DIR / "data"))
    _data.mkdir(parents=True, exist_ok=True)
    DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": _data / "api.sqlite3"}}

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
USE_TZ = True
TIME_ZONE = "UTC"
APPEND_SLASH = False

REST_FRAMEWORK = {
    "DEFAULT_RENDERER_CLASSES": ["rest_framework.renderers.JSONRenderer"],
    "DEFAULT_PARSER_CLASSES": ["rest_framework.parsers.JSONParser"],
    "DEFAULT_AUTHENTICATION_CLASSES": [],
    "DEFAULT_PERMISSION_CLASSES": ["rest_framework.permissions.AllowAny"],
    "UNAUTHENTICATED_USER": None,
    "EXCEPTION_HANDLER": "backtests.views.exception_handler",
    "DEFAULT_THROTTLE_RATES": {
        "parse": os.environ.get("PARSE_RATE", "60/hour"),  # per client IP
        "research": os.environ.get("RESEARCH_RATE", "20/hour"),
        # everyone together: the ceiling on what the app can spend on its keys (views.SpendThrottle)
        "parse_global": os.environ.get("PARSE_RATE_GLOBAL", "300/hour"),
        "parse_global_day": os.environ.get("PARSE_RATE_GLOBAL_DAY", "1500/day"),
        "research_global": os.environ.get("RESEARCH_RATE_GLOBAL", "60/hour"),
        "research_global_day": os.environ.get("RESEARCH_RATE_GLOBAL_DAY", "200/day"),
        # trial runs and final exams: about a second of CPU each, and a stored trade log
        "run": os.environ.get("RUN_RATE", "120/hour"),
        "run_global": os.environ.get("RUN_RATE_GLOBAL", "600/hour"),
        "run_global_day": os.environ.get("RUN_RATE_GLOBAL_DAY", "2000/day"),
        # new projects: a database row each
        "project": os.environ.get("PROJECT_RATE", "30/hour"),
        "project_global": os.environ.get("PROJECT_RATE_GLOBAL", "200/hour"),
        "project_global_day": os.environ.get("PROJECT_RATE_GLOBAL_DAY", "500/day"),
    },
    "NUM_PROXIES": PROXY_HOPS or None,  # behind a proxy every request comes from the proxy's IP
}

CORS_ALLOW_ALL = os.environ.get("DJANGO_CORS", "0") == "1"
BASELINE_RUNS = int(os.environ.get("BASELINE_RUNS", "1000"))

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {"console": {"class": "logging.StreamHandler"}},
    "root": {"handlers": ["console"], "level": "INFO"},
}
