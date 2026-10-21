"""The owner's phone, 2026-10-21: five screenshots (scenarios AU1–AU8)."""
from __future__ import annotations

import importlib.util
import uuid
from decimal import Decimal
from pathlib import Path

from sqlalchemy import select

from app.db.tenant import use_company
from tests.test_ai_guardrails import _calls, _text
from tests.test_personal_money import API, person, scripted  # noqa: F401 — fixtures

ROOT = Path(__file__).resolve().parents[1]


def _place(db, cid, code, name):
    from app.models.account import Account
    with use_company(cid):
        cash = db.execute(select(Account).where(Account.code == "1120")).scalars().one()
        db.add(Account(code=code, name=name, parent_id=cash.id, level=cash.level))
        db.commit()


def _say(person, phone, message):
    r = person["client"].post(f"{API}/chat", headers=phone, json={"message": message})
    assert r.status_code == 200, r.text
    return r.json()


def _loan(code):
    return ("propose_create_transaction", {
        "date": "2026-10-21", "description": "دریافت ۳۰۰ ریال عمانی قرض از امیر", "currency": "OMR",
        "lines": [{"account_code": code, "debit": "300", "credit": 0},
                  {"account_code": "2130", "debit": 0, "credit": "300"}]})


def _card(reply):
    return next(b for b in reply["blocks"] if b["type"] == "proposal")


def _stored(db, token):
    from app.models.ai_accountant import AIProposal
    db.expire_all()
    row = db.execute(select(AIProposal).where(AIProposal.confirmation_token == uuid.UUID(token))).scalar_one()
    return [ln["account_code"] for ln in row.tool_input["lines"]]


def test_au1_the_card_goes_to_the_place_they_named(person, db, scripted):
    cid = person["cid"]
    _place(db, cid, "112001", "کیف پول سبز")
    _place(db, cid, "112002", "کیف پول قشنگم")
    phone = person["phone"]()
    scripted(_calls(_loan("112001")), _text("کارت آماده است."))         # the model gave the green wallet's code
    card = _card(_say(person, phone, "کیف پول قشنگم"))
    assert [ln["account"] for ln in card["lines"]] == ["112002", "2130"]
    with use_company(cid):
        assert _stored(db, card["token"]) == ["112002", "2130"]          # what Post will post


def test_au1_a_name_taught_for_a_place_is_that_place(person, db, scripted):
    from app.models.learned_preference import LearnedPreference
    cid = person["cid"]
    _place(db, cid, "112001", "کیف پول سبز")
    _place(db, cid, "112003", "پاکت توی کشو")
    with use_company(cid):
        db.add(LearnedPreference(pattern="کیف پول قشنگم", label="کیف پول قشنگم", account_code="112001", source="chat"))
        db.commit()
    phone = person["phone"]()
    scripted(_calls(_loan("112001")), _text("کارت آماده است."))
    card = _card(_say(person, phone, "کیف پول قشنگم"))
    assert [ln["account"] for ln in card["lines"]] == ["112001", "2130"]   # «کیف پول قشنگم» is the green wallet


def test_au1_two_places_named_are_left_as_they_are(person, db, scripted):
    cid = person["cid"]
    _place(db, cid, "112001", "کیف پول سبز")
    _place(db, cid, "112002", "کیف پول قشنگم")
    phone = person["phone"]()
    scripted(_calls(_loan("112001")), _text("کارت آماده است."))
    card = _card(_say(person, phone, "از کیف پول قشنگم گذاشتم توی کیف پول سبز"))
    assert [ln["account"] for ln in card["lines"]] == ["112001", "2130"]


def test_au2_au3_au5_the_prompt():
    from app.services.ai_accountant.orchestrator import PERSONAL_MODE_ADDENDUM as P
    assert "AN ANSWER NEVER DROPS THE LIST" in P and "never answer only \"nothing recorded\"" in P
    assert "«لوازم و تعمیرات خانه»" in P and "«۲٫۰۰۰ ریال عمان», never «۲ ریال عمان»" in P


