import inspect
from pathlib import Path
from types import SimpleNamespace

from anthropic.resources.messages import Messages

from triage.fake import STUB_MODEL, KeywordStub
from triage.llm import LLMError, TriageClient, build_system_prompt
from triage.models import Extraction
from triage.pipeline import build_message, run_triage
from triage.signals import load_inbox

ROOT = Path(__file__).resolve().parent.parent


def stub_client():
    stub = KeywordStub()
    return TriageClient(STUB_MODEL, build_system_prompt(), client=stub), stub


# --------------------------------------------------------------------------- stub
def test_stub_accepts_exactly_what_the_real_sdk_accepts():
    real = set(inspect.signature(Messages.parse).parameters) - {"self"}
    stub = set(inspect.signature(KeywordStub().messages.parse).parameters)
    assert stub <= real


def test_stub_returns_a_valid_extraction(email_factory):
    email = email_factory(body="The brake on my rental bike stopped working, booking LCW-1111.")
    tc, _ = stub_client()
    out = tc.extract(build_message(email, None, {}))
    assert isinstance(out.parsed, Extraction)
    assert out.parsed.category == "REPAIR"
    assert out.parsed.safety_risk is True


# --------------------------------------------------------------------------- end to end
def test_full_dataset_runs_with_one_call_per_email_except_bounces():
    emails = load_inbox(ROOT / "data" / "inbox.json")
    tc, stub = stub_client()
    results = run_triage(emails, tc, simulated=True)

    assert [r.id for r in results] == [e.id for e in emails]
    bounces = [r for r in results if r.signals.is_bounce]
    assert len(bounces) == 1
    assert stub.calls == len(emails) - len(bounces)
    assert all(r.meta.error is None for r in results)


def test_draft_is_present_exactly_when_status_is_ready():
    tc, _ = stub_client()
    for r in run_triage(load_inbox(ROOT / "data" / "inbox.json"), tc, simulated=True):
        assert (r.draft is not None) == (r.draft_status == "READY"), r.id


def test_simulated_run_reports_no_dollar_cost():
    tc, _ = stub_client()
    results = run_triage(load_inbox(ROOT / "data" / "inbox.json")[:3], tc, simulated=True)
    assert all(r.meta.simulated and r.meta.cost_usd is None for r in results)


def test_bounce_inherits_routing_from_the_email_it_answers(email_factory):
    original = email_factory("A1", subject="Squeaky pedal", body="My Trek pedal squeaks, can you fix it?",
                             domain="example-mail.example")
    bounce = email_factory(
        "A2", name="Mail Delivery Subsystem", domain="ourshop.example",
        subject="Undeliverable: Re: Squeaky pedal",
        body="Your message could not be delivered to the recipient at domain exmaple-mail.example.\n"
        "Original subject: Re: Squeaky pedal",
        when="2026-03-02T12:00:00",
    )
    tc, stub = stub_client()
    first, second = run_triage([original, bounce], tc, simulated=True)
    assert stub.calls == 1
    assert (second.category, second.urgency, second.department) == (first.category, first.urgency, first.department)
    assert second.draft_status == "NOT_APPLICABLE"
    assert "example-mail.example" in second.human_action


def test_follow_up_message_tells_the_model_about_the_thread(email_factory):
    a = email_factory("A1", subject="Gears")
    b = email_factory("A2", subject="Re: Gears", body="still waiting", when="2026-03-03T09:00:00")
    tc, stub = stub_client()
    run_triage([a, b], tc, simulated=True)
    assert "A1" in stub.messages_seen[1]


def test_a_failed_email_is_reported_and_the_run_continues(email_factory):
    class Failing:
        def extract(self, message):
            raise LLMError("boom")

        model = "x"

    results = run_triage([email_factory("A1"), email_factory("A2", when="2026-03-03T09:00:00")], Failing())
    assert all(r.meta.error == "boom" for r in results)
    assert all(r.draft_status is None and "manually" in r.human_action for r in results)
