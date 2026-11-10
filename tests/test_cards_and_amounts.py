"""Cards that wait, cards set aside, and the amounts they are checked against (chat review 2026-11-10,
scenarios BP1–BP8).

From the production chats: a card read an hour or a night later was refused as expired and typed again; a card
the user moved past («خب ولش …») stayed waiting and was counted in what they owed; «390 بیسه» was refused as not
near 390; an amount changed on the phone's card lost a digit with nothing said.
"""
from __future__ import annotations

import asyncio
import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from app.db.tenant import use_company
from app.models.ai_accountant import AIProposal
from app.services.ai_accountant.base import ToolContext
from app.services.ai_eval.fixture import GREEN_WALLET, seed_personal_books
from tests.test_personal_money import API, person, scripted  # noqa: F401 — fixtures

USER = "u-cards"


@pytest.fixture()
def books(db):
    ctx = seed_personal_books(db)
    yield ctx
    from tests.test_admin_audit import _purge_company
    _purge_company(db, ctx.company_id)


def _session(db) -> str:
    from app.services.ai_accountant.orchestrator import _get_or_create_session
    return str(_get_or_create_session(db, user_id=USER, session_id=None).id)


def _card(db, said, amount, *, session, desc="تاکسی", ago=None, lines=None) -> str:
    from app.services.ai_accountant.proposal_tools import ProposeCreateTransaction, ProposeCreateTransactionInput
    amount = Decimal(str(amount))
    out = asyncio.run(ProposeCreateTransaction().run(
        ToolContext(db=db, user_id=USER, mode="personal", user_message=said, chat_session_id=session),
        ProposeCreateTransactionInput(date=date.today(), description=desc, currency="OMR", lines=lines or [
            {"account_code": "6130", "debit": amount, "credit": 0},
            {"account_code": GREEN_WALLET, "debit": 0, "credit": amount}])))
    token = out["confirmation_token"]
    if ago is not None:
        _row(db, token).created_at = datetime.now(timezone.utc) - ago
        db.commit()
    return token


def _row(db, token) -> AIProposal:
    return db.execute(select(AIProposal).where(AIProposal.confirmation_token == uuid.UUID(token))).scalar_one()


def _confirm(db, token):
    from app.services.ai_accountant.execute_service import execute_proposal
    return execute_proposal(db, confirmation_token=token, actor_user_id=USER)


def test_bp1_a_card_waits_a_day(db, books):
    from app.services.ai_accountant.execute_service import ProposalExpired
    with use_company(books.company_id):
        s = _session(db)
        night = _card(db, "۱.۴۳ تاکسی دادم", "1.43", session=s, ago=timedelta(hours=10))
        assert _confirm(db, night).transaction_id                       # made last night, confirmed this morning
        stale = _card(db, "۲ تاکسی دادم", 2, session=s, ago=timedelta(hours=25))
        with pytest.raises(ProposalExpired):
            _confirm(db, stale)
        assert _row(db, stale).status == "expired"


def test_bp2_a_card_typed_again_is_recorded_once(db, books):
    from app.core.messages import localize_detail
    from app.services.ai_accountant.execute_service import ProposalCancelled
    said = "2.5 تاکسی از دفتر به خونه دادم"
    with use_company(books.company_id):
        s = _session(db)
        first = _card(db, said, "2.5", session=s, ago=timedelta(minutes=70))
        again = _card(db, said, "2.5", session=s)
        assert _confirm(db, again).transaction_id
        with pytest.raises(ProposalCancelled) as e:
            _confirm(db, first)                                          # the old card still showed Confirm
        assert "would record it twice" in str(e.value)
        assert "دو بار" in localize_detail(str(e.value), "fa")
        assert _row(db, first).status == "cancelled"
        # a taxi each way at the same fare is two entries
        there = _card(db, "۲.۵ تاکسی از خونه به دفتر", "2.5", session=s, desc="تاکسی رفت", ago=timedelta(hours=9))
        back = _card(db, "۲.۵ تاکسی از دفتر به خونه", "2.5", session=s, desc="تاکسی برگشت")
        assert _confirm(db, back).transaction_id and _confirm(db, there).transaction_id


def test_bp3_a_card_past_its_time_is_not_waiting(db, books):
    from app.services.ai_accountant.personal_tools import GetPeopleBalances, GetPeopleBalancesInput
    msg = "امیر به من ۶۰۰ ریال عمان دیگه داد"
    lines = [{"account_code": GREEN_WALLET, "debit": 600, "credit": 0}, {"account_code": "2130", "debit": 0, "credit": 600}]
    with use_company(books.company_id):
        s = _session(db)
        token = _card(db, msg, 600, session=s, lines=lines)
        tc = ToolContext(db=db, user_id=USER, mode="personal", user_message="حساب من با امیر؟", chat_session_id=s)
        assert "cards_waiting" in asyncio.run(GetPeopleBalances().run(tc, GetPeopleBalancesInput(person="امیر")))["people"][0]
        _row(db, token).created_at = datetime.now(timezone.utc) - timedelta(hours=25)
        db.commit()
        out = asyncio.run(GetPeopleBalances().run(tc, GetPeopleBalancesInput(person="امیر")))
    assert "cards_waiting" not in out["people"][0] and "once" not in (out.get("note") or "")


