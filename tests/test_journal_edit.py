"""Editing a posted journal entry.

`PATCH /manager-reports/journal/{id}` imported a validator from
`app.api.transactions` that had never been defined there. The import sits
inside the handler, so it only raised at call time: any attempt to edit an
entry's lines returned a 500 rather than validating anything. Nothing covered
this path, which is exactly how it survived.
"""
from __future__ import annotations

from datetime import date

import pytest
from sqlalchemy import select

from app.models.transaction import Transaction, TransactionLine


@pytest.fixture
def entry(auth_client, make_transaction):
    return make_transaction(
        [("1110", 500_000, 0), ("6112", 0, 500_000)],
        description="اصلاح‌شدنی", reference="JE-EDIT-1",
    )


def _lines(db, txn_id):
    return db.execute(
        select(TransactionLine).where(TransactionLine.transaction_id == txn_id)
    ).scalars().all()


def test_editing_the_lines_succeeds(auth_client, db, entry):
    """The regression: this used to 500 on an ImportError."""
    r = auth_client.patch(f"/manager-reports/journal/{entry.id}", json={
        "lines": [
            {"account_code": "1110", "debit": 750_000, "credit": 0},
            {"account_code": "6112", "debit": 0, "credit": 750_000},
        ],
    })
    assert r.status_code == 200, r.text

    db.expire_all()
    lines = _lines(db, entry.id)
    assert sum(l.debit for l in lines) == 750_000
    assert sum(l.credit for l in lines) == 750_000


def test_unbalanced_lines_are_rejected(auth_client, db, entry):
    """The point of the validator: an edit must not be able to unbalance
    the books."""
    r = auth_client.patch(f"/manager-reports/journal/{entry.id}", json={
        "lines": [
            {"account_code": "1110", "debit": 900_000, "credit": 0},
            {"account_code": "6112", "debit": 0, "credit": 100_000},
        ],
    })
    assert r.status_code == 400
    assert "equal" in r.json()["detail"].lower()

    db.expire_all()
    lines = _lines(db, entry.id)
    assert sum(l.debit for l in lines) == 500_000  # untouched


def test_zero_value_lines_are_rejected(auth_client, db, entry):
    r = auth_client.patch(f"/manager-reports/journal/{entry.id}", json={
        "lines": [
            {"account_code": "1110", "debit": 0, "credit": 0},
            {"account_code": "6112", "debit": 0, "credit": 0},
        ],
    })
    assert r.status_code == 400


def test_editing_the_description_only(auth_client, db, entry):
    r = auth_client.patch(f"/manager-reports/journal/{entry.id}",
                          json={"description": "شرح تازه"})
    assert r.status_code == 200
    db.expire_all()
    assert db.get(Transaction, entry.id).description == "شرح تازه"


def test_editing_a_missing_entry_is_404(auth_client):
    r = auth_client.patch("/manager-reports/journal/00000000-0000-0000-0000-000000000000",
                          json={"description": "x"})
    assert r.status_code == 404


def test_an_unknown_account_code_is_rejected(auth_client, entry):
    r = auth_client.patch(f"/manager-reports/journal/{entry.id}", json={
        "lines": [
            {"account_code": "9999999", "debit": 1000, "credit": 0},
            {"account_code": "6112", "debit": 0, "credit": 1000},
        ],
    })
    assert r.status_code == 400
