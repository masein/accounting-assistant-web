"""Input-validation LOWs from the 2026-09-24 QA run.

2.16 duplicate entity name accepted · 2.15 invalid IBAN accepted · 3.1 duplicate
invoice number accepted · 3.26 zero budget accepted · 3.11 25 h in one day
accepted · 3.25 petty-cash expense far above the float accepted · 3.22/3.11
year-summary and my-summary 422 without parameters.
"""
from __future__ import annotations

import uuid
from datetime import date

import pytest

from app.utils.iban import normalize_iban


def _uniq(prefix: str) -> str:
    return f"{prefix} {uuid.uuid4().hex[:8]}"


# ---------------------------------------------------------------------------
# IBAN
# ---------------------------------------------------------------------------

class TestIban:
    def test_normalises_spaces_and_case(self):
        assert normalize_iban("ir82 0540 1026 8002 0817 9090 02") == "IR820540102680020817909002"
        assert normalize_iban("GB82 WEST 1234 5698 7654 32") == "GB82WEST12345698765432"
        assert normalize_iban("") is None and normalize_iban(None) is None

    @pytest.mark.parametrize("bad", ["not-an-iban", "IR82054010268002081790900", "IR820540102680020817909003", "1234567890123456"])
    def test_rejects_garbage_wrong_length_and_bad_checksum(self, bad):
        with pytest.raises(ValueError):
            normalize_iban(bad)

    def test_entity_create_rejects_invalid_iban(self, auth_client):
        r = auth_client.post("/entities", json={"type": "employee", "name": _uniq("Emp"), "iban": "not-an-iban"})
        assert r.status_code == 422
        assert "IBAN" in r.text

    def test_entity_create_stores_normalised_iban(self, auth_client):
        r = auth_client.post("/entities", json={"type": "employee", "name": _uniq("Emp"),
                                                "iban": "ir82 0540 1026 8002 0817 9090 02"})
        assert r.status_code == 201, r.text
        assert r.json()["iban"] == "IR820540102680020817909002"

    def test_entity_update_rejects_invalid_iban(self, auth_client):
        r = auth_client.post("/entities", json={"type": "supplier", "name": _uniq("Sup")})
        eid = r.json()["id"]
        assert auth_client.patch(f"/entities/{eid}", json={"iban": "XX00BAD"}).status_code == 422
        ok = auth_client.patch(f"/entities/{eid}", json={"iban": "IR200170000000121518633003"})
        assert ok.status_code == 200 and ok.json()["iban"] == "IR200170000000121518633003"


# ---------------------------------------------------------------------------
# Duplicate entity names
# ---------------------------------------------------------------------------

class TestDuplicateEntity:
    def test_same_name_and_type_is_refused(self, auth_client):
        name = _uniq("QA Client Acme")
        assert auth_client.post("/entities", json={"type": "client", "name": name}).status_code == 201
        dup = auth_client.post("/entities", json={"type": "client", "name": name.lower()})
        assert dup.status_code == 409
        assert "already exists" in dup.json()["detail"]

    def test_same_name_other_type_is_fine(self, auth_client):
        name = _uniq("Dual Role")
        assert auth_client.post("/entities", json={"type": "client", "name": name}).status_code == 201
        assert auth_client.post("/entities", json={"type": "supplier", "name": name}).status_code == 201

    def test_allow_duplicate_overrides(self, auth_client):
        name = _uniq("Twin")
        assert auth_client.post("/entities", json={"type": "client", "name": name}).status_code == 201
        r = auth_client.post("/entities", json={"type": "client", "name": name, "allow_duplicate": True})
        assert r.status_code == 201


# ---------------------------------------------------------------------------
# Duplicate invoice numbers
# ---------------------------------------------------------------------------

def _invoice(auth_client, number: str, kind: str = "sales", **extra):
    ent = auth_client.post("/entities", json={"type": "client" if kind == "sales" else "supplier",
                                              "name": _uniq("Inv party")}).json()
    payload = {"number": number, "kind": kind, "status": "draft", "issue_date": "2026-09-01",
               "due_date": "2026-09-30", "amount": 1_000_000, "currency": "IRR", "entity_id": ent["id"]}
    payload.update(extra)
    return auth_client.post("/invoices", json=payload)


