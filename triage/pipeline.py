"""Signals -> threads -> one model call per email (none for bounces) -> rules -> Result."""

from __future__ import annotations

from typing import Optional

from . import config
from .llm import LLMError, cost_usd
from .models import Email, Meta, Result, Thread
from .rules import Decision, decide, decide_bounce
from .signals import compute_signals
from .threads import link_threads

_TEMPLATE = (config.PROMPTS_DIR / "classify.md").read_text(encoding="utf-8")


def build_message(email: Email, thread: Optional[Thread], earlier: dict[str, Result]) -> str:
    """The user turn: volatile per-email content, kept after the cached system prompt."""
    context = ""
    if thread and thread.linked_to:
        lines = []
        for prior_id in thread.linked_to:
            prior = earlier.get(prior_id)
            if prior is not None:
                lines.append(f"- {prior_id}, subject '{prior.email.subject}', triaged as "
                             f"{prior.category or 'unknown'} for {prior.department or 'unknown'}")
        context = "Earlier emails from the same sender:\n" + "\n".join(lines) + "\n"
    return _TEMPLATE.format(
        received_at=email.received_at.isoformat(timespec="minutes"),
        weekday=email.received_at.strftime("%A"),
        sender_name=email.sender_name,
        sender_domain=email.sender_domain,
        subject=email.subject,
        thread_context=context,
        body=email.body,
    )


def _result(email, signals, thread, decision: Decision, meta: Meta) -> Result:
    meta.corrections, meta.warnings, meta.violations = decision.corrections, decision.warnings, decision.violations
    return Result(
        id=email.id, category=decision.category, urgency=decision.urgency, department=decision.department,
        draft=decision.draft, reason=decision.reason, draft_status=decision.draft_status,
        draft_note=decision.draft_note, human_action=decision.human_action, intake=decision.intake,
        email=email, signals=signals, thread=thread, meta=meta,
    )


def run_triage(emails: list[Email], client, *, simulated: bool = False) -> list[Result]:
    ordered = sorted(emails, key=lambda e: e.received_at)
    signals = {e.id: compute_signals(e) for e in ordered}
    threads = link_threads(ordered, signals)
    done: dict[str, Result] = {}

    for email in ordered:
        sig, thread = signals[email.id], threads[email.id]
        if sig.is_bounce:
            original = done.get(thread.linked_to[0]) if thread.linked_to else None
            done[email.id] = _result(email, sig, thread, decide_bounce(sig, thread, original),
                                     Meta(model="none (handled in code)", simulated=simulated))
            continue

        try:
            reply = client.extract(build_message(email, thread, done))
        except LLMError as e:
            done[email.id] = Result(id=email.id, email=email, signals=sig, thread=thread,
                                    human_action="automatic triage failed: review this email manually",
                                    meta=Meta(model=client.model, error=str(e), simulated=simulated))
            continue

        inherited = [done[i].intake for i in reversed(thread.linked_to) if i in done]
        meta = Meta(
            model=reply.model,
            **reply.usage,
            cost_usd=None if simulated else cost_usd(reply.model, reply.usage),
            from_disk_cache=reply.from_cache,
            api_calls=reply.api_calls,
            unmetered_calls=reply.unmetered_calls,
            simulated=simulated,
        )
        done[email.id] = _result(email, sig, thread, decide(reply.parsed, sig, thread, inherited), meta)

    return [done[e.id] for e in ordered]
