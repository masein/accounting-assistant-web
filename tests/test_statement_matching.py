"""A bank statement reconciles row for row (chat review 2026-11-10, scenarios BN1–BN7).

A school's Q2 statement (Day bank, 205 rows): each confirmed card's entry became a "match" for every row of
its amount within 14 days — one 300,000,000 entry for 16 deposits — and step by step, which only walks rows
with nothing in the books, ended with «همهٔ ردیف‌ها در دفاتر است» while 47 deposits (12.72bn) were missing.
18 per-cheque fees repeated on one day were set aside as duplicates; deposits went to the app's «فروش» and
not the school's own «درآمد شهریه»; and the model that categorizes rows refused every request.
"""
from __future__ import annotations

import asyncio
import uuid
from datetime import date, timedelta

from sqlalchemy import select

from app.models.bank_statement import BankStatementRow
from app.services.statement_review import build_statement_review
from tests.test_own_codes_engine import _MADE, _in, _purge_made  # noqa: F401 — fixture
from tests.test_own_codes_engine import _company, _post
from tests.test_statement_review import _stmt

M = 1_000_000


def _rows(db, s):
    return db.execute(select(BankStatementRow).where(BankStatementRow.statement_id == s.id)
                      .order_by(BankStatementRow.row_index)).scalars().all()


def _kinds(review):
    return {f.row_index: f.kind for f in review.findings if f.row_index}


def test_bn1_an_entry_is_one_rows(db, make_transaction):
    """Five 300,000,000 deposits, newest first as the bank lists them; the oldest one's card is confirmed: its
    entry is that row's alone (the newer rows came first and each took it)."""
    d = date(2032, 7, 6)
    s = _stmt(db, [(d + timedelta(days=3 * i), f"واریز شهریه {i}", 0, 300 * M, None, "unmatched") for i in range(4, -1, -1)])
    rows = _rows(db, s)
    posted = make_transaction([("1110", 300 * M, 0), ("4110", 0, 300 * M)], tx_date=d, description="واریز شهریه 0")
    rows[-1].created_transaction_id, rows[-1].recon_status, rows[-1].user_approved = posted.id, "matched", True
    db.commit()
    review = build_statement_review(db, s)
    assert _kinds(review) == {1: "unrecorded", 2: "unrecorded", 3: "unrecorded", 4: "unrecorded"}
    assert all(r.matched_transaction_id is None for r in _rows(db, s)[:-1])


def test_bn1_the_best_pair_first_whichever_row_comes_first(db, make_transaction):
    """One entry dated the 10th; rows on the 5th (amount only) and the 10th (same day): the 10th takes it."""
    d = date(2032, 8, 10)
    entry = make_transaction([("6112", 620_000, 0), ("1110", 0, 620_000)], tx_date=d, description="hosting")
    db.commit()
    s = _stmt(db, [(d - timedelta(days=5), "برداشت", 620_000, 0, None, "unmatched"),
                   (d, "hosting", 620_000, 0, None, "unmatched")])
    review = build_statement_review(db, s)
    first, second = _rows(db, s)
    assert second.recon_status == "matched" and second.matched_transaction_id == entry.id
    assert _kinds(review) == {1: "unrecorded"} and first.matched_transaction_id is None


def test_bn2_rows_linked_to_another_rows_entry_are_reopened(db, make_transaction):
    """A statement reconciled before BN1, newest first: rows 1–2 still point at the oldest row's own entry."""
    d = date(2032, 9, 6)
    s = _stmt(db, [(d + timedelta(days=2 * i), f"کارت به کارت {i}", 0, 100 * M, None, "unmatched") for i in range(2, -1, -1)])
    rows = _rows(db, s)
    own = make_transaction([("1110", 100 * M, 0), ("4110", 0, 100 * M)], tx_date=d, description="کارت به کارت 0")
    rows[-1].created_transaction_id, rows[-1].recon_status, rows[-1].user_approved = own.id, "matched", True
    for r in rows[:-1]:
        r.recon_status, r.matched_transaction_id = "partial", own.id
    db.commit()
    review = build_statement_review(db, s)
    assert _kinds(review) == {1: "unrecorded", 2: "unrecorded"}
    assert [r.recon_status for r in _rows(db, s)] == ["unmatched", "unmatched", "matched"]


def test_bn3_repeated_fees_are_separate_charges(db):
    """Three 18,000 cheque fees on one day, the running balance moving on each: three rows to record."""
    d = date(2032, 10, 3)
    fee = "كارمزد واگذاري چك عادي"
    s = _stmt(db, [(d, fee, 18_000, 0, 1_000_000 - 18_000 * (i + 1), "unmatched") for i in range(3)]
              + [(d, "خرید", 50_000, 0, 500_000, "unmatched"), (d, "خرید", 50_000, 0, 500_000, "unmatched")])
    review = build_statement_review(db, s)
    assert _kinds(review) == {1: "unrecorded", 2: "unrecorded", 3: "unrecorded", 4: "unrecorded", 5: "duplicate"}


