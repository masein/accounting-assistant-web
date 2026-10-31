"""Can I afford it? (scenarios AM1–AM10).

From the owner (2026-10-20): «I want to buy something, and the system checks my finances and tells me
whether I can or can't buy it, or by installments, or reminds me to save if I set a goal». A person's
books (Gregorian) with three complete months — July to September — of 40,000,000 income and 25,000,000
spending, and 120,000,000 in the bank today: a month's spending is the cushion, 15,000,000 a month is
spare."""
from __future__ import annotations

from datetime import date, timedelta

import pytest
from sqlalchemy import select

from tests.test_ai_guardrails import _calls, _text
from tests.test_personal_money import API, person, scripted  # noqa: F401 — fixtures
from tests.test_personal_people import _a_list_is_many_requests, _confirm, _say  # noqa: F401
from tests.test_proactive_reviews import _in, _post

TODAY = date(2026, 10, 20)


def _books(db, cid, *, income=40_000_000, spending=25_000_000, opening=75_000_000):
    _post(db, cid, date(2026, 6, 15), "موجودی اولیه", "1110", "3110", opening)
    for month in (7, 8, 9):
        _post(db, cid, date(2026, month, 1), "حقوق", "1110", "4110", income)
        _post(db, cid, date(2026, month, 10), "خرج ماه", "6110", "1110", spending)


def _check(db, cid, price, **kw):
    from app.services.affordability import check
    with _in(cid):
        return check(db, price=price, today=TODAY, **kw)


@pytest.mark.parametrize("price, verdict", [
    (20_000_000, "now"),             # AM1: 95,000,000 is free
    (105_000_000, "wait"),           # AM2: 10,000,000 short, 15,000,000 spare a month: a month
    (150_000_000, "installments"),   # AM3: four months' wait; 24 × 6,250,000 is within half the spare
    (300_000_000, "save"),           # AM4: no plan fits: 14 months
])
def test_am1_am4_the_answer_by_the_price(person, db, price, verdict):
    _books(db, person["cid"])
    f = _check(db, person["cid"], price, item="لپ‌تاپ")
    assert f["verdict"] == verdict
    assert (f["cash"], f["income"], f["spending"], f["cushion"], f["spare"]) == \
        (120_000_000, 40_000_000, 25_000_000, 25_000_000, 15_000_000)
    if verdict == "wait":
        assert f["wait_months"] == 1
    if verdict == "installments":
        assert (f["plan"], f["plan_each"], f["wait_months"]) == (24, 6_250_000, 4)
    if verdict == "save":
        assert f["wait_months"] == 14 and f["plan"] is None


def test_am5_nothing_spare_is_not_now(person, db):
    _books(db, person["cid"], income=40_000_000, spending=45_000_000, opening=150_000_000)
    f = _check(db, person["cid"], 200_000_000)
    assert f["verdict"] == "not_now" and f["spare"] == -5_000_000
    from app.services.affordability import card
    c = card(f, "fa")
    assert (c["emoji"], c["mood"], c["headline"]["label"]) == ("✋", "watch", "فعلاً نه")
    assert [i["ask"] for i in c["ideas"]] == ["چطور خرجم را کم کنم؟"]


def test_am7_payments_due_and_goals_are_counted(person, db):
    from app.models.commitment import Commitment
    from app.models.savings_goal import SavingsGoal
    cid = person["cid"]
    _books(db, cid)
    with _in(cid):
        db.add(Commitment(kind="installment", direction="pay", title="قسط وام", amount=10_000_000,
                          due_date=TODAY + timedelta(days=10), status="pending"))
        db.add(Commitment(kind="installment", direction="pay", title="قسط بعدی", amount=10_000_000,
                          due_date=TODAY + timedelta(days=45), status="pending"))      # beyond 30 days
        db.add(SavingsGoal(name="سفر", account_code="1140", target_amount=24_000_000, target_date=date(2027, 9, 20)))
        db.commit()
    f = _check(db, cid, 20_000_000)
    assert f["due_30"] == 10_000_000 and f["cushion"] == 35_000_000
    assert f["goals_need"] > 0 and f["spare_free"] == 15_000_000 - f["goals_need"]
    from app.services.affordability import card
    facts = card(f, "en")["facts"]
    assert any("due in the next 30 days" in x for x in facts) and any("set aside for goals" in x for x in facts)


def test_am8_another_currency(person, db, monkeypatch):
    from app.services import fx_base
    from app.services.affordability import AffordError
    _books(db, person["cid"])
    with pytest.raises(AffordError) as e:
        _check(db, person["cid"], 100, currency="USD")
    assert e.value.code == "no_rate"
    monkeypatch.setattr(fx_base, "rate_to_base", lambda db_, ccy, on, base=None: 500_000.0 if ccy == "USD" else None)
    f = _check(db, person["cid"], 100, currency="USD")
    assert (f["price"], f["currency"], f["price_base"], f["verdict"]) == (100, "USD", 50_000_000, "now")


