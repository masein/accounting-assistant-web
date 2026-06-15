"""Bank reconciliation & CSV-import robustness (PR 4).

Covers: locale-aware cash matching (the "Matched 0" bug), same-amount/same-day
pairing without swapping, file-hash duplicate detection, the needs-mapping
response, malformed-row skipping, mixed date formats, and the bank-fee /
interest suggestion + exact unreconciled difference.

Endpoint functions are called directly against an isolated in-memory chart
(UK + Iran) so seeding one locale can't leak into the shared session fixture.
"""
from __future__ import annotations

import asyncio
from datetime import date
from io import BytesIO

import pytest
from starlette.datastructures import Headers, UploadFile
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.brain import reconcile_statement as reconcile_endpoint
from app.api.brain import upload_bank_statement
from app.db.base import Base
from app.db.seed import SEED_ACCOUNTS, UK_SEED_ACCOUNTS, _parent_code_ir, _parent_code_uk
from app.models.account import Account
from app.models.bank_statement import BankStatement, BankStatementRow
from app.models.transaction import Transaction, TransactionLine
from app.services.account_resolver import resolve_account_code
from app.services.bank_statement_parser import parse_csv
from app.services.locale_service import set_reporting_locale
from app.services.reconciliation import reconcile_statement


def _make_session(chart, parent_fn, locale: str) -> Session:
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)

    @event.listens_for(engine, "connect")
    def _fk(conn, _rec):  # pragma: no cover
        cur = conn.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()

    Base.metadata.create_all(bind=engine)
    db = sessionmaker(bind=engine, autoflush=False, autocommit=False)()
    by_code: dict[str, Account] = {}
    for code, name, level in chart:
        acc = Account(code=code, name=name, level=level)
        db.add(acc)
        by_code[code] = acc
    db.flush()
    for code, _n, _l in chart:
        p = parent_fn(code)
        if p and p in by_code:
            by_code[code].parent_id = by_code[p].id
    set_reporting_locale(db, locale)
    db.commit()
    return db


@pytest.fixture
def uk():
    db = _make_session(UK_SEED_ACCOUNTS, _parent_code_uk, "uk")
    try:
        yield db
    finally:
        db.close()


@pytest.fixture
def ir():
    db = _make_session(SEED_ACCOUNTS, _parent_code_ir, "ir")
    try:
        yield db
    finally:
        db.close()


def _post_cash(db: Session, when: date, desc: str, ref: str | None,
               cash_code: str, other_code: str, amount: int, *, cash_in: bool) -> Transaction:
    """Post a balanced two-line cash entry. cash_in=True → money received
    (Dr cash); cash_in=False → money paid (Cr cash)."""
    cash = db.execute(select(Account).where(Account.code == cash_code)).scalar_one()
    other = db.execute(select(Account).where(Account.code == other_code)).scalar_one()
    txn = Transaction(date=when, description=desc, reference=ref)
    db.add(txn)
    db.flush()
    if cash_in:
        db.add(TransactionLine(transaction_id=txn.id, account_id=cash.id, debit=amount, credit=0))
        db.add(TransactionLine(transaction_id=txn.id, account_id=other.id, debit=0, credit=amount))
    else:
        db.add(TransactionLine(transaction_id=txn.id, account_id=other.id, debit=amount, credit=0))
        db.add(TransactionLine(transaction_id=txn.id, account_id=cash.id, debit=0, credit=amount))
    db.flush()
    return txn


def _make_statement(db: Session, rows: list[dict], currency: str) -> BankStatement:
    dates = [r["tx_date"] for r in rows]
    stmt = BankStatement(
        bank_name="Test Bank", source_type="csv", source_filename="t.csv",
        currency=currency, from_date=min(dates), to_date=max(dates),
        status="parsed", total_rows=len(rows),
    )
    db.add(stmt)
    db.flush()
    for i, r in enumerate(rows, start=1):
        db.add(BankStatementRow(
            statement_id=stmt.id, row_index=i, tx_date=r["tx_date"],
            description=r.get("description"), reference=r.get("reference"),
            debit=r.get("debit", 0), credit=r.get("credit", 0),
            category=r.get("category"),
        ))
    db.flush()
    return stmt


# ─── 1. Locale-aware matching (the "Matched 0" bug) ────────────────────

def test_uk_statement_matches_against_cash_leg(uk):
    """UK ledger uses 12xx for cash; reconciliation must match it (the bug was
    a hardcoded 1110 that made every UK statement read Matched 0)."""
    when = date(2025, 3, 10)
    _post_cash(uk, when, "Payment from Acme", "INV-1", "1200", "4000", 5_000, cash_in=True)
    stmt = _make_statement(uk, [
        {"tx_date": when, "description": "Payment from Acme", "reference": "INV-1", "credit": 5_000},
    ], "GBP")
    rows = list(stmt.rows)
    results = reconcile_statement(uk, rows)
    assert results[0].status == "matched"
    assert results[0].auto_match is True


