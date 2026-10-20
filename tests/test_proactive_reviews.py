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
    assert "Income: 10,000,000 Iranian rial." in en["text"] and "Biggest spend: شام تولد" in en["text"]
    assert en["choices"] == [f"Set a monthly budget of 3,000,000 Iranian rial for {dining}"]     # its average month
    assert en["headline"].startswith("You spent 5,000,000")
    fa = render(review, "fa")                                                     # AL16
    assert "این هفته ۵٬۰۰۰٬۰۰۰ ریال ایران خرج کردید؛ ۶۷٪ بیشتر از هفته‌های معمولتان" in fa["text"]
    table = next(b for b in fa["blocks"] if b["type"] == "table")
    assert table["rows"][0]["sub"] == "۲۰۰٪ بیشتر از معمول" and table["rows"][1]["sub"] == "معمول"


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
    assert [b["type"] for b in ours] == ["text", "table", "text"]
    assert ours[0]["kind"] == "review" and ours[0]["text"].startswith("**Your week, 12 Oct – 18 Oct 2026**")
    assert ours[2]["choices"] and "stop the weekly review" in ours[2]["text"]           # said the first time
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
    msgs = web.get(f"/ai-accountant/sessions/{out['session_id']}/messages").json()
    kept = [m["content"] for m in msgs if (m.get("content") or {}).get("review")]
    assert len(kept) == 1 and kept[0]["text"].startswith("**Your week") and kept[0]["choices"]   # in its history
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


# --- AL12: a company's suggestions ---------------------------------------------------------------------------

def test_al12_a_companys_most_pressing_first(person, db):
    from app.services.proactive_reviews import suggestions
    facts = {"personal": False, "runway": 2.5, "overdue": {"count": 4, "total": 120_000_000, "oldest": "Arman", "days": 60},
             "top": [], "spent": 0}
    with _in(person["cid"]):
        out = suggestions(db, "weekly", facts, MONDAY)
    assert [s["key"] for s in out] == ["runway", "overdue_invoices"]


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
