"""The accountant speaks first: weekly and monthly reviews, suggestions, a nudge (scenarios AL1–AL16).

From the owner (2026-10-20): «each week or month the AI gives me a report in the chat, a message or
notification about our spending, or suggestions», and «remind me to save if I set a goal». Here a
person's books (Gregorian: a week is Monday to Sunday) have four ordinary weeks — 2,000,000 at the
supermarket and 1,000,000 eating out each — and then a week with a 3,000,000 birthday dinner and the
month's pay."""
from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from tests.test_personal_money import API, person, scripted  # noqa: F401 — fixtures
from tests.test_personal_people import _a_list_is_many_requests, _confirm, _say  # noqa: F401

MONDAY = date(2026, 10, 19)          # last week: 12–18 October


def _in(cid):
    from app.db.tenant import use_company
    return use_company(cid)


def _post(db, cid, on, desc, dr, cr, amount, *, created=None, who=None):
    from app.models.entity import Entity, TransactionEntity
    from app.schemas.transaction import TransactionCreate, TransactionLineCreate
    from app.services.ledger_posting import create_transaction_from_payload
    with _in(cid):
        t = create_transaction_from_payload(db, TransactionCreate(date=on, description=desc, lines=[
            TransactionLineCreate(account_code=dr, debit=amount, credit=0),
            TransactionLineCreate(account_code=cr, debit=0, credit=amount)]))
        if created is not None:
            t.created_at = created
        if who is not None:
            ent = db.execute(select(Entity).where(Entity.name == who)).scalars().first()
            if ent is None:
                ent = Entity(type="supplier", name=who)
                db.add(ent)
                db.flush()
            db.add(TransactionEntity(transaction_id=t.id, entity_id=ent.id, role="payee"))
        db.commit()
        return t


def _weeks(db, cid):
    for w in range(1, 5):
        day = MONDAY - timedelta(days=7 * (w + 1) - 2)
        _post(db, cid, day, "سوپرمارکت", "6110", "1110", 2_000_000)
        _post(db, cid, day, "رستوران", "6180", "1110", 1_000_000)
    last = MONDAY - timedelta(days=5)
    _post(db, cid, last, "سوپرمارکت", "6110", "1110", 2_000_000)
    _post(db, cid, last, "شام تولد", "6180", "1110", 3_000_000)
    _post(db, cid, last, "حقوق مهر", "1110", "4110", 10_000_000)


def _make(db, cid, today=MONDAY):
    from app.services.proactive_reviews import generate_due
    with _in(cid):
        return generate_due(db, today)


def _reviews(db, cid, kind=None):
    from app.models.proactive_review import ProactiveReview
    with _in(cid):
        db.expire_all()
        q = select(ProactiveReview).order_by(ProactiveReview.created_at)
        if kind:
            q = q.where(ProactiveReview.kind == kind)
        return db.execute(q).scalars().all()


def _user(db, cid):
    from app.models.user import User
    db.expire_all()
    return db.execute(select(User).where(User.company_id == uuid.UUID(cid))).scalars().first()


def _name(db, cid, code):
    from app.models.account import Account
    with _in(cid):
        return db.execute(select(Account.name).where(Account.code == code)).scalar_one()


# --- AL1, AL15, AL16: a week's review, once; none for quiet books; in each reader's language -------------------

