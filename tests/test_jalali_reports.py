"""Jalali everywhere in reports (roadmap 2026-09 §3.5): an Iranian company's
budgets, monthly and seasonal trends, cash-flow periods, dashboard months,
insight comparisons, ledger groupings and payroll year follow Jalali months —
مهر ۱۴۰۵ is 23 Sep – 22 Oct 2026 — instead of cutting each of them in two."""
from __future__ import annotations

import asyncio
import uuid
from datetime import date
from pathlib import Path

import pytest

from app.models.company import Company
from app.services import calendar_periods as cp

JS = Path(__file__).resolve().parents[1] / "app" / "static" / "js"


# ─── 1. The calendar maths ───────────────────────────────────────────────────

def test_months_in_both_calendars():
    assert cp.month_key(date(2026, 9, 23), cp.JALALI) == "1405-07"
    assert cp.month_key(date(2026, 9, 22), cp.JALALI) == "1405-06"
    assert cp.bounds(1405, 7, cp.JALALI) == (date(2026, 9, 23), date(2026, 10, 22))
    assert cp.key_bounds("1405-07") == (date(2026, 9, 23), date(2026, 10, 22))
    assert cp.key_bounds("2026-09") == (date(2026, 9, 1), date(2026, 9, 30))
    assert cp.bounds(1403, 12, cp.JALALI)[1] == date(2025, 3, 20)                 # 1403 leap: Esfand has 30 days
    assert cp.calendar_of_key("1405-01") == cp.JALALI and cp.calendar_of_key("2026-01") == cp.GREGORIAN


def test_months_between_crosses_nowruz_and_labels_them():
    ps = cp.months_between(date(2026, 2, 25), date(2026, 4, 25), cp.JALALI, lang="fa")
    assert [p.key for p in ps] == ["1404-12", "1405-01", "1405-02"]
    assert ps[1].start == date(2026, 3, 21) and ps[1].label == "فروردین ۱۴۰۵"
    assert cp.month_label("1405-07") == "Mehr 1405" and cp.month_label("2026-10") == "Oct 2026"
    assert [p.key for p in cp.last_n_months(date(2026, 4, 5), 3, cp.JALALI)] == ["1404-11", "1404-12", "1405-01"]


def test_seasons_are_jalali_quarters_and_weeks_start_on_saturday():
    assert cp.quarter_key(date(2026, 6, 21), cp.JALALI) == "1405-Q1"               # last day of Khordad
    assert cp.quarter_bounds("1405-Q1") == (date(2026, 3, 21), date(2026, 6, 21))
    assert cp.quarter_label("1405-Q3", "fa") == "پاییز ۱۴۰۵" and cp.quarter_label("2026-Q3") == "Q3 2026"
    # a Gregorian month or quarter in Persian is Persian too (the close-pack picker read "Oct 2026")
    assert cp.month_label("2026-10", "fa") == "اکتبر ۲۰۲۶" and cp.month_label("2026-05", "fa") == "مه ۲۰۲۶"
    assert cp.quarter_label("2026-Q3", "fa") == "سه‌ماهه ۳ ۲۰۲۶"
    # Spanish and Arabic name their months too
    assert cp.month_label("2026-10", "es") == "oct 2026" and cp.month_label("2026-10", "ar") == "أكتوبر 2026"
    assert cp.month_label("1405-07", "ar") == "مهر 1405" and cp.month_label("1405-12", "ar") == "إسفند 1405"
    assert cp.quarter_label("1405-Q1", "es") == "Primavera 1405" and cp.quarter_label("2026-Q3", "ar") == "الربع 3 2026"
    wed = date(2026, 9, 30)
    assert cp.week_start(wed, cp.JALALI) == date(2026, 9, 26) and cp.week_start(wed, cp.GREGORIAN) == date(2026, 9, 28)
    assert cp.week_key(wed, cp.JALALI) == "1405-07-04" and cp.week_key(wed, cp.GREGORIAN) == "2026-W39"


def test_period_ends_for_each_granularity():
    a, b = date(2026, 8, 1), date(2026, 10, 5)
    assert cp.period_ends(a, b, "monthly", cp.JALALI) == [
        ("1405-05", date(2026, 8, 22)), ("1405-06", date(2026, 9, 22)), ("1405-07", date(2026, 10, 5))]
    assert cp.period_ends(a, b, "monthly", cp.GREGORIAN)[-1] == ("2026-10", date(2026, 10, 5))
    assert cp.period_ends(a, b, "seasonal", cp.JALALI) == [("1405-Q2", date(2026, 9, 22)), ("1405-Q3", date(2026, 10, 5))]
    assert [k for k, _ in cp.period_ends(a, b, "seasonal", cp.GREGORIAN)] == ["2026-Summer", "2026-Autumn"]
    weeks = cp.period_ends(date(2026, 9, 26), date(2026, 10, 9), "weekly", cp.JALALI)
    assert weeks == [("1405-07-04", date(2026, 10, 2)), ("1405-07-11", date(2026, 10, 9))]


