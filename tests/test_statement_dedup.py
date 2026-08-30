"""Row-level duplicate detection on statement import.

The file-hash gate only catches re-uploading the *same export*. The realistic
accident is an overlapping date range — import Aug 1-15, then Aug 1-31 — which
has a different hash and used to re-import and re-post every shared row,
silently doubling those figures.
"""
from __future__ import annotations

import asyncio
from datetime import date, timedelta
from io import BytesIO

import pytest
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool
from starlette.datastructures import Headers, UploadFile

from app.api.brain import BatchApprovalRequest, RowApproval, batch_approve_rows, upload_bank_statement
from app.db.base import Base
from app.db.seed import PERSONAL_SEED_ACCOUNTS, _parent_code_ir
from app.models.account import Account
from app.models.bank_statement import BankStatement, BankStatementRow
from app.models.transaction import Transaction
from app.services.locale_service import set_reporting_locale
from app.services.statement_dedup import duplicate_row_indices, row_fingerprint

D1 = date(2026, 8, 3)
D2 = date(2026, 8, 5)


@pytest.fixture
def db():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)

    @event.listens_for(engine, "connect")
    def _fk(conn, _rec):  # pragma: no cover
        cur = conn.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()

    Base.metadata.create_all(bind=engine)
    s = sessionmaker(bind=engine, autoflush=False, autocommit=False)()
    by_code = {}
    for code, name_fa, _en, level in PERSONAL_SEED_ACCOUNTS:
        acc = Account(code=code, name=name_fa, level=level)
        s.add(acc)
        by_code[code] = acc
    s.flush()
    for code, *_ in PERSONAL_SEED_ACCOUNTS:
        p = _parent_code_ir(code)
        if p and p in by_code:
            by_code[code].parent_id = by_code[p].id
    set_reporting_locale(s, "ir")
    s.commit()
    try:
        yield s
    finally:
        s.close()


def _csv(rows: list[tuple[str, str, int]]) -> bytes:
    body = "Date,Description,Amount\n" + "\n".join(f"{d},{desc},{amt}" for d, desc, amt in rows)
    return body.encode("utf-8")


def _upload(db: Session, content: bytes, filename: str = "s.csv"):
    up = UploadFile(file=BytesIO(content), filename=filename,
                    headers=Headers({"content-type": "text/csv"}))
    return asyncio.run(upload_bank_statement(
        file=up, bank_name="Bank", column_map=None, confirm_duplicate=False, db=db,
    ))


def _rows(db: Session, statement_id) -> list[BankStatementRow]:
    return db.execute(
        select(BankStatementRow)
        .where(BankStatementRow.statement_id == statement_id)
        .order_by(BankStatementRow.row_index)
    ).scalars().all()


# ---------------------------------------------------------------------------
# Fingerprint
# ---------------------------------------------------------------------------
def test_same_movement_fingerprints_alike_despite_reference_noise():
    a = row_fingerprint(tx_date=D1, debit=120_000, credit=0, description="POS-4821 SNAPP 111")
    b = row_fingerprint(tx_date=D1, debit=120_000, credit=0, description="POS-9910 SNAPP 222")
    assert a == b


def test_amount_date_and_direction_all_change_the_fingerprint():
    base = dict(tx_date=D1, debit=120_000, credit=0, description="SNAPP")
    assert row_fingerprint(**base) != row_fingerprint(**{**base, "debit": 130_000})
    assert row_fingerprint(**base) != row_fingerprint(**{**base, "tx_date": D2})
    assert row_fingerprint(**base) != row_fingerprint(
        tx_date=D1, debit=0, credit=120_000, description="SNAPP")


def test_bank_reference_wins_over_the_narration():
    """Two same-day same-amount movements with distinct bank refs are distinct."""
    a = row_fingerprint(tx_date=D1, debit=1000, credit=0, description="COFFEE", reference="TRX-1")
    b = row_fingerprint(tx_date=D1, debit=1000, credit=0, description="COFFEE", reference="TRX-2")
    assert a != b


# ---------------------------------------------------------------------------
# Overlapping re-import
# ---------------------------------------------------------------------------
def test_overlapping_reimport_flags_only_the_shared_rows(db):
    first = _upload(db, _csv([
        ("2026-08-03", "خرید اسنپ", -120000),
        ("2026-08-05", "سوپرمارکت رفاه", -450000),
    ]), "aug-1-15.csv")
    assert first.duplicate_rows == 0

    second = _upload(db, _csv([
        ("2026-08-03", "خرید اسنپ", -120000),      # already imported
        ("2026-08-05", "سوپرمارکت رفاه", -450000),  # already imported
        ("2026-08-20", "قبض برق", -380000),         # new
    ]), "aug-1-31.csv")

    assert second.duplicate_rows == 2
    statuses = [r.recon_status for r in _rows(db, second.id)]
    assert statuses == ["duplicate", "duplicate", "unmatched"]


def test_a_genuinely_new_statement_flags_nothing(db):
    _upload(db, _csv([("2026-08-03", "خرید اسنپ", -120000)]), "a.csv")
    second = _upload(db, _csv([("2026-09-03", "خرید اسنپ", -120000)]), "b.csv")
    assert second.duplicate_rows == 0
    assert [r.recon_status for r in _rows(db, second.id)] == ["unmatched"]


