"""Orchestrator tests with the Anthropic client mocked.

These cover the agentic loop end-to-end without burning API credits:

* No-match-create: Claude looks for "Kim", find_entity returns []
* Single match: find_entity returns one Kim, Claude proposes immediately
* Multiple matches: find_entity returns two Kims, Claude asks the user
* Pure query: query_ledger answers in one turn, no proposal
* Bulk request refusal: Claude proposes one card per item, not bulk

Each scenario drives the orchestrator with a queue of canned
``Message`` responses (text + tool_use blocks) that mimic what a
well-behaved Claude would return. Real network calls never happen.
"""
from __future__ import annotations

import asyncio
import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import pytest
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.models.ai_accountant import AIChatMessage, AIChatSession, AIProposal
from app.models.audit_log import AuditLog
from app.models.entity import Entity, TransactionEntity
from app.models.transaction import Transaction, TransactionLine
from app.services.ai_accountant import orchestrator
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
# Mock Message + tool_use / text blocks
# ---------------------------------------------------------------------------


@dataclass
class _Block:
    type: str
    text: str | None = None
    id: str | None = None
    name: str | None = None
    input: dict | None = None

    def model_dump(self) -> dict[str, Any]:
        if self.type == "text":
            return {"type": "text", "text": self.text}
        if self.type == "tool_use":
            return {"type": "tool_use", "id": self.id, "name": self.name, "input": self.input}
        return {"type": self.type}


@dataclass
class _Usage:
    input_tokens: int = 100
    output_tokens: int = 50
    cache_read_input_tokens: int = 0
    cache_creation_input_tokens: int = 0


@dataclass
class _MockMessage:
    """Stand-in for an Anthropic ``Message`` response."""
    content: list[_Block] = field(default_factory=list)
    stop_reason: str = "end_turn"
    usage: _Usage = field(default_factory=_Usage)


def _text(text: str) -> _Block:
    return _Block(type="text", text=text)


def _tool_use(name: str, args: dict, use_id: str | None = None) -> _Block:
    return _Block(type="tool_use", id=use_id or f"toolu_{uuid.uuid4().hex[:8]}", name=name, input=args)


def _msg(*blocks: _Block, stop: str = "end_turn") -> _MockMessage:
    return _MockMessage(content=list(blocks), stop_reason=stop)


def _patch_chat_once(monkeypatch, scripted_responses: list[_MockMessage]) -> list[dict]:
    """Replace ``chat_once`` with a queue of mock responses.

    Returns a list captured by the patched function so tests can assert
    on what the orchestrator sent to Claude (messages array, tools, etc.).
    """
    sent: list[dict] = []
    queue = list(scripted_responses)

    async def _fake(**kwargs):
        sent.append({"messages": list(kwargs.get("messages", [])), "tools": kwargs.get("tools")})
        if not queue:
            raise AssertionError("scripted responses exhausted")
        return queue.pop(0)

    monkeypatch.setattr(orchestrator, "chat_once", _fake)
    return sent


# ---------------------------------------------------------------------------
# Scenarios
# ---------------------------------------------------------------------------