def test_iran_statement_matches_against_cash_leg(ir):
    when = date(2025, 5, 1)
    _post_cash(ir, when, "فروش نقدی", "F-1", "1110", "4110", 9_000_000, cash_in=True)
    stmt = _make_statement(ir, [
        {"tx_date": when, "description": "فروش نقدی", "reference": "F-1", "credit": 9_000_000},
    ], "IRR")
    results = reconcile_statement(ir, list(stmt.rows))
    assert results[0].status == "matched"


# ─── 2. Same-amount / same-day pairing without swapping (§2.4) ──────────

def test_same_amount_same_day_no_swap(uk):
    when = date(2025, 6, 15)
    t1 = _post_cash(uk, when, "Payment Alpha Ltd", "PAY-A", "1200", "4000", 1_000, cash_in=True)
    t2 = _post_cash(uk, when, "Payment Bravo Ltd", "PAY-B", "1200", "4000", 1_000, cash_in=True)
    stmt = _make_statement(uk, [
        {"tx_date": when, "description": "Payment Alpha Ltd", "reference": "PAY-A", "credit": 1_000},
        {"tx_date": when, "description": "Payment Bravo Ltd", "reference": "PAY-B", "credit": 1_000},
    ], "GBP")
    rows = list(stmt.rows)
    results = reconcile_statement(uk, rows)
    # results align with rows order. Row 1 (Alpha) → t1, row 2 (Bravo) → t2 —
    # not swapped, not the same txn.
    assert results[0].best_match.transaction_id == t1.id
    assert results[1].best_match.transaction_id == t2.id
    assert results[0].best_match.transaction_id != results[1].best_match.transaction_id


# ─── 3. Exact difference + fee suggestions (§2.3, §2.5) ────────────────

def test_unreconciled_difference_and_fee_suggestion(uk):
    when = date(2025, 7, 1)
    _post_cash(uk, when, "Sale to Charlie", "S-1", "1200", "4000", 2_000, cash_in=True)
    # One matching row + one un-booked bank fee (money out, not in ledger).
    stmt = _make_statement(uk, [
        {"tx_date": when, "description": "Sale to Charlie", "reference": "S-1", "credit": 2_000},
        {"tx_date": date(2025, 7, 3), "description": "Monthly account fee", "debit": 30,
         "category": "bank_fee"},
    ], "GBP")
    resp = reconcile_endpoint(stmt.id, uk)
    assert resp.matched == 1
    # The fee line is unmatched → exact net difference is -30 (money out).
    assert resp.unreconciled_difference == -30
    assert len(resp.fee_suggestions) == 1
    sug = resp.fee_suggestions[0]
    assert sug.kind == "bank_fee"
    assert sug.amount == 30
    assert sug.direction == "debit"
    # Locale-aware bank-charges account (UK 8000).
    assert sug.account_code == resolve_account_code(uk, "bank_fee")


def test_interest_suggestion_iran(ir):
    when = date(2025, 8, 1)
    stmt = _make_statement(ir, [
        {"tx_date": when, "description": "سود سپرده", "credit": 500_000, "category": "interest"},
    ], "IRR")
    resp = reconcile_endpoint(stmt.id, ir)
    assert len(resp.fee_suggestions) == 1
    sug = resp.fee_suggestions[0]
    assert sug.kind == "interest_income"
    assert sug.direction == "credit"
    assert sug.account_code == resolve_account_code(ir, "interest_income")


# ─── 4. needs-mapping + malformed skip + mixed dates (§11.2–11.4) ──────

def test_parse_csv_needs_mapping_when_no_date_column():
    csv_text = "foo,bar,baz\n1,2,3\n4,5,6\n"
    result = parse_csv(csv_text)
    assert result.needs_mapping is True
    assert result.headers == ["foo", "bar", "baz"]
    assert not result.rows


def test_parse_csv_column_map_overrides_detection():
    csv_text = "foo,bar,baz\n2025-01-05,Payment,100\n"
    result = parse_csv(csv_text, column_map={"date": 0, "description": 1, "amount": 2})
    assert result.needs_mapping is False
    assert len(result.rows) == 1
    assert result.rows[0].tx_date == date(2025, 1, 5)
    assert result.rows[0].credit == 100


def test_parse_csv_skips_malformed_row_and_counts_it():
    csv_text = (
        "Date,Description,Amount\n"
        "2025-01-05,Good row,100\n"
        "not-a-date,Bad row,50\n"
        "2025-01-07,Another good,-25\n"
    )
    result = parse_csv(csv_text)
    assert len(result.rows) == 2
    assert result.skipped_rows == 1
    # The negative amount on the last row is read as money out (debit).
    assert result.rows[1].debit == 25


