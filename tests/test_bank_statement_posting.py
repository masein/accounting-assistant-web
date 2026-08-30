"""Posting a bank-statement row into the ledger.

`POST /brain/bank-statements/{id}/approve` with action="create" used to
hand-build the journal entry inline, which skipped every guard a hand-keyed
voucher gets — period lock, future-date check, balance assertion — and never
set the currency. It also fell back to a hardcoded Iran expense code, so a UK
chart failed with "account code not found". These pin the fixed behaviour.

Uses the same isolated in-memory chart as test_bank_recon so seeding one locale
can't leak into the shared session fixture.
"""
from __future__ import annotations

from datetime import date, timedelta

import pytest
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.brain import batch_approve_rows
from app.db.base import Base
from app.db.seed import (
    PERSONAL_SEED_ACCOUNTS,
    SEED_ACCOUNTS,
    UK_SEED_ACCOUNTS,
    _parent_code_ir,
    _parent_code_uk,
)
from app.models.account import Account
from app.models.bank_statement import BankStatement, BankStatementRow
from app.models.transaction import Transaction, TransactionLine
from app.schemas import __name__ as _schemas  # noqa: F401  (ensure models import)
from app.services.locale_service import set_reporting_locale
from app.services.period_service import set_closed_period

from app.api.brain import BatchApprovalRequest, RowApproval

TODAY = date.today()


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
def ir():
    db = _make_session(SEED_ACCOUNTS, _parent_code_ir, "ir")
    try:
        yield db
    finally:
        db.close()


@pytest.fixture
def uk():
    db = _make_session(UK_SEED_ACCOUNTS, _parent_code_uk, "uk")
    try:
        yield db
    finally:
        db.close()


@pytest.fixture
def personal():
    chart = [(c, fa, lvl) for c, fa, _en, lvl in PERSONAL_SEED_ACCOUNTS]
    db = _make_session(chart, _parent_code_ir, "ir")
    try:
        yield db
    finally:
        db.close()


def _statement(db: Session, rows: list[dict], currency: str = "IRR") -> BankStatement:
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
            suggested_account_code=r.get("suggested_account_code"),
        ))
    db.flush()
    return stmt


def _create_all(db: Session, stmt: BankStatement, *, account_code: str | None = None):
    rows = db.execute(
        select(BankStatementRow).where(BankStatementRow.statement_id == stmt.id)
    ).scalars().all()
    payload = BatchApprovalRequest(approvals=[
        RowApproval(row_id=r.id, action="create", account_code=account_code) for r in rows
    ])
    return batch_approve_rows(stmt.id, payload, db=db)


def _lines(db: Session, txn_id) -> dict[str, tuple[int, int]]:
    rows = db.execute(
        select(TransactionLine).where(TransactionLine.transaction_id == txn_id)
    ).scalars().all()
    out = {}
    for ln in rows:
        acc = db.get(Account, ln.account_id)
        out[acc.code] = (ln.debit, ln.credit)
    return out


# ---------------------------------------------------------------------------
# Posting mechanics
# ---------------------------------------------------------------------------
def test_money_out_debits_the_category_and_credits_the_bank(ir):
    stmt = _statement(ir, [dict(tx_date=TODAY, description="خرید", debit=250_000)])
    resp = _create_all(ir, stmt, account_code="6112")
    assert resp.created == 1 and not resp.errors

    row = ir.execute(select(BankStatementRow)).scalars().one()
    assert row.created_transaction_id is not None
    assert _lines(ir, row.created_transaction_id) == {
        "6112": (250_000, 0),   # expense debited
        "1110": (0, 250_000),   # bank credited — money left the account
    }


def test_money_in_debits_the_bank(ir):
    stmt = _statement(ir, [dict(tx_date=TODAY, description="حقوق", credit=900_000)])
    _create_all(ir, stmt, account_code="4110")
    row = ir.execute(select(BankStatementRow)).scalars().one()
    assert _lines(ir, row.created_transaction_id) == {
        "1110": (900_000, 0),   # bank debited — money arrived
        "4110": (0, 900_000),
    }


def test_posted_entry_carries_the_statement_currency(uk):
    """The inline builder never set currency, so GBP statements posted as IRR."""
    stmt = _statement(uk, [dict(tx_date=TODAY, description="Tesco", debit=4_000)], currency="GBP")
    _create_all(uk, stmt, account_code="5000")
    txn = uk.execute(select(Transaction)).scalars().one()
    assert txn.currency == "GBP"


