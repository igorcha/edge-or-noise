"""Claude through the Anthropic API, behind the same `complete(messages) -> text` as Nemotron.

    LLM_PROVIDER=anthropic
    ANTHROPIC_API_KEY  required
    ANTHROPIC_MODEL    default claude-haiku-5-5

The deployed parser is Nemotron (the hackathon requires it); this is the swap for development and
for after. Current Claude models take no sampling settings, so there's no temperature here; the
reply is plain text that parse.py reads as JSON, like Nemotron's. The system prompt and few-shots
are the same ~6.5k tokens on every call, so they're marked for prompt caching.
"""

import os
import time
from dataclasses import dataclass

from engine.llm.client import Completion, LLMError, LLMNotConfigured, load_env_file

DEFAULT_MODEL = "claude-haiku-5-5"


@dataclass(frozen=True)
class ClaudeConfig:
    api_key: str
    model: str = DEFAULT_MODEL
    timeout_s: float = 60.0
    max_tokens: int = 4096

    @classmethod
    def from_env(cls) -> "ClaudeConfig":
        load_env_file()
        key = os.environ.get("ANTHROPIC_API_KEY", "")
        if not key:
            raise LLMNotConfigured("ANTHROPIC_API_KEY is not set (environment or .env)")
        return cls(api_key=key, model=os.environ.get("ANTHROPIC_MODEL", DEFAULT_MODEL),
                   timeout_s=float(os.environ.get("LLM_TIMEOUT_S", "60")))


class Claude:
    def __init__(self, config: ClaudeConfig | None = None):
        from anthropic import Anthropic  # imported here so the engine runs and tests without the SDK

        self.config = config or ClaudeConfig.from_env()
        self._client = Anthropic(api_key=self.config.api_key, timeout=self.config.timeout_s, max_retries=0)

    def describe(self) -> str:
        return f"anthropic {self.config.model}"

    def complete(self, messages: list[dict], schema: dict | None = None) -> Completion:
        import anthropic

        system = "\n\n".join(m["content"] for m in messages if m["role"] == "system")
        turns = [{"role": m["role"], "content": [{"type": "text", "text": m["content"]}]}
                 for m in messages if m["role"] != "system"]
        # Cache the fixed prefix: everything up to the last assistant turn before the trader's message
        # (the few-shots, or on a retry the model's first reply).
        fixed = [i for i, t in enumerate(turns[:-1]) if t["role"] == "assistant"]
        if fixed:
            turns[fixed[-1]]["content"][0]["cache_control"] = {"type": "ephemeral"}
        start = time.monotonic()
        try:
            r = self._client.messages.create(model=self.config.model, max_tokens=self.config.max_tokens,
                                             system=[{"type": "text", "text": system}], messages=turns)
        except anthropic.APIError as e:
            raise LLMError(f"{self.config.model}: {e}") from e
        u = r.usage
        prompt = u.input_tokens + (u.cache_read_input_tokens or 0) + (u.cache_creation_input_tokens or 0)
        return Completion(text="".join(b.text for b in r.content if b.type == "text"), model=r.model,
                          seconds=time.monotonic() - start, prompt_tokens=prompt, completion_tokens=u.output_tokens)
