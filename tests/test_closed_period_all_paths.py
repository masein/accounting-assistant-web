"""Security review 2026-09-24 H7: every way of writing into the books must
respect the closed period — bulk JSON import, Excel import, FX revaluation,
journal reversal and the chat's "undo" (which also hard-deleted)."""
from __future__ import annotations

import uuid
from datetime import date

from sqlalchemy import select

from app.models.audit_log import AuditLog
from app.models.transaction import Transaction
from tests.test_closed_period_edits import _lock

INSIDE, OUTSIDE = "2024-02-15", "2024-05-15"  # lock runs through 2024-03-31


def _make_latest(db, tid, *, hours):
    from datetime import datetime, timedelta, timezone
    row = db.get(Transaction, uuid.UUID(tid))
    row.created_at = datetime(2035, 1, 1, tzinfo=timezone.utc) + timedelta(hours=hours)
    db.commit()


def _post(auth_client, day, desc="p"):
    r = auth_client.post("/transactions", json={
        "date": day, "description": desc,
        "lines": [{"account_code": "6112", "debit": 1000, "credit": 0}, {"account_code": "1110", "debit": 0, "credit": 1000}],
    })
    assert r.status_code == 201, r.text
    return r.json()["id"]


def test_bulk_import_respects_the_lock_and_leaves_a_trail(auth_client, db):
    outside_before = _post(auth_client, OUTSIDE)  # sanity: books open
    _lock(auth_client, "2024-03-31")
    try:
        body = {"transactions": [{"date": INSIDE, "description": "smuggled", "lines": [
            {"account_code": "6112", "debit": 500, "credit": 0}, {"account_code": "1110", "debit": 0, "credit": 500}]}]}
        r = auth_client.post("/transactions/import", json=body)
        assert r.status_code == 422, r.text
        assert db.execute(select(Transaction).where(Transaction.description == "smuggled")).first() is None

        ok = auth_client.post("/transactions/import", json={"transactions": [{**body["transactions"][0], "date": OUTSIDE, "description": "allowed-import"}]})
        assert ok.status_code == 200, ok.text
        tid = ok.json()["ids"][0]
        events = db.execute(select(AuditLog).where(AuditLog.entity_type == "transaction", AuditLog.entity_id == tid)).scalars().all()
        assert [e.action for e in events] == ["create"]
    finally:
        _lock(auth_client, None)
    assert outside_before


def test_fx_revaluation_inside_the_lock_is_refused(auth_client):
    _lock(auth_client, "2024-03-31")
    try:
        r = auth_client.post("/fx/revalue", json={"as_of": INSIDE, "target_currency": "IRR", "dry_run": True})
        assert r.status_code == 422, r.text
    finally:
        _lock(auth_client, None)


def test_journal_reversal_dated_inside_the_lock_is_refused(auth_client):
    tid = _post(auth_client, OUTSIDE, "to reverse")
    _lock(auth_client, "2024-03-31")
    try:
        r = auth_client.post(f"/manager-reports/journal/{tid}/reverse", params={"reverse_date": INSIDE})
        assert r.status_code == 422, r.text
        ok = auth_client.post(f"/manager-reports/journal/{tid}/reverse", params={"reverse_date": OUTSIDE})
        assert ok.status_code == 200, ok.text
    finally:
        _lock(auth_client, None)


def test_chat_undo_soft_deletes_with_audit_and_respects_the_lock(auth_client, db, monkeypatch):
    from app.api import transactions as tx_api
    monkeypatch.setattr(tx_api, "_chat_limiter", tx_api._RateLimiter(max_requests=1000, window_seconds=60))  # other tests share the global bucket
    tid = _post(auth_client, OUTSIDE, f"undo-me-{uuid.uuid4().hex[:6]}")
    _make_latest(db, tid, hours=1)  # same-second created_at ties with other tests' rows otherwise
    r = auth_client.post("/transactions/chat", json={"messages": [{"role": "user", "content": "undo"}]})
    assert r.status_code == 200, r.text
    assert "Deleted the last voucher" in r.json()["message"]
    db.expire_all()
    row = db.get(Transaction, uuid.UUID(tid))
    assert row is not None and row.deleted_at is not None  # soft, not hard
    actions = sorted(e.action for e in db.execute(select(AuditLog).where(AuditLog.entity_type == "transaction", AuditLog.entity_id == tid)).scalars().all())
    assert actions == ["create", "delete"]
    # a second "undo" never touches the already-deleted entry
    before = db.execute(select(Transaction).where(Transaction.deleted_at.is_(None)).order_by(Transaction.created_at.desc()).limit(1)).scalar_one_or_none()
    if before is not None:
        assert before.id != row.id

    locked_id = _post(auth_client, INSIDE, "locked-latest")
    _make_latest(db, locked_id, hours=2)
    _lock(auth_client, "2024-03-31")
    try:
        r = auth_client.post("/transactions/chat", json={"messages": [{"role": "user", "content": "undo"}]})
        assert r.status_code == 422, r.text
        db.expire_all()
        assert db.get(Transaction, uuid.UUID(locked_id)).deleted_at is None
    finally:
        _lock(auth_client, None)


def test_delete_twice_is_a_404(auth_client):
    tid = _post(auth_client, OUTSIDE, "twice")
    assert auth_client.delete(f"/transactions/{tid}").status_code == 204
    assert auth_client.delete(f"/transactions/{tid}").status_code == 404
