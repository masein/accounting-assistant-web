"""The chat's answer about an attached bank statement reads in the user's
language — Spanish and Arabic users got the English one (only fa/en were
written)."""
from __future__ import annotations

import string

from app.services.ai_accountant import statement_intake as si

INTAKE = {"bank_name": "Mellat", "total_rows": 12, "from_date": "2026-09-01", "to_date": "2026-09-30",
          "counts": {"matched": 8, "unrecorded": 3, "missing_in_bank": 1}, "balance": {"gap": -5000, "explained": True},
          "clean": False}


def _fields(text: str) -> set[str]:
    return {f for _, f, _, _ in string.Formatter().parse(text) if f}


def test_every_reply_reads_in_all_four_languages_with_the_same_values():
    for key, said in si._T.items():
        assert set(said) == {"en", "fa", "es", "ar"}, key
        assert len({frozenset(_fields(s)) for s in said.values()}) == 1, key


def test_the_summary_in_each_language():
    assert si._reply("en", INTAKE) == (
        "I read your Mellat statement: 12 rows (2026-09-01 to 2026-09-30) and checked them against the books. "
        "8 already recorded; 3 not in the books; 1 book entry the bank never shows. "
        "The closing balance differs from the books by 5,000, which posting the new rows would close. "
        "Click \"Fix step by step\" (or say \"next\") and I'll take the differences one at a time; "
        "nothing is posted without your confirmation.")
    es = si._reply("es", INTAKE)
    assert es.startswith("Leí tu extracto de Mellat: 12 filas (del 2026-09-01 al 2026-09-30)") and "3 no están en los libros" in es
    ar = si._reply("ar", INTAKE)
    assert ar.startswith("قرأت كشف حساب Mellat") and "؛ " in ar
    assert si._reply("fa", {**INTAKE, "clean": True}).endswith("همه‌چیز با دفاتر می‌خواند؛ کاری لازم نیست.")
    assert si._reply("de", INTAKE) == si._reply("en", INTAKE)               # an unknown language: English
    assert si._duplicate_reply("es", ["Same file."]).startswith("Este archivo ya se importó. Same file.")


def test_the_language_follows_the_message_then_the_interface():
    assert si._message_language("add these", "es") == "es"
    assert si._message_language("اینا گردش حساب ملته", "en") == "fa"
    assert si._message_language("هذه كشوف البنك", "ar") == "ar"