def test_al1_a_persons_week_against_their_usual_week(person, db):
    cid = person["cid"]
    assert _make(db, cid) == []                                     # AL15: no entries, no review
    _weeks(db, cid)
    assert _make(db, cid) == ["weekly"]
    assert _make(db, cid) == []                                     # once, however often the job runs
    [review] = _reviews(db, cid)
    f = review.facts
    assert (f["start"], f["end"], review.period_key) == ("2026-10-12", "2026-10-18", "2026-10-12")
    assert (f["spent"], f["usual"], f["income"], f["entries"]) == (5_000_000, 3_000_000, 10_000_000, 3)
    dining = _name(db, cid, "6180")
    assert f["top"][0] == {"name": dining, "amount": 3_000_000, "usual": 1_000_000}
    assert f["biggest"]["what"] == "شام تولد" and f["biggest"]["amount"] == 3_000_000
    assert [s["key"] for s in f["suggestions"]] == ["hot_category"]             # AL9: no budget for it
    from app.services.proactive_reviews import render
    en = render(review, "en")
    assert "You spent 5,000,000 Iranian rial this week — 67% more than your usual week (3,000,000 Iranian rial)." in en["text"]
    assert "Income: 10,000,000 Iranian rial." in en["text"] and "Biggest spend: \u2068شام تولد\u2069" in en["text"]
    assert "“\u2068" + dining + "\u2069” was 200% above usual" in en["text"]      # a Persian name keeps its order in English
    assert en["choices"] == [f"Set a monthly budget of 3,000,000 Iranian rial for {dining}"]     # its average month
    assert en["headline"].startswith("You spent 5,000,000")
    fa = render(review, "fa")                                                     # AL16
    assert "این هفته ۵٬۰۰۰٬۰۰۰ ریال ایران خرج کردید؛ ۶۷٪ بیشتر از هفته‌های معمولتان" in fa["text"]
    card = fa["card"]                                                             # AL17: the card
    assert (card["mood"], card["emoji"]) == ("watch", "👀")                       # 67% above the usual week
    assert card["headline"]["label"] == "خرج این هفته" and card["headline"]["value"] == 5_000_000
    assert card["headline"]["delta"] == {"pct": 67, "dir": "up", "good": False, "text": "۶۷٪ بیشتر از معمول"}
    bars = card["chart"]["bars"]
    assert [b["value"] for b in bars] == [3_000_000] * 4 + [5_000_000] and bars[-1]["current"]
    assert bars[-1]["label"] == "این هفته" and card["chart"]["usual"] == 3_000_000
    cats = card["categories"]
    assert cats[0]["sub"] == "۲۰۰٪ بیشتر از معمول" and cats[1]["sub"] == "معمول"
    assert cats[0]["width"] == 1.0 and cats[0]["usual_at"] == round(1_000_000 / 3_000_000, 4)
    assert card["ideas"][0]["icon"] == "🔥" and card["ideas"][0]["ask"] == fa["choices"][0]
    assert [b["type"] for b in fa["blocks"]] == ["review"] and fa["blocks"][0]["fallback_text"].startswith("مرور هفته")


def test_al1_the_jalali_week_runs_saturday_to_friday(person, db):
    from app.services.locale_service import set_display_calendar
    cid = person["cid"]
    with _in(cid):
        set_display_calendar(db, "jalali")
        db.commit()
    _post(db, cid, date(2026, 10, 20), "نان", "6110", "1110", 100_000)          # a Tuesday
    assert _make(db, cid, date(2026, 10, 24)) == ["weekly", "monthly"]           # Saturday 2 Aban: and Mehr's
    [review] = _reviews(db, cid, "weekly")
    assert (review.facts["start"], review.facts["end"]) == ("2026-10-17", "2026-10-23")
    from app.services.proactive_reviews import render
    assert "مرور هفته، ۲۵ مهر تا ۱ آبان ۱۴۰۵" in render(review, "fa")["text"]


def _omani(db, cid):
    from app.models.company import Company
    with _in(cid):
        db.get(Company, uuid.UUID(cid)).base_currency = "OMR"
        db.commit()


