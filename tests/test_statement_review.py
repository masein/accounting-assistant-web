"""Statement-vs-books review (the "upload your statement, we find the
contradictions and fix them step by step" feature) and the pieces the AI
chat builds on: statement detection, the review engine, its endpoint, the
``review_bank_statement`` tool, and posting a row from a chat proposal.
"""
from __future__ import annotations

import asyncio
import uuid
from datetime import date

import pytest
from sqlalchemy import select

from app.models.account import Account
from app.models.bank_statement import BankStatement, BankStatementRow
from app.models.entity import Entity
from app.models.transaction import Transaction
from app.services.statement_import import (
    guess_bank_name,
    looks_like_bank_statement,
    statement_text_score,
)
from app.services.statement_review import (
    bank_account_for_statement,
    book_balance_as_of,
    build_statement_review,
)

USER = "u-stmt-review"

# What pypdf pulls out of an Iranian bank PDF: presentation-form letters, no
# separators between cells, Jalali dates, comma-grouped amounts.
MELLAT_TEXT = (
    "ﺗﺎﺭﯾﺦﺯﻣﺎﻥﺷﻌﺒﻪﮐﺪ ﺣﺴﺎﺑﮕﺮﯼﺷﻤﺎﺭﻩ ﺳﺮﯾﺎﻝﺷﻨﺎﺳﻪ ﻭﺍﺭﯾﺰﻭﺍﺭﯾﺰ ﮐﻨﻨﺪﻩ/ ﺫﯾﺘﻔﻊﺷﺮﺡﻣﺒﻠﻎ ﮔﺮﺩﺵﺑﺪﻫﮑﺎﺭﻣﺎﻧﺪﻩﺭﺩﻳﻒﻣﺒﻠﻎ ﮔﺮﺩﺵﺑﺴﺘﺎﻧﮑﺎﺭ"
    "14:34:50251405/04/06ﺍﻗﺪﺳﯿﻪ 654330143451920 ﮐﺎﺭﻣﺰﺩ ﭘﻞ 3,563,506,455 120,000"
    "14:34:50065433 1405/04/06 2,963,506,455 600,000,000 ﭘﻞ ﺍﺯ ﻫﻤﺮﺍﻩ"
    "15:45:38271405/04/07 ﺍﺩﺍﺭﻩ ﺣﺴﺎﺑﺪﺍﺭﯼ 2,915,426,455 48,080,000"
    "12:09:58065433 1405/04/10 1,415,426,455 1,500,000,000 ﺳﺎﺗﻨﺎ"
    "12:09:58291405/04/10 ﮐﺎﺭﻣﺰﺩ ﺳﺎﺗﻨﺎ 1,415,126,455 300,000"
    "15:21:33 1405/04/15 1,403,759,255 11,367,200 ﮐﺎﺭﺕ ﺑﻪ ﮐﺎﺭﺕ"
)
RECEIPT_TEXT = "فروشگاه رفاه\nتاریخ 1405/04/06\nجمع کل 1,250,000 ریال\nشماره فاکتور 88213\nبا تشکر"


# ---------------------------------------------------------------------------
# Detection
# ---------------------------------------------------------------------------

def test_detects_statement_from_filename_or_message():
    assert looks_like_bank_statement(filename="mellat-transactions.pdf")
    assert looks_like_bank_statement(filename="scan.pdf", message='these are last "Mellat" transactions. add them')
    assert looks_like_bank_statement(filename="IMG_0412.jpg", message="صورتحساب بانک ملت رو ثبت کن")
    assert looks_like_bank_statement(filename="x.pdf", message="بانک سامان — اینا رو اضافه کن")


def test_detects_statement_from_pdf_text_with_presentation_forms():
    dates, headers = statement_text_score(MELLAT_TEXT)
    assert dates >= 5 and headers >= 2   # NFKC folds ﺑﺪﻫﮑﺎﺭ → بدهکار
    assert looks_like_bank_statement(filename="doc.pdf", text=MELLAT_TEXT)


