"""UI / statement LOWs from the 2026-09-24 QA run.

6.1 dashboard alarms on a near-empty company · 3.26/1.21 budget notification
links business tenants to the personal dashboard · 6.2 CFO "runway −0.6
months" when cash is negative · 6.5 employee dropdown filled only at login ·
3.2 amount_paid shows the raw over-payment · 2.12 re-uploading the same Excel
file shows no warning · 2.17 same-day statement rows ordered by id · 4.8
same-statement duplicates labelled "Imported before".
"""
from __future__ import annotations

import hashlib
import uuid
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

from app.db.seed import SEED_ACCOUNTS, _parent_code_ir
from tests.test_agent_convergence_tools import _make_session

CASH, CAPITAL, EXPENSE = "1110", "3110", "6112"


def _ccy(prefix="Q"):
    return prefix + uuid.uuid4().hex[:5].upper()


def _post(db, make_transaction, ccy, lines, day, **kw):
    tx = make_transaction(lines, tx_date=day, **kw)
    tx.currency = ccy
    return tx


# ---------------------------------------------------------------------------
# 6.1 dashboard alerts need a minimum of activity
# ---------------------------------------------------------------------------

def _dashboard(auth_client, ccy):
    from app.api.reports import _dashboard_cache
    _dashboard_cache.clear()
    r = auth_client.get("/reports/owner-dashboard", params={"currency": ccy})
    assert r.status_code == 200, r.text
    return r.json()


def test_three_tiny_vouchers_raise_no_risk_alerts(auth_client, db, make_transaction):
    ccy = _ccy()
    today = date.today()
    _post(db, make_transaction, ccy, [(CASH, 100, 0), (CAPITAL, 0, 100)], today)
    _post(db, make_transaction, ccy, [(EXPENSE, 50, 0), (CASH, 0, 50)], today)
    _post(db, make_transaction, ccy, [(EXPENSE, 50, 0), (CASH, 0, 50)], today)
    db.commit()
    titles = {a["title"] for a in _dashboard(auth_client, ccy)["alerts"]}
    assert not ({"Cash runway is short", "Book quality risk", "Expense spike"} & titles), titles


def test_real_activity_with_short_runway_still_alerts(auth_client, db, make_transaction):
    ccy = _ccy()
    today = date.today()
    last_month = today.replace(day=1) - timedelta(days=31)  # the key the dashboard's burn window uses
    _post(db, make_transaction, ccy, [(CASH, 300_000, 0), (CAPITAL, 0, 300_000)], last_month)
    for i in range(6):
        _post(db, make_transaction, ccy, [(EXPENSE, 100_000, 0), (CASH, 0, 100_000)], today, description=f"e{i}")
    for i in range(5):
        _post(db, make_transaction, ccy, [(EXPENSE, 100_000, 0), (CASH, 0, 100_000)], last_month, description=f"l{i}")
    db.commit()
    titles = {a["title"] for a in _dashboard(auth_client, ccy)["alerts"]}
    assert "Cash runway is short" in titles


# ---------------------------------------------------------------------------
# 3.26 / 1.21 budget notification link
# ---------------------------------------------------------------------------

def test_budget_link_page_depends_on_tenant_kind(db, monkeypatch):
    from app.services import fx_service, notification_service

    monkeypatch.setattr(fx_service, "_current_company_row", lambda _db: SimpleNamespace(kind="personal"))
    assert notification_service._budget_link_page(db) == "personal-dashboard"
    monkeypatch.setattr(fx_service, "_current_company_row", lambda _db: SimpleNamespace(kind="business"))
    assert notification_service._budget_link_page(db) == "dashboard"
    monkeypatch.setattr(fx_service, "_current_company_row", lambda _db: None)
    assert notification_service._budget_link_page(db) == "dashboard"


# ---------------------------------------------------------------------------
# 6.2 CFO: negative cash is "overdrawn", not "-0.6 months of runway"
# ---------------------------------------------------------------------------

def test_cfo_reports_overdrawn_instead_of_negative_runway(db, make_transaction):
    from app.services.cfo_intelligence import answer_cfo_question, build_cfo_report

    ccy = _ccy("R")
    today = date.today()
    last_month = today.replace(day=1) - timedelta(days=1)
    _post(db, make_transaction, ccy, [(EXPENSE, 2_000_000, 0), (CASH, 0, 2_000_000)], today)
    _post(db, make_transaction, ccy, [(EXPENSE, 2_000_000, 0), (CASH, 0, 2_000_000)], last_month)
    db.commit()

    report = build_cfo_report(db, currency=ccy, lang="en")
    cash = next(k for k in report.kpis if k.key == "cash_on_hand").value
    assert cash < 0
    assert report.runway_months == 0
    assert any(i.title == "Cash is overdrawn: no runway" for i in report.insights)
    assert "overdrawn" in report.narrative
    assert "-0." not in report.narrative
    assert "overdrawn" in answer_cfo_question(db, "how long can we survive?", currency=ccy, lang="en")

    fa = build_cfo_report(db, currency=ccy, lang="fa")
    assert any("منفی" in i.title for i in fa.insights)


