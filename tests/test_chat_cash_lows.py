"""Chat LOWs from the 2026-09-24 QA run.

5.9  "how much cash do we have?" answered from account 1110 alone.
A.8-2 personal mode: "نقدی" (cash) posted to 1110 (the bank) instead of 1120.
5.2  "ناهار" had no account → the model asked instead of using general expenses.
"""
from __future__ import annotations

import asyncio
import uuid
from datetime import date

import pytest

from app.db.seed import PERSONAL_SEED_ACCOUNTS, SEED_ACCOUNTS
from app.services.ai_accountant.base import ToolContext
from app.services.ai_accountant.cash_tools import GetCashPosition, GetCashPositionInput, cash_account_codes
from app.services.ai_accountant.read_tools import SearchAccounts, SearchAccountsInput
from tests.test_agent_convergence_tools import _make_session, _parent_code_ir


@pytest.fixture()
def personal_db():
    chart = [(code, fa, level) for code, fa, _en, level in PERSONAL_SEED_ACCOUNTS]
    db = _make_session(chart, _parent_code_ir, "ir")
    try:
        yield db
    finally:
        db.close()


@pytest.fixture()
def business_db():
    db = _make_session(SEED_ACCOUNTS, _parent_code_ir, "ir")
    try:
        yield db
    finally:
        db.close()


def _search(db, query, mode="default"):
    out = asyncio.run(SearchAccounts().run(ToolContext(db=db, user_id="u", mode=mode), SearchAccountsInput(query=query)))
    return [m["code"] for m in out["matches"]]


class TestPersonalAliases:
    def test_cash_words_go_to_cash_on_hand(self, personal_db):
        assert _search(personal_db, "نقدی", mode="personal")[0] == "1120"
        assert _search(personal_db, "پول نقد", mode="personal")[0] == "1120"
        assert _search(personal_db, "cash", mode="personal")[0] == "1120"

    def test_card_and_bank_words_go_to_the_bank_account(self, personal_db):
        assert _search(personal_db, "کارت به کارت", mode="personal")[0] == "1110"
        assert _search(personal_db, "bank", mode="personal")[0] == "1110"

    def test_everyday_categories_resolve(self, personal_db):
        assert _search(personal_db, "نان", mode="personal")[0] == "6110"
        assert _search(personal_db, "ناهار", mode="personal")[0] == "6180"
        assert _search(personal_db, "تاکسی", mode="personal")[0] == "6130"
        assert _search(personal_db, "حقوق", mode="personal")[0] == "4110"

    def test_business_mode_keeps_the_sme_mapping(self, business_db):
        assert _search(business_db, "نقد")[0] == "1110"
        assert _search(business_db, "cash")[0] == "1110"


def _run_cash(db, mode="default"):
    return asyncio.run(GetCashPosition().run(ToolContext(db=db, user_id="u", mode=mode), GetCashPositionInput()))


def test_cash_position_sums_the_cash_box_and_every_bank_account(auth_client, db, make_transaction):
    bank = auth_client.post("/entities", json={"type": "bank", "name": f"بانک ملت {uuid.uuid4().hex[:6]}"}).json()
    bank_code = bank["code"]
    assert bank_code and bank_code != "1110"
    before = _run_cash(db)
    make_transaction([(bank_code, 4_000_000, 0), ("3110", 0, 4_000_000)], tx_date=date(2026, 3, 2))
    make_transaction([("6112", 1_000_000, 0), ("1110", 0, 1_000_000)], tx_date=date(2026, 3, 2))
    db.flush()
    after = _run_cash(db)
    codes = {a["account_code"] for a in after["accounts"]}
    assert {"1110", bank_code} <= codes
    assert after["total"] - before["total"] == 3_000_000
    by = {a["account_code"]: a["balance"] for a in after["accounts"]}
    by0 = {a["account_code"]: a["balance"] for a in before["accounts"]}
    assert by[bank_code] - by0.get(bank_code, 0) == 4_000_000
    assert by["1110"] - by0.get("1110", 0) == -1_000_000
    assert after["currency"]


def test_cash_position_in_personal_mode_includes_cash_on_hand(personal_db):
    assert cash_account_codes(personal_db, locale="ir", mode="personal") == ["1110", "1120"]
    out = _run_cash(personal_db, mode="personal")
    assert [a["account_code"] for a in out["accounts"]] == ["1110", "1120"]
    assert out["total"] == 0 and "note" not in out


class TestPromptWiring:
    def test_rules_and_tool_are_in_the_prompt(self, db):
        from app.services.ai_accountant.orchestrator import run_chat_turn
        from tests.test_ai_accountant_orchestrator import _assistant_text

        captured: dict = {}

        class _Cap:
            shape = "fake"

            async def chat(self, *, system_prompt, tools, messages, model=None, max_tokens=8192):
                captured["system_prompt"] = system_prompt
                captured["tools"] = tools
                return _assistant_text("ok")

        asyncio.run(run_chat_turn(db, user_id="u1", user_message="hi", client=_Cap(), mode="personal"))
        sp = captured["system_prompt"]
        assert "get_cash_position" in sp
        assert "When no expense category matches" in sp
        assert "موجودی نقد (1120" in sp
        assert "get_cash_position" in {t["name"] for t in captured["tools"]}

    def test_personal_turn_resolves_cash_to_1120(self, personal_db):
        from app.services.ai_accountant.orchestrator import run_chat_turn
        from tests.test_ai_accountant_orchestrator import _FakeClient, _assistant_text, _assistant_tool_call

        client = _FakeClient([
            _assistant_tool_call("search_accounts", {"query": "نقدی"}),
            _assistant_text("ثبت شد."),
        ])
        result = asyncio.run(run_chat_turn(personal_db, user_id="u1", user_message="۵۰ هزار تومان نان نقدی خریدم",
                                           client=client, mode="personal"))
        assert result.turns == 2
        tool_result_text = str(client.sent[1][-1].to_dict())
        assert '"code": "1120"' in tool_result_text.replace("'", '"')
        first_code = tool_result_text.replace("'", '"').split('"code": "')[1][:4]
        assert first_code == "1120"
