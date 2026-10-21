"""Net worth and savings goals in each currency (scenarios AR1–AR6, docs/PLAN_CURRENCIES.md phase 3).

Net worth added everything up in the books' currency at its base value, and a goal counted its account
the same way: Omani rials with no rate counted as nothing, and a goal saving into an Omani-rial wallet
read 0%."""
from __future__ import annotations

import uuid
from datetime import date, timedelta
from decimal import Decimal

from tests.test_personal_money import API, person, scripted  # noqa: F401 — fixtures
from tests.test_reviews_by_currency import _in, _post

TODAY = date.today()


def _books(db, cid):
    """10,000,000 IRR at the bank, 100 OMR in the wallet, 30 OMR owed to a friend."""
    _post(db, cid, TODAY, "موجودی اولیه", "1110", "3110", 10_000_000, "IRR")
    _post(db, cid, TODAY, "موجودی اولیه", "1120", "3110", Decimal("100"), "OMR")
    _post(db, cid, TODAY, "قرض از دوست", "1120", "2130", Decimal("30"), "OMR")
    _post(db, cid, TODAY, "خرج", "6180", "1120", Decimal("30"), "OMR")


def test_ar1_net_worth_per_currency(person, db):
    cid = person["cid"]
    _books(db, cid)
    from app.services.net_worth_service import compute_net_worth, net_worth_by_currency
    with _in(cid):
        every = net_worth_by_currency(db)
        omr = compute_net_worth(db, currency="OMR", with_trend=True)
    assert [(x.currency, x.net_worth, x.total_assets, x.total_liabilities) for x in every] == [
        ("IRR", 10_000_000, 10_000_000, 0), ("OMR", 70, 100, 30)]
    assert omr.trend[-1][1] == 70
    api = person["web"]()
    d = api.get("/personal/net-worth").json()
    assert (d["currency"], d["net_worth"]) == ("IRR", 10_000_000)
    assert [(x["currency"], x["name"], x["net_worth"]) for x in d["by_currency"]] == [
        ("IRR", "Iranian rial", 10_000_000), ("OMR", "Omani rial", 70)]
    d = api.get("/personal/net-worth", params={"currency": "OMR"}).json()
    assert (d["currency"], d["net_worth"], d["total_liabilities"]) == ("OMR", 70, 30)


def test_ar2_asked_in_the_chat(person, db, scripted):
    _books(db, person["cid"])
    r = person["client"].post(f"{API}/chat", headers=person["phone"](), json={"message": "دارایی خالصم چقدره؟"}).json()
    words = [b for b in r["blocks"] if b["type"] == "text"][-1]["text"]
    assert words == "دارایی خالص شما امروز، هر ارز جداگانه: ۱۰٬۰۰۰٬۰۰۰ ریال، ۷۰٫۰۰۰ ریال عمان."
    table = next(b for b in r["blocks"] if b["type"] == "table" and b["kind"] == "net_worth_currencies")
    assert [(row["currency"], row["value"]) for row in table["rows"]] == [("IRR", 10_000_000), ("OMR", 70)]


def _goal(db, cid, *, code, target, by):
    from app.models.savings_goal import SavingsGoal
    with _in(cid):
        db.add(SavingsGoal(name="سفر", account_code=code, target_amount=target, target_date=by))
        db.commit()


def _wallet(db, cid):
    """An Omani-rial savings wallet under cash on hand, holding 100 OMR."""
    from app.models.account import Account
    from sqlalchemy import select
    with _in(cid):
        cash = db.execute(select(Account).where(Account.code == "1120")).scalars().one()
        db.add(Account(code="112009", name="پس‌انداز سفر", parent_id=cash.id, level=cash.level))
        db.commit()
    _post(db, cid, TODAY, "پس‌انداز", "112009", "3110", Decimal("100"), "OMR")


def test_ar3_a_goal_in_omani_rials(person, db):
    cid = person["cid"]
    _wallet(db, cid)
    _goal(db, cid, code="112009", target=Decimal("500"), by=TODAY + timedelta(days=100))
    from app.services.personal_goals import list_goals
    with _in(cid):
        [g] = list_goals(db, today=TODAY)
    assert (g["currency"], g["current"], g["remaining"], g["percent"]) == ("OMR", 100, 400, 20.0)
    assert g["needed_per_month"] and g["needed_per_month"] <= 400


def test_ar4_the_reviews_goal_idea_in_its_own_currency(person, db):
    from app.services.proactive_reviews import _suggestion
    said, _ask = _suggestion({"key": "goal_behind", "params": {"goal": "سفر", "needed": 100, "pace": 0,
                                                                "by": "2027-01-31", "currency": "OMR"}},
                             "fa", "IRR", "gregorian")
    assert "۱۰۰٫۰۰۰ ریال عمان" in said and "ریال ایران" not in said


def test_ar5_each_cards_net_worth_in_its_own_currency(person, db):
    cid = person["cid"]
    _post(db, cid, date(2026, 10, 1), "حقوق", "1110", "4110", 20_000_000, "IRR")
    _post(db, cid, date(2026, 10, 2), "حقوق عمان", "1120", "4110", Decimal("400"), "OMR")
    _post(db, cid, date(2026, 10, 14), "خرج", "6180", "1120", Decimal("41.500"), "OMR")
    from app.services.report_card import report_card
    with _in(cid):
        rc = report_card(db, "2026-10", lang="en", today=date(2026, 11, 2))
    assert rc["net_worth"] == {"start": 0, "end": 20_000_000, "change": 20_000_000, "currency": "IRR"}
    [omr] = rc["others"]
    assert omr["net_worth"] == {"start": 0, "end": 358.5, "change": 358.5, "currency": "OMR"}
    from tests.test_proactive_reviews import _make, _reviews
    _make(db, cid, date(2026, 11, 2))
    [review] = _reviews(db, cid, "monthly")
    assert review.facts["net_worth_change"] == 20_000_000 and review.facts["others"][0]["net_worth_change"] == 358.5


def test_ar6_gold_still_at_todays_rate_in_the_rials(person, db):
    cid = person["cid"]
    _post(db, cid, TODAY - timedelta(days=30), "طلا", "1140", "1110", 50_000_000, "IRR")
    _post(db, cid, TODAY - timedelta(days=30), "موجودی", "1110", "3110", 50_000_000, "IRR")
    _post(db, cid, TODAY, "کیف", "1120", "3110", Decimal("10"), "OMR")
    from app.models.exchange_rate import ExchangeRate
    from app.models.personal_holding import PersonalHolding
    with _in(cid):
        db.add(PersonalHolding(account_code="1140", unit="GOLDG", quantity=10))
        db.add(ExchangeRate(company_id=uuid.UUID(cid), from_currency="GOLDG", to_currency="IRR", rate=6_000_000,
                            effective_date=TODAY))
        db.commit()
    from app.services.net_worth_service import net_worth_by_currency
    with _in(cid):
        irr, omr = net_worth_by_currency(db)
    assert (irr.currency, irr.net_worth, irr.unrealized_gain) == ("IRR", 60_000_000, 10_000_000)
    assert (omr.currency, omr.net_worth, omr.unrealized_gain) == ("OMR", 10, 0)