def test_bn3_fees_set_aside_before_the_fix_are_reopened(db):
    d = date(2032, 10, 9)
    fee = "كارمزد واگذاري چك عادي"
    s = _stmt(db, [(d, fee, 18_000, 0, 982_000, "unmatched"), (d, fee, 18_000, 0, 964_000, "duplicate"),
                   (d, "از صورت‌حساب پیشین", 9_000, 0, 955_000, "duplicate")])     # an earlier statement's: kept
    review = build_statement_review(db, s)
    assert _kinds(review) == {1: "unrecorded", 2: "unrecorded", 3: "duplicate"}
    assert next(f for f in review.findings if f.row_index == 3).category == "earlier_statement"


def test_bn4_step_by_step_says_what_is_left(db, make_transaction):
    from app.services.ai_accountant.statement_next import _last_word
    d = date(2032, 11, 10)
    make_transaction([("6112", 620_000, 0), ("1110", 0, 620_000)], tx_date=d, description="quarterly hosting")
    db.commit()
    s = _stmt(db, [(d + timedelta(days=2), "برداشت اینترنتی", 620_000, 0, 9_000_000, "unmatched")])
    said = _last_word(db, s, "u-bn4", str(uuid.uuid4()), "en")
    assert said.startswith("1 rows still need a look") and "The statement closes at 9,000,000; the books show" in said
    assert "Every row" not in said
    fa = _last_word(db, s, "u-bn4", str(uuid.uuid4()), "fa")
    assert "۱ ردیف هنوز نیاز به بررسی دارد" in fa and "ماندهٔ پایانی صورت‌حساب ۹,۰۰۰,۰۰۰" in fa
    clean = _stmt(db, [(date(2032, 11, 25), "clean", 0, 0, None, "matched")])
    assert _last_word(db, clean, "u-bn4", str(uuid.uuid4()), "en").startswith("Every row of this statement")


def test_bn5_the_categorizer_asks_a_gpt5_model_as_it_answers(db, monkeypatch):
    from app.services import ai_suggest, statement_llm_categorizer as llm
    from app.services.ai_accountant.openai_client import plain_request_options
    assert plain_request_options("gpt-5.6-luna", 1000, temperature=0) == {"max_completion_tokens": 1000,
                                                                          "reasoning_effort": "none"}
    assert plain_request_options("gpt-4o-mini", 1000, temperature=0) == {"max_tokens": 1000, "temperature": 0}
    sent = []

    async def post(url, payload, *a, **k):
        sent.append(payload)
        return {"choices": [{"message": {"content": '{"1": "6112"}'}}]}
    monkeypatch.setattr(ai_suggest, "_resolve_ai_base_model", lambda: ("https://example.test/v1", "gpt-5.6-luna"))
    monkeypatch.setattr(ai_suggest, "_post_lm_studio", post)
    out = asyncio.run(llm.suggest_unknown(db, [("k", "خرید لوازم اداری", True)]))
    assert out["k"].account_code == "6112"
    assert "max_completion_tokens" in sent[0] and "max_tokens" not in sent[0] and "temperature" not in sent[0]


def test_bn5_bn6_a_deposit_goes_to_the_companys_own_income(db):
    from app.services.account_resolver import own_income_account, statement_counter_account
    from app.services.chart_service import create_account
    from app.services.reporting.common import REVENUE
    from app.services.statement_llm_categorizer import _candidates
    cid = _company(db)
    with _in(cid):
        assert own_income_account(db) is None
        assert statement_counter_account(db, "in") == "4110"            # no income of its own yet: the chart's
        tuition = create_account(db, name="درآمد شهریه", parent_code="41").code
        for i in range(3):
            _post(db, date(2026, 4, 1 + i), "شهریه", "1110", tuition, 100 * M)
        db.commit()
        assert own_income_account(db) == tuition
        assert statement_counter_account(db, "in") == tuition
        assert statement_counter_account(db, "out") == "6112"
        assert _candidates(db, REVENUE)[0].code == tuition               # the model sees it first


def test_bn7_the_arabic_kaf_is_a_fee(db):
    from app.services.statement_categorizer import normalize_narration
    from app.services.statement_import import classify_fee_row
    row = BankStatementRow(description="كارمزد شاپرك", debit=27_900, credit=0, category=None)
    assert classify_fee_row(row) == "bank_fee"
    assert normalize_narration("كارمزد شاپرك") == normalize_narration("کارمزد شاپرک")
