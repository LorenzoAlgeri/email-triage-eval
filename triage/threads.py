"""Deterministic threading: an email joins the thread of every earlier email from the same
sender (display name and domain). Matching on domain alone would merge strangers who share a
webmail provider; matching on subject alone would merge unrelated "Booking" emails. No model call.

A bounce is linked by the subject it quotes, and the failed domain is compared with the
domains we know to spot a typo in our own reply.
"""

from __future__ import annotations

from difflib import SequenceMatcher

from .models import Email, Signals, Thread
from .signals import normalize_subject

TYPO_SIMILARITY = 0.8  # difflib ratio above which a failed domain counts as a typo of a known one


def _same_sender(a: Email, b: Email) -> bool:
    return a.sender_domain.lower() == b.sender_domain.lower() and a.sender_name.lower() == b.sender_name.lower()


def _bounce_thread(signals: Signals, earlier: list[Email], all_signals: dict[str, Signals]) -> Thread:
    candidates = [
        e for e in earlier
        if not all_signals[e.id].is_bounce and normalize_subject(e.subject) == signals.bounced_subject
    ]
    if not candidates:
        return Thread(type="BOUNCE")
    original = candidates[-1]
    typo_of = None
    if signals.failed_domain and signals.failed_domain != original.sender_domain.lower():
        if SequenceMatcher(None, signals.failed_domain, original.sender_domain.lower()).ratio() >= TYPO_SIMILARITY:
            typo_of = original.sender_domain
    return Thread(type="BOUNCE", linked_to=[original.id], contacts=1, typo_of_domain=typo_of)


def link_threads(emails: list[Email], signals: dict[str, Signals]) -> dict[str, Thread]:
    """Thread for every email, looking only at emails that arrived before it."""
    ordered = sorted(emails, key=lambda e: e.received_at)
    threads: dict[str, Thread] = {}
    for i, email in enumerate(ordered):
        earlier = ordered[:i]
        own = signals[email.id]
        if own.is_bounce:
            threads[email.id] = _bounce_thread(own, earlier, signals)
            continue
        linked = [e.id for e in earlier if not signals[e.id].is_bounce and _same_sender(e, email)]
        if not linked:
            threads[email.id] = Thread()
            continue
        threads[email.id] = Thread(
            linked_to=linked,
            type="FOLLOW_UP" if own.chaser else "RELATED",
            contacts=len(linked) + 1,
        )
    return threads
