"""Settings read from a single environment variable (kept out of settings.py so it can be tested)."""

from urllib.parse import unquote, urlsplit


def database_from_url(url: str) -> dict:
    """A Django DATABASES entry from postgres://user:password@host:port/name."""
    u = urlsplit(url)
    if u.scheme not in ("postgres", "postgresql") or not u.hostname or not u.path.strip("/"):
        raise ValueError("DATABASE_URL must look like postgres://user:password@host:port/name")
    return {
        "ENGINE": "django.db.backends.postgresql",
        "HOST": u.hostname,
        "PORT": str(u.port or 5432),
        "NAME": unquote(u.path.strip("/")),
        "USER": unquote(u.username or ""),
        "PASSWORD": unquote(u.password or ""),
        "CONN_MAX_AGE": 60,
        "CONN_HEALTH_CHECKS": True,  # reconnect after a database restart instead of failing a request
    }
