"""Shared fixtures. Every test runs offline: no API key, no real client."""

from __future__ import annotations

from datetime import datetime

import pytest

from triage import llm
from triage.models import Email


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    """Fail loudly if any test tries to build the real Anthropic client from the environment."""
    for name in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_BASE_URL", "TRIAGE_MODEL", "TRIAGE_EFFORT"):
        monkeypatch.delenv(name, raising=False)

    def _forbidden(*args, **kwargs):
        raise AssertionError("a test tried to create the real Anthropic client")

    monkeypatch.setattr(llm, "make_default_client", _forbidden)


def make_email(
    id: str = "T01",
    *,
    when: str = "2026-03-02T10:00:00",
    name: str = "Alex Rider",
    domain: str = "example-mail.example",
    subject: str = "Hello",
    body: str = "Hi there",
) -> Email:
    return Email(
        id=id,
        received_at=datetime.fromisoformat(when),
        sender_name=name,
        sender_domain=domain,
        subject=subject,
        body=body,
    )


@pytest.fixture
def email_factory():
    return make_email
