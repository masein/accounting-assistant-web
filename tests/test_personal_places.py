"""A person's places asked about one at a time, closed when given up, and only the tools their books have
(chat review 2026-11-10, scenarios BV1–BV5).

From the owner's phone: «چقدر پول داریم تو کیف پول سبز مون» got every place's money; a wallet whose money is now
with the user's wife could only be "a starting balance of 0" and stayed in every list and total; and asking the
assistant for "operations" tools in a person's books got "No such group" back.
"""
from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import select

from app.db.tenant import use_company
from app.models.account import Account
from app.services.ai_accountant.base import ToolContext, ToolError
from app.services.ai_eval.fixture import GREEN_WALLET, PRETTY_WALLET, WALL_E, seed_personal_books

USER = "u-places"


@pytest.fixture()
def books(db):
    ctx = seed_personal_books(db)
    yield ctx
    from tests.test_admin_audit import _purge_company
    _purge_company(db, ctx.company_id)


def _fast(db, books, message, lang="en"):
    from app.services.ai_accountant.fast_paths import answer
    with use_company(books.company_id):
        out = asyncio.run(answer(db, message, user_id=USER, username=None, lang=lang, mode="personal"))
    return out.text if out else None


def test_bv1_one_place_asked_about(db, books):
    green = _fast(db, books, "چقدر پول داریم تو کیف پول سبز مون؟")
    assert green.startswith("کیف پول سبز today:") and "383.750" in green and "39.750" not in green
    wall_e = _fast(db, books, "how much money do we have in کیف پول والای")
    assert "2,400" in wall_e and "1,785" in wall_e
    assert _fast(db, books, "چقدر پول داریم تو کیف پولم؟") is None             # which wallet? the model asks
    assert "حساب ملت" not in (_fast(db, books, "چقدر پول داریم؟") or "")     # everything, as before
    fa = _fast(db, books, "چقدر پول داریم تو کیف پول قشنگم؟", lang="fa")
    assert fa.startswith("موجودی کیف پول قشنگم امروز:")


def test_bv2_one_currency_asked_about(db, books):
    omr = _fast(db, books, "چقدر پول داریم به ریال عمان؟")
    assert "423.500" in omr and "2,400" not in omr                              # 383.75 + 39.75, nothing else
    usd = _fast(db, books, "how much money do we have in dollars?")
    assert "2,400" in usd and "1,785" not in usd
    green_usd = _fast(db, books, "چقدر دلار داریم تو کیف پول سبز؟ چقدر پول داریم")
    assert green_usd is None or "دلار" not in green_usd or "nothing" in green_usd


def _empty(db, books, **kw):
    from app.services.ai_accountant.money_places import ProposeEmptyPlace, ProposeEmptyPlaceInput
    with use_company(books.company_id):
        return asyncio.run(ProposeEmptyPlace().run(
            ToolContext(db=db, user_id=USER, mode="personal", user_message="کیف پول والای رو ببند"),
            ProposeEmptyPlaceInput(**kw)))


def _confirm(db, books, card):
    from app.services.ai_accountant.execute_service import execute_proposal
    with use_company(books.company_id):
        return execute_proposal(db, confirmation_token=card["confirmation_token"], actor_user_id=USER)


def test_bv3_a_place_given_up_is_closed(db, books):
    from app.services.ai_accountant.execute_service import undo_action
    from app.services.ai_accountant.money_places import _places
    from app.services.ai_accountant.personal_glance import places
    with pytest.raises(ToolError) as e:
        _empty(db, books, place="کیف پول والای", close=True, rest_account="6195")
    assert e.value.code == "close_several"                                   # dollars and dirhams in it
    _confirm(db, books, _empty(db, books, place="کیف پول والای", currency="AED", rest_account="6195"))
    card = _empty(db, books, place="کیف پول والای", currency="USD", rest_account="6195", close=True)
    assert card["preview"]["close"] is True and "closed" in card["summary"]
    done = _confirm(db, books, card)
    with use_company(books.company_id):
        wall_e = db.execute(select(Account).where(Account.code == WALL_E)).scalar_one()
        assert wall_e.is_active is False
        assert WALL_E not in {a.code for a in _places(db)} and WALL_E not in {a.code for a, _k, _c in places(db)}
        undo_action(db, audit_log_id=done.audit_log_id, actor_user_id=USER)       # taken back: open again
        db.refresh(wall_e)
        assert wall_e.is_active is True
    # a place with nothing in it is just closed
    _confirm(db, books, _empty(db, books, place="کیف پول قشنگم", rest_account="6110"))
    card = _empty(db, books, place="کیف پول قشنگم", close=True)
    assert card["preview"]["lines"] == [] and "holds nothing" in card["summary"]
    _confirm(db, books, card)
    with use_company(books.company_id):
        assert db.execute(select(Account.is_active).where(Account.code == PRETTY_WALLET)).scalar_one() is False
    with pytest.raises(ToolError) as e:
        _empty(db, books, place="1120", close=True)
    assert e.value.code in ("main_place", "no_place")


def test_bv4_a_zero_starting_balance_is_a_close(db, books):
    from app.services.ai_accountant.money_places import ProposeMoneyPlace, ProposeMoneyPlaceInput
    with use_company(books.company_id), pytest.raises(ToolError) as e:
        asyncio.run(ProposeMoneyPlace().run(
            ToolContext(db=db, user_id=USER, mode="personal", user_message="کیف پول سبز رو حذف کن، پولاش دست همسره"),
            ProposeMoneyPlaceInput(place="کیف پول سبز", kind="cash", currency="OMR", amount=0)))
    assert e.value.code == "close_with_empty_place" and GREEN_WALLET in e.value.message
    from app.services.ai_accountant.orchestrator import PERSONAL_MODE_ADDENDUM
    assert "CLOSING A PLACE" in PERSONAL_MODE_ADDENDUM and "close=true" in PERSONAL_MODE_ADDENDUM


class _Capture:
    shape = "fake"

    def __init__(self, *replies):
        self.replies, self.calls = list(replies), []

    async def chat(self, *, system_prompt, tools, messages, model=None, max_tokens=8192):
        self.calls.append({"tools": tools, "messages": list(messages)})
        from tests.test_ai_accountant_orchestrator import _assistant_text
        return self.replies.pop(0) if self.replies else _assistant_text("ok")


def test_bv5_only_the_groups_a_persons_books_have(db):
    from app.services.ai_accountant import tool_routing as r
    from app.services.ai_accountant.orchestrator import run_chat_turn
    from tests.test_ai_accountant_orchestrator import _assistant_text, _assistant_tool_call
    cap = _Capture(_assistant_tool_call("more_tools", {"groups": ["operations", "dates"]}), _assistant_text("ok"))
    asyncio.run(run_chat_turn(db, user_id="u-bv5", user_message="hmm", client=cap, mode="personal"))
    spec = next(t for t in cap.calls[0]["tools"] if t["name"] == "more_tools")
    offered = spec["input_schema"]["properties"]["groups"]["items"]["enum"]
    assert "operations" not in offered and "payroll" not in offered and "money" in offered
    assert "- operations:" not in spec["description"]
    said = [m.text for m in cap.calls[1]["messages"] if m.role == "tool"][-1]
    assert said.startswith("Added: propose_change_date.") and "Not in these books: operations" in said
    assert "No such group" not in said
    assert set(r.more_tools_spec()["input_schema"]["properties"]["groups"]["items"]["enum"]) == set(r.GROUPS)
