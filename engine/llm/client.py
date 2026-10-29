"""The model behind the parser. `make_llm()` picks it from LLM_PROVIDER:

    nemotron (default)  Nemotron on Nebius Token Factory, the deployed parser (the hackathon requires it)
    anthropic           Claude (claude.py), for development and for after the hackathon

Configuration comes from the environment. For local runs a repo-root `.env` (gitignored) is
read too, without overriding anything already set. Nemotron, through Token Factory's
OpenAI-compatible chat completions API:

    NEBIUS_API_KEY     required for live calls
    NEBIUS_BASE_URL    default https://api.tokenfactory.us-central1.nebius.com/v1/
    NEBIUS_MODEL       default nvidia/nemotron-3-super-120b-a12b
    NEBIUS_JSON_MODE   json_object (default) | json_schema | off
    NEBIUS_THINKING    off (default) | on | default: Nemotron's reasoning mode, sent as
                       chat_template_kwargs.enable_thinking; "default" sends nothing
    NEBIUS_TIMEOUT_S   per call, default 60. Parsing makes at most two calls and the API runs
                       it inside a request, so this has to stay under half Gunicorn's timeout.

Parsing runs at temperature 0. Anything that only matters to this one provider lives here, so
the parser (parse.py) only sees `complete(messages) -> text`.
"""

import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

DEFAULT_BASE_URL = "https://api.tokenfactory.us-central1.nebius.com/v1/"
DEFAULT_MODEL = "nvidia/nemotron-3-super-120b-a12b"
JSON_MODES = ("json_object", "json_schema", "off")
THINKING = ("off", "on", "default")
ENV_FILE = Path(__file__).resolve().parents[2] / ".env"


class LLMError(RuntimeError):
    """The model couldn't be reached or refused the request (not: it answered badly)."""


class LLMNotConfigured(LLMError):
    pass


@dataclass(frozen=True)
class Completion:
    text: str
    model: str
    seconds: float
    prompt_tokens: int | None = None
    completion_tokens: int | None = None


class LLM(Protocol):
    def complete(self, messages: list[dict], schema: dict | None = None) -> Completion: ...


PROVIDERS = ("nemotron", "anthropic")


def make_llm() -> LLM:
    """The configured model. Raises LLMNotConfigured when its key is missing."""
    load_env_file()
    provider = os.environ.get("LLM_PROVIDER", "nemotron")
    if provider == "nemotron":
        return Nemotron()
    if provider == "anthropic":
        from engine.llm.claude import Claude
        return Claude()
    raise LLMNotConfigured(f"LLM_PROVIDER must be one of {PROVIDERS}, not {provider!r}")


def load_env_file(path: Path | None = None) -> None:
    """KEY=VALUE lines from `.env` into os.environ, never overriding what's already set."""
    path = path or ENV_FILE
    if not path.is_file():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.removeprefix("export ").strip()
        os.environ.setdefault(key, value.strip().strip("'\""))


@dataclass(frozen=True)
class Config:
    api_key: str
    base_url: str = DEFAULT_BASE_URL
    model: str = DEFAULT_MODEL
    json_mode: str = "json_object"
    thinking: str = "off"
    timeout_s: float = 60.0
    max_tokens: int = 4096

    @classmethod
    def from_env(cls) -> "Config":
        load_env_file()
        key = os.environ.get("NEBIUS_API_KEY", "")
        if not key:
            raise LLMNotConfigured("NEBIUS_API_KEY is not set (environment or .env)")
        mode = os.environ.get("NEBIUS_JSON_MODE", "json_object")
        thinking = os.environ.get("NEBIUS_THINKING", "off")
        if mode not in JSON_MODES:
            raise LLMNotConfigured(f"NEBIUS_JSON_MODE must be one of {JSON_MODES}, not {mode!r}")
        if thinking not in THINKING:
            raise LLMNotConfigured(f"NEBIUS_THINKING must be one of {THINKING}, not {thinking!r}")
        return cls(api_key=key, base_url=os.environ.get("NEBIUS_BASE_URL", DEFAULT_BASE_URL),
                   model=os.environ.get("NEBIUS_MODEL", DEFAULT_MODEL), json_mode=mode, thinking=thinking,
                   timeout_s=float(os.environ.get("NEBIUS_TIMEOUT_S", "60")),
                   # thinking spends tokens before the JSON starts
                   max_tokens=16384 if thinking == "on" else 4096)


class Nemotron:
    def __init__(self, config: Config | None = None):
        from openai import OpenAI  # imported here so the engine runs and tests without the SDK

        self.config = config or Config.from_env()
        self._client = OpenAI(api_key=self.config.api_key, base_url=self.config.base_url,
                              timeout=self.config.timeout_s, max_retries=0)  # parse() owns the one retry

    def describe(self) -> str:
        cfg = self.config
        return f"nemotron {cfg.model} (json_mode {cfg.json_mode}, thinking {cfg.thinking})"

    def complete(self, messages: list[dict], schema: dict | None = None) -> Completion:
        import openai

        cfg = self.config
        kwargs: dict = {"model": cfg.model, "messages": messages, "temperature": 0, "max_tokens": cfg.max_tokens}
        if cfg.json_mode == "json_object":
            kwargs["response_format"] = {"type": "json_object"}
        elif cfg.json_mode == "json_schema" and schema is not None:
            kwargs["response_format"] = {"type": "json_schema",
                                         "json_schema": {"name": "envelope", "schema": schema, "strict": False}}
        if cfg.thinking != "default":
            kwargs["extra_body"] = {"chat_template_kwargs": {"enable_thinking": cfg.thinking == "on"}}
        start = time.monotonic()
        try:
            r = self._client.chat.completions.create(**kwargs)
        except openai.APIError as e:
            raise LLMError(f"{cfg.model} at {cfg.base_url}: {e}") from e
        if not r.choices:
            raise LLMError(f"{cfg.model} returned no choices")
        usage = r.usage
        return Completion(text=r.choices[0].message.content or "", model=r.model or cfg.model,
                          seconds=time.monotonic() - start,
                          prompt_tokens=usage.prompt_tokens if usage else None,
                          completion_tokens=usage.completion_tokens if usage else None)
