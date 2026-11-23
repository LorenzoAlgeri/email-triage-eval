import json
import re
from pathlib import Path

from triage.__main__ import main

ROOT = Path(__file__).resolve().parent.parent


def test_demo_runs_end_to_end_offline_and_writes_reports(tmp_path, capsys, monkeypatch):
    cache = tmp_path / "cache"
    monkeypatch.setattr("triage.config.CACHE_DIR", cache)
    out = tmp_path / "demo"

    assert main(["demo", "--out", str(out)]) == 0

    for name in ("results.json", "results.md", "costs.md", "eval-dev.md", "eval-holdout.md"):
        assert (out / name).exists(), name
    results = json.loads((out / "results.json").read_text(encoding="utf-8"))
    assert len(results) == 23
    assert "SIMULATED" in (out / "eval-holdout.md").read_text(encoding="utf-8")
    assert "SIMULATED" in (out / "costs.md").read_text(encoding="utf-8")
    assert not cache.exists()  # stub replies never reach the cache that a live run reads
    printed = capsys.readouterr().out
    assert "holdout" in printed


def test_reports_contain_no_absolute_paths(tmp_path):
    out = tmp_path / "demo"
    main(["demo", "--out", str(out)])
    for f in out.iterdir():
        text = f.read_text(encoding="utf-8")
        assert str(tmp_path) not in text and str(ROOT) not in text, f.name


def test_live_path_prices_calls_writes_the_cache_and_offline_replay_calls_nothing(tmp_path, monkeypatch):
    from triage.fake import KeywordStub

    sdk = KeywordStub(model_id="claude-opus-5-5")  # stands in for the real client; no network
    monkeypatch.setattr("triage.llm.make_default_client", lambda: sdk)
    monkeypatch.setattr("triage.config.CACHE_DIR", tmp_path / "cache")

    assert main(["run", "--out", str(tmp_path / "live")]) == 0
    calls = sdk.calls
    assert calls == 22  # 23 emails, one bounce handled in code
    assert len(list((tmp_path / "cache").glob("*.json"))) == calls
    costs = (tmp_path / "live" / "costs.md").read_text(encoding="utf-8")
    assert "SIMULATED" not in costs
    assert re.search(r"\| 0\.\d{4} \|", costs)  # priced with the claude-opus-5-5 list price

    assert main(["run", "--out", str(tmp_path / "replay"), "--offline"]) == 0
    assert sdk.calls == calls
    replay = json.loads((tmp_path / "replay" / "results.json").read_text(encoding="utf-8"))
    assert all(r["meta"]["from_disk_cache"] for r in replay if not r["signals"]["is_bounce"])


def test_eval_command_reads_results_and_gold(tmp_path):
    out = tmp_path / "demo"
    main(["demo", "--out", str(out)])
    report = tmp_path / "again.md"
    assert main(["eval", "--results", str(out / "results.json"),
                 "--gold", str(ROOT / "eval" / "gold_dev.json"), "--out", str(report)]) == 0
    assert "dev" in report.read_text(encoding="utf-8")
