"""Only the tools and instructions a message needs (scenarios AX1–AX5, 2026-10-22)."""
from __future__ import annotations

import asyncio

from app.services.ai_accountant import tool_routing as r
from tests.test_ai_accountant_orchestrator import _assistant_text, _assistant_tool_call


class _Capture:
    """A scripted model that keeps what each call offered."""
    shape = "fake"

    def __init__(self, *replies):
        self.replies, self.calls = list(replies), []

    async def chat(self, *, system_prompt, tools, messages, model=None, max_tokens=8192):
        self.calls.append({"system": system_prompt, "tools": [t["name"] for t in tools]})
        return self.replies.pop(0) if self.replies else _assistant_text("ok")


def test_ax1_a_lunch_gets_the_core_and_the_money_tools(db):
    from app.services.ai_accountant.orchestrator import run_chat_turn
    cap = _Capture(_assistant_text("ok"))
    asyncio.run(run_chat_turn(db, user_id="u1", user_message="امروز ۷ ریال عمان ناهار خوردم از کیف پول قشنگم",
                              client=cap, mode="personal", lang="fa"))
    tools = cap.calls[0]["tools"]
    assert tools[:3] == ["find_entity", "search_accounts", "propose_create_transaction"]
    assert {"get_cash_position", "propose_money_place"} <= set(tools)
    assert not {"propose_set_budget", "review_bank_statement", "get_payroll", "propose_create_invoice"} & set(tools)
    assert len(tools) <= 23                     # «امروز» brings spending too; 37 in all
    system = cap.calls[0]["system"]
    assert "# Behavioural rules" in system and "PERSONAL-FINANCE MODE" in system
    assert "# Payroll, budgets and month end" not in system and "# Bank statements" not in system
    assert "# Shareholder equity" not in system


def test_ax2_an_answer_carries_on():
    groups = r.route("کیف پول قشنگم", recent_tools=["get_people_balances", "ask_with_choices"], personal=True)
    assert "money" in groups


def test_ax3_a_tool_it_was_not_offered_is_added_in_the_same_message(db):
    from app.services.ai_accountant.orchestrator import run_chat_turn
    cap = _Capture(_assistant_tool_call("more_tools", {"groups": ["planning"]}),
                   _assistant_tool_call("get_budget_status", {}),
                   _assistant_text("ok"))
    asyncio.run(run_chat_turn(db, user_id="u1", user_message="hmm", client=cap, mode="personal"))
    assert "get_budget_status" not in cap.calls[0]["tools"]
    assert "get_budget_status" in cap.calls[1]["tools"] and "get_budget_status" in cap.calls[2]["tools"]


def test_ax4_the_same_start_every_time():
    avail = {n for g in r.GROUPS.values() for n in g[1]} | set(r.CORE)
    a = r.tool_names(r.route("I paid 3.5 OMR for a taxi", personal=True), avail)
    b = r.tool_names(r.route("how much did I spend this month?", personal=True), avail)
    core = [t for t in r.CORE if t != "more_tools"]
    assert a[:len(core)] == b[:len(core)] == core
    assert r.more_tools_spec() == r.more_tools_spec()               # the last tool never changes
    assert set(r.more_tools_spec()["input_schema"]["properties"]["groups"]["items"]["enum"]) == set(r.GROUPS)


def test_ax4_every_registered_tool_is_core_or_in_a_group():
    from app.services.ai_accountant.orchestrator import build_default_registry, build_personal_registry
    grouped = set(r.CORE) | {n for _d, tools in r.GROUPS.values() for n in tools}
    for reg in (build_default_registry(), build_personal_registry()):
        missing = {t["name"] for t in reg.to_anthropic()} - grouped
        assert not missing, missing                                  # a new tool must join a group


def test_ax4_every_topic_section_is_still_in_the_instructions():
    from app.services.ai_accountant.orchestrator import SYSTEM_PROMPT
    headings = [h for h, _t in r.split_sections(SYSTEM_PROMPT)]
    for start in r.SECTION_GROUPS:
        assert any(h.startswith(start) for h in headings), start      # a renamed heading would fall out
    every = r.instructions(SYSTEM_PROMPT, set(r.GROUPS) | {"reply", "attachments"})
    assert every == SYSTEM_PROMPT                                     # all of it, as written


def test_routing_can_be_switched_off(db, monkeypatch):
    """AI_TOOL_ROUTING=false: every tool and all the instructions, as before."""
    from app.core.config import settings
    from app.services.ai_accountant.orchestrator import SYSTEM_PROMPT, build_personal_registry, run_chat_turn
    monkeypatch.setattr(settings, "ai_tool_routing", False)
    cap = _Capture(_assistant_text("ok"))
    asyncio.run(run_chat_turn(db, user_id="u1", user_message="hmm", client=cap, mode="personal"))
    assert set(cap.calls[0]["tools"]) == {t["name"] for t in build_personal_registry().to_anthropic()}
    assert SYSTEM_PROMPT in cap.calls[0]["system"]
