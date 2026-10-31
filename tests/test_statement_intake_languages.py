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
        "I read your Mellat statement: 12 rows (1 Sep 2026 to 30 Sep 2026) and checked them against the books. "
        "8 already recorded; 3 not in the books; 1 book entry the bank never shows. "
        "The closing balance differs from the books by 5,000, which posting the new rows would close. "
        "Click \"Fix step by step\" (or say \"next\") and I'll take the differences one at a time; "
        "nothing is posted without your confirmation.")
    es = si._reply("es", INTAKE)
    assert es.startswith("Leí tu extracto de Mellat: 12 filas (del 1 sept 2026 al 30 sept 2026)") and "3 no están en los libros" in es
    ar = si._reply("ar", INTAKE)
    assert ar.startswith("قرأت كشف حساب ملت: 12 سطراً (من 1 سبتمبر 2026 إلى 30 سبتمبر 2026)") and "؛ " in ar and "«إصلاح خطوة بخطوة»" in ar
    assert si._reply("fa", {**INTAKE, "clean": True}).endswith("همه‌چیز با دفاتر مطابقت دارد؛ کاری لازم نیست.")
    assert si._reply("de", INTAKE) == si._reply("en", INTAKE)               # an unknown language: English
    assert si._duplicate_reply("es", []) == ("Este archivo ya se importó. Puedes seguir con él desde su tarjeta "
                                             "o pedirme que lo compare con los libros.")


def test_persian_writes_its_digits_and_arabic_western_ones():
    big = {**INTAKE, "total_rows": 3500, "counts": {"unrecorded": 3500}, "balance": {"gap": 1_839_800_000}}
    fa = si._reply("fa", big)
    assert fa.startswith("صورت‌حساب ملت را خواندم: ۳,۵۰۰ ردیف (۱ سپتامبر ۲۰۲۶ تا ۳۰ سپتامبر ۲۰۲۶)"), fa
    assert "۳,۵۰۰ ردیف در دفاتر نیست" in fa and "۱,۸۳۹,۸۰۰,۰۰۰" in fa
    assert not any(ch in "0123456789" for ch in fa)                          # no Western digit inside the Persian
    assert si._reply("fa", big, jalali=True).startswith("صورت‌حساب ملت را خواندم: ۳,۵۰۰ ردیف (۱۰ شهریور ۱۴۰۵ تا ۸ مهر ۱۴۰۵)")
    ar = si._reply("ar", big)
    assert "3,500" in ar and not any(ch in "٠١٢٣٤٥٦٧٨٩" for ch in ar)      # Arabic: Western digits, the owner's call


def test_a_file_imported_before_is_said_in_the_readers_words():
    # the Spanish and Arabic pass (2026-10-11): «Este archivo ya se importó. This file was already imported on 2026-10-11 as…»
    note = ["This file was already imported on 2026-10-11 as 'mellat.csv'."]
    assert si._duplicate_reply("es", note) == ("Este archivo ya se importó el 11 oct 2026 como «mellat.csv». "
                                               "Puedes seguir con él desde su tarjeta o pedirme que lo compare con los libros.")
    assert si._duplicate_reply("fa", note, jalali=True).startswith("این فایل در ۱۹ مهر ۱۴۰۵ با نام «mellat.csv» وارد شده است.")
    ar = si._duplicate_reply("ar", note)
    assert "11 أكتوبر 2026" in ar and "already" not in ar
    assert si._duplicate_reply("en", note).startswith("This file was already imported on 11 Oct 2026 as “mellat.csv”.")


def test_an_intake_card_does_not_carry_the_words_a_second_time(db):
    from app.services.ai_accountant.blocks import build_blocks
    blocks = build_blocks(db, text="I read your statement.", proposals=None, tool_calls=None,
                          intake={"kind": "bank_statement", "status": "duplicate"}, lang="en", calendar="gregorian")
    assert [(b["type"], b["fallback_text"]) for b in blocks] == [("intake", ""), ("text", "I read your statement.")]
    # sent again, the card comes again: an id of its own, or the phone keeps only the first
    again = build_blocks(db, text="x", proposals=None, tool_calls=None, lang="en", calendar="gregorian",
                         intake={"kind": "bank_statement", "status": "duplicate", "statement_id": "s1"})
    twice = build_blocks(db, text="x", proposals=None, tool_calls=None, lang="en", calendar="gregorian",
                         intake={"kind": "bank_statement", "status": "duplicate", "statement_id": "s1"})
    assert again[0]["id"].startswith("intake:bank_statement:s1:") and again[0]["id"] != twice[0]["id"]


def test_the_language_follows_the_message_then_the_interface():
    assert si._message_language("add these", "es") == "es"
    assert si._message_language("اینا گردش حساب ملته", "en") == "fa"
    assert si._message_language("هذه كشوف البنك", "ar") == "ar"
