"""The only module that talks to Claude.

One structured-output call per email (`messages.parse` with a pydantic schema), the fixed
system prompt marked for prompt caching, and every full reply saved on disk under a
sha256 key so a repeated run costs nothing and tests never need the network.
Transient errors (429, 5xx, connection) are retried by the SDK itself; this module only
retries once when a reply does not fit the schema.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import anthropic
from pydantic import ValidationError

from . import config
from .models import Extraction

USAGE_KEYS = ("input_tokens", "output_tokens", "cache_creation_input_tokens", "cache_read_input_tokens")
SCHEMA_RETRIES = 1


class LLMError(Exception):
    """Anything that stops us from getting a valid Extraction for one email."""


class _BadReply(Exception):
    """The SDK could not parse the reply into the schema (it raises before returning usage)."""


@dataclass
class LLMResponse:
    parsed: Extraction
    usage: dict[str, int]
    from_cache: bool
    api_calls: int
    model: str
    unmetered_calls: int = 0  # calls whose token usage we could not read (counted in api_calls)


def make_default_client() -> Any:
    """Real client, built only from ANTHROPIC_API_KEY.

    The key is passed explicitly so the SDK cannot silently fall back to other credentials
    (an auth token or a saved login profile) that the person running this did not choose.
    """
    key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not key:
        raise LLMError("ANTHROPIC_API_KEY is not set; use `demo`, `--stub` or `--offline` to run without a key")
    return anthropic.Anthropic(api_key=key)


def build_system_prompt() -> str:
    """System prompt with the company profile inlined. Byte-identical on every call, so it caches."""
    system = (config.PROMPTS_DIR / "system.md").read_text(encoding="utf-8")
    company = (config.PROMPTS_DIR / "company.md").read_text(encoding="utf-8").strip()
    return system.replace("{{COMPANY}}", company)


def cost_usd(model: str, usage: dict[str, int]) -> Optional[float]:
    prices = config.PRICES_USD_PER_MTOK.get(model)
    if prices is None:
        return None
    return (
        usage.get("input_tokens", 0) * prices["input"]
        + usage.get("output_tokens", 0) * prices["output"]
        + usage.get("cache_creation_input_tokens", 0) * prices["cache_write"]
        + usage.get("cache_read_input_tokens", 0) * prices["cache_read"]
    ) / 1_000_000


def _usage(response: Any) -> dict[str, int]:
    usage = getattr(response, "usage", None)
    return {k: int(getattr(usage, k, 0) or 0) for k in USAGE_KEYS}


class TriageClient:
    def __init__(
        self,
        model: str,
        system: str,
        *,
        client: Any = None,
        cache_dir: Optional[Path] = None,
        offline: bool = False,
        effort: str = config.DEFAULT_EFFORT,
        max_tokens: int = config.MAX_TOKENS,
    ) -> None:
        self.model = model
        self.system = system
        self.cache_dir = Path(cache_dir) if cache_dir else None
        self.offline = offline
        self.effort = effort
        self.max_tokens = max_tokens
        self._client = client

    # ------------------------------------------------------------------ disk cache
    def cache_key(self, message: str) -> str:
        schema = json.dumps(Extraction.model_json_schema(), sort_keys=True)
        material = "\n\x00".join([self.model, self.effort, self.system, schema, message])
        return hashlib.sha256(material.encode("utf-8")).hexdigest()

    def _read_cache(self, key: str) -> Optional[LLMResponse]:
        if self.cache_dir is None:
            return None
        path = self.cache_dir / f"{key}.json"
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            parsed = Extraction.model_validate(data["parsed"])
            usage = {k: int(data["usage"].get(k, 0)) for k in USAGE_KEYS}
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            return None  # missing or unreadable: ask the API again
        return LLMResponse(parsed, usage, from_cache=True, api_calls=0, model=data.get("model", self.model))

    def _write_cache(self, key: str, message: str, out: LLMResponse) -> None:
        if self.cache_dir is None:
            return
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        record = {
            "model": out.model,
            "effort": self.effort,
            "system_sha256": hashlib.sha256(self.system.encode("utf-8")).hexdigest(),
            "message": message,
            "usage": out.usage,
            "api_calls": out.api_calls,
            "parsed": out.parsed.model_dump(mode="json"),
            "created": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }
        (self.cache_dir / f"{key}.json").write_text(json.dumps(record, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    # ------------------------------------------------------------------ API
    def extract(self, message: str) -> LLMResponse:
        key = self.cache_key(message)
        cached = self._read_cache(key)
        if cached is not None:
            return cached
        if self.offline:
            raise LLMError("offline mode: no cached reply for this email")

        spent = {k: 0 for k in USAGE_KEYS}
        problem, unmetered = "", 0
        for attempt in range(1, SCHEMA_RETRIES + 2):
            try:
                response = self._call(message)
            except _BadReply as e:
                problem, unmetered = str(e), unmetered + 1
                continue
            for k, v in _usage(response).items():
                spent[k] += v
            if response.stop_reason == "refusal":
                details = getattr(response, "stop_details", None)
                raise LLMError(f"model refused (category: {getattr(details, 'category', None)})")
            if response.stop_reason == "max_tokens":
                problem = "reply truncated at max_tokens"
            elif response.parsed_output is None:
                problem = "reply without structured output"
            else:
                out = LLMResponse(response.parsed_output, spent, from_cache=False, api_calls=attempt,
                                  model=getattr(response, "model", None) or self.model, unmetered_calls=unmetered)
                self._write_cache(key, message, out)
                return out
        raise LLMError(f"{problem} (after {SCHEMA_RETRIES + 1} attempts)")

    def _call(self, message: str) -> Any:
        if self._client is None:
            self._client = make_default_client()
        try:
            return self._client.messages.parse(
                model=self.model,
                max_tokens=self.max_tokens,
                system=[{"type": "text", "text": self.system, "cache_control": {"type": "ephemeral"}}],
                messages=[{"role": "user", "content": message}],
                output_config={"effort": self.effort},
                output_format=Extraction,
            )
        except ValidationError as e:  # truncated or off-schema JSON: the SDK raises inside parse()
            raise _BadReply(f"reply did not validate against the schema ({e.error_count()} errors)") from e
        except anthropic.APIStatusError as e:
            raise LLMError(f"API error {e.status_code}: {e.message}") from e
        except anthropic.APIConnectionError as e:
            raise LLMError(f"cannot reach the API: {e}") from e
