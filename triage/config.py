"""Settings read from the environment, and the price table used for cost logging."""

from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PROMPTS_DIR = Path(__file__).resolve().parent / "prompts"
CACHE_DIR = ROOT / "cache"

DEFAULT_MODEL = "claude-opus-5-5"
DEFAULT_EFFORT = "low"  # classification and extraction rarely gain from deeper thinking
MAX_TOKENS = 8000  # thinking counts against max_tokens; the JSON itself is a few hundred tokens


def model() -> str:
    return os.environ.get("TRIAGE_MODEL", "").strip() or DEFAULT_MODEL


def effort() -> str:
    return os.environ.get("TRIAGE_EFFORT", "").strip() or DEFAULT_EFFORT


# USD per million tokens, list prices for the Claude API (cache write = 5-minute TTL).
# Source: the model table of Anthropic's Claude API reference as cached on 2026-11-03, not yet
# checked against the live pricing page. Verify there before trusting a cost report. A model missing here is logged with cost "unknown", never guessed.
PRICES_USD_PER_MTOK = {
    "claude-opus-5-5": {"input": 4.00, "output": 20.00, "cache_write": 5.00, "cache_read": 0.20},
    "claude-sonnet-5-5": {"input": 2.00, "output": 10.00, "cache_write": 2.50, "cache_read": 0.10},
}