def test_receipt_is_not_a_statement():
    assert not looks_like_bank_statement(filename="receipt.pdf", message="record this expense", text=RECEIPT_TEXT)
    assert not looks_like_bank_statement(filename="contract.pdf", text="This agreement is made on 2026-01-01 between…")


def test_guess_bank_name():
    assert guess_bank_name("mellat-transactions (1).pdf") == "Mellat"
    assert guess_bank_name("scan.pdf", "صورتحساب بانک سامان") == "Saman"
    assert guess_bank_name("scan.pdf", "add these") == "Unknown"


# ---------------------------------------------------------------------------
# Review engine
# ---------------------------------------------------------------------------

def _stmt(db, rows, *, bank_name="Review Test Bank", from_date=None, to_date=None, currency="IRR"):
    """rows: (date, description, debit, credit, balance, recon_status)"""
    s = BankStatement(
        bank_name=bank_name, source_type="csv", source_filename=f"{uuid.uuid4().hex[:6]}.csv",
        currency=currency, from_date=from_date or min(r[0] for r in rows),
        to_date=to_date or max(r[0] for r in rows), status="parsed", total_rows=len(rows),
    )
    db.add(s)
    db.flush()
    for i, (d, desc, debit, credit, bal, status) in enumerate(rows, start=1):
        db.add(BankStatementRow(
            statement_id=s.id, row_index=i, tx_date=d, description=desc, debit=debit,
            credit=credit, balance=bal, confidence=1.0, recon_status=status,
            suggested_account_code="6112" if debit else None, category="misc" if debit else None,
        ))
    db.commit()
    return s


def test_review_classifies_every_kind_of_contradiction(db, make_transaction):
    # Isolated date window (no other test posts cash here).
    d = date(2031, 3, 10)
    exact = make_transaction([("6112", 510_000, 0), ("1110", 0, 510_000)], tx_date=d, description="اجاره فروردین")
    shifted = make_transaction([("6112", 620_000, 0), ("1110", 0, 620_000)], tx_date=d, description="quarterly hosting invoice")
    close = make_transaction([("6112", 1_000_000, 0), ("1110", 0, 1_000_000)], tx_date=date(2031, 3, 14), description="خرید تجهیزات")
    never_seen = make_transaction([("6112", 777_000, 0), ("1110", 0, 777_000)], tx_date=date(2031, 3, 16), description="پرداخت نقدی که بانک ندید")
    db.commit()

    s = _stmt(db, [
        (d, "اجاره فروردین", 510_000, 0, None, "unmatched"),                      # matched exactly
        (date(2031, 3, 12), "برداشت اینترنتی", 620_000, 0, None, "unmatched"),  # same amount, +2 days → needs_confirmation
        (date(2031, 3, 14), "خرید تجهیزات", 1_030_000, 0, None, "unmatched"),   # 3% off → amount_mismatch
        (date(2031, 3, 15), "کارمزد بانکی", 90_000, 0, None, "unmatched"),       # nothing in the books → unrecorded
        (date(2031, 3, 15), "قبلاً وارد شده", 45_000, 0, None, "duplicate"),     # flagged at import
    ], from_date=d, to_date=date(2031, 3, 20))

    review = build_statement_review(db, s)
    kinds = {f.kind: f for f in review.findings}

    assert review.total_rows == 5
    assert review.counts["matched"] == 1
    assert kinds["needs_confirmation"].matched_transaction_id == shifted.id
    assert kinds["needs_confirmation"].suggested_fix == "approve_match"
    assert kinds["amount_mismatch"].matched_transaction_id == close.id
    assert kinds["amount_mismatch"].matched_amount == 1_000_000 and kinds["amount_mismatch"].amount == 1_030_000
    assert kinds["unrecorded"].amount == 90_000 and kinds["unrecorded"].direction == "out"
    assert kinds["unrecorded"].suggested_fix == "post_row" and kinds["unrecorded"].suggested_account_code == "6112"
    assert kinds["duplicate"].severity == "info" and kinds["duplicate"].suggested_fix == "none"
    missing = [f for f in review.findings if f.kind == "missing_in_bank"]
    assert {f.transaction_id for f in missing} == {never_seen.id}
    assert review.bank_account_code == "1110"
    assert review.clean is False
    # Ordering: what is wrong in the books comes before what is merely missing.
    order = [f.kind for f in review.findings]
    assert order.index("amount_mismatch") < order.index("unrecorded") < order.index("duplicate")
    # The exactly-matched row produced no finding at all.
    assert not any(f.row_index == 1 for f in review.findings)


