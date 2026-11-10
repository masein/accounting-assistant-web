"""A Persian conversation answered in Persian, whatever the phone's language (chat review 2026-11-10, scenarios
BQ1–BQ4).

From the production chats, on a phone whose profile language is English: one brand name in Latin letters made a
Persian message count as English; a receipt sent alone was answered in English, by the receipt's own printed
words; a card changed on the phone came back drawn in English, and its English summary went back to the model as
what the assistant had said; receipts were described in English in the books.
"""
from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timedelta, timezone

from app.db.tenant import use_company
from app.models.ai_accountant import AIChatMessage
from app.services.ai_accountant.orchestrator import _message_language, thread_language, turn_language
from tests.test_personal_money import API, person, scripted  # noqa: F401 — fixtures

USER = "u-lang"
OCR = ("Attached document OCR (attachment_id=00000000-0000-0000-0000-000000000001, file=receipt.jpg):\n"
       " vendor: Corner Hypermarket LLC\n date: 2026-11-07\n total amount: 5.9 OMR\n reference: 594543")


def test_bq1_most_of_the_words_say_the_language():
    assert _message_language("دیشب ۲.۸ ریال KFC خوردیم از کیف پول قشنگم", "en") == "fa"      # one brand name
    assert _message_language("KFC خوردیم", "en") == "fa"                                     # a tie: Persian
    assert _message_language("دفعت خمسة ريالات للتاكسي", "en") == "ar"
    assert _message_language("we paid 36000 for کارمزد صورتحساب", "en") == "en"              # English with a name
    assert _message_language("what are the last 10 transactions on آینده", "fa") == "en"
    assert _message_language("What was our biggest expense last month?", "fa") == "en"       # as before
    assert _message_language("کی به ما بدهکاره؟", "en") == "fa" and _message_language("۲۵۰۰", "en") == "en"


def _said(db, session_id, content, minutes_ago):
    db.add(AIChatMessage(session_id=session_id, role="user", content={"role": "user", **content},
                         created_at=datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)))
    db.commit()


def test_bq2_a_receipt_sent_alone_keeps_the_conversations_language(db, person):  # noqa: F811
    from app.services.ai_accountant.orchestrator import _get_or_create_session
    with use_company(person["cid"]):
        session = _get_or_create_session(db, user_id=USER, session_id=None)
        _said(db, session.id, {"text": "از آیکیا یه سری خرید کردیم شد ۳۳ و نیم ریال"}, 5)
        _said(db, session.id, {"said": "", "text": OCR}, 2)                                  # a receipt alone
        assert thread_language(db, session.id, "en") == "fa"                                # not the receipt's English
        assert turn_language(db, "", session.id, "en") == "fa"
        assert turn_language(db, "ok?", session.id, "en") == "fa"                            # "ok" doesn't say: the thread
        assert turn_language(db, "and this one too?", session.id, "fa") == "en"              # English words: English
        assert turn_language(db, "", None, "en") == "en"                                     # nothing said yet


def test_bq2_the_model_is_told_to_answer_in_persian(db, person):  # noqa: F811
    from app.services.ai_accountant.orchestrator import _get_or_create_session, run_chat_turn
    from tests.test_ai_guardrails import _FakeClient, _text

    class _Seeing(_FakeClient):
        prompts: list[str] = []

        async def chat(self, *, system_prompt, **kw):
            self.prompts.append(system_prompt)
            return await super().chat(system_prompt=system_prompt, **kw)

    model = _Seeing([_text("کارت آماده است.")])
    with use_company(person["cid"]):
        session = _get_or_create_session(db, user_id=USER, session_id=None)
        _said(db, session.id, {"text": "۱.۷ ریال کروسان خوردم"}, 5)
        asyncio.run(run_chat_turn(db, user_id=USER, user_message="", session_id=str(session.id), lang="en",
                                  ocr_context=OCR, client=model, mode="personal"))
    assert "This turn: the user's message is in Persian" in model.prompts[-1]


def test_bq3_a_card_changed_on_the_phone_stays_in_the_conversations_language(person, scripted, db):  # noqa: F811
    from datetime import date

    from sqlalchemy import select

    from tests.test_ai_guardrails import _text
    from tests.test_personal_people import _calls
    phone = person["phone"]()
    client = person["client"]
    scripted(_calls(("propose_create_transaction", {
        "date": date.today().isoformat(), "description": "تاکسی", "currency": "IRR",
        "lines": [{"account_code": "6130", "debit": 3, "credit": 0}, {"account_code": "1120", "debit": 0, "credit": 3}]})),
        _text("کارت آماده است."))
    first = client.post(f"{API}/chat", headers=phone, json={"message": "۳ ریال تاکسی دادم"}).json()
    [card] = [b for b in first["blocks"] if b.get("type") == "proposal"]
    r = client.post(f"{API}/proposals/{card['token']}/edit", headers=phone, json={"description": "تاکسی برگشت"})
    assert r.status_code == 200, r.text
    with use_company(person["cid"]):
        kept = db.execute(select(AIChatMessage).where(AIChatMessage.session_id == uuid.UUID(first["thread_id"]),
                                                      AIChatMessage.role == "assistant")
                          .order_by(AIChatMessage.created_at.desc())).scalars().first()
    said = kept.content["text"]
    assert said.startswith("کارت تغییر کرد: تاکسی برگشت") and "Proposed journal entry" not in said, said
    assert "۳ ریال" in said


def test_bq4_entries_are_described_in_the_conversations_language():
    from app.services.ai_accountant.orchestrator import SYSTEM_PROMPT
    assert "a receipt printed in English, from a user who writes Persian, gets a Persian description" in SYSTEM_PROMPT
    assert "answer in the language of the user's earlier messages" in SYSTEM_PROMPT
