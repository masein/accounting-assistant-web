"""The first-run questions the phone offers are answered at once, from the books
(scenario N10, N24): read from the app's own strings, in every language, for a
company's books and for a person's — the 2026-10-11 pass found personal books
offered "who owes us?", answered with sales invoices."""
from __future__ import annotations

import re
from pathlib import Path

from app.services.ai_accountant.fast_paths import match

RES = Path(__file__).resolve().parents[1] / "android" / "app" / "src" / "main" / "res"
LANGS = {"values": "en", "values-fa": "fa", "values-es": "es", "values-ar": "ar"}


def _questions(folder: str, name: str) -> list[str]:
    s = (RES / folder / "strings.xml").read_text(encoding="utf-8")
    block = re.search(rf'<string-array name="{name}">(.*?)</string-array>', s, re.S).group(1)
    return [q.replace("\\'", "'") for q in re.findall(r"<item>(.*?)</item>", block)]


def test_a_companys_questions_are_answered_at_once_in_every_language():
    for folder, lang in LANGS.items():
        assert [match(q) for q in _questions(folder, "suggestions")] == ["cash", "receivables", "payables", "spending"], lang


def test_a_persons_questions_are_a_persons_and_answered_at_once():
    for folder, lang in LANGS.items():
        assert [match(q) for q in _questions(folder, "suggestions_personal")] == ["cash", "spending", "budget", "net_worth"], lang
