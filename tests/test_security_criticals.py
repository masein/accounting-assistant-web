"""Security review 2026-09-24 — the findings fixed immediately.

C1 monthly snapshot was world-readable at a guessable /uploads URL shared by
every tenant · C2 owner-dashboard cache key had no tenant · H2 stored XSS via
the attachment file extension · H8 soft-deleted transactions still counted in
exports / budgets / running balances · payroll my-payslips showed voided runs
· bank-statement upload had no size cap · L8 CSV formula injection.
"""
from __future__ import annotations

import io
import uuid
from datetime import date
from pathlib import Path

import pytest

from app.core.auth import CSRF_COOKIE, create_session_token, generate_csrf_token
from app.core.config import settings
from app.models.company import Company
from app.models.entity import Entity
from app.models.pay_run import PayRun, PayRunLine
from tests.conftest import _CSRFTestClient


def _company(db, name="Sec Co"):
    c = Company(id=uuid.uuid4(), name=name, slug=f"sec-{uuid.uuid4().hex[:8]}", locale="ir",
                base_currency="IRR", status="active", token_version=0)
    db.add(c); db.flush()
    return c


def _as(client, *, company=None, role="owner", entity_id=None):
    tok = create_session_token(user_id=str(uuid.uuid4()), username=f"{role}-{uuid.uuid4().hex[:4]}",
                               is_admin=(role == "owner"), role=role,
                               company_id=(str(company.id) if company else None),
                               entity_id=(str(entity_id) if entity_id else None))
    csrf = generate_csrf_token()
    client.cookies.clear()
    client.cookies.set(settings.auth_cookie_name, tok)
    client.cookies.set(CSRF_COOKIE, csrf)
    return _CSRFTestClient(client, csrf)


# ---------------------------------------------------------------------------
# C2 dashboard cache is per tenant
# ---------------------------------------------------------------------------

def test_dashboard_cache_key_carries_the_company(client, db):
    from app.api.reports import _dashboard_cache
    a, b = _company(db, "A"), _company(db, "B")
    db.commit()
    _dashboard_cache.clear()
    assert _as(client, company=a).get("/reports/owner-dashboard").status_code == 200
    assert _as(client, company=b).get("/reports/owner-dashboard").status_code == 200
    keys = list(_dashboard_cache)
    assert any(k.startswith(f"dashboard:{a.id}:") for k in keys)
    assert any(k.startswith(f"dashboard:{b.id}:") for k in keys)
    assert not any(k.startswith("dashboard:12:") for k in keys)  # the old tenant-less shape


# ---------------------------------------------------------------------------
# C1 snapshot is private and per company
# ---------------------------------------------------------------------------

def test_snapshot_is_served_only_to_its_own_company(client, db):
    from app.api import exports
    a, b = _company(db, "A"), _company(db, "B")
    db.commit()
    owner_a = _as(client, company=a)
    r = owner_a.post("/exports/monthly-snapshot")
    assert r.status_code == 200, r.text
    link = r.json()["snapshot_file"]
    assert link.startswith("/exports/monthly-snapshot/snapshot-")
    assert "/uploads/" not in link
    name = link.rsplit("/", 1)[1]
    stored = exports.SNAPSHOT_DIR / str(a.id) / name
    assert stored.is_file()
    assert "uploads" not in stored.parts  # outside the public static mount

    got = owner_a.get(link)
    assert got.status_code == 200 and got.headers["content-type"].startswith("application/zip")
    assert got.content[:2] == b"PK"

    # another company: same name → nothing
    assert _as(client, company=b).get(link).status_code == 404
    # bad names never touch the filesystem
    assert owner_a.get("/exports/monthly-snapshot/evil.zip").status_code == 404
    assert owner_a.get("/exports/monthly-snapshot/snapshot-2026-09-deadbeef.zip").status_code == 404


# ---------------------------------------------------------------------------
# H2 stored extension comes from the validated type
# ---------------------------------------------------------------------------

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64


