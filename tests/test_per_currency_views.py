"""Per-currency views (QA 2026-09-24, MEDIUM 2.5/2.26).

Ledger summary, account detail, trial balance and the owner dashboard used to
sum IRR and USD face values when no currency filter was given. Decision: a
report shows ONE currency (the reporting currency by default) and lists the
others present so the UI can offer them as separate views. Never summed.
"""
from __future__ import annotations

import uuid
from datetime import date, timedelta

import pytest
from sqlalchemy import func, select

from app.core.auth import CSRF_COOKIE, create_session_token, generate_csrf_token
from app.core.config import settings
from app.models.account import Account
from app.models.transaction import Transaction, TransactionLine
from tests.conftest import _CSRFTestClient

CASH, REVENUE = "1110", "4110"


def _api(client):
    tok = create_session_token(user_id=str(uuid.uuid4()), username="owner", is_admin=True, role="owner")
    csrf = generate_csrf_token()
    client.cookies.set(settings.auth_cookie_name, tok)
    client.cookies.set(CSRF_COOKIE, csrf)
    return _CSRFTestClient(client, csrf)


def _debit_turnover(db, code: str, currency: str, from_date=None, to_date=None) -> int:
    q = (
        select(func.coalesce(func.sum(TransactionLine.debit), 0))
        .join(Transaction, Transaction.id == TransactionLine.transaction_id)
        .join(Account, Account.id == TransactionLine.account_id)
        .where(Account.code == code, Transaction.currency == currency, Transaction.deleted_at.is_(None))
    )
    if from_date is not None:
        q = q.where(Transaction.date >= from_date)
    if to_date is not None:
        q = q.where(Transaction.date <= to_date)
    return int(db.execute(q).scalar() or 0)


@pytest.fixture()
def mixed_books(db, make_transaction):
    """One IRR sale and one USD sale, both recent (inside the dashboard window)."""
    day = date.today() - timedelta(days=3)
    ref = f"mixed-{uuid.uuid4().hex[:8]}"
    irr = make_transaction([(CASH, 1_000_000, 0), (REVENUE, 0, 1_000_000)], tx_date=day, reference=ref + "-IRR")
    irr.currency = "IRR"
    usd = make_transaction([(CASH, 120, 0), (REVENUE, 0, 120)], tx_date=day, reference=ref + "-USD")
    usd.currency = "USD"
    db.commit()
    from app.api.reports import _dashboard_cache
    _dashboard_cache.clear()
    return {"day": day, "ref": ref}


def test_ledger_summary_defaults_to_reporting_currency_and_lists_others(db, client, mixed_books):
    api = _api(client)
    data = api.get("/reports/ledger-summary").json()
    assert data["currency"] == "IRR"
    assert "USD" in data["other_currencies"]
    assert "IRR" not in data["other_currencies"]
    cash = next(r for r in data["rows"] if r["account_code"] == CASH)
    assert cash["debit_turnover"] == _debit_turnover(db, CASH, "IRR")  # USD 120 not folded in


def test_ledger_summary_explicit_currency_is_that_currency_only(db, client, mixed_books):
    api = _api(client)
    data = api.get("/reports/ledger-summary", params={"currency": "usd"}).json()  # case-insensitive
    assert data["currency"] == "USD"
    assert "IRR" in data["other_currencies"]
    cash = next(r for r in data["rows"] if r["account_code"] == CASH)
    assert cash["debit_turnover"] == _debit_turnover(db, CASH, "USD")
    assert data["total_debit_turnover"] == sum(r["debit_turnover"] for r in data["rows"])


def test_account_detail_is_single_currency(db, client, mixed_books):
    api = _api(client)
    ref = mixed_books["ref"]
    irr = api.get(f"/reports/accounts/{CASH}/detail").json()
    assert irr["currency"] == "IRR" and "USD" in irr["other_currencies"]
    refs = {l["reference"] for l in irr["lines"]}
    assert ref + "-IRR" in refs and ref + "-USD" not in refs
    usd = api.get(f"/reports/accounts/{CASH}/detail", params={"currency": "USD"}).json()
    refs = {l["reference"] for l in usd["lines"]}
    assert ref + "-USD" in refs and ref + "-IRR" not in refs


def test_trial_balance_is_single_currency(db, client, mixed_books):
    api = _api(client)
    day = mixed_books["day"]
    window = {"from_date": (day - timedelta(days=1)).isoformat(), "to_date": day.isoformat()}
    data = api.get("/manager-reports/books/trial-balance", params=window).json()
    assert data["currency"] == "IRR"
    assert "USD" in data["other_currencies"]
    cash = next(r for r in data["rows"] if r["account_code"] == CASH)
    assert cash["debit_turnover"] == _debit_turnover(db, CASH, "IRR", day - timedelta(days=1), day)

    usd = api.get("/manager-reports/books/trial-balance", params={**window, "currency": "USD"}).json()
    assert usd["currency"] == "USD" and "IRR" in usd["other_currencies"]
    cash = next(r for r in usd["rows"] if r["account_code"] == CASH)
    assert cash["debit_turnover"] == _debit_turnover(db, CASH, "USD", day - timedelta(days=1), day)


def test_trial_balance_other_currencies_respect_the_period(db, client, mixed_books):
    api = _api(client)
    far = date(2031, 6, 1)
    data = api.get("/manager-reports/books/trial-balance",
                   params={"from_date": far.isoformat(), "to_date": far.isoformat()}).json()
    assert data["currency"] == "IRR"
    assert data["other_currencies"] == []  # no USD vouchers in that window


def test_owner_dashboard_is_single_currency(db, client, mixed_books):
    api = _api(client)
    data = api.get("/reports/owner-dashboard").json()
    assert data["currency"] == "IRR"
    assert "USD" in data["other_currencies"]
    cash_kpi = next(k for k in data["kpis"] if k["key"] == "cash_on_hand")
    assert cash_kpi["unit"] == "IRR"

    from app.api.reports import _dashboard_cache
    _dashboard_cache.clear()
    usd = api.get("/reports/owner-dashboard", params={"currency": "USD"}).json()
    assert usd["currency"] == "USD" and "IRR" in usd["other_currencies"]
    assert next(k for k in usd["kpis"] if k["key"] == "cash_on_hand")["unit"] == "USD"


def test_frontend_has_currency_view_controls():
    html = open("app/static/index.html", encoding="utf-8").read()
    assert 'id="ledger-currency"' in html
    assert 'id="ledger-currency-note"' in html
    assert 'id="dash-currency-note"' in html
    js = open("app/static/js/03-ui.js", encoding="utf-8").read()
    assert "function renderCurrencyViewNote" in js
    ledger_js = open("app/static/js/09-ledger.js", encoding="utf-8").read()
    assert "'/reports/ledger-summary' + (ccy ? ('?currency='" in ledger_js
    i18n = open("app/static/js/02-i18n.js", encoding="utf-8").read()
    assert i18n.count("currencyViewNote:") == 4 and i18n.count("currencyViewOnly:") == 4
