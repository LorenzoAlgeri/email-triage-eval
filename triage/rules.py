"""House rules in code. The model proposes an Extraction; this module decides.

Same input, same decision, on every run, and every rule is testable without a network.
Each override is logged in `corrections`, each disagreement left as is in `warnings`,
each broken draft rule in `violations`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

from .models import Extraction, Intake, Result, Signals, Thread
from .signals import MONEY

DEPARTMENT_FOR = {
    "REPAIR": "WORKSHOP",
    "RENTAL": "FRONT_DESK",
    "QUOTE": "SALES",
    "BILLING": "ACCOUNTS",
    "COMPLAINT": "MANAGEMENT",
    "IGNORE": "NONE",
}
INTAKE_REQUIRED_FOR = {"REPAIR", "RENTAL"}
INTAKE_LABELS = {
    "customer_name": "customer name",
    "bike_or_booking": "bike or booking reference",
    "issue": "what the problem or request is",
}
DRAFT_MAX_WORDS = 90
REASON_MAX_CHARS = 160
ESCALATE_AT_CONTACTS = 3
_URGENCY_UP = {"LOW": "MEDIUM", "MEDIUM": "HIGH", "HIGH": "HIGH"}

_TIME_PROMISE = re.compile(
    r"\b(?:monday|tuesday|wednesday|thursday|friday|saturday|sunday|today|tonight|tomorrow)\b"
    r"|\b\d{1,2}[:.]\d{2}\b"
    r"|\b\d{1,2}/\d{1,2}\b"
    r"|\b(?:within|in)\s+(?:\d+|an?|one|two|half an?)\s+(?:minutes?|hours?|days?)\b",
    re.IGNORECASE,
)
_REFUND_PROMISE = re.compile(r"refund|reimburs|compensat|money back", re.IGNORECASE)


@dataclass
class Decision:
    category: str
    urgency: str
    department: str
    reason: str
    intake: Intake
    human_action: Optional[str] = None
    draft: Optional[str] = None
    draft_status: str = "NOT_APPLICABLE"
    draft_note: Optional[str] = None
    corrections: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    violations: list[str] = field(default_factory=list)

    def set(self, name: str, value: str, why: str) -> None:
        old = getattr(self, name)
        if old != value:
            setattr(self, name, value)
            self.corrections.append(f"{name} {old}->{value} ({why})")

    def add_action(self, text: str) -> None:
        self.human_action = f"{self.human_action}; {text}" if self.human_action else text


def _blank(value: Optional[str]) -> bool:
    return value is None or not value.strip()


def check_draft(draft: Optional[str], category: str) -> list[str]:
    """House rules for a written reply. Empty list means the draft may go to a human for sending."""
    if _blank(draft):
        return ["draft:missing"]
    found = []
    if len(draft.split()) > DRAFT_MAX_WORDS:
        found.append("draft:too_long")
    if MONEY.search(draft):
        found.append("draft:price")
    if _TIME_PROMISE.search(draft):
        found.append("draft:time_promise")
    if category == "COMPLAINT" and _REFUND_PROMISE.search(draft):
        found.append("draft:refund_promise")
    return found


def _merged_intake(own: Intake, inherited: list[Intake]) -> tuple[Intake, list[str]]:
    values, filled = {}, []
    for name in INTAKE_LABELS:
        value = getattr(own, name)
        if _blank(value):
            value = next((getattr(i, name) for i in inherited if not _blank(getattr(i, name))), None)
            if value is not None:
                filled.append(f"intake.{name} taken from the thread")
        values[name] = value.strip() if isinstance(value, str) and value.strip() else None
    return Intake(**values), filled


def _apply_floors(d: Decision, ex: Extraction, thread: Thread) -> None:
    if ex.safety_risk:
        d.set("urgency", "HIGH", "safety risk")
    if ex.escalation_threat:
        d.set("category", "COMPLAINT", "escalation threat")
        d.set("department", "MANAGEMENT", "escalation threat")
        d.set("urgency", "HIGH", "escalation threat")
    if thread.type == "FOLLOW_UP":
        d.set("urgency", _URGENCY_UP[d.urgency], "follow-up")
        if thread.contacts >= ESCALATE_AT_CONTACTS:
            d.set("department", "MANAGEMENT", f"contact number {thread.contacts}")
    if d.category == "IGNORE":
        d.set("urgency", "LOW", "nothing to do")
        d.set("department", "NONE", "nothing to do")


def _align_department(d: Decision) -> None:
    expected = DEPARTMENT_FOR[d.category]
    if d.department == expected or d.corrections and any("department" in c for c in d.corrections):
        return
    if d.department == "NONE":
        d.set("department", expected, "team for this category")
    else:
        d.warnings.append(f"department {d.department} for {d.category} (usual team: {expected})")


def _gate(d: Decision, ex: Extraction, signals: Signals) -> None:
    """The first rule that fires decides whether a draft may leave."""
    if ex.sender_type == "SYSTEM" or signals.is_bounce:
        d.draft_status, d.draft_note = "NOT_APPLICABLE", "automated sender: nobody to answer"
    elif d.category == "IGNORE":
        d.draft_status, d.draft_note = "NOT_APPLICABLE", "nothing to answer"
    elif ex.sender_type == "SUPPLIER":
        d.set("department", "ACCOUNTS", "supplier")
        d.draft_status, d.draft_note = "BLOCKED_BY_RULE", "suppliers are handled by accounts, not answered from the inbox"
    elif d.category in INTAKE_REQUIRED_FOR and (missing := [
        label for name, label in INTAKE_LABELS.items() if _blank(getattr(d.intake, name))
    ]):
        d.draft_status, d.draft_note = "MISSING_INFO", "missing: " + ", ".join(missing)
        d.add_action("ask the customer for: " + ", ".join(missing))
    elif violations := check_draft(ex.draft_reply, d.category):
        d.violations.extend(violations)
        d.draft_status, d.draft_note = "BLOCKED_BY_RULE", "draft rejected: " + ", ".join(violations)
    else:
        d.draft, d.draft_status = ex.draft_reply, "READY"


def decide(ex: Extraction, signals: Signals, thread: Thread, inherited: list[Intake]) -> Decision:
    intake, filled = _merged_intake(ex.intake, inherited)
    d = Decision(
        category=ex.category,
        urgency=ex.urgency,
        department=ex.department,
        reason=ex.reason.strip(),
        intake=intake,
        human_action=ex.human_action,
        corrections=filled,
    )
    _apply_floors(d, ex, thread)
    _align_department(d)
    for mismatch in signals.date_mismatches:
        d.warnings.append(f"date check: {mismatch}")
        d.add_action(f"confirm the date with the sender ({mismatch})")
    _gate(d, ex, signals)
    if len(d.reason) > REASON_MAX_CHARS:
        d.reason = d.reason[: REASON_MAX_CHARS - 3] + "..."
        d.warnings.append("reason truncated")
    return d


def decide_bounce(signals: Signals, thread: Thread, original: Optional[Result]) -> Decision:
    """A bounce of one of our replies never goes to the model: it inherits the original's routing."""
    if original is None or original.category is None:
        d = Decision(category="IGNORE", urgency="LOW", department="NONE",
                     reason="Delivery failure that matches no known email.", intake=Intake())
        d.draft_note = "automated sender: nobody to answer"
        return d
    target = thread.typo_of_domain or original.email.sender_domain
    typo = f" (typo: {signals.failed_domain} instead of {thread.typo_of_domain})" if thread.typo_of_domain else ""
    d = Decision(
        category=original.category,
        urgency=original.urgency or "LOW",
        department=original.department or "NONE",
        reason=f"Our reply to {original.id} was not delivered{typo}; routing inherited from {original.id}."[:REASON_MAX_CHARS],
        intake=original.intake,
        human_action=f"resend the reply to {original.email.sender_name} at {target}, or phone them",
    )
    d.draft_note = "automated sender: nobody to answer"
    return d
