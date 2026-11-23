"""Guard: the code and the prompts must not know the dataset. Rules stay general, never per email."""

import re
from pathlib import Path

from triage.signals import load_inbox

ROOT = Path(__file__).resolve().parent.parent
SOURCES = [p for p in (ROOT / "triage").rglob("*") if p.suffix in {".py", ".md"}]


def _forbidden() -> set[str]:
    words: set[str] = set()
    for email in load_inbox(ROOT / "data" / "inbox.json"):
        words.add(email.id)
        words.add(email.sender_name)
        words.add(email.sender_domain)
        words.update(re.findall(r"\b(?:LCW|W)-\d{4}\b", email.body))
    return words


def test_package_contains_no_ids_names_domains_or_references_from_the_dataset():
    forbidden = _forbidden()
    hits = []
    for path in SOURCES:
        text = path.read_text(encoding="utf-8")
        hits += [f"{path.name}: {w}" for w in forbidden if re.search(rf"\b{re.escape(w)}\b", text)]
    assert hits == []


def test_the_guard_would_catch_a_leak(tmp_path):
    forbidden = _forbidden()
    sample = next(iter(sorted(forbidden)))
    assert any(re.search(rf"\b{re.escape(w)}\b", f"x = '{sample}'") for w in forbidden)
