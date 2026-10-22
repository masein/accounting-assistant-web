"""A lean conversation (scenarios AW1–AW7, 2026-10-22).

Each chat message sent the model the whole thread — every earlier tool result's JSON included — with
~24k tokens of instructions and tools, on each of its two or three calls."""
from __future__ import annotations

import json
import uuid

import pytest

from app.services.ai_accountant.llm_protocol import ChatMessage, ToolCall
from app.services.ai_accountant.orchestrator import KEEP_COMPACT, KEEP_WHOLE, _compact_history


def _exchange(i: int, *, card: bool = False, status: str = "pending") -> list[ChatMessage]:
    """A question, a lookup with a sizeable result, and an answer — and maybe a card."""
    call = ToolCall(id=f"c{i}", name="get_spending_summary", input={"period": "last_week"})
    result = {"kind": "spending", "total": 41.5, "by_category": [
        {"category_code": f"61{n:02d}", "category_name": "خوراک و سوپرمارکت", "amount": 20.25, "count": 3}
        for n in range(12)], "period": {"from": "2026-10-10", "to": "2026-10-16", "label_fa": "۱۸ تا ۲۴ مهر"}}
    msgs = [ChatMessage(role="user", text=f"سوال {i}: هفته گذشته چقدر خرج کردم؟"),
            ChatMessage(role="assistant", tool_calls=[call]),
            ChatMessage(role="tool", tool_call_id=call.id, text=json.dumps(result, ensure_ascii=False))]
    if card:
        pc = ToolCall(id=f"p{i}", name="propose_create_transaction", input={"description": f"ناهار {i}"})
        msgs += [ChatMessage(role="assistant", tool_calls=[pc]),
                 ChatMessage(role="tool", tool_call_id=pc.id, text=json.dumps({
                     "confirmation_token": str(uuid.UUID(int=i)), "status": status,
                     "summary": f"ناهار {i} — ۷٫۰۰۰ ریال عمان از کیف پول", "preview": {"lines": [1, 2, 3]}},
                     ensure_ascii=False))]
    msgs.append(ChatMessage(role="assistant", text=f"جواب {i}: ۴۱٫۵۰۰ ریال عمان."))
    return msgs


def _thread(n: int, **kw) -> list[ChatMessage]:
    return [m for i in range(n) for m in _exchange(i, card=(i % 3 == 0), **kw)]


def test_aw1_the_last_exchanges_as_they_were():
    history = _thread(6)
    out = _compact_history(list(history))
    tail = [m for i in (4, 5) for m in _exchange(i, card=(i % 3 == 0))]
    assert [m.to_dict() for m in out[-len(tail):]] == [m.to_dict() for m in tail]
    assert KEEP_WHOLE == 2


def test_aw2_aw7_older_ones_as_what_was_said():
    out = _compact_history(_thread(6, status="executed"))
    first = out[:2]
    assert [m.role for m in first] == ["user", "assistant"]
    assert first[0].text == "سوال 0: هفته گذشته چقدر خرج کردم؟"
    assert first[1].text.startswith("جواب 0: ۴۱٫۵۰۰ ریال عمان.")
    assert "[Cards: ناهار 0 — ۷٫۰۰۰ ریال عمان از کیف پول — executed (card 00000000-0000-0000-0000-000000000000)]" in first[1].text
    older = out[:-len([m for i in (4, 5) for m in _exchange(i, card=(i % 3 == 0))])]
    assert all(m.role in ("user", "assistant") and not m.tool_calls for m in older)   # no tool calls or results


def test_aw3_the_oldest_left_out():
    out = _compact_history(_thread(KEEP_WHOLE + KEEP_COMPACT + 5))
    assert out[0].role == "user" and out[0].text.startswith("[Earlier messages of this thread are left out.")
    assert "سوال 5:" in out[0].text                                  # the first kept is the sixth
    assert not any("سوال 4:" in (m.text or "") for m in out)


def test_aw4_half_or_less():
    history = _thread(20)
    full = sum(len(json.dumps(m.to_dict(), ensure_ascii=False)) for m in history)
    lean = sum(len(json.dumps(m.to_dict(), ensure_ascii=False)) for m in _compact_history(list(history)))
    assert lean < full / 2, (lean, full)


def test_aw2_a_short_thread_is_as_it_was():
    history = _thread(2)
    assert _compact_history(list(history)) == history


def test_aw5_the_same_instructions_every_call(monkeypatch):
    """Two messages, a day apart and in two languages: the prompts are the same up to «Today:»."""
    from datetime import date
    from app.services.ai_accountant import orchestrator as o
    src = o.SYSTEM_PROMPT + o.PERSONAL_MODE_ADDENDUM
    assert "{today}" not in src and "{lang_name}" not in src
    import inspect
    body = inspect.getsource(o.run_chat_turn)
    assert body.index('system_prompt += f"\\n\\nToday: {today_str}."') > body.index("prompt_block(db)")
    assert body.index("The interface language is") > body.index("Today: {today_str}")


def test_aw6_a_cached_prompt_counts_as_it_costs(db):
    from datetime import datetime, timezone
    from app.models.ai_usage import AIUsageEvent
    from app.models.company import Company
    from app.services import ai_usage
    from tests.test_admin_audit import _purge_company
    c = Company(id=uuid.uuid4(), name="Cache Co", slug=f"cc-{uuid.uuid4().hex[:8]}", locale="uk",
                base_currency="GBP", status="active", token_version=0)
    db.add(c)
    db.commit()
    try:
        db.add(AIUsageEvent(company_id=c.id, user_id="u1", username="x", provider="metis", model="gpt-4.1-mini",
                            purpose="chat", input_tokens=1_000_000, output_tokens=0, cached_tokens=800_000,
                            cache_write_tokens=0, estimated=False, cost_micros=0, outcome="ok", duration_ms=5,
                            created_at=datetime.now(timezone.utc)))
        db.commit()
        used, _oldest = ai_usage._used_since(db, datetime.now(timezone.utc) - ai_usage.WINDOW, company_id=c.id)
        assert used == 400_000
    finally:
        _purge_company(db, str(c.id))