def test_al1_al9_a_week_in_omani_rials_with_baisa(person, db):
    """Found by the deep test of 2026-10-20: a review of books whose amounts have places — the UK's pence, the
    owner's own Omani rials — kept 2459.97 as the text "2459.97", and the card compared it with a number, so
    the bell, the chat and the phone's briefing lost it; and a category running hot multiplied a Decimal by a float."""
    from decimal import Decimal
    cid = person["cid"]
    _omani(db, cid)
    for w in range(1, 5):
        day = MONDAY - timedelta(days=7 * (w + 1) - 2)
        _post(db, cid, day, "سوپرمارکت", "6110", "1110", Decimal("20.250"))
        _post(db, cid, day, "رستوران", "6180", "1110", Decimal("10.125"))
    last = MONDAY - timedelta(days=5)
    _post(db, cid, last, "سوپرمارکت", "6110", "1110", Decimal("20.250"))
    _post(db, cid, last, "شام تولد", "6180", "1110", Decimal("30.500"))
    _post(db, cid, last, "حقوق", "1110", "4110", Decimal("100.750"))
    assert _make(db, cid) == ["weekly"]
    [review] = _reviews(db, cid)
    f = review.facts
    assert (f["spent"], f["usual"], f["income"], f["currency"]) == (50.75, 30.375, 100.75, "OMR")    # numbers, not text
    assert f["top"][0]["amount"] == 30.5 and f["top"][0]["usual"] == 10.125
    assert [s["key"] for s in f["suggestions"]] == ["hot_category"]
    from app.services.proactive_reviews import render
    fa = render(review, "fa")
    assert "۵۰٫۷۵۰ ریال عمان" in fa["text"] and fa["card"]["mood"] == "watch"
    assert fa["card"]["headline"]["value"] == 50.75 and fa["card"]["chart"]["usual"] == 30.375
    # one kept before the fix, its figures as text, reads the same
    from app.models.proactive_review import ProactiveReview

    def as_text(v):
        if isinstance(v, float):
            return str(v)
        if isinstance(v, dict):
            return {k: as_text(x) for k, x in v.items()}
        return [as_text(x) for x in v] if isinstance(v, list) else v
    old = ProactiveReview(id=review.id, kind=review.kind, period_key=review.period_key, facts=as_text(f),
                          created_at=review.created_at)
    assert old.facts["spent"] == "50.75"
    assert render(old, "fa") == fa and render(old, "en") == render(review, "en")


# --- AL4, AL5: in the chat once, as a notification until then ----------------------------------------------------

def test_al4_al5_the_phone_is_told_then_shown_it_once(person, db):
    from app.models.notification import Notification
    from app.services.notification_service import refresh_notifications
    cid, client = person["cid"], person["client"]
    _weeks(db, cid)
    _make(db, cid)
    user = _user(db, cid)
    with _in(cid):
        refresh_notifications(db)
        bell = db.execute(select(Notification).where(Notification.kind == "review")).scalars().all()
    assert [(n.user_id, n.text_key, n.dismissed_at) for n in bell] == [(str(user.id), "review_weekly", None)]
    phone = person["phone"]()
    feed = client.get(f"{API}/feed", headers=phone).json()["items"]
    [item] = [i for i in feed if i["kind"] == "review"]
    assert item["title"] == "Your weekly review is ready" and item["body"].startswith("You spent 5,000,000")
    blocks = client.post(f"{API}/briefing", headers=phone, json={}).json()["blocks"]
    ours = [b for b in blocks if str(b["id"]).startswith("review")]
    assert [b["type"] for b in ours] == ["review"]
    assert ours[0]["kind"] == "weekly" and ours[0]["title"] == "Your week, 12 Oct – 18 Oct 2026"
    assert ours[0]["ideas"] and "stop the weekly review" in ours[0]["note"]             # said the first time
    from tests.test_mobile_contract import contract_errors
    assert contract_errors(ours[0]) == []
    again = client.post(f"{API}/briefing", headers=phone, json={}).json()["blocks"]
    assert not [b for b in again if str(b["id"]).startswith("review")]                 # once
    assert not [i for i in client.get(f"{API}/feed", headers=phone).json()["items"] if i["kind"] == "review"]
    with _in(cid):
        refresh_notifications(db)
        db.expire_all()
        assert all(n.dismissed_at for n in db.execute(select(Notification).where(Notification.kind == "review")).scalars())


