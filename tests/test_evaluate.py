from triage.evaluate import evaluate, render_report


def result(id, category="RENTAL", urgency="LOW", department="FRONT_DESK", draft_status="READY",
           draft="Hello. Larkspur Cycle Works", linked=(), ttype="NEW", mismatches=()):
    return {
        "id": id, "category": category, "urgency": urgency, "department": department,
        "draft_status": draft_status, "draft": draft if draft_status == "READY" else None,
        "thread": {"linked_to": list(linked), "type": ttype},
        "signals": {"date_mismatches": list(mismatches)},
        "meta": {"error": None},
    }


def gold(id, accept=None, linked=(), ttype="NEW", flags=None, **expected):
    base = {"category": "RENTAL", "urgency": "LOW", "department": "FRONT_DESK", "draft_status": "READY"}
    base.update(expected)
    g = {"id": id, "expected": base, "thread": {"linked_to": list(linked), "type": ttype}}
    if accept:
        g["acceptable"] = accept
    if flags:
        g["flags"] = flags
    return g


def test_exact_match_counts_as_exact_and_acceptable():
    s = evaluate([result("A")], [gold("A")])
    assert s["rows_exact"] == 1 and s["rows_acceptable"] == 1 and s["total"] == 1


def test_alternative_is_accepted_only_when_all_its_fields_match():
    g = gold("A", accept=[{"category": "QUOTE", "department": "SALES"}])
    ok = evaluate([result("A", category="QUOTE", department="SALES")], [g])
    half = evaluate([result("A", category="QUOTE", department="FRONT_DESK")], [g])
    assert ok["rows_exact"] == 0 and ok["rows_acceptable"] == 1
    assert half["rows_acceptable"] == 0


def test_missing_result_is_a_failure():
    s = evaluate([], [gold("A")])
    assert s["rows_acceptable"] == 0
    assert s["rows"][0]["error"] == "no result"


def test_thread_link_and_type_are_checked():
    s = evaluate([result("B", linked=["A"], ttype="RELATED")], [gold("B", linked=["A"], ttype="FOLLOW_UP")])
    row = s["rows"][0]
    assert row["thread_link_ok"] is True and row["thread_type_ok"] is False
    assert s["threads_ok"] == 0


def test_flags_are_checked():
    s = evaluate([result("A")], [gold("A", flags=["date_mismatch"])])
    assert s["flags_ok"] == 0 and s["flags_total"] == 1
    s = evaluate([result("A", mismatches=["x"])], [gold("A", flags=["date_mismatch"])])
    assert s["flags_ok"] == 1


def test_house_rule_breaches_in_ready_drafts_are_counted():
    s = evaluate([result("A", draft="It is 20 EUR, see you tomorrow.")], [gold("A")])
    assert s["drafts_with_price"] == 1 and s["drafts_with_time_promise"] == 1


def test_report_header_says_when_the_run_is_simulated():
    s = evaluate([result("A")], [gold("A")])
    text = render_report(s, title="dev split", run_label="offline stub", simulated=True)
    assert "SIMULATED" in text
    assert "1/1" in text
    text.encode("ascii")  # plain ASCII so it prints on any console
