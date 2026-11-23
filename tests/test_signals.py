from pathlib import Path

import pytest

from triage.signals import compute_signals, load_inbox, normalize_subject, reply_depth

ROOT = Path(__file__).resolve().parent.parent


@pytest.mark.parametrize(
    ("subject", "expected"),
    [
        ("Re: Re: Fwd:  Broken chain ", "broken chain"),
        ("FW: invoice 12", "invoice 12"),
        ("Automatic reply: Re: Hello", "hello"),
        ("Plain subject", "plain subject"),
    ],
)
def test_normalize_subject_strips_reply_prefixes(subject, expected):
    assert normalize_subject(subject) == expected


def test_reply_depth_counts_reply_prefixes_only():
    assert reply_depth("Re: Re: Fwd: Something") == 3
    assert reply_depth("Something") == 0


def test_load_inbox_reads_dataset_in_chronological_order():
    emails = load_inbox(ROOT / "data" / "inbox.json")
    assert 20 <= len(emails) <= 25
    assert [e.received_at for e in emails] == sorted(e.received_at for e in emails)
    assert len({e.id for e in emails}) == len(emails)


def test_chaser_phrase_is_detected(email_factory):
    s = compute_signals(email_factory(body="I am still waiting for an answer, second time I write."))
    assert s.chaser is True


def test_plain_message_has_no_chaser(email_factory):
    assert compute_signals(email_factory(body="Can I book a bike?")).chaser is False


@pytest.mark.parametrize("text", ["costs 1,240.00 EUR", "about 30 USD", "a 10% discount", "only $45"])
def test_money_is_detected(email_factory, text):
    assert compute_signals(email_factory(body=text)).money_mentioned is True


def test_bounce_is_detected_with_failed_domain_and_original_subject(email_factory):
    email = email_factory(
        name="Mail Delivery Subsystem",
        subject="Undeliverable: Re: Squeaky pedal",
        body="Your message could not be delivered to the recipient at domain exmaple-mail.example: "
        "the domain was not found.\n\nOriginal subject: Re: Squeaky pedal",
    )
    s = compute_signals(email)
    assert s.is_bounce is True
    assert s.failed_domain == "exmaple-mail.example"
    assert s.bounced_subject == "squeaky pedal"


def test_normal_email_is_not_a_bounce(email_factory):
    assert compute_signals(email_factory(subject="Undelivered parcel?", body="Where is my order")).is_bounce is False


def test_weekday_that_does_not_match_the_date_is_flagged(email_factory):
    # 3 March 2026 is a Tuesday.
    s = compute_signals(email_factory(when="2026-03-02T10:00:00", body="Can we move it to Monday 3 March?"))
    assert len(s.date_mismatches) == 1
    assert "Tuesday" in s.date_mismatches[0]


def test_weekday_that_matches_the_date_is_not_flagged(email_factory):
    s = compute_signals(email_factory(when="2026-03-02T10:00:00", body="See you Tuesday 3 March."))
    assert s.date_mismatches == []
