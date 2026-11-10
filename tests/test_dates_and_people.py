"""The day it happened, and the people in it (chat review 2026-11-10, scenarios BO1–BO8).

From the production chats: «دیشب» was dated today; «دیشب … امروز …» put every card on one day; a follow-up
(«proceed», «رقم همینه») lost the date said a message before; «4th of Mordad» wasn't read; «همسر» was matched
to a friend's name and linked to the wife's entries; a longer spelling of a client was someone new beside the
shorter one; money handed to the wife was carded as a debt she owed.
"""
from __future__ import annotations

import asyncio
import uuid
from datetime import date, timedelta

from app.services.ai_accountant.date_resolver import jalali_named_date, resolve_entry_date
from tests.test_personal_money import API, person, scripted  # noqa: F401 — fixtures

D = date(2026, 11, 5)
MORDAD_4 = date(2026, 7, 26)


def test_bo1_last_night_is_yesterday():
    assert resolve_entry_date("شام دیشب ۱۰ ریال شد", D, today=D) == D - timedelta(days=1)
    assert resolve_entry_date("پریشب تاکسی گرفتم", D, today=D) == D - timedelta(days=2)
    assert resolve_entry_date("we had dinner last night", D, today=D) == D - timedelta(days=1)
    assert resolve_entry_date("امشب شام خوردیم", D - timedelta(days=1), today=D) == D       # tonight is today


def test_bo2_several_days_in_one_message_each_card_keeps_its_own():
    from app.services.ai_accountant.date_resolver import mentioned_dates
    said = "دیشب ۱۲ ریال غذا خریدیم، امروز ۲ ریال تاکسی دادم"
    assert mentioned_dates(said, D) == {D - timedelta(days=1), D}
    assert resolve_entry_date(said, D - timedelta(days=1), today=D) == D - timedelta(days=1)   # the food
    assert resolve_entry_date(said, D, today=D) == D                                             # the taxi
    assert resolve_entry_date(said, D - timedelta(days=9), today=D) == D - timedelta(days=1)   # neither: the first said
    morning = "دیروز ۵ ریال آب خریدم، امروز صبح ۱ نون و ۲ تاکسی"
    assert resolve_entry_date(morning, D, today=D) == D


def test_bo3_bo4_a_follow_up_or_a_correction_keeps_its_day():
    earlier = ["در تاریخ ۴ مرداد ۵۰ میلیون تومان به حساب دیگرم منتقل کردم"]
    assert resolve_entry_date("رقم همینه دیگه", MORDAD_4, today=D, earlier=earlier) == MORDAD_4
    assert resolve_entry_date("proceed the transaction", MORDAD_4, today=D, earlier=earlier) == MORDAD_4
    assert resolve_entry_date("رقم همینه دیگه", date(2026, 3, 1), today=D, earlier=earlier) == D   # not said: today
    assert resolve_entry_date("نه، مبلغش ۱۲ بود", D, today=D, replaced_date=date(2026, 10, 26)) == date(2026, 10, 26)
    # a correction that names a day takes it
    assert resolve_entry_date("نه، دیروز بود", D, today=D, replaced_date=date(2026, 10, 26)) == D - timedelta(days=1)


def test_bo5_jalali_months_in_latin_letters():
    on = date(2026, 8, 2)
    assert jalali_named_date("transferred 50 million toman on 4th of Mordad", on) == MORDAD_4
    assert jalali_named_date("Mordad 4", on) == MORDAD_4
    assert resolve_entry_date("in 4th of Mordad", on, today=on) == MORDAD_4
    assert resolve_entry_date("paid 4 days ago", on, today=on) == on - timedelta(days=4)       # "day" is not «دی»


def test_bo3_through_the_chat_a_follow_up_keeps_the_day(person, scripted):
    from tests.test_ai_guardrails import _text
    from tests.test_personal_people import _calls
    day = jalali_named_date("۴ مرداد", date.today())
    phone = person["phone"]()
    client = person["client"]
    scripted(_text("چقدر شد؟"))
    first = client.post(f"{API}/chat", headers=phone, json={"message": "۴ مرداد تاکسی گرفتم"}).json()
    scripted(_calls(("propose_create_transaction", {
        "date": day.isoformat(), "description": "تاکسی", "currency": "IRR",
        "lines": [{"account_code": "6130", "debit": 3, "credit": 0}, {"account_code": "1120", "debit": 0, "credit": 3}]})),
        _text("کارت آماده است."))
    out = client.post(f"{API}/chat", headers=phone, json={"message": "۳ ریال", "thread_id": first["thread_id"]}).json()
    [card] = [b for b in out["blocks"] if b.get("type") == "proposal"]
    assert card["date"]["iso"] == day.isoformat()


def _find(db, query, said, *names):
    from app.models.entity import Entity
    from app.services.ai_accountant.base import ToolContext
    from app.services.ai_accountant.read_tools import FindEntity, FindEntityInput
    made = [Entity(id=uuid.uuid4(), type="client", name=n) for n in names]
    db.add_all(made)
    db.flush()
    try:
        return asyncio.run(FindEntity().run(ToolContext(db=db, user_id="u-bo", user_message=said),
                                            FindEntityInput(query=query)))
    finally:
        for e in made:
            db.delete(e)
        db.flush()


def test_bo6_a_family_word_is_not_a_name(db):
    out = _find(db, "همسر", "۵ ریال دادم به همسرم", "رضا")
    assert out["matches"] == [] and "family member" in out["note"]
    assert _find(db, "my wife", "gave my wife 5", "Wifey Ltd")["matches"] == []
    saved = _find(db, "همسر", "۵ ریال دادم به همسر", "همسر", "رضا")
    assert [m["name"] for m in saved["matches"]] == ["همسر"]                        # someone saved under that word


def test_bo7_a_longer_name_finds_the_shorter_one(db):
    out = _find(db, "zkarimi nia", "received 1bn from zkarimi nia", "Zkarimi")
    assert out["matches"] and out["matches"][0]["name"] == "Zkarimi" and "note" not in out
    # AY5 still: a person named beside the one asked for is someone else
    ay5 = _find(db, "زسارا", "یکیش رو زسارو بهم داده، بقیه رو زسارا", "زسارو")
    assert not [m for m in ay5["matches"] if m["confidence"] >= 0.5] and "different people" in ay5["note"]


def test_bo8_money_given_to_family_is_family_spending():
    from app.services.ai_accountant.orchestrator import PERSONAL_MODE_ADDENDUM
    assert "is family spending, «خانواده و هدایا» (6195), never 1150" in PERSONAL_MODE_ADDENDUM
