"""The rest of the readers on base values (roadmap 2026-09 §4.6, part 4):
anything that totals money without naming a currency — the AI tools, cash on
hand, budgets, net worth, insights, the CFO figures, statements called with
no currency — adds every currency at its base value instead of adding dollars
to pounds as raw numbers. Bank statements compare with entries in their own
currency. The dashboard gains the combined view, and entries saved as IRR by
the old default can be relabelled."""
from __future__ import annotations

import asyncio
import uuid
from datetime import date, timedelta

import pytest
from sqlalchemy import select

from app.models.company import Company
from app.models.exchange_rate import ExchangeRate
from app.models.transaction import Transaction


def _login(client, cid, role="owner"):
    from app.core.auth import CSRF_COOKIE, create_session_token, generate_csrf_token
    from app.core.config import settings
    from tests.conftest import _CSRFTestClient
    tok = create_session_token(user_id=str(uuid.uuid4()), username=role, is_admin=role == "owner", role=role,
                               company_id=cid)
    csrf = generate_csrf_token()
    client.cookies.clear()
    client.cookies.set(settings.auth_cookie_name, tok)
    client.cookies.set(CSRF_COOKIE, csrf)
    return _CSRFTestClient(client, csrf)


@pytest.fixture()
def uk(client, db):
    from app.db.seed import seed_chart_if_empty
    from app.db.tenant import use_company
    from tests.test_admin_audit import _purge_company
    c = Company(id=uuid.uuid4(), name="Sterling Ltd", slug=f"gb-{uuid.uuid4().hex[:6]}", locale="uk",
                base_currency="GBP", status="active", token_version=0)
    db.add(c)
    db.commit()
    cid = str(c.id)
    with use_company(cid):
        seed_chart_if_empty(db, locale="uk")
        db.commit()
    db.expunge_all()
    yield _login(client, cid), cid
    client.cookies.clear()
    db.rollback()
    db.execute(ExchangeRate.__table__.delete().where(ExchangeRate.company_id.is_(None)))
    db.commit()
    _purge_company(db, cid)


TODAY = date.today()


def _rate(api, fc, rate, when, tc="GBP"):
    r = api.post("/fx/rates", json={"from_currency": fc, "to_currency": tc, "rate": rate, "effective_date": when})
    assert r.status_code == 201, r.text


def _post(api, lines, currency=None, when=None):
    body = {"date": (when or TODAY).isoformat(), "description": "t",
            "lines": [{"account_code": c, "debit": d, "credit": k} for c, d, k in lines]}
    if currency:
        body["currency"] = currency
    r = api.post("/transactions", json=body)
    assert r.status_code == 201, r.text
    return r.json()


@pytest.fixture()
def books(uk):
    """£1,000 of sales and £200 of rent, $500 of sales at 0.8 (£400) and
    $100 of rent (£80), and AED 300 of sales with no rate (left out)."""
    api, cid = uk
    _rate(api, "USD", 0.8, (TODAY - timedelta(days=90)).isoformat())
    _post(api, [("1200", 1000, 0), ("4000", 0, 1000)])
    _post(api, [("7200", 200, 0), ("1200", 0, 200)])
    _post(api, [("1200", 500, 0), ("4000", 0, 500)], "USD")
    _post(api, [("7200", 100, 0), ("1200", 0, 100)], "USD")
    _post(api, [("1200", 300, 0), ("4000", 0, 300)], "AED")
    return api, cid


def _tool(tool, db, cid, **kw):
    from app.db.tenant import use_company
    from app.services.ai_accountant.base import ToolContext
    with use_company(cid):
        return asyncio.run(tool().run(ToolContext(db=db, user_id="u"), tool.InputSchema(**kw)))


# ─── 1. The AI tools ─────────────────────────────────────────────────────