# ─── 2. An Iranian company ─────────────────────────────────────────────────────

def _login(client, cid):
    from app.core.auth import CSRF_COOKIE, create_session_token, generate_csrf_token
    from app.core.config import settings
    from tests.conftest import _CSRFTestClient
    tok = create_session_token(user_id=str(uuid.uuid4()), username="owner", is_admin=True, role="owner", company_id=cid)
    csrf = generate_csrf_token()
    client.cookies.clear()
    client.cookies.set(settings.auth_cookie_name, tok)
    client.cookies.set(CSRF_COOKIE, csrf)
    return _CSRFTestClient(client, csrf)


@pytest.fixture()
def ir(client, db):
    from app.db.seed import seed_chart_if_empty
    from app.db.tenant import use_company
    from tests.test_admin_audit import _purge_company
    c = Company(id=uuid.uuid4(), name="Tehran Co", slug=f"ir-{uuid.uuid4().hex[:6]}", locale="ir",
                base_currency="IRR", status="active", token_version=0)
    db.add(c)
    db.commit()
    cid = str(c.id)
    with use_company(cid):
        seed_chart_if_empty(db, locale="ir")
        db.commit()
    db.expunge_all()
    api = _login(client, cid)
    # Mordad 1405 ends 22 Aug, Shahrivar runs 23 Aug – 22 Sep, Mehr starts 23 Sep.
    for when, amount in (("2026-08-20", 100), ("2026-08-25", 300), ("2026-09-20", 500), ("2026-09-25", 700)):
        r = api.post("/transactions", json={"date": when, "description": "rent", "lines": [
            {"account_code": "6112", "debit": amount, "credit": 0}, {"account_code": "1110", "debit": 0, "credit": amount}]})
        assert r.status_code == 201, r.text
    yield api, cid
    client.cookies.clear()
    _purge_company(db, cid)


def test_the_company_calendar_is_jalali_for_iran(ir, db):
    from app.db.tenant import use_company
    _api, cid = ir
    with use_company(cid):
        assert cp.company_calendar(db) == cp.JALALI


def test_a_budget_for_shahrivar_counts_shahrivar(ir):
    api, _ = ir
    from app.models.account import Account  # noqa: F401  (chart seeded by the fixture)
    name = api.get("/accounts").json()
    cat = next(a["name"] for a in name if a["code"] == "6112")
    assert api.post("/budgets", json={"month": "1405-06", "category": cat, "limit_amount": 1000}).status_code in (200, 201)
    assert api.post("/budgets", json={"month": "2026-09", "category": cat, "limit_amount": 1000}).status_code in (200, 201)
    rows = api.get("/budgets/actual-vs-budget", params={"month": "1405-06"}).json()["rows"]
    assert rows[0]["actual_amount"] == 800                                        # 25 Aug + 20 Sep
    greg = api.get("/budgets/actual-vs-budget", params={"month": "2026-09"}).json()["rows"]
    assert greg[0]["actual_amount"] == 1200                                       # a Gregorian key still means September


def test_dashboard_months_are_jalali(ir, monkeypatch):
    api, _ = ir
    import app.api.reports as reports
    real = reports.date

    class Today(real):
        @classmethod
        def today(cls):
            return real(2026, 9, 27)
    monkeypatch.setattr(reports, "date", Today)
    reports._dashboard_cache.clear()
    d = api.get("/reports/owner-dashboard").json()
    series = {r["period"]: r for r in d["monthly_expense_series"]}
    assert series["1405-06"]["value"] == 800 and series["1405-05"]["value"] == 100 and series["1405-07"]["value"] == 700
    assert series["1405-06"]["label"] == "Shahrivar 1405"