def test_am3_am8_omani_rials_with_baisa(person, db, monkeypatch):
    """Found by the deep test of 2026-10-20: in books whose amounts have places (the owner's own count in Omani
    rials) half the spare was a Decimal times a float, and so was a dollar price times its rate: TypeError."""
    from decimal import Decimal

    from app.models.company import Company
    from app.services import fx_base
    import uuid
    cid = person["cid"]
    with _in(cid):
        db.get(Company, uuid.UUID(cid)).base_currency = "OMR"
        db.commit()
    _books(db, cid, income=Decimal("400.250"), spending=Decimal("250.125"), opening=Decimal("75.000"))
    f = _check(db, cid, Decimal("900.500"))
    assert (f["cash"], f["cushion"], f["spare"]) == (Decimal("525.375"), Decimal("250.125"), Decimal("150.125"))
    assert (f["verdict"], f["plan"], f["wait_months"]) == ("installments", 12, 5)
    assert f["plan_each"] == Decimal("75.042")                                   # 900.500 / 12, to the baisa
    monkeypatch.setattr(fx_base, "rate_to_base", lambda db_, ccy, on, base=None: 0.385 if ccy == "USD" else None)
    f = _check(db, cid, Decimal("100.50"), currency="USD")
    assert (f["price_base"], f["verdict"]) == (Decimal("38.693"), "now")
    from app.services.affordability import card
    assert card(f, "fa")["headline"]["value"] == Decimal("38.693")               # the card counts in the books' rials


def test_am10_too_new_to_tell(person, db):
    _post(db, person["cid"], TODAY - timedelta(days=3), "موجودی اولیه", "1110", "3110", 10_000_000)
    f = _check(db, person["cid"], 30_000_000)
    assert f["verdict"] == "too_new" and f["months"] == 0
    assert _check(db, person["cid"], 5_000_000)["verdict"] == "now"          # the cash part still answers


def test_am9_the_card_on_the_phone_and_the_web(person, scripted, db):
    from tests.test_mobile_contract import contract_errors
    _books(db, person["cid"])
    call = ("check_affordability", {"price": 300_000_000, "item": "ماشین"})
    body, _phone = _say(person, scripted, "می‌خوام یه ماشین ۳۰ میلیون تومنی بخرم، می‌تونم؟", call,
                        text="فعلاً نه به‌صورت نقد؛ ماهی حدود ۱۵ میلیون ریال کنار بگذارید.")
    [card] = [b for b in body["blocks"] if b["type"] == "review"]
    assert contract_errors(card) == []
    assert (card["kind"], card["emoji"], card["mood"]) == ("afford", "🐷", "watch")
    assert card["title"] == "ماشین به قیمت ۳۰۰٬۰۰۰٬۰۰۰ ریال ایران؟"
    assert [b["label"] for b in card["chart"]["bars"]] == ["حالا", "بعد از خرید"]
    assert card["chart"]["usual"] == 25_000_000 and card["ring"]["pct"] == round(95 / 300 * 100, 1)
    assert card["ideas"][0]["ask"].startswith("یک هدف پس‌انداز ۳۰۰٬۰۰۰٬۰۰۰ ریال ایران برای ماشین تا ")
    scripted(_calls(call), _text("Not in cash yet."))                         # the web gets the card too
    out = person["web"]().post("/ai-accountant/chat", json={"message": "can I buy a car for 300,000,000?"}).json()
    [web] = out["review_cards"]
    assert web["kind"] == "afford" and web["headline"]["value"] == 300_000_000


def test_am6_a_savings_goal_from_the_chat(person, scripted, db):
    from app.models.account import Account
    from app.models.savings_goal import SavingsGoal
    cid = person["cid"]
    _books(db, cid)
    body, phone = _say(person, scripted, "یک هدف پس‌انداز ۳۰۰ میلیونی برای ماشین تا دی ۱۴۰۶ بساز",
                       ("propose_savings_goal", {"name": "ماشین", "target_amount": 300_000_000, "target_date": "2027-12-22"}),
                       text="کارت آماده است.")
    block = next(b for b in body["blocks"] if b["type"] == "proposal")
    assert block["title"] == "هدف پس‌انداز: ماشین"
    _confirm(person, phone, block["token"])
    with _in(cid):
        db.expire_all()
        [goal] = db.execute(select(SavingsGoal)).scalars().all()
        place = db.execute(select(Account).where(Account.code == goal.account_code)).scalar_one()
    assert (goal.name, goal.target_amount, goal.target_date) == ("ماشین", 300_000_000, date(2027, 12, 22))
    assert place.name == "پس‌انداز ماشین" and goal.account_code.startswith("1110")
    from app.services.personal_goals import list_goals
    with _in(cid):
        [g] = list_goals(db, today=TODAY)
    assert g["needed_per_month"] > 0 and g["on_track"] is False                  # the weekly review now reminds (AL6)