class TestDuplicateInvoiceNumber:
    def test_second_sales_invoice_with_same_number_is_refused(self, auth_client):
        number = _uniq("QA-INV")
        assert _invoice(auth_client, number).status_code == 201
        dup = _invoice(auth_client, number)
        assert dup.status_code == 409 and "already exists" in dup.json()["detail"]

    def test_purchase_invoice_may_reuse_a_sales_number(self, auth_client):
        number = _uniq("SHARED")
        assert _invoice(auth_client, number, kind="sales").status_code == 201
        assert _invoice(auth_client, number, kind="purchase").status_code == 201

    def test_renumbering_onto_an_existing_number_is_refused(self, auth_client):
        a, b = _uniq("A"), _uniq("B")
        assert _invoice(auth_client, a).status_code == 201
        rb = _invoice(auth_client, b)
        assert rb.status_code == 201
        clash = auth_client.patch(f"/invoices/{rb.json()['id']}", json={"number": a})
        assert clash.status_code == 409
        same = auth_client.patch(f"/invoices/{rb.json()['id']}", json={"number": b})  # keeping its own number is fine
        assert same.status_code == 200


# ---------------------------------------------------------------------------
# Budgets, time entries, summaries
# ---------------------------------------------------------------------------

def test_zero_budget_is_refused(auth_client):
    r = auth_client.post("/budgets", json={"month": "2026-09", "category": _uniq("cat"), "limit_amount": 0})
    assert r.status_code == 422
    ok = auth_client.post("/budgets", json={"month": "2026-09", "category": _uniq("cat"), "limit_amount": 50_000_000})
    assert ok.status_code == 201


class TestTimeEntryHours:
    @pytest.fixture()
    def worker(self, auth_client):
        emp = auth_client.post("/entities", json={"type": "employee", "name": _uniq("Worker")}).json()
        cli = auth_client.post("/entities", json={"type": "client", "name": _uniq("Client")}).json()
        return emp["id"], cli["id"]

    def _post(self, auth_client, worker, hours, day="2026-09-15"):
        emp, cli = worker
        return auth_client.post("/time/entries", json={"employee_id": emp, "client_id": cli,
                                                       "work_date": day, "hours": hours, "description": "work"})

    def test_more_than_24_hours_in_one_entry(self, auth_client, worker):
        assert self._post(auth_client, worker, 25).status_code == 422

    def test_day_total_cannot_pass_24(self, auth_client, worker):
        assert self._post(auth_client, worker, 20).status_code == 201
        r = self._post(auth_client, worker, 6.5)
        assert r.status_code == 422 and "24" in r.json()["detail"]
        assert self._post(auth_client, worker, 4).status_code == 201  # exactly 24 is allowed
        assert self._post(auth_client, worker, 6.5, day="2026-09-16").status_code == 201  # another day is fine

    def test_update_respects_the_day_cap(self, auth_client, worker):
        first = self._post(auth_client, worker, 20).json()
        second = self._post(auth_client, worker, 3).json()
        assert auth_client.patch(f"/time/entries/{second['id']}", json={"hours": 5}).status_code == 422
        assert auth_client.patch(f"/time/entries/{second['id']}", json={"hours": 4}).status_code == 200
        assert auth_client.patch(f"/time/entries/{first['id']}", json={"hours": 30}).status_code == 422


def test_summaries_default_their_period(auth_client):
    ys = auth_client.get("/payroll/year-summary")
    assert ys.status_code == 200, ys.text
    ms = auth_client.get("/time/my-summary")
    # Owner without a linked employee record: still not a 422 — the period defaults.
    assert ms.status_code in (200, 404), ms.text
    assert ms.status_code != 422


# ---------------------------------------------------------------------------
# Petty cash: spend within the float
# ---------------------------------------------------------------------------

def test_petty_cash_expense_cannot_exceed_the_float(auth_client, db):
    from tests.test_recurring_notify_pettycash import _petty_account

    acc, _holder = _petty_account(auth_client, db)
    too_much = auth_client.post(f"/petty-cash/accounts/{acc['id']}/expenses", json={"amount": 99_000_000, "description": "x"})
    assert too_much.status_code == 422 and "exceeds" in too_much.json()["detail"]

    dep = auth_client.post(f"/petty-cash/accounts/{acc['id']}/deposit", json={"amount": 5_000_000, "bank_account_code": "1110"})
    assert dep.status_code == 200
    ok = auth_client.post(f"/petty-cash/accounts/{acc['id']}/expenses", json={"amount": 3_000_000, "description": "a"})
    assert ok.status_code == 201
    # pending spend counts against the float too
    over = auth_client.post(f"/petty-cash/accounts/{acc['id']}/expenses", json={"amount": 2_500_000, "description": "b"})
    assert over.status_code == 422
    fits = auth_client.post(f"/petty-cash/accounts/{acc['id']}/expenses", json={"amount": 2_000_000, "description": "c"})
    assert fits.status_code == 201