def test_trends_and_cash_flow_periods_are_jalali(ir):
    api, _ = ir
    bs = api.get("/manager-reports/financial/balance-sheet-periods",
                 params={"from_date": "2026-08-01", "to_date": "2026-09-27", "granularity": "monthly"}).json()
    assert [p["period"] for p in bs["periods"]] == ["1405-05", "1405-06", "1405-07"]
    assert bs["periods"][1]["date"] == "2026-09-22"
    seasons = api.get("/manager-reports/financial/balance-sheet-periods",
                      params={"from_date": "2026-08-01", "to_date": "2026-09-27", "granularity": "seasonal"}).json()
    assert [p["period"] for p in seasons["periods"]] == ["1405-Q2", "1405-Q3"]
    cf = api.get("/manager-reports/financial/cash-flow-periods",
                 params={"from_date": "2026-08-01", "to_date": "2026-09-27", "granularity": "monthly"})
    assert cf.status_code == 200, cf.text
    keys = [p["period"] for p in cf.json()["periods"]]
    assert set(keys) <= {"1405-05", "1405-06", "1405-07"} and "1405-06" in keys


def test_the_ledger_tool_groups_by_jalali_month(ir, db):
    from app.db.tenant import use_company
    from app.services.ai_accountant.base import ToolContext
    from app.services.ai_accountant.read_tools import QueryLedger, QueryLedgerInput
    _api, cid = ir
    with use_company(cid):
        out = asyncio.run(QueryLedger().run(ToolContext(db=db, user_id="u"), QueryLedgerInput(
            account_code="6112", group_by="month", from_date=date(2026, 8, 1), to_date=date(2026, 9, 27))))
    months = {m["month"]: m for m in out["by_month"]}
    assert months["1405-06"]["debit"] == 800 and months["1405-06"]["label"] == "Shahrivar 1405"


def test_insights_run_in_the_company_calendar_and_reset_it(ir, db):
    from app.db.tenant import use_company
    from app.services import insight_service
    _api, cid = ir
    seen = []
    real = insight_service._month_key

    def spy(d):
        seen.append(real(d))
        return seen[-1]
    insight_service._month_key = spy
    try:
        with use_company(cid):
            insight_service.compute_insights(db, today=date(2026, 9, 27), use_cache=False)
    finally:
        insight_service._month_key = real
    assert seen and all(k.startswith("140") for k in seen)
    assert insight_service._CALENDAR.get() == "gregorian"                         # reset after the run


def test_payroll_year_summary_defaults_to_the_jalali_year(ir):
    api, _ = ir
    out = api.get("/payroll/year-summary").json()
    assert out["year"] == cp.year_of(date.today(), cp.JALALI)
    assert api.get("/payroll/year-summary", params={"year": 1405}).json()["year"] == 1405


def test_the_budget_pickers_follow_the_display_calendar():
    core = (JS / "01-core.js").read_text(encoding="utf-8")
    assert "function applyCalendarMonthPickers" in core and "function currentMonthKey" in core
    assert "'budget-month', 'pd-budget-month'" in core
    manager = (JS / "05-reports-manager.js").read_text(encoding="utf-8")
    assert "new Date().toISOString().slice(0, 7)" not in manager
    assert manager.count("formatPeriodKey(p.period)") >= 5
    forms = (JS / "10-forms-fx-bank.js").read_text(encoding="utf-8")
    assert "getElementById('budget-month').addEventListener('change', loadBudgets)" in forms
    assert "mgrFromDateEl.dataset.auto = monthStartIso()" in forms
    assert "new Date().toISOString().slice(0, 10)" not in forms                    # UTC: a day early in Tehran
    from tests.i18n_source import i18n_text
    i18n = i18n_text()
    vouchers = (JS / "06-vouchers.js").read_text(encoding="utf-8")
    assert "<th>Actual</th>" not in vouchers and "t('budgetColActual')" in vouchers
    assert i18n.count("budgetColActual: ") == 4 and i18n.count("budgetNoRows: ") == 4
    assert "setLabel('budget-month', 'labelMonth')" in i18n and "forId + '-jalali\"]'" in i18n


@pytest.mark.parametrize("role", ["accountant", "manager", "employee", "viewer"])
def test_every_role_reads_the_display_calendar(client, role):
    """The calendar every page draws in: roles without settings access got
    Gregorian dates and month pickers in an Iranian company."""
    from app.core.auth import CSRF_COOKIE, create_session_token, generate_csrf_token
    from app.core.config import settings
    from tests.conftest import _CSRFTestClient
    tok = create_session_token(user_id=str(uuid.uuid4()), username=role, is_admin=False, role=role)
    csrf = generate_csrf_token()
    client.cookies.clear()
    client.cookies.set(settings.auth_cookie_name, tok)
    client.cookies.set(CSRF_COOKIE, csrf)
    api = _CSRFTestClient(client, csrf)
    try:
        r = api.get("/admin/display-calendar")
        assert r.status_code == 200 and r.json()["calendar"] in ("jalali", "gregorian")
        assert api.put("/admin/display-calendar", json={"calendar": "jalali"}).status_code == 403
    finally:
        client.cookies.clear()