def test_al4_the_web_chat_gets_it_whenever_the_chat_opens(person, db):
    cid = person["cid"]
    _weeks(db, cid)
    _make(db, cid)
    web = person["web"]()
    out = web.post("/ai-accountant/reviews", json={}).json()
    [said] = out["reviews"]
    assert said["kind"] == "weekly" and said["text"].startswith("**Your week") and said["choices"]
    assert said["card"]["headline"]["value"] == 5_000_000 and said["card"]["chart"]["bars"]
    msgs = web.get(f"/ai-accountant/sessions/{out['session_id']}/messages").json()
    kept = [m["content"] for m in msgs if (m.get("content") or {}).get("review")]
    assert len(kept) == 1 and kept[0]["text"].startswith("**Your week") and kept[0]["choices"]   # in its history
    assert kept[0]["card"]["mood"] == "watch"                                                   # drawn again as a card
    assert web.post("/ai-accountant/reviews", json={"session_id": out["session_id"]}).json()["reviews"] == []


# --- AL6, AL7: a goal behind; money came in -------------------------------------------------------------------

def _goal(db, cid, *, target, by, account="1140"):
    from app.models.savings_goal import SavingsGoal
    with _in(cid):
        db.add(SavingsGoal(name="لپ‌تاپ", account_code=account, target_amount=target, target_date=by))
        db.commit()


def test_al6_a_goal_behind_says_what_each_month_needs(person, db):
    cid = person["cid"]
    _weeks(db, cid)
    _goal(db, cid, target=60_000_000, by=date(2027, 2, 20))
    _make(db, cid)
    [review] = _reviews(db, cid)
    s = review.facts["suggestions"][0]
    assert s["key"] == "goal_behind" and s["params"]["goal"] == "لپ‌تاپ" and s["params"]["needed"] > 0
    from app.services.proactive_reviews import render
    fa = render(review, "fa")
    assert "برای رسیدن به «لپ‌تاپ» تا" in fa["text"] and "چطور به هدف لپ‌تاپ برسم؟" in fa["choices"]


def test_al7_paid_this_week_and_a_goal_on_track(person, db):
    cid = person["cid"]
    _weeks(db, cid)
    _post(db, cid, MONDAY - timedelta(days=60), "پس‌انداز", "1140", "1110", 3_000_000)
    _goal(db, cid, target=10_000_000, by=date(2027, 8, 20))
    _make(db, cid)
    [review] = _reviews(db, cid)
    keys = [s["key"] for s in review.facts["suggestions"]]
    assert keys[0] == "goal_payday"
    from app.services.proactive_reviews import render
    en = render(review, "en")
    assert "Money came in this week — put" in en["text"] and en["choices"][0].startswith("Move ")


# --- AL8: a budget's pace -------------------------------------------------------------------------------------

def test_al8_a_budget_spending_faster_than_the_days(person, db):
    from app.models.budget import BudgetLimit
    cid = person["cid"]
    _weeks(db, cid)                                                 # October so far: 4,000,000 at the supermarket
    with _in(cid):
        db.add(BudgetLimit(month="2026-10", category="6110", limit_amount=5_000_000))
        db.commit()
    _make(db, cid)
    [review] = _reviews(db, cid)
    pace = next(s for s in review.facts["suggestions"] if s["key"] == "budget_pace")
    assert pace["params"]["spent"] == 4_000_000 and pace["params"]["limit"] == 5_000_000
    assert pace["params"]["on"] == "2026-10-24"                     # 5,000,000 at 4,000,000 per 19 days


# --- AL10: owed a while -----------------------------------------------------------------------------------------

def test_al10_someone_owed_a_month_with_no_word(person, db):
    cid = person["cid"]
    _weeks(db, cid)
    _post(db, cid, MONDAY - timedelta(days=40), "بلیط — امیر حساب کرد", "6185", "2130", 4_000_000, who="امیر")
    _make(db, cid)
    [review] = _reviews(db, cid)
    owed = next(s for s in review.facts["suggestions"] if s["key"] == "owed_long_you_owe")
    assert owed["params"]["person"] == "امیر" and owed["params"]["amount"] == 4_000_000
    from app.services.proactive_reviews import render
    fa = render(review, "fa")
    assert "هنوز به امیر ۴٬۰۰۰٬۰۰۰ ریال ایران بدهکارید" in fa["text"] and "پس دادن پول امیر را ثبت کن" in fa["choices"]
    assert "به امیر ۴٬۰۰۰٬۰۰۰ ریال ایران بدهکارید." in fa["text"]          # and in the week's own words


