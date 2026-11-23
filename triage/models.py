"""Data shapes. `Extraction` is the only schema the model fills; everything else is ours."""

from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field

Category = Literal["REPAIR", "RENTAL", "QUOTE", "BILLING", "COMPLAINT", "IGNORE"]
Urgency = Literal["HIGH", "MEDIUM", "LOW"]
Department = Literal["WORKSHOP", "FRONT_DESK", "SALES", "ACCOUNTS", "MANAGEMENT", "NONE"]
SenderType = Literal["CUSTOMER", "PROSPECT", "SUPPLIER", "SYSTEM", "UNKNOWN"]
DraftStatus = Literal["READY", "MISSING_INFO", "BLOCKED_BY_RULE", "NOT_APPLICABLE"]
ThreadType = Literal["NEW", "RELATED", "FOLLOW_UP", "BOUNCE"]


class Email(BaseModel):
    """One message from the inbox. Senders are a display name plus a domain, never a full address."""

    id: str
    received_at: datetime
    sender_name: str
    sender_domain: str
    subject: str = ""
    body: str = ""


class Intake(BaseModel):
    """The three facts staff need before acting on a repair or rental request."""

    customer_name: Optional[str] = None
    bike_or_booking: Optional[str] = None
    issue: Optional[str] = None


class Extraction(BaseModel):
    """What the model returns (structured output). A proposal: rules.py has the final say."""

    sender_type: SenderType
    category: Category
    urgency: Urgency
    department: Department
    reason: str
    intake: Intake = Field(default_factory=Intake)
    safety_risk: bool = False
    escalation_threat: bool = False
    secondary_requests: list[str] = Field(default_factory=list)
    draft_reply: Optional[str] = None
    human_action: Optional[str] = None


class Signals(BaseModel):
    """Deterministic facts computed in code before any model call."""

    reply_depth: int = 0
    chaser: bool = False
    money_mentioned: bool = False
    is_bounce: bool = False
    failed_domain: Optional[str] = None
    bounced_subject: Optional[str] = None
    date_mismatches: list[str] = Field(default_factory=list)


class Thread(BaseModel):
    linked_to: list[str] = Field(default_factory=list)
    type: ThreadType = "NEW"
    contacts: int = 1
    typo_of_domain: Optional[str] = None  # bounce only: the known domain the failed one resembles


class Meta(BaseModel):
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    cache_creation_input_tokens: int = 0
    cache_read_input_tokens: int = 0
    cost_usd: Optional[float] = None
    from_disk_cache: bool = False
    api_calls: int = 0
    unmetered_calls: int = 0  # calls whose reply the SDK rejected before we could read its usage
    simulated: bool = False
    corrections: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    violations: list[str] = Field(default_factory=list)
    error: Optional[str] = None


class Result(BaseModel):
    """One output row: the five fields staff read first, then the evidence."""

    id: str
    category: Optional[Category] = None
    urgency: Optional[Urgency] = None
    department: Optional[Department] = None
    draft: Optional[str] = None
    reason: Optional[str] = None
    draft_status: Optional[DraftStatus] = None
    draft_note: Optional[str] = None
    human_action: Optional[str] = None
    intake: Intake = Field(default_factory=Intake)
    email: Email
    signals: Signals
    thread: Thread = Field(default_factory=Thread)
    meta: Meta
