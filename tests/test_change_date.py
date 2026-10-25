"""Dates from the chat, and the place a message names (scenarios BB1, BB4, owner's phone 2026-10-25)."""
from __future__ import annotations

import asyncio
import uuid
from datetime import date, timedelta

import pytest

from app.db.tenant import use_company
from app.services.ai_accountant.base import ToolContext, ToolError
from app.services.ai_eval.fixture import GREEN_WALLET, PRETTY_WALLET, seed_personal_books

USER = "u-dates"


@pytest.fixture()
def person(db):
    ctx = seed_personal_books(db)
    yield ctx
    from tests.test_admin_audit import _purge_company
    _purge_company(db, ctx.company_id)


def _propose(db, entries, message="تاریخش رو درست کن"):
    from app.services.ai_accountant.date_tools import ProposeChangeDate, ProposeChangeDateInput
    return asyncio.run(ProposeChangeDate().run(ToolContext(db=db, user_id=USER, user_message=message, mode="personal"),
                                               ProposeChangeDateInput(entries=entries)))


def _date_of(db, tid):
    from app.models.transaction import Transaction
    db.expire_all()
    return db.get(Transaction, uuid.UUID(tid)).date


def test_bb4_a_wrong_date_is_changed_and_put_back(db, person):
    from app.services.ai_accountant.execute_service import execute_proposal, undo_action
    tid = person.txns["airbnb"]
    with use_company(person.company_id):
        before = _date_of(db, tid)
        real = date.today() - timedelta(days=40)
        card = _propose(db, [{"transaction_id": tid, "date": real.isoformat()}])
        assert card["preview"]["entries"][0]["old_date"] == before.isoformat()
        assert "Muscat Airbnb" in card["preview"]["description"] and "تاریخ" in card["preview"]["description"]
        assert "amount" not in card["preview"] and "lines" not in card["preview"]       # it moves no money
        done = execute_proposal(db, confirmation_token=card["confirmation_token"], actor_user_id=USER)
        assert _date_of(db, tid) == real
        undone = undo_action(db, audit_log_id=done.audit_log_id, actor_user_id=USER)
        assert undone.mode == "restored" and _date_of(db, tid) == before


def test_bb4_the_weekly_spending_follows_the_date(db, person):
    from app.services.ai_accountant.execute_service import execute_proposal
    from app.services.ai_accountant.spending_tools import GetSpendingSummary, GetSpendingSummaryInput
    ctx = lambda: ToolContext(db=db, user_id=USER, mode="personal")  # noqa: E731
    with use_company(person.company_id):
        usd = lambda: next((c for c in asyncio.run(GetSpendingSummary().run(ctx(), GetSpendingSummaryInput(days=10, currency="USD")))  # noqa: E731
                            .get("by_category") or []), None)
        assert usd() is not None                                                # the Airbnb, 8 days ago
        card = _propose(db, [{"transaction_id": person.txns["airbnb"], "date": (date.today() - timedelta(days=60)).isoformat()}])
        execute_proposal(db, confirmation_token=card["confirmation_token"], actor_user_id=USER)
        assert usd() is None                                                    # no longer in the last 10 days


def test_bb4_a_future_date_or_no_change_is_refused(db, person):
    with use_company(person.company_id):
        with pytest.raises(ToolError) as e:
            _propose(db, [{"transaction_id": person.txns["airbnb"], "date": (date.today() + timedelta(days=3)).isoformat()}])
        assert e.value.code == "future_date"
        same = _date_of(db, person.txns["airbnb"])
        with pytest.raises(ToolError) as e:
            _propose(db, [{"transaction_id": person.txns["airbnb"], "date": same.isoformat()}])
        assert e.value.code == "nothing_to_change"
        with pytest.raises(ToolError) as e:
            _propose(db, [{"transaction_id": str(uuid.uuid4()), "date": same.isoformat()}])
        assert e.value.code == "not_found"


def test_bb4_a_jalali_date_is_converted():
    from app.services.ai_accountant.orchestrator import _gregorian_dates
    out = _gregorian_dates({"entries": [{"transaction_id": "x", "date": "1405-06-15"}]})
    assert out["entries"][0]["date"] == "2026-09-06"


def test_bb4_offered_for_words_about_dates():
    from app.services.ai_accountant import tool_routing as r
    assert "dates" in r.route("تاریخش رو درست کن", personal=True)
    assert "dates" in r.route("the Airbnb was dated wrong", personal=False)


def test_bb1_said_amounts_keep_their_decimals():
    from app.services.ai_accountant.orchestrator import _said_amount, _said_amounts
    said = _said_amounts("باقی برای من ۱۵.۳ ریال آورد و ۱٬۱۴۵ تومن")
    assert _said_amount("15.300") in said and _said_amount(1145) in said and _said_amount(13.7) not in said


def test_bb1_a_named_place_moves_only_the_cards_of_the_amounts_said(db, person):
    from app.services.ai_accountant.orchestrator import run_chat_turn
    from tests.test_ai_accountant_orchestrator import _assistant_text, _assistant_tool_call

    class _Model:
        shape = "fake"

        def __init__(self, *replies):
            self.replies = list(replies)

        async def chat(self, *, system_prompt, tools, messages, model=None, max_tokens=8192):
            return self.replies.pop(0)

    today = date.today().isoformat()
    lulu = {"date": today, "description": "لولو", "currency": "OMR",
            "lines": [{"account_code": "6110", "debit": "33", "credit": 0}, {"account_code": PRETTY_WALLET, "debit": 0, "credit": "33"}]}
    rest = {"date": today, "description": "خرید همسرم", "currency": "OMR",
            "lines": [{"account_code": "6110", "debit": "15.3", "credit": 0}, {"account_code": PRETTY_WALLET, "debit": 0, "credit": "15.3"}]}
    with use_company(person.company_id):
        out = asyncio.run(run_chat_turn(db, user_id=USER, client=_Model(
            _assistant_tool_call("propose_create_transaction", lulu), _assistant_tool_call("propose_create_transaction", rest),
            _assistant_text("دو کارت.")), user_message="۱۵.۳ ریال از کیف پول سبز برای خرید همسرم دادم", mode="personal", lang="fa"))
        by = {p["preview"]["description"]: p["preview"]["lines"] for p in out.proposals}
        assert by["لولو"][1]["account_code"] == PRETTY_WALLET                   # 33 wasn't said: left as it was
        assert by["خرید همسرم"][1]["account_code"] == GREEN_WALLET              # 15.3 was: to the place named
        bare = asyncio.run(run_chat_turn(db, user_id=USER, client=_Model(
            _assistant_tool_call("propose_create_transaction", lulu), _assistant_text("کارت.")),
            user_message="کیف پول سبز", mode="personal", lang="fa"))
        assert bare.proposals[0]["preview"]["lines"][1]["account_code"] == GREEN_WALLET   # a bare answer: as before
