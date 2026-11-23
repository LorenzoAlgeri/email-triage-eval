"""Command line.

  python -m triage demo                     offline: keyword stub, full pipeline, both eval splits
  python -m triage run --input data/inbox.json --out output/live      needs ANTHROPIC_API_KEY
  python -m triage run ... --offline        replay the disk cache only, never call the API
  python -m triage eval --results output/live/results.json --gold eval/gold_holdout.json --out output/live/eval-holdout.md
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import config
from .evaluate import evaluate, load_json_list, render_report
from .fake import STUB_MODEL, KeywordStub
from .llm import TriageClient, build_system_prompt
from .pipeline import run_triage
from .render import costs_markdown, results_markdown
from .signals import load_inbox

DEFAULT_INPUT = config.ROOT / "data" / "inbox.json"
GOLD = {"dev": config.ROOT / "eval" / "gold_dev.json", "holdout": config.ROOT / "eval" / "gold_holdout.json"}


def _show(path: Path) -> str:
    """Path for console output, relative when possible so logs never carry a home directory."""
    try:
        return str(Path(path).resolve().relative_to(Path.cwd().resolve()))
    except ValueError:
        return Path(path).name


def _run(input_path: Path, out: Path, *, stub: bool, offline: bool, no_cache: bool) -> list[dict]:
    emails = load_inbox(input_path)
    if stub:
        # The stub never reads or writes the disk cache: a later live run must not find stub replies there.
        client = TriageClient(STUB_MODEL, build_system_prompt(), client=KeywordStub(), cache_dir=None)
    else:
        from dotenv import load_dotenv

        load_dotenv(config.ROOT / ".env")
        client = TriageClient(config.model(), build_system_prompt(), effort=config.effort(),
                              cache_dir=None if no_cache else config.CACHE_DIR, offline=offline)
    results = run_triage(emails, client, simulated=stub)

    out.mkdir(parents=True, exist_ok=True)
    data = [r.model_dump(mode="json") for r in results]
    (out / "results.json").write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    (out / "results.md").write_text(results_markdown(results), encoding="utf-8")
    (out / "costs.md").write_text(costs_markdown(results, simulated=stub), encoding="utf-8")
    errors = sum(1 for r in results if r.meta.error)
    print(f"triaged {len(results)} emails ({errors} errors) with {client.model} -> {_show(out)}")
    return data


def _eval(results: list[dict], gold_path: Path, out_path: Path, title: str) -> dict:
    simulated = any((r.get("meta") or {}).get("simulated") for r in results)
    models = sorted({(r.get("meta") or {}).get("model") for r in results} - {None, "none (handled in code)"})
    summary = evaluate(results, load_json_list(gold_path))
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(render_report(summary, title=title, run_label=", ".join(models) or "unknown",
                                      simulated=simulated), encoding="utf-8")
    tag = " [SIMULATED: offline stub, not Claude]" if simulated else ""
    print(f"eval {title}: {summary['rows_acceptable']}/{summary['total']} acceptable, "
          f"{summary['rows_exact']}/{summary['total']} exact{tag} -> {_show(out_path)}")
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m triage", description="Triage a shared inbox with Claude.")
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="triage an inbox file")
    run.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    run.add_argument("--out", type=Path, required=True)
    run.add_argument("--stub", action="store_true", help="use the offline keyword stub instead of Claude")
    run.add_argument("--offline", action="store_true", help="answer only from the disk cache")
    run.add_argument("--no-cache", action="store_true", help="ignore the disk cache")

    ev = sub.add_parser("eval", help="compare results with a gold file")
    ev.add_argument("--results", type=Path, required=True)
    ev.add_argument("--gold", type=Path, required=True)
    ev.add_argument("--out", type=Path, required=True)

    demo = sub.add_parser("demo", help="offline end-to-end run with the keyword stub, then both evals")
    demo.add_argument("--out", type=Path, default=config.ROOT / "output" / "demo")

    args = parser.parse_args(argv)
    if args.command == "run":
        _run(args.input, args.out, stub=args.stub, offline=args.offline, no_cache=args.no_cache)
    elif args.command == "eval":
        _eval(load_json_list(args.results), args.gold, args.out, title=args.gold.stem.replace("gold_", ""))
    else:
        results = _run(DEFAULT_INPUT, args.out, stub=True, offline=False, no_cache=True)
        for split, gold in GOLD.items():
            _eval(results, gold, args.out / f"eval-{split}.md", title=split)
    return 0


if __name__ == "__main__":
    sys.exit(main())