# --- AL3, AL11: a month's review, repeating payments -----------------------------------------------------------

def test_al3_al11_the_month_and_its_repeating_payments(person, db):
    cid = person["cid"]
    for month in (8, 9, 10):
        _post(db, cid, date(2026, month, 5), "Netflix", "6190", "1110", 500_000)
        _post(db, cid, date(2026, month, 6), "Spotify", "6190", "1110", 300_000)
        _post(db, cid, date(2026, month, 1), "حقوق", "1110", "4110", 20_000_000)
    _post(db, cid, date(2026, 10, 20), "سوپرمارکت", "6110", "1110", 6_000_000)
    made = _make(db, cid, date(2026, 11, 2))                        # Monday 2 November: last week and October
    assert "monthly" in made
    [review] = _reviews(db, cid, "monthly")
    f = review.facts
    assert (review.period_key, f["income"], f["spent"], f["saved"]) == ("2026-10", 20_000_000, 6_800_000, 13_200_000)
    assert [s["key"] for s in f["suggestions"]] == ["subscriptions"]
    from app.services.proactive_reviews import render
    en = render(review, "en")
    assert "Your month: Oct 2026" in en["text"] and "saved 13,200,000 Iranian rial (66% of income)" in en["text"]
    assert "2 payments repeat every month, 800,000 Iranian rial a month in all: Netflix, Spotify." in en["text"]
    card = en["card"]                                                             # saved two thirds: a trophy
    assert (card["mood"], card["emoji"]) == ("good", "🏆") and card["ring"]["pct"] == 66.0
    assert card["headline"]["label"] == "Spent in Oct 2026" and [b["label"] for b in card["chart"]["bars"]] == \
        ["Jul", "Aug", "Sep", "Oct"]


# --- AL12: a company's suggestions ---------------------------------------------------------------------------

def test_al12_a_companys_most_pressing_first(person, db):
    from app.services.proactive_reviews import suggestions
    facts = {"personal": False, "runway": 2.5, "overdue": {"count": 4, "total": 120_000_000, "oldest": "Arman", "days": 60},
             "top": [], "spent": 0}
    with _in(person["cid"]):
        out = suggestions(db, "weekly", facts, MONDAY)
    assert [s["key"] for s in out] == ["runway", "overdue_invoices"]


def test_al16_a_companys_week_in_english_names_a_persian_debtor_in_order():
    """Deep test 2026-10-20: «1 invoices overdue», and the debtor's Persian name scrambled the English sentence
    («the oldest is 10 ,(فروشگاه مهرگان (شعبه ۲ days»). The name now sits between isolates; Persian is as before."""
    from app.models.proactive_review import ProactiveReview
    from app.services.proactive_reviews import render
    shop = "فروشگاه مهرگان (شعبه ۲)"
    review = ProactiveReview(id=uuid.uuid4(), kind="weekly", period_key="2026-10-12", created_at=datetime.now(timezone.utc), facts={
        "personal": False, "start": "2026-10-12", "end": "2026-10-18", "calendar": "gregorian", "currency": "IRR",
        "income": 50_000_000, "spent": 8_000_000, "usual": 10_000_000, "usual_income": 20_000_000, "entries": 2,
        "cash": 100_000_000, "runway": 12.0, "receivables": 7_000_000, "payables": 0, "top": [], "suggestions": [],
        "overdue": {"count": 1, "total": 7_000_000, "oldest": shop, "days": 10}, "bills_due": {"count": 1, "total": 2_000_000}})
    en = render(review, "en")["text"]
    assert f"Overdue invoices: 1, 7,000,000 Iranian rial in all; the oldest is \u2068{shop}\u2069, 10 days." in en
    assert "Bills due in the next 7 days: 1, 2,000,000 Iranian rial." in en
    assert f"قدیمی‌ترین: {shop}،" in render(review, "fa")["text"]                   # no isolates in its own language