# ---------------------------------------------------------------------------
# 3.2 over-payment display
# ---------------------------------------------------------------------------

def test_amount_paid_is_the_settled_part_and_overpaid_is_separate():
    from tests.test_ar_ap_payments import PaymentCreate, _issue, _read, add_payment

    db = _make_session(SEED_ACCOUNTS, _parent_code_ir, "ir")
    try:
        inv = _issue(db, kind="sales", amount=3_000_000, currency="IRR")
        add_payment(inv.id, PaymentCreate(amount=8_000_000), db)
        row = _read(db, inv.id)
        assert row.status == "paid"
        assert row.amount_paid == 3_000_000
        assert row.overpaid == 5_000_000
        assert row.balance_due == 0

        exact = _issue(db, kind="sales", amount=1_000_000, currency="IRR")
        add_payment(exact.id, PaymentCreate(amount=400_000), db)
        row = _read(db, exact.id)
        assert row.amount_paid == 400_000 and row.overpaid == 0 and row.balance_due == 600_000
    finally:
        db.close()


# ---------------------------------------------------------------------------
# 2.12 Excel re-upload warning
# ---------------------------------------------------------------------------

def test_excel_import_history_is_remembered_per_file(db, tmp_path):
    from app.api.transactions import _excel_import_history, _record_excel_import
    from app.schemas.transaction import ExcelImportPreviewResponse

    content = b"fake xlsx " + uuid.uuid4().bytes
    sha = hashlib.sha256(content).hexdigest()
    f = tmp_path / "journal.xlsx"
    f.write_bytes(content)
    assert _excel_import_history(db, sha) is None
    _record_excel_import(db, str(f), sha[:16] + "_journal 1404.xlsx", 2)
    hist = _excel_import_history(db, sha)
    assert hist and hist["times"] == 1 and hist["imported"] == 2 and hist["filename"] == "journal 1404.xlsx"
    assert hist["at"]
    _record_excel_import(db, str(f), sha[:16] + "_journal 1404.xlsx", 2)
    assert _excel_import_history(db, sha)["times"] == 2
    assert "already_imported" in ExcelImportPreviewResponse.model_fields
    assert "file_sha256" in ExcelImportPreviewResponse.model_fields


# ---------------------------------------------------------------------------
# 2.17 same-day rows follow creation order
# ---------------------------------------------------------------------------

def test_same_day_rows_are_ordered_by_creation_time(auth_client, db, make_transaction):
    name = f"Client order {uuid.uuid4().hex[:6]}"
    ent = auth_client.post("/entities", json={"type": "client", "name": name}).json()
    day = date(2026, 4, 10)
    t0 = datetime(2026, 4, 10, 9, 0, tzinfo=timezone.utc)
    later = make_transaction([("1112", 250_000, 0), ("4110", 0, 250_000)], tx_date=day,
                             description="second (created later)", entity_links=[("client", name)])
    earlier = make_transaction([("1112", 100_000, 0), ("4110", 0, 100_000)], tx_date=day,
                               description="first (created earlier)", entity_links=[("client", name)])
    later.created_at = t0 + timedelta(hours=2)
    earlier.created_at = t0
    db.commit()
    rows = auth_client.get(f"/reports/entities/{ent['id']}/transactions").json()
    descs = [r["description"] for r in rows if r.get("date") == day.isoformat()]
    assert descs == ["first (created earlier)", "second (created later)"]


# ---------------------------------------------------------------------------
# frontend wiring
# ---------------------------------------------------------------------------

def test_frontend_wiring_for_the_lows():
    admin = open("app/static/js/04-admin-settings.js", encoding="utf-8").read()
    i = admin.index("async function loadUsers()")
    assert "populateEntityLinkOptions()" in admin[i:i + 400]
    assert "bsFindDuplicateSame" in open("app/static/js/10-forms-fx-bank.js", encoding="utf-8").read()
    assert "excelAlreadyImported" in open("app/static/js/14-excel-import.js", encoding="utf-8").read()
    assert "invOverpaidCredit" in open("app/static/js/06-vouchers.js", encoding="utf-8").read()
    i18n = open("app/static/js/02-i18n.js", encoding="utf-8").read()
    for key in ("invOverpaidCredit:", "excelAlreadyImported:", "bsFindDuplicateSame:"):
        assert i18n.count(key) == 4, key
    assert i18n.count("{dupes} imported before") == 0