def test_review_is_clean_when_everything_matches(db, make_transaction):
    d = date(2031, 4, 2)
    make_transaction([("6112", 333_000, 0), ("1110", 0, 333_000)], tx_date=d, description="clean row")
    db.commit()
    s = _stmt(db, [(d, "clean row", 333_000, 0, None, "unmatched")])
    review = build_statement_review(db, s)
    assert review.clean is True and review.findings == []


def test_rerun_keeps_rows_the_user_already_posted(db, make_transaction):
    d = date(2031, 5, 5)
    s = _stmt(db, [(d, "posted from page", 12_345, 0, None, "unmatched")])
    row = db.execute(select(BankStatementRow).where(BankStatementRow.statement_id == s.id)).scalar_one()
    txn = make_transaction([("6112", 12_345, 0), ("1110", 0, 12_345)], tx_date=d, description="posted from page")
    row.created_transaction_id = txn.id
    row.recon_status = "matched"
    row.user_approved = True
    db.commit()
    review = build_statement_review(db, s)
    assert review.clean is True
    db.refresh(row)
    assert row.created_transaction_id == txn.id and row.recon_status == "matched"


def test_closing_balance_gap_uses_the_statement_banks_own_account(db, make_transaction):
    code = "1116"
    from app.services.account_resolver import _ensure_account
    _ensure_account(db, code, "بانک ریویو — bank", "ir")
    bank = Entity(type="bank", name=f"Gap Bank {uuid.uuid4().hex[:4]}", code=code)
    db.add(bank)
    db.commit()
    d = date(2031, 6, 1)
    make_transaction([(code, 1_000, 0), ("4110", 0, 1_000)], tx_date=d, description="opening")
    db.commit()
    assert book_balance_as_of(db, code, d) == 1_000

    # Statement says 1,000 too → no gap, no finding.
    s = _stmt(db, [(d, "deposit", 0, 1_000, 1_000, "unmatched")], bank_name=bank.name)
    assert bank_account_for_statement(db, s) == code
    review = build_statement_review(db, s)
    assert review.balance is not None and review.balance.gap == 0
    assert not any(f.kind == "balance_gap" for f in review.findings)

    # Statement says 1,500 → a 500 gap. The deposit row matches the entry on
    # the bank's own account (nothing left to post), so the gap is a real
    # alarm straight away.
    s2 = _stmt(db, [(date(2031, 6, 2), "deposit", 0, 1_000, 1_500, "unmatched")], bank_name=bank.name)
    review2 = build_statement_review(db, s2)
    # (row dated a day after the entry → "probably the same entry", not a firm match)
    assert review2.counts["matched"] + review2.counts["needs_confirmation"] == 1
    assert review2.counts["unrecorded"] == 0
    gap = next(f for f in review2.findings if f.kind == "balance_gap")
    assert review2.balance.gap == 500 and gap.severity == "high" and gap.amount == 500
    assert gap.suggested_fix == "review_entry"
    # A statement with an unrecorded row keeps the gap informational.
    s3 = _stmt(db, [(date(2031, 6, 3), "unknown deposit", 0, 250, 1_750, "unmatched")], bank_name=bank.name)
    review3 = build_statement_review(db, s3)
    assert review3.counts["unrecorded"] == 1
    gap3 = next(f for f in review3.findings if f.kind == "balance_gap")
    assert gap3.severity == "info"


