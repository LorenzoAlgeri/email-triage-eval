import pytest

from triage.models import Extraction, Intake, Signals, Thread
from triage.rules import check_draft, decide

FULL_INTAKE = Intake(customer_name="Alex Rider", bike_or_booking="LCW-1234", issue="wants a helmet")
GOOD_DRAFT = "Hello Alex, thank you for your message. We will check and get back to you.\nLarkspur Cycle Works"


def extraction(**overrides) -> Extraction:
    base = dict(
        sender_type="CUSTOMER",
        category="RENTAL",
        urgency="MEDIUM",
        department="FRONT_DESK",
        reason="Asks to add a helmet to an existing booking.",
        intake=FULL_INTAKE,
        draft_reply=GOOD_DRAFT,
    )
    base.update(overrides)
    return Extraction(**base)


def run(ex=None, signals=None, thread=None, inherited=None):
    return decide(ex or extraction(), signals or Signals(), thread or Thread(), inherited or [])


# --------------------------------------------------------------------------- draft validators
def test_clean_draft_passes():
    assert check_draft(GOOD_DRAFT, "RENTAL") == []


def test_missing_draft_is_a_violation():
    assert check_draft(None, "RENTAL") == ["draft:missing"]
    assert check_draft("   ", "RENTAL") == ["draft:missing"]


def test_long_draft_is_a_violation():
    assert "draft:too_long" in check_draft("word " * 91, "RENTAL")


@pytest.mark.parametrize("text", ["It costs 25 EUR per day.", "We can offer 10% off.", "Only $40."])
def test_price_in_draft_is_a_violation(text):
    assert "draft:price" in check_draft(text, "QUOTE")


@pytest.mark.parametrize("text", ["We will call you tomorrow.", "Come by on Friday.", "See you at 9:30.",
                                  "A mechanic will be there within 20 minutes."])
def test_time_promise_in_draft_is_a_violation(text):
    assert "draft:time_promise" in check_draft(text, "REPAIR")


def test_refund_promise_is_a_violation_only_for_complaints():
    text = "We will refund the full amount."
    assert "draft:refund_promise" in check_draft(text, "COMPLAINT")
    assert "draft:refund_promise" not in check_draft(text, "RENTAL")


# --------------------------------------------------------------------------- happy path
def test_complete_request_gets_a_ready_draft():
    d = run()
    assert (d.category, d.urgency, d.department, d.draft_status) == ("RENTAL", "MEDIUM", "FRONT_DESK", "READY")
    assert d.draft == GOOD_DRAFT


# --------------------------------------------------------------------------- floors and overrides
def test_safety_risk_forces_high_urgency():
    d = run(extraction(category="REPAIR", department="WORKSHOP", urgency="LOW", safety_risk=True))
    assert d.urgency == "HIGH"
    assert any("safety" in c for c in d.corrections)


def test_escalation_threat_makes_it_a_management_complaint():
    d = run(extraction(escalation_threat=True))
    assert (d.category, d.urgency, d.department) == ("COMPLAINT", "HIGH", "MANAGEMENT")


def test_follow_up_raises_urgency_one_level():
    d = run(thread=Thread(type="FOLLOW_UP", linked_to=["X"], contacts=2))
    assert d.urgency == "HIGH"


def test_related_email_keeps_urgency():
    assert run(thread=Thread(type="RELATED", linked_to=["X"], contacts=2)).urgency == "MEDIUM"


def test_third_contact_in_a_follow_up_goes_to_management():
    d = run(thread=Thread(type="FOLLOW_UP", linked_to=["X", "Y"], contacts=3))
    assert d.department == "MANAGEMENT"


def test_ignore_wins_over_everything():
    d = run(
        extraction(category="IGNORE", urgency="HIGH", department="FRONT_DESK", draft_reply=None),
        thread=Thread(type="FOLLOW_UP", linked_to=["X", "Y"], contacts=3),
    )
    assert (d.category, d.urgency, d.department, d.draft_status) == ("IGNORE", "LOW", "NONE", "NOT_APPLICABLE")


def test_department_none_outside_ignore_is_taken_from_the_category():
    d = run(extraction(category="QUOTE", department="NONE"))
    assert d.department == "SALES"


def test_other_department_mismatch_is_only_a_warning():
    d = run(extraction(category="QUOTE", department="FRONT_DESK"))
    assert d.department == "FRONT_DESK"
    assert any("department" in w for w in d.warnings)


# --------------------------------------------------------------------------- draft gate
def test_system_sender_gets_no_draft():
    d = run(extraction(sender_type="SYSTEM"))
    assert d.draft_status == "NOT_APPLICABLE" and d.draft is None


def test_supplier_is_never_answered_and_goes_to_accounts():
    d = run(extraction(sender_type="SUPPLIER", category="BILLING", department="MANAGEMENT"))
    assert d.draft_status == "BLOCKED_BY_RULE"
    assert d.department == "ACCOUNTS"
    assert d.draft is None


@pytest.mark.parametrize("field", ["customer_name", "bike_or_booking", "issue"])
def test_repair_or_rental_without_intake_is_held(field):
    intake = FULL_INTAKE.model_copy(update={field: None})
    d = run(extraction(category="REPAIR", department="WORKSHOP", intake=intake))
    assert d.draft_status == "MISSING_INFO"
    assert d.draft is None
    assert d.human_action and "ask" in d.human_action.lower()


def test_quote_does_not_need_intake():
    d = run(extraction(category="QUOTE", department="SALES", intake=Intake()))
    assert d.draft_status == "READY"


def test_missing_intake_is_filled_from_the_thread():
    partial = FULL_INTAKE.model_copy(update={"bike_or_booking": None})
    d = run(extraction(intake=partial), inherited=[Intake(bike_or_booking="LCW-9999")])
    assert d.draft_status == "READY"
    assert d.intake.bike_or_booking == "LCW-9999"


def test_blank_strings_from_the_model_count_as_missing():
    partial = FULL_INTAKE.model_copy(update={"issue": "  "})
    assert run(extraction(intake=partial)).draft_status == "MISSING_INFO"


def test_draft_that_breaks_a_house_rule_is_blocked():
    d = run(extraction(draft_reply="Hello, the e-bike costs 30 EUR per day. Larkspur Cycle Works"))
    assert d.draft_status == "BLOCKED_BY_RULE"
    assert "draft:price" in d.violations
    assert d.draft is None


# --------------------------------------------------------------------------- calendar and reason
def test_date_mismatch_adds_a_human_action_and_a_warning():
    d = run(signals=Signals(date_mismatches=["'Wednesday 14 May': 14 May 2026 is a Thursday"]))
    assert "confirm the date" in d.human_action.lower()
    assert any("date" in w for w in d.warnings)
    assert d.draft_status == "READY"


def test_long_reason_is_truncated():
    d = run(extraction(reason="x" * 300))
    assert len(d.reason) <= 160