def test_repeated_movement_within_one_statement_is_not_a_duplicate(db):
    """Two identical coffees on the same day are two real transactions."""
    result = _upload(db, _csv([
        ("2026-08-03", "کافه", -50000),
        ("2026-08-03", "کافه", -50000),
    ]))
    assert result.duplicate_rows == 0
    assert [r.recon_status for r in _rows(db, result.id)] == ["unmatched", "unmatched"]


def test_counts_are_matched_not_just_presence(db):
    """One already on file, two arriving → exactly one is a duplicate."""
    _upload(db, _csv([("2026-08-03", "کافه", -50000)]), "a.csv")
    second = _upload(db, _csv([
        ("2026-08-03", "کافه", -50000),
        ("2026-08-03", "کافه", -50000),
    ]), "b.csv")
    assert second.duplicate_rows == 1
    assert [r.recon_status for r in _rows(db, second.id)] == ["duplicate", "unmatched"]


# ---------------------------------------------------------------------------
# Consequences: duplicates must never post
# ---------------------------------------------------------------------------
def test_duplicate_rows_are_refused_by_the_poster(db):
    _upload(db, _csv([
        ("2026-08-03", "خرید اسنپ", -120000),
        ("2026-07-01", "پرداخت الف", -1000),
    ]), "a.csv")
    second = _upload(db, _csv([
        ("2026-08-03", "خرید اسنپ", -120000),
        ("2026-07-02", "پرداخت ب", -2000),
    ]), "b.csv")
    row = _rows(db, second.id)[0]
    assert row.recon_status == "duplicate"

    resp = batch_approve_rows(
        second.id,
        BatchApprovalRequest(approvals=[
            RowApproval(row_id=row.id, action="create", account_code="6130")
        ]),
        db=db,
    )
    assert resp.created == 0
    assert len(resp.errors) == 1 and "already imported" in resp.errors[0].lower()
    assert db.execute(select(Transaction)).scalars().all() == []


def test_reconcile_does_not_hand_duplicates_back_as_postable(db):
    """Reconciling rewrites recon_status; a duplicate must survive that."""
    from app.api.brain import reconcile_statement as reconcile_endpoint

    _upload(db, _csv([
        ("2026-08-03", "خرید اسنپ", -120000),
        ("2026-07-01", "پرداخت الف", -1000),
    ]), "a.csv")
    second = _upload(db, _csv([
        ("2026-08-03", "خرید اسنپ", -120000),
        ("2026-07-02", "پرداخت ب", -2000),
    ]), "b.csv")

    reconcile_endpoint(second.id, db=db)

    assert _rows(db, second.id)[0].recon_status == "duplicate"


def test_end_to_end_overlap_does_not_double_the_books(db):
    """The actual bug: importing an overlapping range twice must not post the
    shared spending twice."""
    first = _upload(db, _csv([
        ("2026-08-03", "خرید اسنپ", -120000),
        ("2026-08-05", "سوپرمارکت رفاه", -450000),
    ]), "aug-1-15.csv")
    for r in _rows(db, first.id):
        batch_approve_rows(first.id, BatchApprovalRequest(approvals=[
            RowApproval(row_id=r.id, action="create", account_code=r.suggested_account_code)
        ]), db=db)

    second = _upload(db, _csv([
        ("2026-08-03", "خرید اسنپ", -120000),
        ("2026-08-05", "سوپرمارکت رفاه", -450000),
        ("2026-08-20", "قبض برق", -380000),
    ]), "aug-1-31.csv")
    for r in _rows(db, second.id):
        batch_approve_rows(second.id, BatchApprovalRequest(approvals=[
            RowApproval(row_id=r.id, action="create", account_code=r.suggested_account_code)
        ]), db=db)

    txns = db.execute(select(Transaction)).scalars().all()
    assert len(txns) == 3  # 2 from the first import + only the genuinely new one


# ---------------------------------------------------------------------------
# Helper-level behaviour
# ---------------------------------------------------------------------------
def test_duplicate_row_indices_is_empty_without_history(db):
    class _R:
        def __init__(self, d, desc, debit):
            self.tx_date, self.description, self.debit = d, desc, debit
            self.credit, self.reference = 0, None

    assert duplicate_row_indices(db, [_R(D1, "x", 100)]) == set()


def test_flagged_duplicates_do_not_mask_later_genuine_rows(db):
    """A row already marked duplicate must not itself count as 'on file', or a
    third import would wrongly flag a real transaction."""
    _upload(db, _csv([
        ("2026-08-03", "کافه", -50000),
        ("2026-07-01", "پرداخت الف", -1000),
    ]), "a.csv")
    _upload(db, _csv([
        ("2026-08-03", "کافه", -50000),
        ("2026-07-02", "پرداخت ب", -2000),
    ]), "b.csv")   # the کافه row is flagged duplicate here
    third = _upload(db, _csv([
        ("2026-08-03", "کافه", -50000),
        ("2026-08-03", "کافه", -50000),
    ]), "c.csv")
    # Only one is genuinely on file, so exactly one of the two is a duplicate.
    assert third.duplicate_rows == 1
