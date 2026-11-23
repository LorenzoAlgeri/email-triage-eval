"""An offline stand-in for `client.messages` so the whole pipeline runs without a key.

It is a keyword baseline, NOT Claude: it guesses facts from a few words. Its eval score is
the floor a model has to beat, and its token counts are rough estimates (characters / 4).
It answers under its own model id, so its replies can never be mistaken for Claude's.
"""

from __future__ import annotations

import re
from types import SimpleNamespace
from typing import Any, Optional

from .models import Extraction, Intake
from .rules import DEPARTMENT_FOR

STUB_MODEL = "offline-keyword-stub"

_FROM = re.compile(r"^From:\s*(.+?)\s*\(([^)]*)\)\s*$", re.MULTILINE)
_SUBJECT = re.compile(r"^Subject:\s*(.*)$", re.MULTILINE)
_BODY = re.compile(r"<email_body>\n?(.*?)\n?</email_body>", re.DOTALL)
_REFERENCE = re.compile(r"\b(?:LCW|W)-\d{4}\b")
# Booking and job formats come from the company profile; bike types are generic words, no brands.
_BIKE = re.compile(r"\b(?:e-bikes?|hybrid bike|road bike|city bike|mountain bike|cargo bikes?)\b", re.IGNORECASE)


def _has(text: str, *words: str) -> bool:
    return any(w in text for w in words)


def _guess(sender: str, subject: str, body: str) -> Extraction:
    text = f"{subject}\n{body}".lower()
    automated = _has(text, "automatic reply", "out of the office", "automated message", "do not reply",
                     "click the link", "password", "congratulations", "could not be delivered")
    supplier = not automated and _has(text, "invoice", "policy", "premium")

    if automated:
        category, sender_type = "IGNORE", "SYSTEM"
    elif supplier:
        category, sender_type = "BILLING", "SUPPLIER"
    elif _has(text, "refund", "money back", "not happy", "dispute"):
        category, sender_type = "COMPLAINT", "CUSTOMER"
    elif _has(text, "brake", "gear", "tyre", "service", "squeak", "chain"):
        category, sender_type = "REPAIR", "CUSTOMER"
    elif _has(text, "quote", "tour", "proposal", "group"):
        category, sender_type = "QUOTE", "PROSPECT"
    elif _has(text, "booking", "booked", "rent"):
        category, sender_type = "RENTAL", "CUSTOMER"
    else:
        category, sender_type = "IGNORE", "UNKNOWN"

    safety = _has(text, "stopped working", "stuck", "not feel safe", "don't feel safe")
    threat = _has(text, "review", "dispute", "lawyer")
    if safety or threat:
        urgency = "HIGH"
    elif _has(text, "booking", "deadline", "confirm") or category == "REPAIR":
        urgency = "MEDIUM"
    else:
        urgency = "LOW"

    reference = _REFERENCE.search(body) or _BIKE.search(body)
    first_name = sender.split()[0] if sender else "there"
    draft: Optional[str] = None
    if category != "IGNORE" and sender_type != "SYSTEM":
        team = DEPARTMENT_FOR[category].replace("_", " ").lower()
        draft = (f"Hello {first_name},\n\nthank you for your email. A colleague from our {team} team "
                 "will look at it and reply to you personally.\n\nLarkspur Cycle Works")
    return Extraction(
        sender_type=sender_type,
        category=category,
        urgency=urgency,
        department=DEPARTMENT_FOR[category],
        reason=f"Keyword baseline: looks like {category.lower()}.",
        intake=Intake(customer_name=sender or None,
                      bike_or_booking=reference.group(0).strip() if reference else None,
                      issue=subject or None),
        safety_risk=safety,
        escalation_threat=threat,
        draft_reply=draft,
    )


class KeywordStub:
    """Duck-types `anthropic.Anthropic().messages` for the one method the pipeline uses."""

    def __init__(self, model_id: str = STUB_MODEL) -> None:
        self.model_id = model_id  # tests may pose as a priced model to exercise the live code path
        self.messages = self
        self.calls = 0
        self.messages_seen: list[str] = []
        self._cache_warm = False

    def parse(self, *, max_tokens: int, messages: list[dict], model: str, system: Any = None,
              output_config: Any = None, output_format: Any = None) -> SimpleNamespace:
        self.calls += 1
        content = messages[-1]["content"]
        self.messages_seen.append(content)
        sender = _FROM.search(content)
        subject = _SUBJECT.search(content)
        body = _BODY.search(content)
        parsed = _guess(sender.group(1) if sender else "", subject.group(1) if subject else "",
                        body.group(1) if body else content)

        system_tokens = sum(len(block.get("text", "")) for block in system or []) // 4
        usage = SimpleNamespace(
            input_tokens=len(content) // 4,
            output_tokens=len(parsed.model_dump_json()) // 4,
            cache_creation_input_tokens=0 if self._cache_warm else system_tokens,
            cache_read_input_tokens=system_tokens if self._cache_warm else 0,
        )
        self._cache_warm = True
        return SimpleNamespace(parsed_output=parsed, stop_reason="end_turn", stop_details=None,
                               usage=usage, model=self.model_id)
