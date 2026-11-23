"""Deterministic signals: everything a regex or a calendar can tell us, computed before the model call."""

from __future__ import annotations

import json
import re
from datetime import date
from pathlib import Path

from .models import Email, Signals

_REPLY_PREFIX = re.compile(r"^\s*(?:re|fwd?|fw|aw)\s*:\s*", re.IGNORECASE)
_AUTO_PREFIX = re.compile(r"^\s*(?:automatic reply|auto-reply|out of office|undeliverable|delivery failure)\s*:\s*", re.IGNORECASE)

_CHASER = re.compile(
    r"still waiting|haven'?t heard|have not heard|no (?:reply|answer|response)|"
    r"(?:second|third|fourth) time|chasing|following up|as I wrote before",
    re.IGNORECASE,
)

MONEY = re.compile(
    r"\d+(?:[.,]\d+)*\s*(?:€|£|\$|eur\b|euros?\b|usd\b|gbp\b|dollars?\b|pounds?\b)"
    r"|(?:€|£|\$)\s*\d+"
    r"|\d+(?:[.,]\d+)?\s*%",
    re.IGNORECASE,
)

_BOUNCE_SUBJECT = re.compile(r"undeliverable|delivery (?:status|failure)|returned mail|mail delivery failed", re.IGNORECASE)
_BOUNCE_BODY = re.compile(r"could not be delivered", re.IGNORECASE)
_FAILED_DOMAIN = re.compile(r"recipient at domain\s+([a-z0-9.-]+[a-z0-9])", re.IGNORECASE)
_ORIGINAL_SUBJECT = re.compile(r"original subject:\s*(.+)", re.IGNORECASE)

_WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
_MONTHS = ["january", "february", "march", "april", "may", "june", "july",
           "august", "september", "october", "november", "december"]
_WEEKDAY_DATE = re.compile(
    r"\b(" + "|".join(_WEEKDAYS) + r")\s+(\d{1,2})(?:st|nd|rd|th)?\s+(" + "|".join(_MONTHS) + r")\b",
    re.IGNORECASE,
)


def load_inbox(path: Path) -> list[Email]:
    """Read the inbox JSON and return emails sorted by arrival time."""
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    return sorted((Email.model_validate(item) for item in raw), key=lambda e: e.received_at)


def normalize_subject(subject: str) -> str:
    """Lowercase subject without auto-reply and reply/forward prefixes."""
    text = subject
    while True:
        stripped = _REPLY_PREFIX.sub("", _AUTO_PREFIX.sub("", text))
        if stripped == text:
            break
        text = stripped
    return " ".join(text.split()).lower()


def reply_depth(subject: str) -> int:
    depth, text = 0, subject
    while match := _REPLY_PREFIX.match(text):
        depth += 1
        text = text[match.end():]
    return depth


def _date_mismatches(email: Email) -> list[str]:
    """'Wednesday 14 May' where 14 May is a Thursday: models miss this, a calendar does not."""
    found = []
    for weekday, day, month in _WEEKDAY_DATE.findall(email.body):
        year = email.received_at.year
        try:
            actual = date(year, _MONTHS.index(month.lower()) + 1, int(day))
        except ValueError:
            found.append(f"'{weekday} {day} {month}' is not a valid date in {year}")
            continue
        real = actual.strftime("%A")
        if real.lower() != weekday.lower():
            found.append(f"'{weekday} {day} {month}': {day} {month.capitalize()} {year} is a {real}")
    return found


def compute_signals(email: Email) -> Signals:
    is_bounce = bool(_BOUNCE_SUBJECT.search(email.subject) and _BOUNCE_BODY.search(email.body))
    failed = _FAILED_DOMAIN.search(email.body) if is_bounce else None
    original = _ORIGINAL_SUBJECT.search(email.body) if is_bounce else None
    return Signals(
        reply_depth=reply_depth(email.subject),
        chaser=bool(_CHASER.search(email.body)),
        money_mentioned=bool(MONEY.search(email.body)),
        is_bounce=is_bounce,
        failed_domain=failed.group(1).lower() if failed else None,
        bounced_subject=normalize_subject(original.group(1)) if original else None,
        date_mismatches=_date_mismatches(email),
    )
