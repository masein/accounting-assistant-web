"""The assistant's own Persian (copy review, 2026-10-31): the 34 Persian eval
conversations on the production model said a card was «ثبت شد» before its
confirm, turned who owes whom round, mixed a Gregorian day into a Jalali month
and wrote «۳۳۳.۷۷» (scenarios BF1–BF5)."""
from __future__ import annotations

import asyncio
import inspect
from datetime import date, timedelta

import pytest

from app.db.tenant import use_company
from app.services.ai_accountant.base import ToolContext
from tests.test_people_direction import USER, person  # noqa: F401 — the person's books (owes Amir 300 OMR, $333.77)


def test_bf1_a_persian_turn_is_told_how_persian_reads():
    """The style rule goes last, and only on a Persian turn: English users pay nothing for it."""
    from app.services.ai_accountant import orchestrator as o
    body = inspect.getsource(o.run_chat_turn)
    rule = body.index("never «ثبت شد» or")
    assert rule > body.index("This turn: the user's message is in") and rule > body.index('if lang != "fa":')
    assert "«شما به امیر " in body and "«لیست حقوق»" in body and "«کتاب‌ها»" in body
    assert "I'll log 250,000 Toman" not in o.PERSONAL_MODE_ADDENDUM                  # it read «ثبت کردم» in Persian
    assert "is ready" in o.PERSONAL_MODE_ADDENDUM


def test_bf2_who_owes_whom_is_said_in_code(db, person):
    from app.services.ai_accountant.personal_tools import GetPeopleBalances, GetPeopleBalancesInput, _owing_fa
    with use_company(person.company_id):
        out = asyncio.run(GetPeopleBalances().run(
            ToolContext(db=db, user_id=USER, mode="personal", user_message="چقدر به امیر بدهکارم؟"),
            GetPeopleBalancesInput(person="امیر")))
        english = asyncio.run(GetPeopleBalances().run(
            ToolContext(db=db, user_id=USER, mode="personal", user_message="how much do I owe Amir?"),
            GetPeopleBalancesInput(person="امیر")))
    said = out["people"][0]["said_fa"]
    assert said.startswith("شما ") and said.endswith(" به امیر بدهکارید"), said
    assert "۳۰۰٫۰۰۰ ریال عمان" in said and "۳۳۳٫۷۷" in said and "said_fa" in out["note"]
    assert "said_fa" not in english["people"][0]
    assert _owing_fa("امیر", {"owes_you": {"USD": 50}}) .startswith("امیر ۵۰٫۰۰ ")
    assert _owing_fa("امیر", {"owes_you": {"USD": 50}}).endswith(" به شما بدهکار است")
    assert _owing_fa("امیر", {"you_owe": {}, "owes_you": {"OMR": 0}}) == "حساب شما با امیر صاف است"


def test_bf3_dates_as_the_reader_says_them(db, person):
    from app.services.ai_accountant.date_tools import ProposeChangeDate, ProposeChangeDateInput
    from app.services.ai_accountant.orchestrator import _DATE_KEY, _for_the_model
    from app.services.locale_service import set_display_calendar
    from app.utils.jalali import format_jalali_long
    real = date.today() - timedelta(days=40)

    def summary():
        return asyncio.run(ProposeChangeDate().run(
            ToolContext(db=db, user_id=USER, mode="personal", user_message="تاریخش رو درست کن"),
            ProposeChangeDateInput(entries=[{"transaction_id": person.txns["airbnb"], "date": real.isoformat()}])))["summary"]
    with use_company(person.company_id):
        gregorian = summary()                                  # books kept in the Gregorian calendar: both
        set_display_calendar(db, "jalali")
        db.commit()
        jalali = summary()                                     # a Jalali company: the words alone
    assert f"{real.isoformat()} ({format_jalali_long(real)})" in gregorian
    assert format_jalali_long(real) in jalali and real.isoformat() not in jalali
    # a pay run's period is dates too: it came out «۱ نوامبر تا ۳۰ نوامبر ۲۰۲۶» in a Jalali company
    for key in ("period_start", "period_end", "year_start", "date", "due", "as_of"):
        assert _DATE_KEY.search(key), key
    for key in ("spend", "weekend", "trend", "dividend", "due_amount", "duration"):
        assert not _DATE_KEY.search(key), key
    seen = _for_the_model({"period_start": "2026-11-01", "period_end": "2026-11-30"}, jalali=True)
    assert seen["period_start_jalali"] == "۱۰ آبان ۱۴۰۵" and seen["period_end_jalali"] == "۹ آذر ۱۴۰۵"


@pytest.mark.parametrize("said, shown", [
    ("۳۳۳.۷۷ دلار", "۳۳۳٫۷۷ دلار"),
    ("۴۵،۰۰۰،۰۰۰ ریال", "۴۵٬۰۰۰٬۰۰۰ ریال"),
    ("موارد ۱، ۲ و ۳", "موارد ۱، ۲ و ۳"),                    # a list keeps its comma
    ("تا ۱۴۰۵. سپس", "تا ۱۴۰۵. سپس"),                        # a full stop after a year
    ("333.77 USD", "333.77 USD"),                              # Latin figures as they are
])
def test_bf4_persian_figures_take_persian_separators(said, shown):
    from app.services.ai_accountant.orchestrator import _persian_separators
    assert _persian_separators(said) == shown


def test_bf5_notes_the_model_would_say_back(db, person):
    from app.services.ai_accountant import cash_tools, spending_tools
    assert "say each one" in inspect.getsource(cash_tools)
    src = inspect.getsource(spending_tools)
    assert "they are in their own currencies" in src and "not personal and (left_out" in src
