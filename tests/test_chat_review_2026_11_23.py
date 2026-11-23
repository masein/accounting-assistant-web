"""The chats of 2026-11-16 to 11-23 (chat review 2026-11-23, scenarios CL–CR).

From production: a week's review said «این هفته −۴۹۰٫۰۲۰ ریال عمان خرج کردید؛ ۱۸۳٪ کمتر …» after a purchase of an
earlier week was paid back, and a month's said «۳۶۹۸٪ کمتر از ماه قبل» against a month a correction left below
nothing; the morning's briefing came again alone that evening, with «1,300.00 USD» in its Persian; a conversation
opened again on the web showed «最近‌ترین» the phone never did; two receipts lost their printed dates, their items
never reached the assistant, and a card receipt was paid from the cash wallet; «اجاره ماشین» found the rent;
a person's ledger said a signed net of 0 beside 5,806.15 owed; and an empty book answered «0 IRR» alone. Figures
made up.
"""
from __future__ import annotations

import asyncio
import uuid
from datetime import date, timedelta
from decimal import Decimal

import pytest

from tests.test_personal_money import API, person, scripted  # noqa: F401 — fixtures
from tests.test_personal_people import _a_list_is_many_requests  # noqa: F401 — our requests don't use up the suite's
from tests.test_proactive_reviews import MONDAY, _in, _make, _name, _post, _reviews


@pytest.fixture()
def books(db):
    from app.services.ai_eval.fixture import seed_personal_books
    from tests.test_admin_audit import _purge_company
    ctx = seed_personal_books(db)
    yield ctx
    _purge_company(db, ctx.company_id)


# --- CL1, CL2: the reviews' figures -------------------------------------------------------------------------------

def test_cl1_money_back_from_an_earlier_week_is_not_spending_below_nothing(person, db):
    cid = person["cid"]
    for w in range(1, 5):
        day = MONDAY - timedelta(days=7 * (w + 1) - 2)
        _post(db, cid, day, "سوپرمارکت", "6110", "1110", 2_000_000)
        _post(db, cid, day, "رستوران", "6180", "1110", 1_000_000)
    _post(db, cid, MONDAY - timedelta(days=19), "جاروبرقی", "6125", "1110", 6_000_000)
    last = MONDAY - timedelta(days=5)
    _post(db, cid, last, "سوپرمارکت", "6110", "1110", 2_000_000)
    _post(db, cid, last, "بازپرداخت جاروبرقی از شرکت", "1110", "6125", 6_000_000)    # paid back this week
    assert _make(db, cid) == ["weekly"]
    [review] = _reviews(db, cid)
    f = review.facts
    home = _name(db, cid, "6125")
    assert (f["spent"], f["back"], f["back_to"]) == (2_000_000, 6_000_000, [home])
    assert f["usual"] == 4_500_000                    # (9 + 3 + 3 + 3) million: the weeks' spending, refunds apart
    assert [c["name"] for c in f["top"]] == [_name(db, cid, "6110")]              # nothing listed below nothing
    from app.services.proactive_reviews import render
    en = render(review, "en")["text"]
    assert "You spent 2,000,000 Iranian rial this week — 56% less than your usual week" in en
    assert "6,000,000 Iranian rial came back for earlier spending (⁨" + home + "⁩)." in en \
        or f"6,000,000 Iranian rial came back for earlier spending ({home})." in en
    fa = render(review, "fa")["text"]
    assert "−" not in fa and "۶,۰۰۰,۰۰۰ ریال ایران از خرج‌های قبلی برگشت («" + home + "»)." in fa