def test_bp4_never_mind_sets_the_last_turns_card_aside(db, books):
    from app.models.ai_accountant import AIChatMessage
    from app.services.ai_accountant.orchestrator import _set_aside_on_never_mind
    with use_company(books.company_id):
        s = _session(db)
        earlier = _card(db, "۲ تاکسی دادم", 2, session=s, ago=timedelta(minutes=30))
        db.add(AIChatMessage(session_id=uuid.UUID(s), role="user", content={"role": "user", "text": "x"},
                             created_at=datetime.now(timezone.utc) - timedelta(minutes=5)))
        db.commit()
        just = _card(db, "کیف پول قرمز رو بده به همسر", 2, session=s)
        assert _set_aside_on_never_mind(db, "ولش کن همینجوری ثبتش کن", user_id=USER, session_id=s) == []
        assert _set_aside_on_never_mind(db, "من ولش کردم بعدا", user_id=USER, session_id=s) == []
        assert _set_aside_on_never_mind(db, "خب ولش الان کل موجودی ریال عمان مون چقدره؟", user_id=USER,
                                        session_id=s) == [just]
        db.commit()
        assert _row(db, just).status == "cancelled" and _row(db, earlier).status == "pending"
        for said in ("never mind, how much cash?", "بی‌خیال", "Forget it"):
            assert _set_aside_on_never_mind(db, said, user_id=USER, session_id=s) == [], said    # nothing left


def test_bp4_through_the_chat_before_a_quick_answer(person, scripted):  # noqa: F811
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
    scripted(_text("۰ ریال."))
    r = client.post(f"{API}/chat", headers=phone, json={"message": "خب ولش، الان چقدر پول داریم؟",
                                                         "thread_id": first["thread_id"]})
    assert r.status_code == 200, r.text
    r = client.post(f"{API}/proposals/{card['token']}/confirm", headers=phone)
    assert r.status_code == 409, r.text                                  # set aside: nothing to confirm


def test_bp5_baisa_and_cents_are_the_small_unit():
    from app.services.ai_accountant.orchestrator import _numbers_in_text
    said = set(_numbers_in_text("390 بیسه نون خریدم"))
    assert Decimal("0.39") in said and 390 not in said
    assert {86, Decimal("0.1")} <= set(_numbers_in_text("۸۶ ریال و ۱۰۰ بیسه توی کیف پول سبزم دارم"))
    assert Decimal("0.5") in set(_numbers_in_text("2 dollars and 50 cents for the bus"))
    assert set(_numbers_in_text("paid 390 for bread")) == {390}                                  # as before


def test_bp5_the_check_takes_baisa(db, books):
    from app.services.ai_accountant.base import ToolError
    from app.services.ai_accountant.orchestrator import _numbers_in_text
    from app.services.ai_accountant.proposal_tools import _guard_amount
    ctx = ToolContext(db=db, user_id=USER, source_amounts=_numbers_in_text("390 بیسه نون خریدم"))
    _guard_amount(ctx, Decimal("0.39"))                                                         # was refused
    with pytest.raises(ToolError):
        _guard_amount(ctx, Decimal("390"))                                                      # 390 rials is not it


def test_bp6_amounts_said_in_words():
    from app.services.ai_accountant.orchestrator import _numbers_in_text
    assert _numbers_in_text("ششصد و پنجاه بیسه ویفر و چیپس خریدم") == [Decimal("0.65")]
    assert {86, Decimal("0.1")} <= set(_numbers_in_text("من هشتاد و شش ریال و صد بیسه توی کیف پول سبزم دارم"))
    assert _numbers_in_text("دو تا نون خریدم") == []                                         # not an amount
    assert _numbers_in_text("یه سه و چهاردهم ریال پیتزا") == []                              # not a number: asked


def test_bp7_an_amount_changed_far_from_the_card_is_refused(db, books):
    from app.core.messages import localize_detail
    from app.services.ai_accountant.proposal_edit import EditRefused, edit_proposal
    user = SimpleNamespace(user_id=USER, username="u", is_admin=False)
    with use_company(books.company_id):
        s = _session(db)
        token = _card(db, "۱۲.۵۸ تاکسی دادم", "12.58", session=s)
        with pytest.raises(EditRefused) as e:
            asyncio.run(edit_proposal(db, token=token, user=user, amount=Decimal("1.258")))       # a digit dropped
        msg = str(e.value)
        assert msg == ("The card said 12.580 Omani rial; 1.258 is far from it. If it's right, tell the assistant "
                       "the amount in the chat.")
        fa = localize_detail(msg, "fa")
        assert "ریال عمان" in fa and "۱۲.۵۸۰" in fa.replace("٫", ".") and "گفتگو" in fa
        assert _row(db, token).status == "pending"
        pizza = _card(db, "سه و چهارصد پیتزا", "3.14", session=s, desc="پیتزا")
        out = asyncio.run(edit_proposal(db, token=pizza, user=user, amount=Decimal("3.4")))       # a correction
        assert out["status"] == "pending" and _row(db, pizza).status == "cancelled"


def test_bp8_an_amount_in_words_that_isnt_a_number_is_asked():
    from app.services.ai_accountant.orchestrator import PERSONAL_MODE_ADDENDUM
    assert "«چهاردهم» is fourteenth, a misheard word) is asked about" in PERSONAL_MODE_ADDENDUM
    assert "«سه و چهارصد» said of Omani rials is 3.400" in PERSONAL_MODE_ADDENDUM