def test_gap_explained_by_unrecorded_rows_is_informational(db):
    code = "1110"
    d = date(2031, 7, 3)
    book = book_balance_as_of(db, code, d)
    # One unrecorded credit of 250 → closing = book + 250 explains itself.
    s = _stmt(db, [(d, "unrecorded deposit", 0, 250, book + 250, "unmatched")], bank_name="Explained Bank")
    review = build_statement_review(db, s)
    assert review.balance.gap == 250 and review.balance.unrecorded_net == 250
    assert review.balance.explained is True
    gap = next(f for f in review.findings if f.kind == "balance_gap")
    assert gap.severity == "info" and gap.suggested_fix == "post_row"
    # The balance check comes after the rows that will move it.
    kinds = [f.kind for f in review.findings]
    assert kinds.index("unrecorded") < kinds.index("balance_gap")


def test_unexplained_gap_stays_informational_while_rows_are_unrecorded(db):
    """A brand-new company importing its first statement: 0 in the books, a
    big closing balance. That is not an alarm yet — post the rows first."""
    code = "1110"
    d = date(2031, 10, 1)
    book = book_balance_as_of(db, code, d)
    s = _stmt(db, [(d, "first ever row", 500, 0, book + 9_999_000, "unmatched")], bank_name="Fresh Books Bank")
    review = build_statement_review(db, s)
    gap = next(f for f in review.findings if f.kind == "balance_gap")
    assert gap.severity == "info" and gap.suggested_fix == "review_entry"
    assert "Post the unrecorded rows first" in gap.detail
    assert [f.kind for f in review.findings][0] == "unrecorded"


def test_identical_rows_in_one_statement_are_labelled_as_such(db):
    d = date(2031, 11, 5)
    s = _stmt(db, [
        (d, "خرید اینترنتی پارسیان ۹۹۲۲۸۵۵۲", 1_560_000, 0, None, "unmatched"),
        (d, "خرید اینترنتی پارسیان ۹۹۲۲۸۵۵۲", 1_560_000, 0, None, "unmatched"),
    ])
    review = build_statement_review(db, s)
    dup = next(f for f in review.findings if f.kind == "duplicate")
    assert dup.row_index == 2 and dup.category == "same_statement" and dup.matched_amount == 1_560_000
    assert "row #1" in dup.detail


# ---------------------------------------------------------------------------
# Endpoint + AI tool
# ---------------------------------------------------------------------------

def test_review_endpoint(auth_client, db):
    d = date(2031, 8, 1)
    s = _stmt(db, [(d, "api unrecorded", 5_000, 0, None, "unmatched")])
    r = auth_client.post(f"/brain/bank-statements/{s.id}/review")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["statement_id"] == str(s.id)
    assert body["counts"]["unrecorded"] == 1
    assert body["findings"][0]["kind"] == "unrecorded"
    assert auth_client.post(f"/brain/bank-statements/{uuid.uuid4()}/review").status_code == 404


def test_review_tool_defaults_to_latest_statement_and_explains_fixes(db):
    from app.services.ai_accountant.base import ToolContext, ToolError
    from app.services.ai_accountant.statement_tools import ReviewBankStatement, ReviewBankStatementInput

    d = date(2031, 9, 1)
    s = _stmt(db, [(d, "tool unrecorded", 7_000, 0, None, "unmatched")])
    # Other tests import statements in the same second; pin this one as the
    # unambiguous latest.
    from datetime import datetime, timezone
    s.created_at = datetime(2099, 12, 31, tzinfo=timezone.utc)
    db.commit()
    ctx = ToolContext(db=db, user_id=USER)
    out = asyncio.run(ReviewBankStatement().run(ctx, ReviewBankStatementInput()))
    assert out["statement_id"] == str(s.id)
    assert out["findings"][0]["kind"] == "unrecorded"
    assert out["bank_account_code"] == "1110"
    assert "post_row" in out["how_to_fix"]

    out2 = asyncio.run(ReviewBankStatement().run(ctx, ReviewBankStatementInput(statement_id=str(s.id), max_findings=1)))
    assert out2["findings_total"] >= 1 and len(out2["findings"]) == 1

    with pytest.raises(ToolError):
        asyncio.run(ReviewBankStatement().run(ctx, ReviewBankStatementInput(statement_id=str(uuid.uuid4()))))