def test_cl2_no_percentage_against_a_period_below_nothing(person, db):
    from app.services.proactive_reviews import _against, _delta
    assert _against({"name": "x", "amount": 215.9, "usual": -6}, "fa", monthly=True) == ""
    assert _against({"name": "x", "amount": 215.9, "usual": None}, "en", monthly=True) == "new"
    assert _delta(215.9, -6, "fa", monthly=True, lower_is_good=True) is None
    cid = person["cid"]
    _post(db, cid, date(2026, 9, 10), "جابه‌جایی از خانواده و هدایا", "6125", "6195", 6_000)
    _post(db, cid, date(2026, 10, 5), "هدیه", "6195", "1110", 215_900)
    from app.services.report_card import report_card
    with _in(cid):
        card = report_card(db, "2026-10", today=date(2026, 10, 25))
    family = next(c for c in card["categories"] if c["category"] == _name(db, cid, "6195"))
    assert family["previous"] == -6_000 and family["change_pct"] is None          # not «3698% less»


# --- CM1, CM2: the briefing once, in Persian figures ----------------------------------------------------------------

def test_cm1_the_briefing_said_with_the_reviews_isnt_said_again(person, db, monkeypatch):
    import app.services.insight_service as ins
    from tests.test_proactive_reviews import _weeks
    cid, client = person["cid"], person["client"]
    _weeks(db, cid)
    _make(db, cid)
    monkeypatch.setattr(ins, "briefing_text", lambda insights, lang, **kw: "Before we start: cash is short.")
    phone = person["phone"]()
    first = client.post(f"{API}/briefing", headers=phone, json={}).json()
    kinds = [str(b["id"]).split(":")[0] for b in first["blocks"]]
    assert "review" in kinds and "briefing" in kinds
    again = client.post(f"{API}/briefing", headers=phone, json={"thread_id": first["thread_id"]}).json()
    assert again["blocks"] == []                       # the reviews were seen; the briefing was said today
    web = person["web"]()
    assert web.post("/ai-accountant/briefing", json={"session_id": first["thread_id"]}).json()["text"] is None
    monkeypatch.setattr(ins, "briefing_text", lambda insights, lang, **kw: "Before we start: something new.")
    changed = client.post(f"{API}/briefing", headers=phone, json={"thread_id": first["thread_id"]}).json()
    assert [b["text"] for b in changed["blocks"]] == ["Before we start: something new."]   # a new one is said


def test_cm2_another_currencys_amount_in_a_persian_briefing():
    from app.services.insight_service import _said
    assert _said("1,300.00 USD", "fa") == "۱,۳۰۰٫۰۰ دلار آمریکا"
    assert _said("1,290.530 OMR", "fa") == "۱,۲۹۰٫۵۳۰ ریال عمان"
    assert _said("-464.890 OMR", "fa").endswith("ریال عمان") and "OMR" not in _said("-464.890 OMR", "fa")
    assert _said("1,300.00 USD", "en") == "1,300.00 USD"
    assert _said("0.4", "fa") == "۰٫۴"


# --- CN1: the reply kept as it was shown ----------------------------------------------------------------------------

def test_cn1_a_conversation_opened_again_shows_the_reply_as_it_was_said(person, scripted, db):  # noqa: F811
    from tests.test_ai_guardrails import _text
    scripted(_text("بررسی کردم. موجودی درست است.最近‌ترین افزایش‌ها از امیر است."))
    web = person["web"]()
    r = web.post("/ai-accountant/chat", json={"message": "سلام، یه نگاهی بنداز ببین درسته"})
    assert r.status_code == 200, r.text
    said = r.json()
    assert "最近" not in said["text"]
    msgs = web.get(f"/ai-accountant/sessions/{said['session_id']}/messages").json()
    kept = [m["content"]["text"] for m in msgs if m["role"] == "assistant" and (m["content"] or {}).get("text")]
    assert kept[-1] == said["text"]


# --- CO1–CO3: receipts' dates, items, and how they were paid -------------------------------------------------------