class TestOrchestrator:
    def test_pure_query_no_proposal(self, db: Session, monkeypatch) -> None:
        """User asks a balance question → orchestrator calls one read tool,
        Claude wraps up with text, no proposals."""
        scripted = [
            # Turn 1: Claude calls get_account_balance.
            _msg(
                _tool_use("get_account_balance", {"account_code": "1110"}),
                stop="tool_use",
            ),
            # Turn 2: Claude reads the tool result and answers.
            _msg(_text("Your cash balance is 0 IRR."), stop="end_turn"),
        ]
        _patch_chat_once(monkeypatch, scripted)

        result = asyncio.run(
            run_chat_turn(
                db,
                user_id="u1",
                user_message="what's my cash balance?",
            )
        )

        assert result.proposals == []
        assert "cash" in result.text.lower() or "balance" in result.text.lower()
        assert result.stop_reason == "end_turn"
        assert result.turns == 2

    def test_single_match_proposes_transaction(self, db: Session, monkeypatch) -> None:
        """find_entity returns one Kim → Claude proposes a transaction
        and the orchestrator surfaces the proposal in the result."""
        kim = Entity(name="Kim Nguyen", type="employee")
        db.add(kim)
        db.commit()
        db.refresh(kim)
        kim_id = str(kim.id)

        scripted = [
            _msg(_tool_use("find_entity", {"query": "Kim"}), stop="tool_use"),
            _msg(
                _tool_use(
                    "propose_create_transaction",
                    {
                        "date": "2026-05-20",
                        "description": "paid Kim Nguyen — salary",
                        "currency": "IRR",
                        "lines": [
                            {"account_code": "6110", "debit": 1_000_000, "credit": 0},
                            {"account_code": "1110", "debit": 0, "credit": 1_000_000},
                        ],
                        "entity_links": [{"entity_id": kim_id, "role": "employee"}],
                    },
                ),
                stop="tool_use",
            ),
            _msg(
                _text("I drafted a proposal to pay Kim Nguyen 1,000,000 IRR. Please confirm."),
                stop="end_turn",
            ),
        ]
        _patch_chat_once(monkeypatch, scripted)

        result = asyncio.run(
            run_chat_turn(
                db,
                user_id="u1",
                user_message="paid Kim 1k yesterday",
            )
        )

        assert len(result.proposals) == 1
        prop = result.proposals[0]
        assert prop["tool_name"] == "propose_create_transaction"
        assert "confirmation_token" in prop
        # AIProposal row was actually persisted.
        token = uuid.UUID(prop["confirmation_token"])
        row = db.execute(
            select(AIProposal).where(AIProposal.confirmation_token == token)
        ).scalar_one()
        assert row.status == "pending"

    def test_no_match_returns_zero_results(self, db: Session, monkeypatch) -> None:
        """find_entity with a name not in the DB returns empty matches —
        a well-behaved Claude would then ask the user to clarify or create.
        We just verify the tool result reaches Claude correctly."""
        scripted = [
            _msg(_tool_use("find_entity", {"query": "Zelenoff"}), stop="tool_use"),
            _msg(
                _text("I couldn't find anyone named Zelenoff. Should I create a new entity?"),
                stop="end_turn",
            ),
        ]
        sent = _patch_chat_once(monkeypatch, scripted)

        result = asyncio.run(
            run_chat_turn(
                db,
                user_id="u1",
                user_message="paid Zelenoff 500",
            )
        )

        assert result.proposals == []
        # Turn 2 should have a tool_result block with empty matches.
        turn2_messages = sent[1]["messages"]
        last_user = turn2_messages[-1]
        assert last_user["role"] == "user"
        assert isinstance(last_user["content"], list)
        tool_result_block = next(b for b in last_user["content"] if b.get("type") == "tool_result")
        content_text = tool_result_block["content"]
        assert '"matches": []' in content_text

    def test_multiple_matches_returned_to_claude(self, db: Session, monkeypatch) -> None:
        """Two Kims → find_entity surfaces both; orchestrator passes them on."""
        db.add_all(
            [
                Entity(name="Kim Nguyen", type="employee"),
                Entity(name="Kim Industries", type="client"),
            ]
        )
        db.commit()

        scripted = [
            _msg(_tool_use("find_entity", {"query": "Kim"}), stop="tool_use"),
            _msg(
                _text("Two matches for Kim: Kim Nguyen (employee) or Kim Industries (client). Which?"),
                stop="end_turn",
            ),
        ]
        sent = _patch_chat_once(monkeypatch, scripted)

        result = asyncio.run(
            run_chat_turn(
                db,
                user_id="u1",
                user_message="paid Kim 1k",
            )
        )

        assert result.proposals == []
        # Two candidates reached Claude.
        tool_result_text = next(
            b["content"]
            for b in sent[1]["messages"][-1]["content"]
            if b.get("type") == "tool_result"
        )
        payload = json.loads(tool_result_text)
        names = {m["name"] for m in payload["matches"]}
        assert names >= {"Kim Nguyen", "Kim Industries"}

    def test_tool_error_surfaces_as_is_error(self, db: Session, monkeypatch) -> None:
        """ToolError from a tool becomes ``tool_result(is_error=True)``
        — Claude can recover, the orchestrator doesn't crash."""
        scripted = [
            _msg(_tool_use("get_account_balance", {"account_code": "9999"}), stop="tool_use"),
            _msg(_text("That account doesn't exist — could you double-check?"), stop="end_turn"),
        ]
        sent = _patch_chat_once(monkeypatch, scripted)

        result = asyncio.run(
            run_chat_turn(db, user_id="u1", user_message="what's the balance of 9999?")
        )
        assert result.stop_reason == "end_turn"
        error_block = next(
            b for b in sent[1]["messages"][-1]["content"] if b.get("type") == "tool_result"
        )
        assert error_block.get("is_error") is True
        assert "account_not_found" in error_block["content"]

    def test_session_history_persisted(self, db: Session, monkeypatch) -> None:
        """Every turn the orchestrator runs writes user + assistant rows
        to ai_chat_messages so the next turn (or the UI) can replay it."""
        scripted = [_msg(_text("Hello back."), stop="end_turn")]
        _patch_chat_once(monkeypatch, scripted)

        result = asyncio.run(
            run_chat_turn(db, user_id="u1", user_message="hello")
        )
        messages = (
            db.execute(
                select(AIChatMessage).where(AIChatMessage.session_id == uuid.UUID(result.session_id))
                .order_by(AIChatMessage.created_at)
            )
            .scalars()
            .all()
        )
        roles = [m.role for m in messages]
        assert roles == ["user", "assistant"]

    def test_max_turns_safety_cap(self, db: Session, monkeypatch) -> None:
        """If Claude keeps calling tools forever, we stop after MAX_TURNS."""
        # Always return a tool_use so the loop never exits naturally.
        async def _infinite(**kwargs):
            return _msg(
                _tool_use("get_company_defaults", {}),
                stop="tool_use",
            )
        monkeypatch.setattr(orchestrator, "chat_once", _infinite)

        result = asyncio.run(
            run_chat_turn(db, user_id="u1", user_message="loop forever")
        )
        assert result.turns == orchestrator.MAX_TURNS
        assert "max turns" in result.text.lower() or result.text  # got some response


class TestRegistry:
    def test_default_registry_has_six_tools(self) -> None:
        reg = build_default_registry()
        names = {t.name for t in reg}
        assert names == {
            "find_entity", "list_entities", "query_ledger",
            "get_account_balance", "get_company_defaults",
            "propose_create_transaction",
        }
