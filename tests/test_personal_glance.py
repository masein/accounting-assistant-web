"""A person's books at a glance in the assistant's instructions (scenarios AY3–AY5, 2026-10-22)."""
from __future__ import annotations

import asyncio

import pytest

from app.services.ai_eval.fixture import GREEN_WALLET, MELLAT, PRETTY_WALLET, seed_personal_books
from tests.test_ai_accountant_orchestrator import _assistant_text


@pytest.fixture()
def person(db):
    ctx = seed_personal_books(db)
    yield ctx
    from tests.test_admin_audit import _purge_company
    _purge_company(db, ctx.company_id)


def _prompt(db, message, *, mode):
    from app.services.ai_accountant.orchestrator import run_chat_turn
    seen = {}

    class _Cap:
        shape = "fake"

        async def chat(self, *, system_prompt, tools, messages, model=None, max_tokens=8192):
            seen["system"] = system_prompt
            return _assistant_text("ok")

    asyncio.run(run_chat_turn(db, user_id="u1", user_message=message, client=_Cap(), mode=mode))
    return seen["system"]


def test_ay3_the_places_with_their_currencies_the_categories_and_the_people(db, person):
    from app.db.tenant import use_company
    from app.services.ai_accountant.personal_glance import glance_block
    with use_company(person.company_id):
        block = glance_block(db)
    assert f"{GREEN_WALLET} «کیف پول سبز» (cash, OMR)" in block
    assert f"{PRETTY_WALLET} «کیف پول قشنگم» (cash, OMR)" in block
    assert f"{MELLAT} «حساب ملت» (bank, IRR)" in block
    assert "«موجودی نقد»" not in block                       # a heading with wallets under it and no entries
    for line in ("6125 «لوازم و تعمیرات خانه»", "6130 «حمل‌ونقل»", "6185 «سفر»", "4110 «حقوق و دستمزد»"):
        assert line in block
    assert f"«امیر» (entity_id {person.entities['امیر']})" in block
    assert "never stop to ask whether to add them" in block    # AY5: someone new goes on the card


def test_ay3_a_persons_instructions_carry_it_and_a_companys_do_not(db, person):
    from app.db.tenant import use_company
    with use_company(person.company_id):
        personal = _prompt(db, "I paid 3.5 OMR for a taxi from my green wallet", mode="personal")
    assert "# YOUR BOOKS AT A GLANCE" in personal and "«کیف پول سبز» (cash, OMR)" in personal
    # the same for every message of these books: before the currency line, the date and the language
    assert personal.index("# YOUR BOOKS AT A GLANCE") < personal.index("These books count in") < personal.rindex("Today:")
    assert "# YOUR BOOKS AT A GLANCE" not in _prompt(db, "how much cash do we have?", mode="default")


def test_ay2_the_fast_answer_lists_the_same_categories(db, person):
    from app.db.tenant import use_company
    from app.services.ai_accountant import fast_paths
    with use_company(person.company_id):
        out = asyncio.run(fast_paths.answer(db, "چه دسته‌بندی‌هایی برای خرج داری؟", user_id="u1", username=None,
                                            lang="fa", mode="personal"))
    assert out is not None and out.intent == "categories"
    assert "سفر" in out.text and "لوازم و تعمیرات خانه" in out.text and "حقوق و دستمزد" in out.text