def test_posted_entry_is_balanced(ir):
    stmt = _statement(ir, [dict(tx_date=TODAY, description="x", debit=123_456)])
    _create_all(ir, stmt, account_code="6112")
    txn = ir.execute(select(Transaction)).scalars().one()
    lines = ir.execute(
        select(TransactionLine).where(TransactionLine.transaction_id == txn.id)
    ).scalars().all()
    assert sum(l.debit for l in lines) == sum(l.credit for l in lines) == 123_456


# ---------------------------------------------------------------------------
# Guards the inline builder used to skip
# ---------------------------------------------------------------------------
def test_closed_period_blocks_the_row_without_sinking_the_request(ir):
    """Back-dating into a locked period must be refused per row — reported in
    errors, not raised as a 500 and not silently posted."""
    set_closed_period(ir, TODAY - timedelta(days=1))
    ir.commit()
    stmt = _statement(ir, [dict(tx_date=TODAY - timedelta(days=10), description="old", debit=5_000)])

    resp = _create_all(ir, stmt, account_code="6112")

    assert resp.created == 0
    assert len(resp.errors) == 1 and "closed" in resp.errors[0].lower()
    assert ir.execute(select(Transaction)).scalars().all() == []


def test_future_dated_row_is_refused(ir):
    stmt = _statement(ir, [dict(tx_date=TODAY + timedelta(days=30), description="ahead", debit=5_000)])
    resp = _create_all(ir, stmt, account_code="6112")
    assert resp.created == 0
    assert len(resp.errors) == 1 and "future" in resp.errors[0].lower()
    assert ir.execute(select(Transaction)).scalars().all() == []


def test_one_bad_row_does_not_block_the_good_ones(ir):
    stmt = _statement(ir, [
        dict(tx_date=TODAY, description="good", debit=1_000),
        dict(tx_date=TODAY + timedelta(days=30), description="future", debit=2_000),
        dict(tx_date=TODAY, description="also good", debit=3_000),
    ])
    resp = _create_all(ir, stmt, account_code="6112")
    assert resp.created == 2
    assert len(resp.errors) == 1
    assert len(ir.execute(select(Transaction)).scalars().all()) == 2


def test_unknown_account_code_is_reported_not_raised(ir):
    stmt = _statement(ir, [dict(tx_date=TODAY, description="x", debit=1_000)])
    resp = _create_all(ir, stmt, account_code="9999999")
    assert resp.created == 0 and len(resp.errors) == 1


# ---------------------------------------------------------------------------
# Locale-aware fallback (the hardcoded 6190 bug)
# ---------------------------------------------------------------------------
def test_uk_row_with_no_suggestion_falls_back_to_the_uk_expense_account(uk):
    """Previously fell back to the Iran-only code 6190 and failed outright."""
    stmt = _statement(uk, [dict(tx_date=TODAY, description="Unknown payee", debit=7_500)], currency="GBP")
    resp = _create_all(uk, stmt)  # no account_code, no suggestion
    assert resp.created == 1, resp.errors
    row = uk.execute(select(BankStatementRow)).scalars().one()
    posted = _lines(uk, row.created_transaction_id)
    assert "5000" in posted and "1200" in posted  # UK purchases + UK bank


def test_ir_row_with_no_suggestion_falls_back_to_the_ir_expense_account(ir):
    stmt = _statement(ir, [dict(tx_date=TODAY, description="نامشخص", debit=7_500)])
    resp = _create_all(ir, stmt)
    assert resp.created == 1, resp.errors
    row = ir.execute(select(BankStatementRow)).scalars().one()
    assert set(_lines(ir, row.created_transaction_id)) == {"6112", "1110"}


def test_suggested_code_from_import_is_used_when_the_user_gives_none(ir):
    stmt = _statement(ir, [
        dict(tx_date=TODAY, description="اجاره", debit=5_000, suggested_account_code="6112"),
    ])
    resp = _create_all(ir, stmt)
    assert resp.created == 1
    row = ir.execute(select(BankStatementRow)).scalars().one()
    assert "6112" in _lines(ir, row.created_transaction_id)


def test_personal_chart_posts_against_its_own_categories(personal):
    """A personal tenant's chart has no 6112; the row posts to the category the
    user picked, with the personal bank account as the other leg."""
    stmt = _statement(personal, [dict(tx_date=TODAY, description="سوپرمارکت", debit=920_000)])
    resp = _create_all(personal, stmt, account_code="6110")
    assert resp.created == 1, resp.errors
    row = personal.execute(select(BankStatementRow)).scalars().one()
    posted = _lines(personal, row.created_transaction_id)
    assert posted["6110"] == (920_000, 0)
    assert any(code.startswith("11") and cr == 920_000 for code, (_dr, cr) in posted.items())


