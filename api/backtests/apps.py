from django.apps import AppConfig


class BacktestsConfig(AppConfig):
    name = "backtests"

    def ready(self) -> None:
        from engine.sim import warm
        warm()  # compile the numba kernel once at startup, not on the first request
