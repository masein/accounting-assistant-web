"""Orchestrator tests — provider-neutral.

Drives the agent loop with a fake ``LLMClient`` so we never touch any
real provider. Each scenario asserts on the normalized ``ChatMessage``
exchanges the orchestrator produces.

Covers the brief's acceptance scenarios that don't require a live model:
no-match, single-match, multi-match, pure query, tool errors, session
history persistence, and the MAX_TURNS safety cap. Same set as before
the multi-provider refactor — but the orchestrator now consumes
``LLMResponse`` / ``ChatMessage`` instead of an Anthropic-specific
``Message``.
"""
from __future__ import annotations

import asyncio
import json
import uuid
from dataclasses import dataclass, field
from typing import Any

import pytest
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.models.ai_accountant import AIChatMessage, AIChatSession, AIProposal
from app.models.audit_log import AuditLog
from app.models.entity import Entity, TransactionEntity
from app.models.transaction import Transaction, TransactionLine
from app.services.ai_accountant import orchestrator
from app.services.ai_accountant.llm_protocol import (
    ChatMessage, LLMResponse, LLMUsage, ToolCall,
)
from app.services.ai_accountant.orchestrator import build_default_registry, run_chat_turn


# ---------------------------------------------------------------------------
# Cleanup
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _isolate(db: Session):
    yield
    db.execute(delete(AIChatMessage))
    db.execute(delete(AIChatSession))
    db.execute(delete(AIProposal))
    db.execute(delete(TransactionEntity))
    db.execute(delete(TransactionLine))
    db.execute(delete(Transaction))
    db.execute(delete(AuditLog))
    db.execute(delete(Entity))
    db.commit()


# ---------------------------------------------------------------------------
# Fake LLM client
# ---------------------------------------------------------------------------


class _FakeClient:
    """Drives the orchestrator off a queue of pre-canned ``LLMResponse``
    objects, capturing the messages array sent to it each turn."""

    shape = "fake"

    def __init__(self, scripted: list[LLMResponse]) -> None:
        self._queue = list(scripted)
        self.sent: list[list[ChatMessage]] = []

    async def chat(self, *, system_prompt, tools, messages, model=None, max_tokens=8192) -> LLMResponse:
        self.sent.append([ChatMessage.from_dict(m.to_dict()) for m in messages])
        if not self._queue:
            raise AssertionError("scripted responses exhausted")
        return self._queue.pop(0)


def _assistant_text(text: str, *, stop: str = "end_turn") -> LLMResponse:
    return LLMResponse(
        message=ChatMessage(role="assistant", text=text),
        stop_reason=stop,
        usage=LLMUsage(input_tokens=100, output_tokens=20),
    )


def _assistant_tool_call(name: str, args: dict, *, call_id: str | None = None) -> LLMResponse:
    return LLMResponse(
        message=ChatMessage(
            role="assistant",
            text=None,
            tool_calls=[ToolCall(id=call_id or f"call_{uuid.uuid4().hex[:8]}", name=name, input=args)],
        ),
        stop_reason="tool_use",
        usage=LLMUsage(input_tokens=100, output_tokens=20),
    )


# ---------------------------------------------------------------------------
# Scenarios
# ---------------------------------------------------------------------------


