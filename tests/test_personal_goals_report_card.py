"""Savings goals and the monthly report card for personal books (roadmap §4.12)."""
from __future__ import annotations

import uuid
from datetime import date

import pytest

from app.db.tenant import use_company

TODAY = date(2026, 9, 29)          # 7 Mehr 1405


@pytest.fixture()
def me(client, db):
    from app.core.auth import CSRF_COOKIE, create_session_token, generate_csrf_token
    from app.core.config import settings
    from app.services.company_service import provision_company
    from tests.conftest import _CSRFTestClient
    from tests.test_admin_audit import _purge_company
    company, user = provision_company(db, name="My Money", locale="ir", base_currency="IRR",
                                      username=f"me-{uuid.uuid4().hex[:6]}", password="personalpass123", kind="personal")
    db.commit()
    cid, uid, uname = str(company.id), str(user.id), user.username
    csrf = generate_csrf_token()
    client.cookies.clear()
    client.cookies.set(settings.auth_cookie_name, create_session_token(
        user_id=uid, username=uname, is_admin=False, role="personal", company_id=cid))
    client.cookies.set(CSRF_COOKIE, csrf)
    db.expunge_all()
    yield _CSRFTestClient(client, csrf), cid
    client.cookies.clear()
    _purge_company(db, cid)


def _post(api, d, desc, dr, cr, amount):
    r = api.post("/transactions", json={"date": d, "description": desc, "currency": "IRR", "lines": [
        {"account_code": dr, "debit": amount, "credit": 0}, {"account_code": cr, "debit": 0, "credit": amount}]})
    assert r.status_code == 201, r.text


# Khordad, Tir, Mordad, then Shahrivar 1405 — salary into the bank, spending out of it
BOOKS = [
    ("2026-06-01", "salary", "1110", "4110", 100_000_000), ("2026-06-05", "groceries", "6110", "1110", 60_000_000),
    ("2026-07-01", "salary", "1110", "4110", 100_000_000), ("2026-07-05", "groceries", "6110", "1110", 50_000_000),
    ("2026-08-01", "salary", "1110", "4110", 100_000_000), ("2026-08-05", "groceries", "6110", "1110", 30_000_000),
    ("2026-08-06", "rent", "6120", "1110", 40_000_000),
    ("2026-09-01", "salary", "1110", "4110", 120_000_000), ("2026-09-03", "groceries", "6110", "1110", 45_000_000),
    ("2026-09-04", "rent", "6120", "1110", 40_000_000), ("2026-09-10", "dinner out", "6180", "1110", 10_000_000),
]


def _books(api):
    for row in BOOKS:
        _post(api, *row)


def _name(db, cid, code):
    from sqlalchemy import select

    from app.models.account import Account
    with use_company(cid):
        return db.execute(select(Account.name).where(Account.code == code)).scalar_one()


# --- the report card -----------------------------------------------------------------------------------------------

def test_the_report_card_for_shahrivar(me, db):
    from app.models.budget import BudgetLimit
    from app.services.report_card import report_card
    api, cid = me
    _books(api)
    food, rent, dining = _name(db, cid, "6110"), _name(db, cid, "6120"), _name(db, cid, "6180")
    with use_company(cid):
        db.add(BudgetLimit(month="1405-06", category=food, limit_amount=40_000_000))
        db.commit()
        card = report_card(db, "1405-06", today=TODAY)
    assert (card["label"], card["from_date"], card["to_date"], card["in_progress"]) == (
        "شهریور ۱۴۰۵", "2026-08-23", "2026-09-22", False)
    assert (card["income"], card["spending"], card["saved"], card["savings_rate"]) == (
        120_000_000, 95_000_000, 25_000_000, 20.8)
    assert card["previous"] == {"month": "1405-05", "income": 100_000_000, "spending": 70_000_000,
                                "saved": 30_000_000, "savings_rate": 30.0}
    assert card["average_spending_3m"] == 60_000_000                         # (70 + 50 + 60) / 3
    assert card["categories"] == [
        {"category": food, "amount": 45_000_000, "previous": 30_000_000, "change_pct": 50.0},
        {"category": rent, "amount": 40_000_000, "previous": 40_000_000, "change_pct": 0.0},
        {"category": dining, "amount": 10_000_000, "previous": 0, "change_pct": None},
    ]
    assert card["biggest_rise"] == {"category": food, "increase": 15_000_000}
    assert card["budgets"] == {"set": 1, "kept": 0, "over": [food]}
    assert card["net_worth"] == {"start": 120_000_000, "end": 145_000_000, "change": 25_000_000}
    checks = {c["key"]: c for c in card["checks"]}
    assert (checks["saved"]["ok"], checks["spending"]["ok"], checks["budgets"]["ok"]) == (True, False, False)
    assert checks["spending"]["detail"] == "۹۵,۰۰۰,۰۰۰ در برابر میانگین سه‌ماهه ۶۰,۰۰۰,۰۰۰."
    assert food in checks["budgets"]["detail"] and (card["passed"], card["scored"]) == (1, 3)
    with use_company(cid):
        en = report_card(db, "1405-06", lang="en", today=TODAY)
    assert en["label"] == "Shahrivar 1405" and en["checks"][0]["detail"] == "You kept 20.8% of what came in."


