"""Django settings. Everything configurable comes from the environment.

    DJANGO_SECRET_KEY     required in production
    DJANGO_DEBUG=1        debug mode
    DJANGO_ALLOWED_HOSTS  comma separated, default *
    DJANGO_CORS=1         allow any origin (the Vite dev server); off behind Nginx
    POSTGRES_HOST/DB/USER/PASSWORD/PORT   use Postgres; without POSTGRES_HOST a SQLite file in data/
    BACKTESTER_DATA_DIR   where data/bars lives (engine.data.io)
    NEBIUS_*              the strategy parser's model (engine/llm/client.py)
    PARSE_RATE            parse calls per client IP, default 60/hour (each one spends model tokens)

No auth: projects are reachable by id. Accounts are out of scope for the MVP.
"""

import os
from pathlib import Path

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
    "backtests.middleware.cors",
    "django.middleware.common.CommonMiddleware",
]

ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"
TEMPLATES = []

if os.environ.get("POSTGRES_HOST"):
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.postgresql",
            "HOST": os.environ["POSTGRES_HOST"],
            "PORT": os.environ.get("POSTGRES_PORT", "5432"),
            "NAME": os.environ.get("POSTGRES_DB", "backtester"),
            "USER": os.environ.get("POSTGRES_USER", "backtester"),
            "PASSWORD": os.environ.get("POSTGRES_PASSWORD", ""),
            "CONN_MAX_AGE": 60,
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
    "DEFAULT_THROTTLE_RATES": {"parse": os.environ.get("PARSE_RATE", "60/hour")},
}

CORS_ALLOW_ALL = os.environ.get("DJANGO_CORS", "0") == "1"
BASELINE_RUNS = int(os.environ.get("BASELINE_RUNS", "1000"))

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {"console": {"class": "logging.StreamHandler"}},
    "root": {"handlers": ["console"], "level": "INFO"},
}