def test_co1_a_receipts_date_with_its_time_or_a_two_digit_year():
    from app.services.ocr_extract import _normalize_date
    near = date.today() - timedelta(days=3)
    assert _normalize_date(f"{near.day:02d}/{near.month:02d}/{near.year % 100:02d} 22:42") == near.isoformat()
    assert _normalize_date("11/20/2026, 2:06 PM") == "2026-11-20"
    assert _normalize_date("Date: 20/11/2026 14:06:33") == "2026-11-20"
    assert _normalize_date("2026-11-19T22:42:00") == "2026-11-19"
    assert _normalize_date("1405/08/29 10:30") == "2026-11-20"
    assert _normalize_date("۰۵/۰۸/۳۵") is None        # a Jalali year first, not 5 August 2035: asked, not guessed
    assert _normalize_date("19/11/2026") == "2026-11-19"                               # as before


def test_co2_co3_the_items_and_how_it_was_paid_reach_the_assistant(person, db, monkeypatch):
    import app.services.ocr_extract as ocr
    from app.api.ai_accountant import _build_ocr_context
    from app.models.transaction import TransactionAttachment
    assert (ocr._paid_with("Bank"), ocr._paid_with("CASH"), ocr._paid_with("Visa ****1234"), ocr._paid_with(None)) \
        == ("card", "cash", "card", None)
    shaped = ocr._normalize_extracted({"total": 13.68, "currency": "OMR", "paid_with": "Bank",
                                       "line_items": [{"description": "Lemon Mint Mojito", "amount": 2}]})
    assert shaped["paid_with"] == "card" and shaped["line_items"][0]["description"] == "Lemon Mint Mojito"
    cid = person["cid"]
    with _in(cid):
        att = TransactionAttachment(file_name="receipt.jpg", file_path=f"/tmp/{uuid.uuid4().hex}.jpg",
                                    content_type="image/jpeg", size_bytes=1)
        db.add(att)
        db.commit()
        att_id = str(att.id)

    async def fields(path, ctype):
        return {"vendor_name": "Kabab Planet", "date": "2026-11-20", "amount": 13.68, "currency": "OMR",
                "paid_with": "card", "line_items": [{"description": "Lemon Mint Mojito", "amount": 2},
                                                    {"description": "Special lamb shank with rice", "amount": 7.6}],
                "confidence": 0.95}
    monkeypatch.setattr(ocr, "extract_from_attachment", fields)
    with _in(cid):
        text, _amounts = asyncio.run(_build_ocr_context(db, [att_id]))
    assert "  date: 2026-11-20" in text and "  paid with: card" in text
    assert "  items: Lemon Mint Mojito 2; Special lamb shank with rice 7.6" in text


def test_co3_cp2_the_rules_are_in_the_assistants_instructions():
    from app.services.ai_accountant.orchestrator import PERSONAL_MODE_ADDENDUM, SYSTEM_PROMPT
    assert "How it was paid (``paid with``) decides the kind of place" in SYSTEM_PROMPT
    assert "A WORD TWO OF THEIR PLACES SHARE" in PERSONAL_MODE_ADDENDUM
    assert '8. **"What can you do?" is about this app.**' in SYSTEM_PROMPT                       # CR1


# --- CP1: the words for a car hired and the household's shopping -------------------------------------------------

@pytest.mark.parametrize("query, code", [
    ("اجاره ماشین", "6130"), ("اجاره ماشین برای یک هفته", "6130"), ("car rental", "6130"), ("rent a car", "6130"),
    ("خرید خانه", "6110"), ("خرید خونه", "6110"), ("money owed to others", "2130"),
    ("اجاره", "6120"), ("rent", "6120"), ("لوازم خونه", "6125"),                                     # as before
])
def test_cp1_the_search_finds_a_car_hired_and_the_households_shopping(db, books, query, code):
    from app.db.tenant import use_company
    from app.services.ai_accountant.base import ToolContext
    from app.services.ai_accountant.read_tools import SearchAccounts, SearchAccountsInput
    with use_company(books.company_id):
        out = asyncio.run(SearchAccounts().run(ToolContext(db=db, user_id="u1", mode="personal"),
                                               SearchAccountsInput(query=query)))
    top = out["matches"][0]
    assert top["code"] == code, out["matches"][:3]
    assert all(m["confidence"] < top["confidence"] for m in out["matches"][1:]), out["matches"][:3]