# ---------------------------------------------------------------------------
# Row bookkeeping
# ---------------------------------------------------------------------------
def test_created_row_is_marked_and_back_linked(ir):
    stmt = _statement(ir, [dict(tx_date=TODAY, description="x", debit=1_000)])
    _create_all(ir, stmt, account_code="6112")
    row = ir.execute(select(BankStatementRow)).scalars().one()
    assert row.user_approved is True
    assert row.recon_status == "matched"
    txn = ir.execute(select(Transaction)).scalars().one()
    assert row.created_transaction_id == txn.id


def test_failed_row_stays_unposted_and_unapproved(ir):
    stmt = _statement(ir, [dict(tx_date=TODAY + timedelta(days=30), description="x", debit=1_000)])
    _create_all(ir, stmt, account_code="6112")
    row = ir.execute(select(BankStatementRow)).scalars().one()
    assert row.created_transaction_id is None
    assert row.user_approved is not True


# ---------------------------------------------------------------------------
# End-to-end: personal statement import → suggestion → post
# ---------------------------------------------------------------------------
def test_personal_csv_import_categorizes_and_posts(personal):
    """The whole flow a personal user drives: upload a month of spending, the
    import suggests a category per row from their own chart, and posting turns
    the suggested rows into real balanced entries."""
    import asyncio
    from io import BytesIO

    from starlette.datastructures import Headers, UploadFile

    from app.api.brain import upload_bank_statement

    csv = (
        "Date,Description,Amount\n"
        f"{TODAY.isoformat()},POS-4821 خرید اسنپ 12345,-120000\n"
        f"{TODAY.isoformat()},سوپرمارکت رفاه,-450000\n"
        f"{TODAY.isoformat()},قبض برق,-80000\n"
        f"{TODAY.isoformat()},واریز حقوق,25000000\n"
    ).encode("utf-8")
    up = UploadFile(file=BytesIO(csv), filename="statement.csv",
                    headers=Headers({"content-type": "text/csv"}))
    result = asyncio.run(upload_bank_statement(
        file=up, bank_name="Bank Melli", column_map=None,
        confirm_duplicate=False, db=personal,
    ))
    assert result.total_rows == 4

    rows = personal.execute(
        select(BankStatementRow).order_by(BankStatementRow.row_index)
    ).scalars().all()
    suggested = [r.suggested_account_code for r in rows]
    assert suggested == ["6130", "6110", "6140", "4110"], suggested

    stmt = personal.get(BankStatement, result.id)
    resp = _create_all(personal, stmt)  # post each row on its own suggestion
    assert resp.created == 4, resp.errors

    # Every posted entry is balanced, and the salary row went the other way.
    txns = personal.execute(select(Transaction)).scalars().all()
    assert len(txns) == 4
    for txn in txns:
        lines = personal.execute(
            select(TransactionLine).where(TransactionLine.transaction_id == txn.id)
        ).scalars().all()
        assert sum(l.debit for l in lines) == sum(l.credit for l in lines) > 0

    salary_row = rows[3]
    posted = _lines(personal, salary_row.created_transaction_id)
    assert posted["4110"][1] == 25_000_000   # income credited
    assert posted["1110"][0] == 25_000_000   # bank debited


def test_second_import_reuses_what_the_user_chose_the_first_time(personal):
    """History beats keywords: a merchant the user filed under Education keeps
    coming back as Education on the next statement."""
    import asyncio
    from io import BytesIO

    from starlette.datastructures import Headers, UploadFile

    from app.api.brain import upload_bank_statement

    # The user's earlier decision, already in the ledger.
    other = personal.execute(select(Account).where(Account.code == "6160")).scalars().one()
    bank = personal.execute(select(Account).where(Account.code == "1110")).scalars().one()
    txn = Transaction(date=TODAY - timedelta(days=30), description="POS-1111 کلاس زبان 777")
    personal.add(txn)
    personal.flush()
    personal.add(TransactionLine(transaction_id=txn.id, account_id=other.id, debit=500_000, credit=0))
    personal.add(TransactionLine(transaction_id=txn.id, account_id=bank.id, debit=0, credit=500_000))
    personal.commit()

    csv = f"Date,Description,Amount\n{TODAY.isoformat()},POS-9999 کلاس زبان 222,-500000\n".encode("utf-8")
    up = UploadFile(file=BytesIO(csv), filename="s2.csv",
                    headers=Headers({"content-type": "text/csv"}))
    asyncio.run(upload_bank_statement(
        file=up, bank_name="Bank Melli", column_map=None,
        confirm_duplicate=False, db=personal,
    ))
    row = personal.execute(
        select(BankStatementRow).order_by(BankStatementRow.row_index.desc())
    ).scalars().first()
    assert row.suggested_account_code == "6160"