@pytest.mark.parametrize("raw,expected", [
    ("05/01/2025", date(2025, 1, 5)),    # DMY
    ("2025-01-05", date(2025, 1, 5)),    # ISO
    ("2025/01/05", date(2025, 1, 5)),    # ISO slash
])
def test_parse_csv_mixed_date_formats(raw, expected):
    csv_text = f"Date,Description,Amount\n{raw},Payment,100\n"
    result = parse_csv(csv_text)
    assert result.rows, f"no rows parsed for {raw}"
    assert result.rows[0].tx_date == expected


# ─── 5. File-hash duplicate detection (§11.5) ──────────────────────────

def _upload(db, content: bytes, filename="stmt.csv", *, bank_name="Test Bank",
            column_map=None, confirm_duplicate=False):
    # Called directly (not through FastAPI), so every Query/File-defaulted
    # argument must be passed explicitly — otherwise the FieldInfo default leaks.
    up = UploadFile(file=BytesIO(content), filename=filename,
                    headers=Headers({"content-type": "text/csv"}))
    return asyncio.run(upload_bank_statement(
        file=up, bank_name=bank_name, column_map=column_map,
        confirm_duplicate=confirm_duplicate, db=db,
    ))


def test_identical_file_flagged_as_duplicate(uk):
    content = b"Date,Description,Amount\n2025-01-05,Payment,100\n2025-01-06,Refund,-20\n"
    first = _upload(uk, content)
    assert first.duplicate is False
    assert first.total_rows == 2
    second = _upload(uk, content)
    assert second.duplicate is True
    assert second.duplicate_of == first.id
    # confirm_duplicate=True imports it anyway.
    third = _upload(uk, content, confirm_duplicate=True)
    assert third.duplicate is False
    assert third.id is not None


def test_upload_unknown_layout_returns_needs_mapping(uk):
    content = b"colA,colB,colC\nx,y,z\n"
    resp = _upload(uk, content)
    assert resp.needs_mapping is True
    assert resp.headers == ["colA", "colB", "colC"]
    assert "date" in resp.required_fields


def test_upload_reports_skipped_rows(uk):
    content = (
        b"Date,Description,Amount\n"
        b"2025-02-01,Ok,100\n"
        b"bad,Bad,50\n"
        b"2025-02-03,Ok2,-10\n"
    )
    resp = _upload(uk, content)
    assert resp.total_rows == 2
    assert resp.skipped_rows == 1


# ─── 6. Demo reset seeds a reconcilable statement (PR 4 follow-up) ─────

def _blank_full_db() -> Session:
    """A blank session with the full schema — reset_db seeds the chart itself."""
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)

    @event.listens_for(engine, "connect")
    def _fk(conn, _rec):  # pragma: no cover
        cur = conn.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()

    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine, autoflush=False, autocommit=False)()


@pytest.mark.parametrize("locale,currency", [("uk", "GBP"), ("ir", "IRR")])
def test_demo_reset_seeds_reconcilable_statement(locale, currency):
    from app.api.admin import reset_db
    db = _blank_full_db()
    try:
        reset_db(db=db, locale=locale, with_demo_data=True, _=None)

        stmts = db.execute(select(BankStatement)).scalars().all()
        # Exactly one statement, in the locale's currency — no stale leftovers.
        assert len(stmts) == 1, f"expected 1 demo statement, got {len(stmts)}"
        assert stmts[0].currency == currency
        rows = list(stmts[0].rows)
        assert rows, "demo statement has no rows"

        resp = reconcile_endpoint(stmts[0].id, db)
        assert resp.matched > 0, f"demo statement reconciled 0 ({locale})"
        # An un-booked fee line is included, so at least one suggestion shows.
        assert len(resp.fee_suggestions) >= 1
    finally:
        db.close()


def test_demo_reset_clears_stale_statements():
    """A second reset (different locale) must not leave the first locale's
    statements behind."""
    from app.api.admin import reset_db
    db = _blank_full_db()
    try:
        reset_db(db=db, locale="ir", with_demo_data=True, _=None)
        reset_db(db=db, locale="uk", with_demo_data=True, _=None)
        stmts = db.execute(select(BankStatement)).scalars().all()
        assert len(stmts) == 1
        assert stmts[0].currency == "GBP"
    finally:
        db.close()


# ─── 6b. reset-db must survive AR/AP + adjustment children (FK guard) ──