# --- CQ1: a person's ledger and what is owed -------------------------------------------------------------------------

def test_cq1_a_persons_lines_say_what_is_owed(db, books):
    from app.db.tenant import use_company
    from app.services.ai_accountant.base import ToolContext
    from app.services.ai_accountant.read_tools import QueryLedger, QueryLedgerInput
    from app.services.ai_eval.fixture import AMIR_AIRBNB
    with use_company(books.company_id):
        out = asyncio.run(QueryLedger().run(ToolContext(db=db, user_id="u1", mode="personal"),
                                            QueryLedgerInput(entity_id=books.entities["امیر"], group_by="none",
                                                             from_date=date.today() - timedelta(days=60))))
    per = out["totals_by_currency"]
    assert Decimal(str(per["OMR"]["signed_net"])) == Decimal("300")
    assert Decimal(str(per["USD"]["signed_net"])) == Decimal(str(AMIR_AIRBNB))
    with use_company(books.company_id):
        both = asyncio.run(QueryLedger().run(ToolContext(db=db, user_id="u1", mode="personal"),
                                             QueryLedgerInput(account_code="6110, 1120", group_by="none",
                                                              from_date=date.today() - timedelta(days=60))))
    assert "signed_net" in both or "totals_by_currency" in both                     # an account asked: as before


# --- CR2: an empty book --------------------------------------------------------------------------------------------

def test_cr2_an_empty_book_says_how_to_start(person, db):
    from app.services.ai_accountant import fast_paths
    cid = person["cid"]
    with _in(cid):
        en = asyncio.run(fast_paths.answer(db, "How much money do I have?", user_id="u1", username="u",
                                           lang="en", mode="personal"))
        fa = asyncio.run(fast_paths.answer(db, "چقدر پول دارم؟", user_id="u1", username="u",
                                           lang="fa", mode="personal"))
    assert en.text.endswith("Tell me what you have and where — for example \"I have 5 million tomans in my bank "
                            "account\" — and I'll set it up.")
    assert "هنوز چیزی ثبت نشده است" in fa.text
    _post(db, cid, date.today(), "حقوق", "1110", "4110", 10_000_000)
    with _in(cid):
        later = asyncio.run(fast_paths.answer(db, "How much money do I have?", user_id="u1", username="u",
                                              lang="en", mode="personal"))
    assert "Nothing is recorded yet" not in later.text


# --- CS1, CS2: a wallet's balance in doubt ---------------------------------------------------------------------------
# «۱۰۰ ریال از پاکت جا به جا کردم به کیف پول قشنگم» was taken from an envelope empty for a month (−100 after it), and
# told «بنظرم داری اشتباه میکنی», the assistant added the wallet's 68 entries up again and said it was right.

def _omr(db, books, on, desc, lines):
    from app.db.tenant import use_company
    from app.models.account import Account
    from app.services.ai_eval.fixture import _journal
    with use_company(books.company_id):
        acc = {a.code: a for a in db.execute(__import__("sqlalchemy").select(Account)).scalars()}
        _journal(db, acc, on, desc, [(c, Decimal(str(d)), Decimal(str(k))) for c, d, k in lines], currency="OMR")
        db.commit()


def _card(db, books, lines, desc="انتقال"):
    from app.db.tenant import use_company
    from app.services.ai_accountant.base import ToolContext
    from app.services.ai_accountant.proposal_tools import ProposeCreateTransaction, ProposeCreateTransactionInput
    args = ProposeCreateTransactionInput(date=date.today().isoformat(), description=desc, currency="OMR", lines=[
        {"account_code": c, "debit": d, "credit": k} for c, d, k in lines])
    with use_company(books.company_id):
        return asyncio.run(ProposeCreateTransaction().run(
            ToolContext(db=db, user_id=books.user_id, user_message=desc, mode="personal"), args))