def test_review_tool_is_registered_for_both_modes():
    from app.services.ai_accountant.orchestrator import build_default_registry, build_personal_registry
    assert "review_bank_statement" in {t["name"] for t in build_default_registry().to_anthropic()}
    assert "review_bank_statement" in {t["name"] for t in build_personal_registry().to_anthropic()}


# ---------------------------------------------------------------------------
# Posting an unrecorded row from a chat proposal
# ---------------------------------------------------------------------------

def _propose_row(db, row, *, user_id=USER):
    from app.services.ai_accountant.base import ToolContext
    from app.services.ai_accountant.proposal_tools import (
        ProposeCreateTransaction,
        ProposeCreateTransactionInput,
    )
    ctx = ToolContext(db=db, user_id=user_id, username="tester", user_message="post it")
    payload = ProposeCreateTransactionInput(
        date="2026-05-20", description=row.description or "row", currency="IRR",
        lines=[
            {"account_code": "6112", "debit": row.debit, "credit": 0},
            {"account_code": "1110", "debit": 0, "credit": row.debit},
        ],
        bank_statement_row_id=str(row.id),
    )
    return asyncio.run(ProposeCreateTransaction().run(ctx, payload))["confirmation_token"]


def test_confirming_a_row_proposal_marks_the_statement_row_posted(db):
    from fastapi import HTTPException
    from app.services.ai_accountant.execute_service import execute_proposal

    s = _stmt(db, [(date(2026, 5, 20), "chat posted row", 88_000, 0, None, "unmatched")])
    row = db.execute(select(BankStatementRow).where(BankStatementRow.statement_id == s.id)).scalar_one()

    token = _propose_row(db, row)
    token2 = _propose_row(db, row)          # a second card raised while the row was still open
    ex = execute_proposal(db, confirmation_token=token, actor_user_id=USER)
    db.refresh(row)
    assert str(row.created_transaction_id) == ex.transaction_id
    assert row.recon_status == "matched" and row.user_approved is True

    # Confirming the stale second card must not post the row again …
    with pytest.raises(HTTPException) as ei:
        execute_proposal(db, confirmation_token=token2, actor_user_id=USER)
    assert ei.value.status_code == 409
    # … and a new card for a posted row is refused at proposal time.
    from app.services.ai_accountant.base import ToolError
    with pytest.raises(ToolError):
        _propose_row(db, row)
    # And the review no longer lists it.
    assert build_statement_review(db, s).clean is True


def test_duplicate_row_cannot_be_posted_from_chat(db):
    from fastapi import HTTPException
    from app.services.ai_accountant.execute_service import execute_proposal

    s = _stmt(db, [(date(2026, 5, 21), "dupe row", 66_000, 0, None, "unmatched")])
    row = db.execute(select(BankStatementRow).where(BankStatementRow.statement_id == s.id)).scalar_one()
    token = _propose_row(db, row)
    # Flagged as a duplicate after the card was raised (e.g. an overlapping
    # re-import) → Confirm is refused.
    row.recon_status = "duplicate"
    db.commit()
    with pytest.raises(HTTPException) as ei:
        execute_proposal(db, confirmation_token=token, actor_user_id=USER)
    assert ei.value.status_code == 409
    # And a fresh card for a duplicate row is refused up front.
    from app.services.ai_accountant.base import ToolError
    with pytest.raises(ToolError):
        _propose_row(db, row)
    assert db.execute(select(Transaction).where(Transaction.description == "dupe row")).scalars().all() == []


def test_undoing_a_posted_row_hands_the_row_back(db):
    from app.services.ai_accountant.execute_service import execute_proposal, undo_action

    s = _stmt(db, [(date(2026, 5, 22), "undo me", 31_000, 0, None, "unmatched")])
    row = db.execute(select(BankStatementRow).where(BankStatementRow.statement_id == s.id)).scalar_one()
    ex = execute_proposal(db, confirmation_token=_propose_row(db, row), actor_user_id=USER)
    db.refresh(row)
    assert row.created_transaction_id is not None

    undo_action(db, audit_log_id=ex.audit_log_id, actor_user_id=USER)
    db.refresh(row)
    assert row.created_transaction_id is None and row.recon_status == "unmatched" and row.user_approved is False
    # …and the review offers it again.
    kinds = [f.kind for f in build_statement_review(db, s).findings]
    assert "unrecorded" in kinds