def test_au3_new_books_have_a_home_category(person, db):
    from app.models.account import Account
    with use_company(person["cid"]):
        acc = db.execute(select(Account).where(Account.code == "6125")).scalar_one()
    assert (acc.name, acc.parent.code) == ("لوازم و تعمیرات خانه", "61")


def test_au3_books_kept_from_before_get_it_once(db):
    """Migration 085, as 079 for Travel: run twice, once; English books say it in English."""
    from app.models.account import Account
    from app.services.company_service import provision_company
    from tests.test_admin_audit import _purge_company
    made = []
    for locale in ("default", "uk"):
        company, _user = provision_company(db, name=f"home {locale}", locale=locale, base_currency="IRR",
                                           username=f"home-{uuid.uuid4().hex[:6]}", password="Str0ng-personal-pass!",
                                           kind="personal")
        made.append((str(company.id), locale))
    db.commit()
    try:
        for cid, _locale in made:
            with use_company(cid):
                db.delete(db.execute(select(Account).where(Account.code == "6125")).scalar_one())
                db.commit()
        spec = importlib.util.spec_from_file_location("m085", ROOT / "alembic/versions/085_personal_home.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        from alembic.migration import MigrationContext
        from alembic.operations import Operations
        for _ in range(2):
            with db.get_bind().begin() as conn:
                with Operations.context(MigrationContext.configure(conn)):
                    mod.upgrade()
        db.expire_all()
        for cid, locale in made:
            with use_company(cid):
                home = db.execute(select(Account).where(Account.code == "6125")).scalars().all()
                assert len(home) == 1
                assert home[0].name == ("Home supplies & repairs" if locale == "uk" else "لوازم و تعمیرات خانه")
                assert home[0].parent.code == "61"
    finally:
        for cid, _locale in made:
            _purge_company(db, cid)


def test_au4_what_categories_there_are(person, scripted):
    from app.services.ai_accountant.fast_paths import match
    assert match("چه دسته بندی هوایی داری؟") == "categories"
    assert match("چه دسته‌بندی‌هایی داری؟") == "categories"
    assert match("what categories do you have?") == "categories"
    assert match("خب هفته گذشته خرج ما چیا بوده و توی چه دسته بندی هایی؟") != "categories"
    reply = _say(person, person["phone"](), "چه دسته بندی هوایی داری؟")          # no model: a quick answer
    words = [b for b in reply["blocks"] if b["type"] == "text"][-1]["text"]
    assert words.startswith("دسته‌بندی‌های خرج: خوراک و سوپرمارکت، مسکن و اجاره، لوازم و تعمیرات خانه")
    assert "سفر" in words and "درآمد: حقوق و دستمزد" in words


def test_au5_baisa_always_written_for_the_model():
    from app.services.ai_accountant.orchestrator import _for_the_model
    out = _for_the_model({"currency": "OMR", "total": 2, "by_category": [{"amount": Decimal("7.45")}]}, persian=True)
    assert out["total_fa"] == "۲٫۰۰۰" and out["by_category"][0]["amount_fa"] == "۷٫۴۵۰"
    irr = _for_the_model({"currency": "IRR", "total": 500, "balance": 3_000_000}, persian=True)
    assert "total_fa" not in irr and irr["balance_fa"] == "۳٬۰۰۰٬۰۰۰"        # rials as before
    mixed = _for_the_model({"currency": "IRR", "by_currency": [{"currency": "OMR", "total": 41.5}]}, persian=True)
    assert mixed["by_currency"][0]["total_fa"] == "۴۱٫۵۰۰"


def test_au6_au7_au8_on_the_phone():
    """The phone's own tests draw them: ChatScreenScreenshotTest.englishSpendingTablesSayTheirCurrency,
    .englishAppPersianCardKeepsItsDateInOrder, ChatItemsTest (summaryOf)."""
    kt = (ROOT / "android/app/src/main/kotlin/app/accountingassistant/android/ui/chat/ChatScreen.kt").read_text()
    assert "val mixed = ccys.size > 1" in kt and 'rtl && titleRtl -> "\\u200F$it\\u200F"' in kt
    assert "list.animateScrollBy(100_000f)" in kt and "Brush.verticalGradient" in kt
