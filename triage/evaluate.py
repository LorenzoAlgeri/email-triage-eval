"""Compare results with a gold file and write a markdown report.

The gold file is only read here, from the path the CLI passes. Nothing in the pipeline
knows it exists. An entry may list `acceptable` alternatives: each is a partial set of
fields that replaces the expected values, and it counts only if ALL its fields match.
"""

from __future__ import annotations

import json
from pathlib import Path

from .rules import _REFUND_PROMISE, _TIME_PROMISE
from .signals import MONEY

FIELDS = ("category", "urgency", "department", "draft_status")


def load_json_list(path: Path) -> list[dict]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError(f"{Path(path).name}: expected a JSON list")
    return data


def _candidates(gold: dict) -> list[dict]:
    expected = gold["expected"]
    return [expected] + [{**expected, **alt} for alt in gold.get("acceptable") or []]


def _flag_ok(flag: str, result: dict) -> bool:
    if flag == "bounce":
        return (result.get("thread") or {}).get("type") == "BOUNCE"
    if flag == "date_mismatch":
        return bool((result.get("signals") or {}).get("date_mismatches"))
    return False


def _row(result: dict | None, gold: dict) -> dict:
    expected_thread = gold.get("thread") or {}
    if result is None:
        return {"id": gold["id"], "error": "no result", "exact": False, "acceptable": False,
                "fields": {f: (gold["expected"][f], None) for f in FIELDS},
                "thread_link_ok": False, "thread_type_ok": False,
                "flags": {f: False for f in gold.get("flags") or []}, "note": gold.get("note")}
    got_thread = result.get("thread") or {}
    return {
        "id": gold["id"],
        "error": (result.get("meta") or {}).get("error"),
        "exact": all(result.get(f) == gold["expected"][f] for f in FIELDS),
        "acceptable": any(all(result.get(f) == c[f] for f in FIELDS) for c in _candidates(gold)),
        "fields": {f: (gold["expected"][f], result.get(f)) for f in FIELDS},
        "thread_link_ok": sorted(got_thread.get("linked_to") or []) == sorted(expected_thread.get("linked_to") or []),
        "thread_type_ok": got_thread.get("type") == expected_thread.get("type", "NEW"),
        "flags": {f: _flag_ok(f, result) for f in gold.get("flags") or []},
        "draft": result.get("draft"),
        "note": gold.get("note"),
    }


def evaluate(results: list[dict], gold: list[dict]) -> dict:
    by_id = {r["id"]: r for r in results}
    rows = [_row(by_id.get(g["id"]), g) for g in gold]
    drafts = [r["draft"] for r in rows if r.get("draft")]
    return {
        "total": len(rows),
        "rows_exact": sum(r["exact"] for r in rows),
        "rows_acceptable": sum(r["acceptable"] for r in rows),
        "field_exact": {f: sum(r["fields"][f][0] == r["fields"][f][1] for r in rows) for f in FIELDS},
        "threads_ok": sum(r["thread_link_ok"] and r["thread_type_ok"] for r in rows),
        "flags_total": sum(len(r["flags"]) for r in rows),
        "flags_ok": sum(sum(r["flags"].values()) for r in rows),
        "errors": sum(1 for r in rows if r["error"]),
        "drafts_with_price": sum(bool(MONEY.search(d)) for d in drafts),
        "drafts_with_time_promise": sum(bool(_TIME_PROMISE.search(d)) for d in drafts),
        "drafts_with_refund_word": sum(bool(_REFUND_PROMISE.search(d)) for d in drafts),
        "rows": rows,
    }


def render_report(summary: dict, *, title: str, run_label: str, simulated: bool) -> str:
    n = summary["total"]
    lines = [f"# Eval: {title}", ""]
    if simulated:
        lines += ["> SIMULATED RUN. These results come from the offline keyword stub, not from Claude.",
                  "> They show that the pipeline and the eval work end to end. They say nothing about model quality.", ""]
    lines += [
        f"Run: {run_label}", "",
        "| Measure | Value |", "|---|---|",
        f"| Rows exactly as expected (4 fields) | {summary['rows_exact']}/{n} |",
        f"| Rows acceptable (expected or a listed alternative) | {summary['rows_acceptable']}/{n} |",
    ]
    for field, ok in summary["field_exact"].items():
        lines.append(f"| {field} exact | {ok}/{n} |")
    lines += [
        f"| Thread link and type correct | {summary['threads_ok']}/{n} |",
        f"| Expected flags raised | {summary['flags_ok']}/{summary['flags_total']} |",
        f"| Errors | {summary['errors']} |",
        f"| Ready drafts with a price | {summary['drafts_with_price']} |",
        f"| Ready drafts with a time promise | {summary['drafts_with_time_promise']} |",
        "", "## Rows that are not exact", "",
        "| id | field | expected | got | acceptable | note |", "|---|---|---|---|---|---|",
    ]
    for row in summary["rows"]:
        if row["exact"] and row["thread_link_ok"] and row["thread_type_ok"] and all(row["flags"].values()):
            continue
        wrong = [f for f, (e, g) in row["fields"].items() if e != g]
        if not row["thread_link_ok"] or not row["thread_type_ok"]:
            wrong.append("thread")
        wrong += [f"flag {f}" for f, ok in row["flags"].items() if not ok]
        for field in wrong:
            expected, got = row["fields"].get(field, ("-", "-"))
            lines.append(f"| {row['id']} | {field} | {expected} | {got} | {'yes' if row['acceptable'] else 'no'} | "
                         f"{(row.get('error') or row.get('note') or '').replace('|', '/')} |")
    lines.append("")
    return "\n".join(lines).encode("ascii", "replace").decode("ascii")