def test_deleting_a_posted_row_via_rest_hands_the_row_back(auth_client, db):
    from app.services.ai_accountant.execute_service import execute_proposal

    s = _stmt(db, [(date(2026, 5, 23), "delete me", 32_000, 0, None, "unmatched")])
    row = db.execute(select(BankStatementRow).where(BankStatementRow.statement_id == s.id)).scalar_one()
    ex = execute_proposal(db, confirmation_token=_propose_row(db, row), actor_user_id=USER)
    r = auth_client.delete(f"/transactions/{ex.transaction_id}")
    assert r.status_code == 204, r.text
    db.expire_all()
    row = db.get(BankStatementRow, row.id)
    assert row.created_transaction_id is None and row.recon_status == "unmatched"


def test_row_proposal_keeps_the_banks_date_even_when_the_message_says_next(db):
    """The relative-date resolver anchors chat proposals to 'today' when the
    message has no date; a statement row's date must win over that."""
    from app.models.ai_accountant import AIProposal
    from app.services.ai_accountant.base import ToolContext, ToolError
    from app.services.ai_accountant.proposal_tools import (
        ProposeCreateTransaction,
        ProposeCreateTransactionInput,
    )

    s = _stmt(db, [(date(2026, 6, 27), "کارمزد بل", 120_000, 0, None, "unmatched")])
    row = db.execute(select(BankStatementRow).where(BankStatementRow.statement_id == s.id)).scalar_one()
    ctx = ToolContext(db=db, user_id=USER, username="tester", user_message="next")
    payload = ProposeCreateTransactionInput(
        date="2026-09-22", description="Bank fee (model's paraphrase)", currency="IRR",
        lines=[{"account_code": "6112", "debit": 120_000, "credit": 0},
               {"account_code": "1110", "debit": 0, "credit": 120_000}],
        bank_statement_row_id=str(row.id),
    )
    out = asyncio.run(ProposeCreateTransaction().run(ctx, payload))
    prop = db.execute(select(AIProposal).where(AIProposal.confirmation_token == uuid.UUID(out["confirmation_token"]))).scalar_one()
    assert prop.tool_input["date"] == "2026-06-27"
    assert prop.tool_input["description"] == "کارمزد بل"   # the bank's narration, verbatim
    assert "2026-06-27" in out["summary"]

    with pytest.raises(ToolError):
        asyncio.run(ProposeCreateTransaction().run(ctx, payload.model_copy(update={"bank_statement_row_id": str(uuid.uuid4())})))


def test_proposal_tool_refuses_a_row_that_is_already_posted(db, make_transaction):
    from app.services.ai_accountant.base import ToolContext, ToolError
    from app.services.ai_accountant.proposal_tools import (
        ProposeCreateTransaction,
        ProposeCreateTransactionInput,
    )

    s = _stmt(db, [(date(2026, 6, 28), "already posted", 48_080_000, 0, None, "unmatched"),
                   (date(2026, 6, 28), "dup row", 1_000, 0, None, "duplicate")])
    rows = db.execute(select(BankStatementRow).where(BankStatementRow.statement_id == s.id)
                      .order_by(BankStatementRow.row_index)).scalars().all()
    txn = make_transaction([("6112", 48_080_000, 0), ("1110", 0, 48_080_000)], tx_date=date(2026, 6, 28))
    rows[0].created_transaction_id = txn.id
    db.commit()
    ctx = ToolContext(db=db, user_id=USER, username="tester", user_message="next")

    def payload(row, amount):
        return ProposeCreateTransactionInput(
            date="2026-06-28", description="x", currency="IRR",
            lines=[{"account_code": "6112", "debit": amount, "credit": 0},
                   {"account_code": "1110", "debit": 0, "credit": amount}],
            bank_statement_row_id=str(row.id),
        )

    with pytest.raises(ToolError) as e1:
        asyncio.run(ProposeCreateTransaction().run(ctx, payload(rows[0], 48_080_000)))
    assert "already posted" in str(e1.value)
    with pytest.raises(ToolError) as e2:
        asyncio.run(ProposeCreateTransaction().run(ctx, payload(rows[1], 1_000)))
    assert "duplicate" in str(e2.value)


