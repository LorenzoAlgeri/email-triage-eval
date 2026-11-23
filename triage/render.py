"""Human-readable outputs: one table of results, one table of tokens and cost per call."""

from __future__ import annotations

from .models import Result


def _cell(value) -> str:
    return "" if value is None else str(value).replace("|", "/").replace("\n", " ")


def results_markdown(results: list[Result]) -> str:
    lines = ["# Triage results", "",
             "| id | category | urgency | department | draft | reason | human action |",
             "|---|---|---|---|---|---|---|"]
    for r in results:
        lines.append(f"| {r.id} | {_cell(r.category)} | {_cell(r.urgency)} | {_cell(r.department)} | "
                     f"{_cell(r.draft_status)} | {_cell(r.reason)} | {_cell(r.human_action)} |")
    lines += ["", "## Drafts ready for a human to check and send", ""]
    for r in results:
        if r.draft:
            lines += [f"### {r.id}: {_cell(r.email.subject)}", "", r.draft, ""]
    return "\n".join(lines)


def costs_markdown(results: list[Result], *, simulated: bool) -> str:
    lines = ["# Tokens and cost per email", ""]
    if simulated:
        lines += ["> SIMULATED RUN. Token counts are estimates made by the offline stub (characters / 4).",
                  "> No API call was made, so no cost is shown.", ""]
    lines += ["| id | model | input | output | cache write | cache read | API calls | from disk | cost USD |",
              "|---|---|---|---|---|---|---|---|---|"]
    totals = dict(input=0, output=0, write=0, read=0, calls=0, cost=0.0)
    unknown_cost = False
    for r in results:
        m = r.meta
        cost = "n/a" if m.cost_usd is None else f"{m.cost_usd:.4f}"
        if m.cost_usd is None and m.api_calls and not simulated:
            unknown_cost = True
        lines.append(f"| {r.id} | {m.model} | {m.input_tokens} | {m.output_tokens} | {m.cache_creation_input_tokens} | "
                     f"{m.cache_read_input_tokens} | {m.api_calls} | {'yes' if m.from_disk_cache else 'no'} | {cost} |")
        totals["input"] += m.input_tokens
        totals["output"] += m.output_tokens
        totals["write"] += m.cache_creation_input_tokens
        totals["read"] += m.cache_read_input_tokens
        totals["calls"] += m.api_calls
        totals["cost"] += m.cost_usd or 0.0
    total_cost = "n/a" if simulated else f"{totals['cost']:.4f}"
    lines.append(f"| total | | {totals['input']} | {totals['output']} | {totals['write']} | {totals['read']} | "
                 f"{totals['calls']} | | {total_cost} |")
    lines.append("")
    lines.append("Rows served from the disk cache repeat the tokens of the original call; they cost nothing on a re-run.")
    unmetered = sum(r.meta.unmetered_calls for r in results)
    if unmetered:
        lines.append(f"{unmetered} call(s) returned a reply the SDK could not parse; their tokens are not counted above.")
    if unknown_cost:
        lines.append("Some rows use a model missing from the price table: their cost is not included.")
    return "\n".join(lines) + "\n"
