"""A bank party's code is its ledger account. A typed code that isn't a cash or
bank account is refused in the user's language — «۳۰۱» was silently replaced
with a new 1111, and an existing capital account would have been taken as the
bank's (deep browser test, 2026-10-02, finding #22)."""
from __future__ import annotations

import uuid

import pytest

from app.db.tenant import use_company


@pytest.fixture()
def ir(client, db):
    from tests.test_admin_audit import _purge_company
    from tests.test_statement_export import _company
    api, cid = _company(client, db, "ir", "IRR")
    yield api, cid
    _purge_company(db, cid)


def test_a_bank_takes_a_bank_account_or_opens_its_own(ir):
    api, cid = ir
    fa = {"X-UI-Language": "fa"}
    with use_company(cid):
        r = api.post("/entities", headers=fa, json={"type": "bank", "name": f"بانک ملت {uuid.uuid4().hex[:4]}", "code": "۳۰۱"})
        assert r.status_code == 422 and r.json()["detail"].startswith("حسابی با کد"), r.text
        r = api.post("/entities", headers=fa, json={"type": "bank", "name": f"بانک ملی {uuid.uuid4().hex[:4]}", "code": "3110"})
        assert r.status_code == 422 and "بانک یا صندوق نیست" in r.json()["detail"], r.text
        sub = api.post("/accounts", json={"name": "بانک تجارت جاری", "parent_code": "1110"}).json()["code"]
        r = api.post("/entities", json={"type": "bank", "name": f"بانک تجارت {uuid.uuid4().hex[:4]}", "code": sub})
        assert r.status_code == 201 and r.json()["code"] == sub, r.text
        r = api.post("/entities", json={"type": "bank", "name": f"بانک سپه {uuid.uuid4().hex[:4]}"})
        assert r.status_code == 201 and r.json()["code"].startswith("111"), r.text      # opened its own
        # any other party keeps the code it was given
        r = api.post("/entities", json={"type": "client", "name": f"مشتری {uuid.uuid4().hex[:4]}", "code": "۳۰۱"})
        assert r.status_code == 201 and r.json()["code"] == "301", r.text