def test_the_ledger_tool_adds_currencies_at_base_value_unless_asked_for_one(books, db):
    from app.services.ai_accountant.read_tools import QueryLedger
    _api, cid = books
    allv = _tool(QueryLedger, db, cid, account_code="4000")
    assert allv["currency"] == "GBP" and allv["total_credit"] == 1400      # 1000 + 400, the AED left out
    assert "AED" in allv["unconverted"]
    usd = _tool(QueryLedger, db, cid, account_code="4000", currency="usd")
    assert usd["currency"] == "USD" and usd["total_credit"] == 500 and "unconverted" not in usd
    sample = _tool(QueryLedger, db, cid, account_code="4000", currency="USD")["sample"][0]
    assert sample["currency"] == "USD" and sample["base_credit"] == 400


def test_the_balance_cash_and_spending_tools_are_in_the_base_currency(books, db):
    from app.services.ai_accountant.cash_tools import GetCashPosition
    from app.services.ai_accountant.read_tools import GetAccountBalance
    from app.services.ai_accountant.spending_tools import GetSpendingSummary
    _api, cid = books
    bal = _tool(GetAccountBalance, db, cid, account_code="1200")
    assert bal["currency"] == "GBP" and bal["balance"] == 1000 - 200 + 400 - 80
    assert _tool(GetAccountBalance, db, cid, account_code="1200", currency="USD")["balance"] == 400
    cash = _tool(GetCashPosition, db, cid)
    assert cash["currency"] == "GBP" and cash["total"] == 1120 and "AED" in cash["unconverted"]
    spent = _tool(GetSpendingSummary, db, cid, period="this_year")
    assert spent["total"] == 280 and spent["currency"] == "GBP"                # £200 + $100 at 0.8


# ─── 2. Services that named no currency ────────────────────────────────────────

def test_cash_budget_net_worth_and_statements_use_base_values(books, db):
    from app.db.tenant import use_company
    from app.services.budget_service import expense_actuals_by_category
    from app.services.cash_service import cash_on_hand
    from app.services.net_worth_service import _balances_by_account
    from app.services.reporting.financial_statement_service import build_income_statement
    _api, cid = books
    with use_company(cid):
        assert cash_on_hand(db, locale="uk") == 1120
        assert cash_on_hand(db, locale="uk", currency="USD") == 400
        assert cash_on_hand(db, locale="uk", currency="AED") == 300
        actual = expense_actuals_by_category(db, TODAY.strftime("%Y-%m"))
        assert sum(actual.values()) == 280
        assert _balances_by_account(db, TODAY)["1200"] == 1120
        pl = build_income_statement(db, from_date=date(TODAY.year, 1, 1), to_date=TODAY)
        assert "1400" in pl.model_dump_json().replace(",", "")                     # revenue at base value


def test_the_cfo_figures_are_at_base_value(books, db):
    from app.db.tenant import use_company
    from app.services.cfo_intelligence import _load_monthly_data
    _api, cid = books
    with use_company(cid):
        data = _load_monthly_data(db)
    assert sum(data["monthly_revenue"].values()) == 1400
    assert sum(data["monthly_expense"].values()) == 280


# ─── 3. Bank statements stay in their own currency ────────────────────────────

def test_a_dollar_statement_is_checked_against_dollar_entries(books, db):
    from app.db.tenant import use_company
    from app.services.statement_review import book_balance_as_of
    _api, cid = books
    with use_company(cid):
        assert book_balance_as_of(db, "1200", TODAY, currency="USD") == 400
        assert book_balance_as_of(db, "1200", TODAY, currency="GBP") == 800


def test_reconciliation_matches_only_the_statements_currency(books, db):
    from app.db.tenant import use_company
    from app.models.bank_statement import BankStatementRow
    from app.services.reconciliation import reconcile_statement
    _api, cid = books
    row = BankStatementRow(tx_date=TODAY, description="t", debit=0, credit=500, row_index=0)
    with use_company(cid):
        assert reconcile_statement(db, [row], currency="USD")[0].status != "unmatched"
        assert reconcile_statement(db, [row], currency="GBP")[0].status == "unmatched"   # no £500 receipt


