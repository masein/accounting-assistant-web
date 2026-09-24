"""QA 2026-09-24 2.10: PATCH /transactions/{id} stored a version but wrote no
audit event — edits must be as auditable as creates and deletes."""
from __future__ import annotations

import json

from sqlalchemy import select

from app.models.audit_log import AuditLog, TransactionVersion


def _post(auth_client, description="audit me"):
    r = auth_client.post("/transactions", json={
        "date": "2026-03-01", "description": description,
        "lines": [{"account_code": "6112", "debit": 7000, "credit": 0},
                  {"account_code": "1110", "debit": 0, "credit": 7000}],
    })
    assert r.status_code == 201, r.text
    return r.json()


def _events(db, txn_id):
    return db.execute(
        select(AuditLog).where(AuditLog.entity_type == "transaction", AuditLog.entity_id == str(txn_id))
        .order_by(AuditLog.timestamp)
    ).scalars().all()


def test_patch_writes_an_update_audit_event_with_the_new_state(auth_client, db):
    txn = _post(auth_client)
    r = auth_client.patch(f"/transactions/{txn['id']}", json={
        "description": "edited description",
        "lines": [{"account_code": "6112", "debit": 9000, "credit": 0},
                  {"account_code": "1110", "debit": 0, "credit": 9000}],
    })
    assert r.status_code == 200, r.text

    events = _events(db, txn["id"])
    assert sorted(e.action for e in events) == ["create", "update"]  # same-second timestamps: order-free
    snapshot = json.loads(next(e for e in events if e.action == "update").detail)
    assert snapshot["description"] == "edited description"
    assert {(l["account_code"], l["debit"], l["credit"]) for l in snapshot["lines"]} == {("6112", 9000, 0), ("1110", 0, 9000)}

    versions = db.execute(
        select(TransactionVersion).where(TransactionVersion.transaction_id == str(txn["id"]))
        .order_by(TransactionVersion.version)
    ).scalars().all()
    assert [(v.version, v.action) for v in versions] == [(1, "create"), (2, "update")]


def test_delete_after_edit_keeps_the_full_trail(auth_client, db):
    txn = _post(auth_client, "trail")
    assert auth_client.patch(f"/transactions/{txn['id']}", json={"reference": "REF-1"}).status_code == 200
    assert auth_client.delete(f"/transactions/{txn['id']}").status_code == 204
    assert sorted(e.action for e in _events(db, txn["id"])) == ["create", "delete", "update"]