def _seed_txn_referencing_rows(db: Session) -> None:
    """Create one row in every table that FKs transactions.id or invoices.id,
    so a reset that forgets to delete any of them 500s with a FK violation."""
    from app.models.adjustment import Adjustment
    from app.models.credit_note import CreditNote
    from app.models.entity import Entity, TransactionEntity
    from app.models.inventory import InventoryItem, InventoryMovement, InventoryMovementType
    from app.models.invoice import Invoice
    from app.models.invoice_item import InvoiceItem
    from app.models.payment import Payment
    from app.models.transaction import Transaction, TransactionAttachment, TransactionLine
    from app.models.transaction_fee import TransactionFeeApplication

    acc = db.execute(select(Account).where(Account.code == "1200")).scalar_one()
    when = date(2025, 4, 1)

    ent = Entity(type="client", name="FK Test Ltd")
    db.add(ent)
    txn = Transaction(date=when, description="FK test")
    inv = Invoice(number="FK-1", kind="sales", status="issued", issue_date=when, due_date=when, amount=100)
    db.add_all([txn, inv])
    db.flush()
    inv.transaction_id = txn.id

    item = InventoryItem(name="FK widget")
    db.add(item)
    db.flush()

    db.add_all([
        TransactionLine(transaction_id=txn.id, account_id=acc.id, debit=100, credit=0),
        TransactionEntity(transaction_id=txn.id, entity_id=ent.id, role="client"),
        TransactionAttachment(transaction_id=txn.id, file_name="x.pdf", file_path="/tmp/fk-x.pdf", content_type="application/pdf"),
        TransactionFeeApplication(transaction_id=txn.id),
        InvoiceItem(invoice_id=inv.id, product_name="FK item"),
        CreditNote(invoice_id=inv.id, transaction_id=txn.id, date=when, amount=10),
        Payment(invoice_id=inv.id, transaction_id=txn.id, date=when, amount=20, direction="in"),
        Adjustment(kind="accrual", start_date=when, transaction_id=txn.id, reversal_transaction_id=txn.id),
        InventoryMovement(item_id=item.id, movement_date=when, movement_type=InventoryMovementType.IN,
                          invoice_id=inv.id, transaction_id=txn.id),
    ])
    bs = BankStatement(bank_name="FK", source_type="csv", source_filename="fk.csv",
                       currency="GBP", from_date=when, to_date=when, status="parsed", total_rows=1)
    db.add(bs)
    db.flush()
    db.add(BankStatementRow(statement_id=bs.id, row_index=1, tx_date=when, debit=100,
                            matched_transaction_id=txn.id, created_transaction_id=txn.id))
    db.commit()


def test_reset_db_succeeds_with_arap_and_adjustment_data():
    """Repro: with credit notes / payments / adjustments present, the old reset
    500'd on a ForeignKeyViolation and rolled back entirely."""
    from app.api.admin import reset_db
    from app.models.adjustment import Adjustment
    from app.models.credit_note import CreditNote
    from app.models.invoice import Invoice
    from app.models.payment import Payment
    db = _blank_full_db()
    try:
        reset_db(db=db, locale="uk", with_demo_data=False, _=None)  # seed chart only
        _seed_txn_referencing_rows(db)
        assert db.query(CreditNote).count() == 1 and db.query(Payment).count() == 1

        out = reset_db(db=db, locale="uk", with_demo_data=False, _=None)
        assert out["ok"] is True
        # Every transaction/invoice-referencing table is wiped clean.
        for model in (CreditNote, Payment, Adjustment, Invoice, Transaction,
                      TransactionLine, BankStatement, BankStatementRow):
            assert db.query(model).count() == 0, f"{model.__name__} not cleared"
    finally:
        db.close()


def test_reset_db_with_demo_succeeds_when_arap_present():
    """The user's exact path: AR/AP data present → Reset & load UK demo → 200,
    one GBP statement, reconciles non-zero."""
    from app.api.admin import reset_db
    db = _blank_full_db()
    try:
        reset_db(db=db, locale="uk", with_demo_data=False, _=None)
        _seed_txn_referencing_rows(db)
        out = reset_db(db=db, locale="uk", with_demo_data=True, _=None)
        assert out["ok"] is True
        stmts = db.execute(select(BankStatement)).scalars().all()
        assert len(stmts) == 1 and stmts[0].currency == "GBP"
        resp = reconcile_endpoint(stmts[0].id, db)
        assert resp.matched > 0
    finally:
        db.close()


# ─── 7. Fee/interest account resolution exists on both locales ─────────

@pytest.mark.parametrize("fixture_name", ["uk", "ir"])
def test_fee_and_interest_accounts_resolve(fixture_name, request):
    db = request.getfixturevalue(fixture_name)
    fee = resolve_account_code(db, "bank_fee")
    interest = resolve_account_code(db, "interest_income")
    assert db.execute(select(Account).where(Account.code == fee)).scalar_one_or_none()
    assert db.execute(select(Account).where(Account.code == interest)).scalar_one_or_none()