def test_recurring_suggestions_come_from_base_currency_entries_only(uk, db):
    from app.db.tenant import use_company
    from app.services.recurring_detection import detect_recurring
    api, cid = uk
    _rate(api, "USD", 0.8, (TODAY - timedelta(days=200)).isoformat())
    for months in (3, 2, 1):
        when = TODAY - timedelta(days=30 * months)
        body = {"date": when.isoformat(), "description": "Cloud hosting", "currency": "USD",
                "lines": [{"account_code": "7600", "debit": 50, "credit": 0},
                          {"account_code": "1200", "debit": 0, "credit": 50}]}
        assert api.post("/transactions", json=body).status_code == 201
    with use_company(cid):
        assert not [c for c in detect_recurring(db) if "hosting" in (c.name or "").lower()]


# ─── 4. The dashboard ───────────────────────────────────────────────────────

def test_the_dashboard_has_a_combined_view(books):
    api, _ = books
    one = api.get("/reports/owner-dashboard").json()
    assert one["currency"] == "GBP"
    cash = {k["key"]: k for k in one["kpis"]}["cash_on_hand"]
    assert cash["value"] == 800
    allv = api.get("/reports/owner-dashboard", params={"currency": "ALL"}).json()
    kpis = {k["key"]: k for k in allv["kpis"]}
    assert allv["currency"] == "ALL" and set(allv["other_currencies"]) == {"AED", "GBP", "USD"}
    assert kpis["cash_on_hand"]["value"] == 1120 and kpis["cash_on_hand"]["unit"] == "GBP"


# ─── 5. Entries saved as IRR by the old default ─────────────────────────────────

def test_irr_entries_in_a_pound_book_can_be_relabelled(uk, db):
    from app.db.tenant import use_company
    api, cid = uk
    wrong = _post(api, [("1200", 250, 0), ("4000", 0, 250)], "IRR")
    old = _post(api, [("1200", 70, 0), ("4000", 0, 70)], "IRR", when=date(2025, 1, 10))
    inv = api.post("/invoices", json={"number": f"R-{uuid.uuid4().hex[:5]}", "kind": "sales", "issue_date": TODAY.isoformat(),
                                      "due_date": TODAY.isoformat(), "amount": 40, "currency": "IRR"}).json()
    assert api.put("/admin/closed-period", json={"closed_period": "2025-12-31"}).status_code == 200
    meta = api.get("/fx/metadata").json()
    assert "IRR" in meta["unconverted"]["currencies"]
    dry = api.post("/fx/relabel", json={"from_currency": "IRR"}).json()
    assert dry == {"from_currency": "IRR", "to_currency": "GBP", "entries": 2, "locked": 1, "invoices": 1,
                   "applied": False}                                    # the invoice's own entry counts too
    assert api.post("/fx/relabel", json={"from_currency": "GBP"}).status_code == 400
    done = api.post("/fx/relabel", json={"from_currency": "IRR", "apply": True}).json()
    assert done["applied"] is True and done["entries"] == 2
    db.expire_all()
    with use_company(cid):
        t = db.get(Transaction, uuid.UUID(wrong["id"]))
        assert t.currency == "GBP" and t.fx_rate == 1 and {ln.base_debit for ln in t.lines} == {250, 0}
        assert db.get(Transaction, uuid.UUID(old["id"])).currency == "IRR"   # closed period untouched
        from app.models.invoice import Invoice
        assert db.get(Invoice, uuid.UUID(inv["id"])).currency == "GBP"


def test_only_those_who_manage_settings_may_relabel(uk, client):
    _api, cid = uk
    viewer = _login(client, cid, role="viewer")
    assert viewer.post("/fx/relabel", json={"from_currency": "IRR"}).status_code == 403


def test_an_entry_written_without_a_currency_is_in_the_base_currency(uk, db):
    from app.db.tenant import use_company
    from app.models.account import Account
    from app.models.transaction import TransactionLine
    _api, cid = uk
    with use_company(cid):
        bank, sales = (db.execute(select(Account.id).where(Account.code == c)).scalar_one() for c in ("1200", "4000"))
        t = Transaction(date=TODAY, description="direct, no currency")
        db.add(t)
        db.flush()
        db.add_all([TransactionLine(transaction_id=t.id, account_id=bank, debit=9, credit=0),
                    TransactionLine(transaction_id=t.id, account_id=sales, debit=0, credit=9)])
        db.commit()
        assert t.currency == "GBP" and t.fx_rate == 1                        # not the old column default "IRR"