# ---------------------------------------------------------------------------
# The statement's own bank account (QA 2026-09-24, high #2)
# ---------------------------------------------------------------------------

def _own_bank(db, code, name):
    from app.services.account_resolver import _ensure_account
    _ensure_account(db, code, f"{name} — bank", "ir")
    bank = Entity(type="bank", name=name, code=code)
    db.add(bank)
    db.commit()
    return bank


def test_statement_predicates_prefer_the_banks_own_account(db):
    from app.services.statement_import import statement_predicates

    bank = _own_bank(db, "1121", f"Own Bank {uuid.uuid4().hex[:4]}")
    s = _stmt(db, [(date(2031, 12, 1), "x", 1, 0, None, "unmatched")], bank_name=bank.name)
    code, match, missing = statement_predicates(db, s)
    assert code == "1121"
    assert match("1121") and match("1110") and not match("6112")   # lenient matching
    assert missing("1121") and not missing("1110")                   # strict "missing in bank"

    generic = _stmt(db, [(date(2031, 12, 2), "y", 1, 0, None, "unmatched")], bank_name="Unknown")
    code2, match2, missing2 = statement_predicates(db, generic)
    assert code2 == "1110" and match2("1110") and missing2("1110") and not missing2("1121")


def test_review_matches_entries_on_the_statement_banks_account(db, make_transaction):
    bank = _own_bank(db, "1122", f"Match Bank {uuid.uuid4().hex[:4]}")
    d = date(2032, 1, 10)
    # Already posted on the bank's own account → must match, not "unrecorded".
    make_transaction([(bank.code, 5_000_000, 0), ("4110", 0, 5_000_000)], tx_date=d, description="customer deposit")
    # Petty-cash voucher in the same window → must NOT be "missing in bank".
    make_transaction([("6112", 100, 0), ("1110", 0, 100)], tx_date=date(2032, 1, 12), description="petty cash coffee")
    # Entry on the bank's account the statement never shows → IS missing.
    gone = make_transaction([("6112", 777_000, 0), (bank.code, 0, 777_000)], tx_date=date(2032, 1, 15), description="bank payment not on statement")
    db.commit()
    s = _stmt(db, [(d, "customer deposit", 0, 5_000_000, None, "unmatched")], bank_name=bank.name,
              from_date=d, to_date=date(2032, 1, 31))
    review = build_statement_review(db, s)
    assert review.counts["matched"] == 1 and review.counts["unrecorded"] == 0
    missing = [f for f in review.findings if f.kind == "missing_in_bank"]
    assert [f.transaction_id for f in missing] == [gone.id]
    assert review.bank_account_code == bank.code


def test_page_post_uses_the_statement_banks_account(db):
    from app.api.brain import BatchApprovalRequest, RowApproval, batch_approve_rows

    bank = _own_bank(db, "1123", f"Post Bank {uuid.uuid4().hex[:4]}")
    s = _stmt(db, [(date(2026, 5, 24), "page posted fee", 30_000, 0, None, "unmatched")], bank_name=bank.name)
    row = db.execute(select(BankStatementRow).where(BankStatementRow.statement_id == s.id)).scalar_one()
    resp = batch_approve_rows(s.id, BatchApprovalRequest(approvals=[RowApproval(row_id=row.id, action="create", account_code="6112")]), db=db)
    assert resp.created == 1, resp.errors
    db.refresh(row)
    txn = db.get(Transaction, row.created_transaction_id)
    legs = {(l.account.code, l.debit, l.credit) for l in txn.lines}
    assert legs == {("6112", 30_000, 0), (bank.code, 0, 30_000)}
