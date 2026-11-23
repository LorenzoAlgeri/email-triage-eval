from triage.signals import compute_signals
from triage.threads import link_threads


def _link(emails):
    signals = {e.id: compute_signals(e) for e in emails}
    return link_threads(emails, signals)


def test_first_email_is_new(email_factory):
    threads = _link([email_factory("A1")])
    assert threads["A1"].type == "NEW"
    assert threads["A1"].linked_to == []
    assert threads["A1"].contacts == 1


def test_same_sender_without_chaser_is_related(email_factory):
    a = email_factory("A1", subject="Booking", when="2026-03-02T09:00:00")
    b = email_factory("A2", subject="Re: Booking", body="One more question", when="2026-03-02T11:00:00")
    threads = _link([a, b])
    assert threads["A2"].type == "RELATED"
    assert threads["A2"].linked_to == ["A1"]
    assert threads["A2"].contacts == 2


def test_same_sender_with_chaser_is_follow_up(email_factory):
    a = email_factory("A1", when="2026-03-02T09:00:00")
    b = email_factory("A2", body="I haven't heard back from you", when="2026-03-03T09:00:00")
    assert _link([a, b])["A2"].type == "FOLLOW_UP"


def test_same_subject_from_another_domain_is_not_linked(email_factory):
    a = email_factory("A1", subject="Quote", domain="one.example")
    b = email_factory("A2", subject="Quote", name="Someone Else", domain="two.example", when="2026-03-02T12:00:00")
    assert _link([a, b])["A2"].linked_to == []


def test_different_people_on_the_same_webmail_domain_are_not_linked(email_factory):
    a = email_factory("A1", name="First Person", subject="Flat tyre")
    b = email_factory("A2", name="Second Person", subject="Gears", when="2026-03-02T12:00:00")
    assert _link([a, b])["A2"].linked_to == []


def test_thread_collects_all_earlier_emails(email_factory):
    a = email_factory("A1", when="2026-03-02T09:00:00")
    b = email_factory("A2", when="2026-03-02T10:00:00")
    c = email_factory("A3", when="2026-03-02T11:00:00")
    t = _link([a, b, c])["A3"]
    assert t.linked_to == ["A1", "A2"]
    assert t.contacts == 3


def test_bounce_links_to_email_with_the_original_subject_and_spots_the_typo(email_factory):
    original = email_factory("A1", subject="Squeaky pedal", domain="example-mail.example")
    bounce = email_factory(
        "A2",
        name="Mail Delivery Subsystem",
        domain="ourshop.example",
        subject="Undeliverable: Re: Squeaky pedal",
        body="Your message could not be delivered to the recipient at domain exmaple-mail.example.\n"
        "Original subject: Re: Squeaky pedal",
        when="2026-03-02T12:00:00",
    )
    t = _link([original, bounce])["A2"]
    assert t.type == "BOUNCE"
    assert t.linked_to == ["A1"]
    assert t.typo_of_domain == "example-mail.example"


def test_bounce_without_a_matching_email_stays_unlinked(email_factory):
    bounce = email_factory(
        "A2",
        name="Mail Delivery Subsystem",
        subject="Undeliverable: Re: Nothing known",
        body="could not be delivered to the recipient at domain x.example\nOriginal subject: Re: Nothing known",
    )
    t = _link([bounce])["A2"]
    assert t.type == "BOUNCE"
    assert t.linked_to == []
