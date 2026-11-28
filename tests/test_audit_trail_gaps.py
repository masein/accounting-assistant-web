"""Every write leaves an audit row (review 2026-11-28, scenario CV7).

Equity, commitments and cheques, recurring rules and the company profile wrote
none, unlike every other write endpoint; the assistant's equity and cheque
cards did, the pages did not. Each step here checks the row it leaves."""
from __future__ import annotations

import json
import uuid
from pathlib import Path

from sqlalchemy import select

from app.db.tenant import use_company
from app.models.audit_log import AuditLog
from tests.test_cheque_lifecycle import SAYAD, ir  # noqa: F401  (fixture)


def _rows(books, entity_type, action=None, entity_id=None):
    with use_company(books.cid):
        books.db.expire_all()
        q = select(AuditLog).where(AuditLog.entity_type == entity_type)
        if action:
            q = q.where(AuditLog.action == action)
        if entity_id:
            q = q.where(AuditLog.entity_id == str(entity_id))
        return books.db.execute(q.order_by(AuditLog.timestamp)).scalars().all()


def _ok(r, code=200):
    assert r.status_code == code, r.text
    return r.json() if r.content and r.headers.get("content-type", "").startswith("application/json") else None


def test_cv7_equity_writes_are_audited(ir):  # noqa: F811
    """CV7: a shareholding's create / update / delete, and each posting."""
    api = ir.api
    a = _ok(api.post("/entities", json={"type": "shareholder", "name": f"Holder {uuid.uuid4().hex[:4]}"}), 201)
    b = _ok(api.post("/entities", json={"type": "shareholder", "name": f"Holder {uuid.uuid4().hex[:4]}"}), 201)
    ha = _ok(api.post("/equity/shareholdings", json={"entity_id": a["id"], "percent": 100, "shares": 1000}), 201)
    hb = _ok(api.post("/equity/shareholdings", json={"entity_id": b["id"]}), 201)
    _ok(api.patch(f"/equity/shareholdings/{ha['id']}", json={"shares": 1200}))
    assert api.delete(f"/equity/shareholdings/{hb['id']}").status_code == 204
    assert [r.action for r in _rows(ir, "shareholding", entity_id=ha["id"])] == ["create", "update"]
    upd = json.loads(_rows(ir, "shareholding", "update")[0].detail)
    assert upd["from"] != upd["to"] and '"shares": 1200' in upd["to"]
    assert [r.action for r in _rows(ir, "shareholding", entity_id=hb["id"])] == ["create", "delete"]

    posted = _ok(api.post("/equity/contribution", json={"entity_id": a["id"], "amount": 5_000_000,
                                                         "date": "2026-09-01"}), 201)
    declared = _ok(api.post("/equity/dividend/declare", json={"total_amount": 1_000_000, "date": "2026-09-02"}), 201)
    for result, event in ((posted, "contribution"), (declared, "declare_dividend")):
        row = _rows(ir, "transaction", "create", entity_id=result["transaction_ids"][0])
        assert len(row) == 1 and json.loads(row[0].detail)["equity_event"] == event
        assert row[0].username == "owner"


