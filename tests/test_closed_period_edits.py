"""A closed period must lock edits and deletes, not only new postings
(production QA 2026-09-24: PATCH and DELETE inside the lock succeeded)."""
from __future__ import annotations

import uuid
from datetime import date

import pytest



def _lock(auth_client, through: str | None):
    # Through the API so the change is committed the way the app does it —
    # a session-level set that the db fixture later rolls back leaked the
    # lock into unrelated tests.
    r = auth_client.put("/admin/closed-period", json={"closed_period": through})
    assert r.status_code == 200, r.text


@pytest.fixture()
def locked(auth_client):
    _lock(auth_client, "2024-03-31")
    yield date(2024, 3, 31)
    _lock(auth_client, None)


def _voucher(auth_client, day, desc):
    r = auth_client.post("/transactions", json={
        "date": day, "description": desc,
        "lines": [{"account_code": "6112", "debit": 1000, "credit": 0},
                  {"account_code": "1110", "debit": 0, "credit": 1000}],
    })
    assert r.status_code == 201, r.text
    return r.json()["id"]


def test_edit_and_delete_inside_closed_period_are_refused(auth_client, db):
    inside = _voucher(auth_client, "2024-03-15", "inside the lock")
    _lock(auth_client, "2024-03-31")
    try:
        r = auth_client.patch(f"/transactions/{inside}", json={"description": "tampered"})
        assert r.status_code == 422 and "closed through 2024-03-31" in r.json()["detail"]
        r = auth_client.patch(f"/transactions/{inside}", json={"lines": [
            {"account_code": "6112", "debit": 5, "credit": 0}, {"account_code": "1110", "debit": 0, "credit": 5}]})
        assert r.status_code == 422
        assert auth_client.delete(f"/transactions/{inside}").status_code == 422
        # Manager-report journal edit goes through the same lock.
        r = auth_client.patch(f"/manager-reports/journal/{inside}", json={"description": "tampered"})
        assert r.status_code == 422, r.text
        # Untouched.
        g = auth_client.get(f"/transactions/{inside}").json()
        assert g["description"] == "inside the lock" and g["lines"][0]["debit"] == 1000
    finally:
        _lock(auth_client, None)
    # Reopened → both work again.
    assert auth_client.patch(f"/transactions/{inside}", json={"description": "edited after reopen"}).status_code == 200
    assert auth_client.delete(f"/transactions/{inside}").status_code == 204


def test_moving_an_open_entry_into_the_closed_period_is_refused(auth_client, db, locked):
    outside = _voucher(auth_client, "2024-04-10", "after the lock")
    r = auth_client.patch(f"/transactions/{outside}", json={"date": "2024-03-20"})
    assert r.status_code == 422
    # Edits that stay outside the lock are fine.
    assert auth_client.patch(f"/transactions/{outside}", json={"description": "still open"}).status_code == 200
    assert auth_client.delete(f"/transactions/{outside}").status_code == 204


def test_unknown_transaction_still_404(auth_client, locked):
    assert auth_client.patch(f"/transactions/{uuid.uuid4()}", json={"description": "x"}).status_code == 404
    assert auth_client.delete(f"/transactions/{uuid.uuid4()}").status_code == 404