def test_al2_a_companys_readers_are_its_owner_cfo_and_accountant(db):
    from app.models.user import User
    from app.services.company_service import provision_company
    from app.services.proactive_reviews import readers
    from tests.test_admin_audit import _purge_company
    company, owner = provision_company(db, name="Arman", locale="ir", base_currency="IRR",
                                       username=f"arman-{uuid.uuid4().hex[:6]}", password="Str0ng-arman-pass!")
    cid = str(company.id)
    try:
        for role in ("cfo", "manager", "viewer", "accountant"):
            db.add(User(username=f"{role}-{uuid.uuid4().hex[:6]}", password_hash="x", password_salt="x", role=role,
                        company_id=company.id))
        db.commit()
        with _in(cid):
            assert sorted(u.role for u in readers(db)) == ["accountant", "cfo", "owner"]
    finally:
        _purge_company(db, cid)


# --- AL13: the nudge ----------------------------------------------------------------------------------------------

def test_al13_four_quiet_days_one_nudge_a_week(person, db):
    cid = person["cid"]
    five_days_ago = datetime.now(timezone.utc) - timedelta(days=5)
    for i in range(5):
        _post(db, cid, date.today() - timedelta(days=5), f"خرید {i}", "6110", "1120" if False else "1110", 100_000,
              created=five_days_ago)
    assert "nudge" in _make(db, cid, date.today())
    assert "nudge" not in _make(db, cid, date.today())
    assert "nudge" not in _make(db, cid, date.today() + timedelta(days=2))     # not twice in a week
    from app.services.proactive_reviews import render
    [nudge] = _reviews(db, cid, "nudge")
    assert render(nudge, "en")["text"].startswith("Nothing recorded since ") and "anything to add?" in render(nudge, "en")["text"]


def test_al13_never_after_something_was_recorded(person, db):
    cid = person["cid"]
    for i in range(5):
        _post(db, cid, date.today(), f"خرید {i}", "6110", "1110", 100_000)
    assert "nudge" not in _make(db, cid, date.today())


# --- AL14: switched off, by the person, in the chat or the settings --------------------------------------------

def test_al14_stop_the_weekly_review_from_the_chat(person, scripted, db):
    cid = person["cid"]
    body, phone = _say(person, scripted, "دیگه مرور هفتگی نفرست",
                       ("propose_review_prefs", {"kinds": ["weekly"], "on": False}), text="کارت آماده است.")
    _confirm(person, phone, next(b for b in body["blocks"] if b["type"] == "proposal")["token"])
    assert _user(db, cid).review_prefs == "weekly"
    _weeks(db, cid)
    _make(db, cid)
    from app.services.proactive_reviews import unseen
    with _in(cid):
        assert unseen(db, _user(db, cid)) == []                     # made for the books, not shown to this person
    web = person["web"]()
    assert web.get("/ai-accountant/review-prefs").json() == {"weekly": False, "monthly": True, "suggestions": True,
                                                              "nudge": True}
    assert web.post("/ai-accountant/reviews", json={}).json()["reviews"] == []
    r = web.put("/ai-accountant/review-prefs", json={"weekly": True, "monthly": True, "suggestions": False, "nudge": True})
    assert r.status_code == 200 and _user(db, cid).review_prefs == "suggestions"
    [said] = web.post("/ai-accountant/reviews", json={}).json()["reviews"]
    assert said["choices"] == []                                    # the review, without its suggestions


@pytest.mark.parametrize("kinds", [["weekly", "monthly", "suggestions", "nudge"]])
def test_switches_are_the_persons_own(kinds):
    from types import SimpleNamespace

    from app.services.proactive_reviews import set_switch, switched_off
    user = SimpleNamespace(review_prefs=None)
    for k in kinds:
        set_switch(user, k, False)
    assert user.review_prefs == "weekly,monthly,suggestions,nudge"
    set_switch(user, "monthly", True)
    assert switched_off(user) == {"weekly", "suggestions", "nudge"}
    with pytest.raises(ValueError):
        set_switch(user, "everything", False)