def test_a_first_month_and_one_in_progress(me, db):
    from app.services.report_card import report_card
    api, cid = me
    _post(api, "2026-09-25", "salary", "1110", "4110", 50_000_000)                     # Mehr, so far
    with use_company(cid):
        card = report_card(db, "1405-07", today=TODAY)
        empty = report_card(db, "1405-01", today=TODAY)
    assert card["in_progress"] and card["note"] and card["income"] == 50_000_000
    checks = {c["key"]: c["ok"] for c in card["checks"]}
    assert checks == {"saved": True, "spending": None, "budgets": None}             # nothing to compare yet
    assert card["scored"] == 1
    assert empty["savings_rate"] is None and {c["key"]: c["ok"] for c in empty["checks"]}["saved"] is None


def test_the_report_card_route(me):
    api, _cid = me
    _books(api)
    r = api.get("/personal/report-card", params={"month": "1405-06"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["saved"] == 25_000_000 and len(body["months"]) == 12 and body["lang"] == "fa"
    assert api.get("/personal/report-card", params={"month": "1405-06", "lang": "en"}).json()["label"] == "Shahrivar 1405"
    # Spanish and Arabic readers get their own words, not the English
    es = api.get("/personal/report-card", params={"month": "1405-06", "lang": "es"}).json()
    assert es["lang"] == "es" and es["checks"][0]["item"].startswith("Ahorraste al menos")
    ar = api.get("/personal/report-card", params={"month": "1405-06", "lang": "ar"}).json()
    assert ar["label"] == "شهريور 1405" and ar["checks"][0]["item"].startswith("ادخرت")
    assert api.get("/personal/report-card").status_code == 200                        # last month by default
    for bad in ("1405-13", "1405-6", "abc"):
        assert api.get("/personal/report-card", params={"month": bad}).status_code == 422, bad


# --- savings goals -------------------------------------------------------------------------------------------------

def test_a_goal_tracks_its_account(me, db):
    from sqlalchemy import select

    from app.models.savings_goal import SavingsGoal
    from app.services.personal_goals import pace_start, progress
    api, cid = me
    _books(api)
    r = api.post("/personal/goals", json={"name": "Emergency fund", "account_code": "1110",
                                          "target_amount": 300_000_000, "target_date": "2027-03-20"})
    assert r.status_code == 201, r.text
    assert r.json()["current"] == 145_000_000 and r.json()["percent"] == 48.3
    with use_company(cid):
        g = db.execute(select(SavingsGoal)).scalar_one()
        (p,) = progress(db, [g], today=TODAY)
        assert pace_start(TODAY, "jalali") == date(2026, 6, 28)                     # 7 Tir: three months back
    # Mehr to Esfand: six months, this one included; 40 M in the bank three months ago
    assert (p["remaining"], p["months_left"], p["needed_per_month"]) == (155_000_000, 6, 25_833_334)
    assert p["pace_per_month"] == 35_000_000 and p["on_track"] is True and p["months_to_go"] == 5
    assert p["account_name"] and not p["reached"]


def test_goals_without_a_date_reached_or_overdue(me, db):
    from sqlalchemy import select

    from app.models.savings_goal import SavingsGoal
    from app.services.personal_goals import progress
    api, cid = me
    _books(api)
    for body in ({"name": "Someday", "account_code": "1110", "target_amount": 1_000_000_000},
                 {"name": "Done", "account_code": "1110", "target_amount": 100_000_000},
                 {"name": "Late", "account_code": "1110", "target_amount": 500_000_000, "target_date": "2026-09-01"}):
        assert api.post("/personal/goals", json=body).status_code == 201
    with use_company(cid):
        rows = {x["name"]: x for x in progress(db, list(db.execute(select(SavingsGoal)).scalars()), today=TODAY)}
    assert rows["Someday"]["on_track"] is None and rows["Someday"]["needed_per_month"] is None
    assert rows["Done"]["reached"] and rows["Done"]["percent"] == 100.0 and rows["Done"]["months_to_go"] == 0
    assert rows["Late"]["months_left"] == 0 and rows["Late"]["on_track"] is False
    assert rows["Late"]["needed_per_month"] == rows["Late"]["remaining"]


def test_editing_archiving_and_what_a_goal_refuses(me):
    api, _cid = me
    g = api.post("/personal/goals", json={"name": "Car", "account_code": "1110", "target_amount": 900_000_000,
                                          "target_date": "2027-06-01"}).json()
    r = api.patch(f"/personal/goals/{g['id']}", json={"target_amount": 800_000_000, "clear_target_date": True})
    assert r.status_code == 200 and (r.json()["target_amount"], r.json()["target_date"]) == (800_000_000, None)
    assert api.patch(f"/personal/goals/{g['id']}", json={"archived": True}).json()["archived"] is True
    assert api.get("/personal/goals").json() == []
    assert [x["name"] for x in api.get("/personal/goals", params={"include_archived": True}).json()] == ["Car"]
    for body, detail in (({"name": "x", "account_code": "6110", "target_amount": 1}, "asset account"),
                         ({"name": "x", "account_code": "9999", "target_amount": 1}, "No account"),
                         ({"name": " ", "account_code": "1110", "target_amount": 1}, "name")):
        r = api.post("/personal/goals", json=body)
        assert r.status_code == 422 and detail in r.json()["detail"], body
    assert api.post("/personal/goals", json={"name": "x", "account_code": "1110", "target_amount": 0}).status_code == 422
    r = api.patch(f"/personal/goals/{g['id']}", json={"account_code": "4110"})
    assert r.status_code == 422
    assert api.get("/personal/goals", params={"include_archived": True}).json()[0]["account_code"] == "1110"  # untouched
    assert api.delete(f"/personal/goals/{g['id']}").status_code == 204
    assert api.delete(f"/personal/goals/{g['id']}").status_code == 404
    assert api.patch(f"/personal/goals/{uuid.uuid4()}", json={"name": "y"}).status_code == 404


def test_who_may_see_and_change_goals():
    from app.core.auth import SessionUser
    from app.core.permissions import user_can_access
    for role, read, write in (("personal", True, True), ("owner", True, True), ("viewer", True, False),
                              ("employee", False, False), ("manager", False, False)):
        u = SessionUser(user_id="u", username="u", role=role, is_admin=False)
        assert user_can_access(u, "GET", "/personal/goals") is read, role
        assert user_can_access(u, "GET", "/personal/report-card") is read, role
        assert user_can_access(u, "POST", "/personal/goals") is write, role
        assert user_can_access(u, "DELETE", "/personal/goals/{goal_id}") is write, role


# --- the chat and the page -----------------------------------------------------------------------------------------

def test_the_personal_chat_reads_the_card_and_the_goals(me, db):
    import asyncio

    from app.services.ai_accountant.base import ToolContext, ToolError
    from app.services.ai_accountant.orchestrator import PERSONAL_MODE_ADDENDUM, build_default_registry
    from app.services.ai_accountant.personal_tools import (
        GetReportCard, GetReportCardInput, GetSavingsGoals, GetSavingsGoalsInput,
    )
    api, cid = me
    _books(api)
    api.post("/personal/goals", json={"name": "Emergency fund", "account_code": "1110", "target_amount": 300_000_000})
    with use_company(cid):
        ctx = ToolContext(db=db, user_id="u1", username="me")
        card = asyncio.run(GetReportCard().run(ctx, GetReportCardInput(month="1405-06")))
        goals = asyncio.run(GetSavingsGoals().run(ctx, GetSavingsGoalsInput()))
        with pytest.raises(ToolError):
            asyncio.run(GetReportCard().run(ctx, GetReportCardInput(month="1405-13")))
    assert card["saved"] == 25_000_000 and card["savings_rate"] == 20.8
    assert goals["count"] == 1 and goals["goals"][0]["current"] == 145_000_000
    assert "``get_report_card``" in PERSONAL_MODE_ADDENDUM and "``get_savings_goals``" in PERSONAL_MODE_ADDENDUM
    assert "get_report_card" not in {t.name for t in build_default_registry()}          # a person's tools only


def test_the_panels_are_wired():
    from tests.i18n_source import i18n_text
    html = open("app/static/index.html", encoding="utf-8").read()
    for el in ('id="pd-report-card"', 'id="rc-month"', 'id="rc-checks"', 'id="pd-goals"', 'id="goals-list"',
               'id="goal-save"', 'id="goal-account"'):
        assert el in html, el
    js = open("app/static/js/05-reports-manager.js", encoding="utf-8").read()
    load = js[js.index("async function loadPersonalDashboard()"):js.index("function rcLang()")]
    assert "loadReportCard();" in load and "loadGoals();" in load
    assert "'/personal/report-card?'" in js and "'/personal/goals'" in js
    text = i18n_text()
    for k in ("rcTitle", "rcIncome", "rcSpending", "rcSaved", "rcRate", "rcLastMonth", "rcAverage", "rcTopCategories",
              "rcBiggestRise", "rcNetWorth", "rcFailed", "goalsTitle", "goalsEmpty", "goalAdd", "goalName", "goalAccount",
              "goalTarget", "goalDate", "goalSave", "goalSaved", "goalFailed", "goalMissing", "goalDelete",
              "goalDeleteConfirm", "goalReached", "goalOnTrack", "goalBehind", "goalProgress", "goalNeeded", "goalPace"):
        assert text.count(f"{k}:") == 4, k