def test_cv7_cheque_and_instalment_writes_are_audited(ir):  # noqa: F811
    """CV7: create, every state change of a cheque, print, delete, and the
    print layout."""
    api = ir.api
    c = ir.cheque(counter_account_code="1112", reference="881234")
    ir.step(c, "sayad", sayad_id=SAYAD)
    ir.step(c, "deposit", on="2026-09-10")
    ir.step(c, "bounce", on="2026-09-21", note="insufficient funds")
    ir.step(c, "deposit", on="2026-09-24")
    ir.step(c, "settle", on="2026-09-25")
    assert [r.action for r in _rows(ir, "commitment", entity_id=c["id"])] == [
        "create", "register_sayad", "deposit", "bounce", "deposit", "settle"]
    assert json.loads(_rows(ir, "commitment", "bounce")[0].detail)["note"] == "insufficient funds"

    back = ir.cheque(counter_account_code="1112")
    ir.step(back, "return", on="2026-09-05", note="paid in cash")
    passed = ir.cheque(counter_account_code="1112")
    ir.step(passed, "endorse", to="Landlord", account_code="2140")
    issued = ir.cheque(direction="pay", amount=3_000_000, counterparty="Pars Paper", reference="100442")
    assert api.post(f"/commitments/{issued['id']}/print", json={"national_id": "10101234567"}).status_code == 200
    assert api.post(f"/commitments/{issued['id']}/print", json={"guide": True}).status_code == 200   # a guide: no row
    assert [r.action for r in _rows(ir, "commitment", entity_id=back["id"])] == ["create", "return"]
    assert [r.action for r in _rows(ir, "commitment", entity_id=passed["id"])] == ["create", "endorse"]
    assert [r.action for r in _rows(ir, "commitment", entity_id=issued["id"])] == ["create", "print"]

    plan = _ok(api.post("/commitments/installments", json={"title": "Van loan", "total_amount": 3_000_000,
                                                           "count": 3, "first_due": "2026-12-01"}), 201)
    assert len(_rows(ir, "commitment", "create", entity_id=plan[0]["id"])) == 1
    assert api.delete(f"/commitments/{plan[2]['id']}").status_code == 204
    assert len(_rows(ir, "commitment", "delete", entity_id=plan[2]["id"])) == 1

    _ok(api.put("/commitments/print-layout", json={"offset_y": 1.5}))
    _ok(api.post("/commitments/print-layout/reset"))
    assert [r.detail for r in _rows(ir, "cheque_print_layout")][-1] == "reset"
    assert len(_rows(ir, "cheque_print_layout", "update")) == 2


def test_cv7_recurring_rules_and_what_they_post_are_audited(ir):  # noqa: F811
    """CV7: a rule's create / update / delete, and each entry it posts."""
    api = ir.api
    rule = _ok(api.post("/recurring", json={"name": "Office rent", "amount": 2_000_000, "start_date": "2026-09-01",
                                            "next_run_date": "2026-09-01", "frequency": "monthly",
                                            "bank_account_code": "1110", "counter_account_code": "6112"}), 201)
    _ok(api.patch(f"/recurring/{rule['id']}", json={"amount": 2_500_000}))
    _ok(api.patch(f"/recurring/{rule['id']}", json={"amount": 2_500_000}))      # nothing changed: no row
    out = _ok(api.post("/recurring/run-due"))
    assert api.delete(f"/recurring/{rule['id']}").status_code == 204
    rows = _rows(ir, "recurring_rule", entity_id=rule["id"])
    assert [r.action for r in rows] == ["create", "update", "delete"]
    assert json.loads(rows[1].detail) == {"amount": {"from": 2_000_000, "to": 2_500_000}}
    mine = [p for p in out["posted"] if p["rule_id"] == rule["id"]]
    assert mine
    for p in mine:
        row = _rows(ir, "transaction", "create", entity_id=p["transaction_id"])
        assert len(row) == 1 and "Office rent" in row[0].detail


def test_cv7_company_profile_changes_are_audited(ir):  # noqa: F811
    """CV7: the issuer's details on every invoice — a changed IBAN above all —
    and a new logo leave a row with the old and the new value."""
    from app.api.company_profile import UPLOADS_DIR
    api = ir.api
    _ok(api.put("/admin/company-profile", json={"legal_name": "Pars Ltd", "iban": "IR000000000000000000000001"}))
    _ok(api.put("/admin/company-profile", json={"iban": "IR000000000000000000000002"}))
    _ok(api.put("/admin/company-profile", json={"iban": "IR000000000000000000000002"}))    # unchanged: no row
    rows = _rows(ir, "company_profile", "update")
    assert len(rows) == 2
    assert json.loads(rows[1].detail) == {"iban": {"from": "IR000000000000000000000001",
                                                   "to": "IR000000000000000000000002"}}
    logo = UPLOADS_DIR / "branding" / ir.cid / "logo.png"
    try:
        r = api.post("/admin/company-profile/logo",
                     files={"file": ("logo.png", b"\x89PNG\r\n\x1a\n" + b"\x00" * 32, "image/png")})
        assert r.status_code == 200, r.text
        assert "logo uploaded" in _rows(ir, "company_profile", "update")[-1].detail
    finally:
        if logo.exists():
            logo.unlink()
            Path(logo.parent).rmdir()