def test_attachment_extension_ignores_the_user_file_name(auth_client, db):
    from app.models.transaction import TransactionAttachment
    r = auth_client.post("/transactions/attachments", files={"file": ("evil.html", PNG, "image/png")})
    assert r.status_code == 201, r.text
    assert str(db.get(TransactionAttachment, uuid.UUID(r.json()["id"])).file_path).endswith(".png")
    got = auth_client.get(r.json()["url"])
    assert got.status_code == 200 and got.headers["content-type"].startswith("image/png")
    r2 = auth_client.post("/transactions/attachments",
                          files={"file": ("evil.svg", b"date,amount\n2026-01-01,5\n", "text/csv")})
    assert r2.status_code == 201, r2.text
    assert str(db.get(TransactionAttachment, uuid.UUID(r2.json()["id"])).file_path).endswith(".csv")
    got2 = auth_client.get(r2.json()["url"])
    assert got2.status_code == 200 and got2.headers["content-disposition"].startswith("attachment")


# ---------------------------------------------------------------------------
# H8 + L8 exports: soft-deleted rows excluded, formulas neutralised
# ---------------------------------------------------------------------------

def test_export_skips_deleted_transactions_and_neutralises_formulas(auth_client):
    marker = f"=HYPERLINK(\"http://x/{uuid.uuid4().hex[:6]}\")"
    r = auth_client.post("/transactions", json={
        "date": "2026-03-03", "description": marker,
        "lines": [{"account_code": "6112", "debit": 1200, "credit": 0},
                  {"account_code": "1110", "debit": 0, "credit": 1200}],
    })
    assert r.status_code == 201, r.text
    txn_id = r.json()["id"]
    csv_text = auth_client.get("/exports/transactions.csv").text
    assert txn_id in csv_text
    # csv quotes the cell, so look for the apostrophe right after the opening quote
    assert "\"'=HYPERLINK" in csv_text and ",=HYPERLINK" not in csv_text
    assert auth_client.delete(f"/transactions/{txn_id}").status_code == 204
    assert txn_id not in auth_client.get("/exports/transactions.csv").text


def test_budget_actuals_skip_deleted_transactions(auth_client, db):
    from app.services.budget_service import expense_actuals_by_category
    r = auth_client.post("/transactions", json={
        "date": "2024-02-10", "description": "budget probe",
        "lines": [{"account_code": "6112", "debit": 777_000, "credit": 0},
                  {"account_code": "1110", "debit": 0, "credit": 777_000}],
    })
    assert r.status_code == 201
    before = sum(expense_actuals_by_category(db, "2024-02").values())
    assert before >= 777_000
    assert auth_client.delete(f"/transactions/{r.json()['id']}").status_code == 204
    db.expire_all()
    after = sum(expense_actuals_by_category(db, "2024-02").values())
    assert before - after == 777_000


# ---------------------------------------------------------------------------
# payroll: voided and draft runs are not payslips / year totals
# ---------------------------------------------------------------------------

def test_voided_runs_are_hidden_from_payslips_and_year_summary(client, db):
    co = _company(db)
    emp = Entity(id=uuid.uuid4(), name="Mina", type="employee", company_id=co.id)
    db.add(emp); db.flush()
    for status, net in (("posted", 80_000), ("voided", 999_000), ("draft", 555_000)):
        run = PayRun(id=uuid.uuid4(), period_start=date(2026, 5, 1), period_end=date(2026, 5, 31),
                     pay_date=date(2026, 6, 1), currency="IRR", status=status, company_id=co.id)
        db.add(run); db.flush()
        db.add(PayRunLine(id=uuid.uuid4(), run_id=run.id, entity_id=emp.id, employee_name=emp.name,
                          gross=net + 20_000, net_pay=net, company_id=co.id))
    db.commit()

    me = _as(client, company=co, role="employee", entity_id=emp.id).get("/payroll/my-payslips")
    assert me.status_code == 200, me.text
    nets = [p["net_pay"] for p in me.json()["payslips"]]
    assert nets == [80_000]

    ys = _as(client, company=co).get("/payroll/year-summary", params={"year": 2026})
    assert ys.status_code == 200, ys.text
    text = ys.text
    assert "999000" not in text.replace(",", "") and "555000" not in text.replace(",", "")
    assert "80000" in text.replace(",", "")


# ---------------------------------------------------------------------------
# statement upload cap
# ---------------------------------------------------------------------------

def test_statement_upload_over_20mb_is_refused(auth_client):
    big = io.BytesIO(b"date,amount,description\n" + b"x" * (20 * 1024 * 1024 + 1))
    r = auth_client.post("/brain/bank-statements/upload", files={"file": ("huge.csv", big, "text/csv")})
    assert r.status_code == 413
