"""Budgets in each currency (scenarios AS1–AS7, docs/PLAN_CURRENCIES.md phase 4).

A budget was in the books' currency and counted spending at its base value: Omani rials with no rate
never touched it."""
from __future__ import annotations

from datetime import date
from decimal import Decimal

from tests.test_personal_money import API, person, scripted  # noqa: F401 — fixtures
from tests.test_reviews_by_currency import _in, _post

TODAY = date.today()


def _month(db, cid):
    from app.services.calendar_periods import company_calendar, month_key
    with _in(cid):
        return month_key(TODAY, company_calendar(db))


def _dining(db, cid):
    """41.500 OMR on dining from the wallet, and 3,000,000 IRR on dining from the bank, this month."""
    _post(db, cid, TODAY, "رستوران", "6180", "1120", Decimal("41.500"), "OMR")
    _post(db, cid, TODAY, "رستوران", "6180", "1110", 3_000_000, "IRR")


def test_as1_as2_a_budget_in_omani_rials_counts_omani_rials(person, db):
    cid = person["cid"]
    _dining(db, cid)
    api = person["web"]()
    month = _month(db, cid)
    r = api.post("/budgets", json={"month": month, "category": "6180", "limit_amount": 50, "currency": "omr"})
    assert r.status_code == 201, r.text
    assert r.json()["currency"] == "OMR"
    rows = api.get("/budgets/actual-vs-budget", params={"month": month}).json()["rows"]
    assert [(x["currency"], x["actual_amount"], x["limit_amount"], x["utilization_pct"]) for x in rows] == [
        ("OMR", 41.5, 50, 83.0)]
    # AS2: the rials' budget for the same category is another one
    assert api.post("/budgets", json={"month": month, "category": "6180", "limit_amount": 5_000_000}).status_code == 201
    rows = api.get("/budgets/actual-vs-budget", params={"month": month}).json()["rows"]
    assert sorted((x["currency"], x["actual_amount"], x["limit_amount"]) for x in rows) == [
        ("IRR", 3_000_000, 5_000_000), ("OMR", 41.5, 50)]
    # setting the Omani-rial one again changes only it
    api.post("/budgets", json={"month": month, "category": "6180", "limit_amount": 60, "currency": "OMR"})
    listed = api.get("/budgets", params={"month": month}).json()
    assert sorted((b["currency"], b["limit_amount"]) for b in listed) == [("IRR", 5_000_000), ("OMR", 60)]
    # baisa: three places, not four
    bad = api.post("/budgets", json={"month": month, "category": "6180", "limit_amount": "10.1234", "currency": "OMR"})
    assert bad.status_code == 400                                         # as for a voucher


def test_as3_set_from_the_chat(person, db, scripted):
    from tests.test_ai_guardrails import _calls, _text
    cid = person["cid"]
    phone = person["phone"]()
    scripted(_calls(("propose_set_budget", {"category": "6180", "amount": "50", "currency": "OMR"})),
             _text("کارت بودجه آماده است."))
    reply = person["client"].post(f"{API}/chat", headers=phone, json={"message": "برای رستوران ماهی ۵۰ ریال عمان بودجه بذار"}).json()
    card = next(b for b in reply["blocks"] if b["type"] == "proposal")
    assert card["amount"]["currency"] == "OMR"
    r = person["client"].post(f"{API}/proposals/{card['token']}/confirm", headers=phone, json={})
    assert r.status_code == 200, r.text
    from app.models.budget import BudgetLimit
    from sqlalchemy import select
    with _in(cid):
        db.expire_all()
        [b] = db.execute(select(BudgetLimit)).scalars().all()
    assert (b.category, b.limit_amount, b.currency) == ("تفریح و رستوران", 50, "OMR")


def test_as4_the_reviews_idea_in_its_own_currency(person, db):
    from datetime import timedelta
    from tests.test_proactive_reviews import MONDAY, _make, _reviews
    from tests.test_reviews_by_currency import LAST, _usual_omani_weeks
    cid = person["cid"]
    _usual_omani_weeks(db, cid)
    _post(db, cid, LAST, "شام تولد", "6180", "1120", Decimal("40"), "OMR")
    assert _make(db, cid) == ["weekly"]
    [review] = _reviews(db, cid)
    [idea] = review.facts["suggestions"]
    assert idea["key"] == "hot_category" and idea["params"]["currency"] == "OMR"
    from app.services.proactive_reviews import render
    fa = render(review, "fa")
    assert "ریال عمان" in fa["choices"][0] and "ریال ایران" not in fa["choices"][0]


def test_as5_the_alert_names_its_currency(person, db):
    cid = person["cid"]
    _dining(db, cid)
    from app.models.budget import BudgetLimit
    with _in(cid):
        db.add(BudgetLimit(month=_month(db, cid), category="6180", limit_amount=Decimal("45"), currency="OMR"))
        db.commit()
    from app.models.notification import Notification
    from app.services.notification_service import refresh_notifications
    from sqlalchemy import select
    with _in(cid):
        refresh_notifications(db)
        db.commit()
        rows = db.execute(select(Notification).where(Notification.kind == "budget")).scalars().all()
    [n] = rows
    assert "OMR" in n.message and n.params["actual"] == "41.50 OMR" and n.params["limit"] == "45 OMR"


def test_as6_rolled_forward_in_its_own_currency(person, db):
    cid = person["cid"]
    from app.models.budget import BudgetLimit
    from app.services.budget_service import _key_add, roll_forward
    month = _month(db, cid)
    with _in(cid):
        db.add(BudgetLimit(month=month, category="6180", limit_amount=Decimal("50.125"), currency="OMR"))
        db.add(BudgetLimit(month=month, category="6180", limit_amount=5_000_000))
        db.commit()
        out = roll_forward(db, from_month=month, months=1, change_pct=10)
        db.commit()
        from sqlalchemy import select
        nxt = db.execute(select(BudgetLimit).where(BudgetLimit.month == _key_add(month, 1))).scalars().all()
    assert out["created"] == 2
    assert sorted((b.currency or "IRR", b.limit_amount) for b in nxt) == [("IRR", 5_500_000), ("OMR", Decimal("55.138"))]


def test_as6_the_page_offers_the_currency():
    from pathlib import Path
    html = Path("app/static/index.html").read_text()
    js = Path("app/static/js/05-reports-manager.js").read_text()
    assert 'id="pd-budget-currency"' in html and "...(currency ? { currency } : {})" in js


def test_as7_a_company_as_before(books, db):
    """A company's base-currency budget counts every expense at its base value: £200 of rent and $100 at 0.8."""
    from tests.test_fx_base_readers import TODAY as T
    from app.models.budget import BudgetLimit
    from app.services.budget_service import budget_utilization
    _api, cid = books
    month = f"{T.year:04d}-{T.month:02d}"
    with _in(cid):
        db.add(BudgetLimit(month=month, category="7200", limit_amount=1000))
        db.commit()
        [row] = budget_utilization(db, month)
    assert (row["currency"], row["actual_amount"]) == ("GBP", 280)


from tests.test_fx_base_readers import books, uk  # noqa: E402,F401 — fixtures
