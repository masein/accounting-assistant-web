"""Undone journals are invisible everywhere (roadmap 2026-09 §1.6): every
SELECT leaves out soft-deleted journals and the lines of soft-deleted
journals, unless the caller opts in; refreshes and related-collection loads
still work, and tenancy still applies with the opt-in."""
from __future__ import annotations

import uuid
from datetime import date, datetime, timezone

import pytest
from sqlalchemy import func, select

from app.db.seed import seed_chart_if_empty
from app.db.tenant import use_company
from app.models.account import Account
from app.models.company import Company
from app.models.transaction import Transaction, TransactionLine, include_deleted_transactions
from app.services import audit_service


@pytest.fixture()
def co(db):
    c = Company(id=uuid.uuid4(), name="Soft Co", slug=f"soft-{uuid.uuid4().hex[:8]}", locale="ir",
                base_currency="IRR", status="active", token_version=0)
    db.add(c)
    db.commit()
    with use_company(c.id):
        seed_chart_if_empty(db, locale="ir")
        db.commit()
        acc = {a.code: a for a in db.execute(select(Account)).scalars()}
    cid = c.id                                           # plain values: the tests expunge the session
    yield {"cid": cid, "acc": {code: a.id for code, a in acc.items()}}
    from tests.test_admin_audit import _purge_company
    _purge_company(db, str(cid))


def _journal(db, co, lines, *, deleted=False, ref="J"):
    """``lines`` = [(code, debit, credit)]; may be unbalanced on purpose."""
    with use_company(co["cid"]):
        t = Transaction(id=uuid.uuid4(), date=date(2026, 9, 1), reference=ref, description="j", currency="IRR",
                        deleted_at=datetime.now(timezone.utc) if deleted else None)
        db.add(t)
        db.flush()
        db.add_all([TransactionLine(transaction_id=t.id, account_id=co["acc"][code], debit=d, credit=c)
                    for code, d, c in lines])
        db.commit()
        return t.id


def _scoped(co):
    return use_company(co["cid"])


def test_a_deleted_journal_is_invisible_unless_asked_for(db, co):
    live = _journal(db, co, [("1110", 100, 0), ("4110", 0, 100)], ref="LIVE")
    gone = _journal(db, co, [("1110", 999, 0), ("4110", 0, 999)], deleted=True, ref="GONE")
    db.expunge_all()
    with _scoped(co):
        assert set(db.execute(select(Transaction.reference)).scalars()) == {"LIVE"}
        assert db.get(Transaction, gone) is None and db.get(Transaction, live) is not None
        assert db.execute(select(func.count()).select_from(Transaction)).scalar() == 1
        with include_deleted_transactions():
            assert set(db.execute(select(Transaction.reference)).scalars()) == {"LIVE", "GONE"}
        assert set(db.execute(select(Transaction.reference).execution_options(include_deleted=True))
                   .scalars()) == {"LIVE", "GONE"}


def test_lines_of_a_deleted_journal_are_invisible_even_without_a_join(db, co):
    _journal(db, co, [("1110", 100, 0), ("4110", 0, 100)])
    _journal(db, co, [("1110", 999, 0), ("4110", 0, 999)], deleted=True)
    with _scoped(co):
        by_code = dict(db.execute(
            select(Account.code, func.sum(TransactionLine.debit))
            .join(TransactionLine, TransactionLine.account_id == Account.id).group_by(Account.code)).all())
        assert by_code == {"1110": 100, "4110": 0}
        assert db.execute(select(func.sum(TransactionLine.debit))).scalar() == 100
        joined = db.execute(select(func.sum(TransactionLine.debit))
                            .join(Transaction, Transaction.id == TransactionLine.transaction_id)).scalar()
        assert joined == 100
        with include_deleted_transactions():
            assert db.execute(select(func.sum(TransactionLine.debit))).scalar() == 1_099


def test_soft_deleting_then_reading_back_still_works(db, co):
    tid = _journal(db, co, [("1110", 100, 0), ("4110", 0, 100)])
    with _scoped(co):
        t = db.get(Transaction, tid)
        t.deleted_at = datetime.now(timezone.utc)
        db.commit()                                   # expires t…
        assert t.reference == "J" and t.deleted_at is not None    # …and the refresh still finds it
        assert len(t.lines) == 2                      # its lines load through the relationship
        assert db.get(Transaction, tid) is t          # the identity map keeps what this session holds


def test_the_opt_in_does_not_cross_companies(db, co):
    oid = uuid.uuid4()
    other = Company(id=oid, name="Other", slug=f"oth-{uuid.uuid4().hex[:8]}", locale="ir",
                    base_currency="IRR", status="active", token_version=0)
    db.add(other)
    db.commit()
    try:
        with use_company(oid):
            seed_chart_if_empty(db, locale="ir")
            acc = {a.code: a for a in db.execute(select(Account)).scalars()}
            t = Transaction(date=date(2026, 9, 1), reference="THEIRS", currency="IRR",
                            deleted_at=datetime.now(timezone.utc))
            db.add(t)
            db.flush()
            db.add(TransactionLine(transaction_id=t.id, account_id=acc["1110"].id, debit=5, credit=0))
            db.commit()
        db.expunge_all()
        with _scoped(co), include_deleted_transactions():
            assert "THEIRS" not in set(db.execute(select(Transaction.reference)).scalars())
    finally:
        from tests.test_admin_audit import _purge_company
        _purge_company(db, str(oid))


def test_audit_checks_ignore_undone_journals(db, co):
    """These four checks summed the lines of every journal, undone ones included."""
    _journal(db, co, [("1110", 100, 0), ("3110", 0, 100)])
    _journal(db, co, [("1110", 5_000, 0)], deleted=True)                     # unbalanced, and undone
    _journal(db, co, [("1110", 0, 9_000), ("6112", 9_000, 0)], deleted=True)  # cash negative, undone
    _journal(db, co, [("2110", 0, 70_000), ("6112", 70_000, 0)], deleted=True)  # a big liability, undone
    with _scoped(co):
        assert audit_service.check_debit_credit_balance(db) == []
        assert audit_service.check_accounting_equation(db) == []
        assert audit_service.detect_negative_balances(db) == []
        assert audit_service.check_liability_threshold(db, threshold=50_000) == []
        with include_deleted_transactions():                                  # what they used to see
            assert audit_service.check_debit_credit_balance(db) != []
            assert audit_service.detect_negative_balances(db) != []
            assert audit_service.check_liability_threshold(db, threshold=50_000) != []