class TestOrchestrator:
    def test_pure_query_no_proposal(self, db: Session) -> None:
        client = _FakeClient([
            _assistant_tool_call("get_account_balance", {"account_code": "1110"}),
            _assistant_text("Your cash balance is 0 IRR."),
        ])
        result = asyncio.run(run_chat_turn(
            db, user_id="u1", user_message="what's my cash balance?", client=client,
        ))
        assert result.proposals == []
        assert "cash" in result.text.lower() or "balance" in result.text.lower()
        assert result.stop_reason == "end_turn"
        assert result.turns == 2
        assert result.provider_shape == "fake"

    def test_single_match_proposes_transaction(self, db: Session) -> None:
        kim = Entity(name="Kim Nguyen", type="employee")
        db.add(kim)
        db.commit()
        db.refresh(kim)
        kim_id = str(kim.id)
        client = _FakeClient([
            _assistant_tool_call("find_entity", {"query": "Kim"}),
            _assistant_tool_call("propose_create_transaction", {
                "date": "2026-05-23",
                "description": "paid Kim Nguyen — salary",
                "currency": "IRR",
                "lines": [
                    {"account_code": "6110", "debit": 1_000_000, "credit": 0},
                    {"account_code": "1110", "debit": 0, "credit": 1_000_000},
                ],
                "entity_links": [{"entity_id": kim_id, "role": "employee"}],
            }),
            _assistant_text("I drafted a proposal to pay Kim Nguyen 1,000,000 IRR. Please confirm."),
        ])
        result = asyncio.run(run_chat_turn(
            db, user_id="u1", user_message="paid Kim 1k yesterday", client=client,
        ))
        assert len(result.proposals) == 1
        prop = result.proposals[0]
        assert prop["tool_name"] == "propose_create_transaction"
        token = uuid.UUID(prop["confirmation_token"])
        row = db.execute(
            select(AIProposal).where(AIProposal.confirmation_token == token)
        ).scalar_one()
        assert row.status == "pending"

    def test_no_match_returns_zero_results(self, db: Session) -> None:
        client = _FakeClient([
            _assistant_tool_call("find_entity", {"query": "Zelenoff"}),
            _assistant_text("I couldn't find anyone named Zelenoff. Should I create a new entity?"),
        ])
        result = asyncio.run(run_chat_turn(
            db, user_id="u1", user_message="paid Zelenoff 500", client=client,
        ))
        assert result.proposals == []
        # Turn 2's outgoing messages should include the tool result with empty matches.
        turn2 = client.sent[1]
        tool_msg = next(m for m in turn2 if m.role == "tool")
        assert '"matches": []' in (tool_msg.text or "")

    def test_multiple_matches_returned_to_model(self, db: Session) -> None:
        db.add_all([
            Entity(name="Kim Nguyen", type="employee"),
            Entity(name="Kim Industries", type="client"),
        ])
        db.commit()
        client = _FakeClient([
            _assistant_tool_call("find_entity", {"query": "Kim"}),
            _assistant_text("Two matches: Kim Nguyen (employee) or Kim Industries (client). Which?"),
        ])
        result = asyncio.run(run_chat_turn(
            db, user_id="u1", user_message="paid Kim 1k", client=client,
        ))
        assert result.proposals == []
        tool_msg = next(m for m in client.sent[1] if m.role == "tool")
        payload = json.loads(tool_msg.text or "{}")
        names = {m["name"] for m in payload["matches"]}
        assert names >= {"Kim Nguyen", "Kim Industries"}

    def test_tool_error_surfaces_as_is_error(self, db: Session) -> None:
        client = _FakeClient([
            _assistant_tool_call("get_account_balance", {"account_code": "9999"}),
            _assistant_text("That account doesn't exist — could you double-check?"),
        ])
        result = asyncio.run(run_chat_turn(
            db, user_id="u1", user_message="what's the balance of 9999?", client=client,
        ))
        assert result.stop_reason == "end_turn"
        tool_msg = next(m for m in client.sent[1] if m.role == "tool")
        assert tool_msg.is_error is True
        assert "account_not_found" in (tool_msg.text or "")

    def test_session_history_persisted(self, db: Session) -> None:
        client = _FakeClient([_assistant_text("Hello back.")])
        result = asyncio.run(run_chat_turn(
            db, user_id="u1", user_message="hello", client=client,
        ))
        messages = (
            db.execute(
                select(AIChatMessage)
                .where(AIChatMessage.session_id == uuid.UUID(result.session_id))
                .order_by(AIChatMessage.created_at)
            )
            .scalars()
            .all()
        )
        roles = [m.role for m in messages]
        assert roles == ["user", "assistant"]
        # Stored format is normalized ChatMessage.to_dict().
        assert messages[0].content == {"role": "user", "text": "hello"}
        assert messages[1].content["role"] == "assistant"

    def test_max_turns_safety_cap(self, db: Session) -> None:
        async def _infinite(**kwargs):
            return _assistant_tool_call("get_company_defaults", {})

        class _InfiniteClient:
            shape = "fake"
            async def chat(self, **kw):
                return await _infinite(**kw)

        result = asyncio.run(run_chat_turn(
            db, user_id="u1", user_message="loop forever", client=_InfiniteClient(),
        ))
        assert result.turns == orchestrator.MAX_TURNS

    def test_malformed_tool_args_surfaces_clean_error(self, db: Session) -> None:
        """OpenAI-shape models sometimes return malformed JSON in tool args.
        Our adapter flags them with ``_parse_error``; the orchestrator must
        translate that into an ``is_error=True`` tool result instead of
        crashing on Pydantic."""
        client = _FakeClient([
            LLMResponse(
                message=ChatMessage(
                    role="assistant", text=None,
                    tool_calls=[ToolCall(
                        id="call_x", name="find_entity",
                        input={"_raw_arguments": "{broken", "_parse_error": True},
                    )],
                ),
                stop_reason="tool_use",
                usage=LLMUsage(),
            ),
            _assistant_text("Sorry — that didn't parse. Could you rephrase?"),
        ])
        result = asyncio.run(run_chat_turn(
            db, user_id="u1", user_message="something weird", client=client,
        ))
        assert result.stop_reason == "end_turn"
        tool_msg = next(m for m in client.sent[1] if m.role == "tool")
        assert tool_msg.is_error is True
        assert "malformed JSON" in (tool_msg.text or "")


class TestRegistry:
    def test_default_registry_has_six_tools(self) -> None:
        reg = build_default_registry()
        names = {t.name for t in reg}
        assert names == {
            "find_entity", "list_entities", "query_ledger",
            "get_account_balance", "get_company_defaults",
            "propose_create_transaction",
        }