def test_cs1_cash_doesnt_go_below_zero(db, books):
    from app.services.ai_accountant.base import ToolError
    from app.services.ai_eval.fixture import GREEN_WALLET, PRETTY_WALLET
    # the pretty wallet holds 60 − 20.25 = 39.75 Omani rials; the green one 100 − 12.5 − 3.75 + 300 = 383.75
    with pytest.raises(ToolError) as refused:
        _card(db, books, [(GREEN_WALLET, 100, 0), (PRETTY_WALLET, 0, 100)], "۱۰۰ ریال از کیف پول قشنگم به سبز")
    assert getattr(refused.value, "code", None) == "place_below_zero"
    said = str(refused.value)
    assert "held 39.750 OMR" in said or "held 39.75 OMR" in said
    assert f"«کیف پول سبز» ({GREEN_WALLET})" in said and "ask_with_choices" in said
    moved = _card(db, books, [(GREEN_WALLET, 30, 0), (PRETTY_WALLET, 0, 30)], "۳۰ ریال از کیف پول قشنگم به سبز")
    assert moved["status"] == "pending"                                          # what it holds can move
    spent = _card(db, books, [("6190", 300, 0), (PRETTY_WALLET, 0, 300)], "۳۰۰ ریال از کیف پول قشنگم دادم")
    assert spent["status"] == "pending"            # spending isn't refused: a wallet's earlier money may not be in the books


def test_cs2_a_wallets_doubtful_entries(db, books):
    from app.db.tenant import use_company
    from app.services.ai_accountant.base import ToolContext
    from app.services.ai_accountant.read_tools import QueryLedger, QueryLedgerInput
    from app.services.ai_eval.fixture import GREEN_WALLET, PRETTY_WALLET
    today = date.today()
    _omr(db, books, today - timedelta(days=3), "انتقال ۵۰ از کیف پول سبز", [(PRETTY_WALLET, 50, 0), (GREEN_WALLET, 0, 50)])
    _omr(db, books, today - timedelta(days=2), "انتقال ۵۰ از کیف پول سبز", [(PRETTY_WALLET, 50, 0), (GREEN_WALLET, 0, 50)])
    _omr(db, books, today - timedelta(days=1), "خرید بزرگ", [("6110", 500, 0), (PRETTY_WALLET, 0, 500)])
    with use_company(books.company_id):
        out = asyncio.run(QueryLedger().run(ToolContext(db=db, user_id="u1", mode="personal"), QueryLedgerInput(
            account_code=PRETTY_WALLET, currency="OMR", group_by="none", from_date=today - timedelta(days=60))))
    doubt = out["doubtful"]
    twice = [d for d in doubt if d.get("same_as")]
    assert len(twice) == 1 and twice[0]["same_as"] == (today - timedelta(days=3)).isoformat()
    assert "«کیف پول سبز»" in twice[0]["why"] and "told twice" in twice[0]["why"]
    assert any("below zero" in d["why"] and d["date"] == (today - timedelta(days=1)).isoformat() for d in doubt)
    assert "Change none without them" in out["doubtful_note"]
    with use_company(books.company_id):
        green = asyncio.run(QueryLedger().run(ToolContext(db=db, user_id="u1", mode="personal"), QueryLedgerInput(
            account_code="6110", currency="OMR", group_by="none", from_date=today - timedelta(days=60))))
    assert "doubtful" not in green                                               # only a person's own places


def test_cs2_cr3_the_rules_are_in_the_assistants_instructions():
    from app.services.ai_accountant.orchestrator import PERSONAL_MODE_ADDENDUM, SYSTEM_PROMPT
    assert "Never answer that the balance is right" in PERSONAL_MODE_ADDENDUM
    assert "9. **Only their money and this app.**" in SYSTEM_PROMPT and "is not answered" in SYSTEM_PROMPT
