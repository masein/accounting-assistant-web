"""Tests for the chat-attachment feature, the entity-resolution graceful
partial (AI-1), localized status text (AI-2), and the cash-on-hand
reconciliation between the owner dashboard and CFO mode (AI-6).

All offline — the orchestrator runs against a fake LLM client and OCR is
not invoked (we exercise the proposal/execute linking directly).
"""
from __future__ import annotations

import asyncio
import uuid
from datetime import date, timedelta

import pytest
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.models.ai_accountant import AIChatMessage, AIChatSession, AIProposal
from app.models.audit_log import AuditLog
from app.models.account import Account
from app.models.entity import Entity, TransactionEntity
from app.models.transaction import (
    Transaction,
    TransactionAttachment,
    TransactionLine,
)
from app.services.ai_accountant.base import ToolContext
from app.services.ai_accountant.execute_service import execute_proposal
from app.services.ai_accountant.llm_protocol import (
    ChatMessage,
    LLMResponse,
    LLMUsage,
    ToolCall,
)
from app.services.ai_accountant.orchestrator import run_chat_turn
from app.services.ai_accountant.proposal_tools import (
    ProposeCreateTransaction,
    ProposeCreateTransactionInput,
)

USER = "chat-attach-user"


@pytest.fixture(autouse=True)
def _isolate(db: Session):
    yield
    db.execute(delete(AIChatMessage))
    db.execute(delete(AIChatSession))
    db.execute(delete(AIProposal))
    db.execute(delete(TransactionEntity))
    db.execute(delete(TransactionAttachment))
    db.execute(delete(TransactionLine))
    db.execute(delete(Transaction))
    db.execute(delete(AuditLog))
    db.execute(delete(Entity))
    db.commit()


# ---------------------------------------------------------------------------
# Attachment linking through propose → execute
# ---------------------------------------------------------------------------


def _make_attachment(db: Session) -> TransactionAttachment:
    row = TransactionAttachment(
        file_name="receipt.pdf",
        file_path=f"/tmp/{uuid.uuid4().hex}.pdf",
        content_type="application/pdf",
        size_bytes=1234,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


class TestChatAttachmentLinking:
    def test_ctx_attachment_ids_merged_into_proposal(self, db: Session) -> None:
        att = _make_attachment(db)
        ctx = ToolContext(
            db=db, user_id=USER, username="t",
            user_message="here is the receipt",
            attachment_ids=[str(att.id)],
        )
        tool = ProposeCreateTransaction()
        payload = ProposeCreateTransactionInput(
            date="2026-05-20",
            description="Office supplies receipt",
            currency="IRR",
            lines=[
                {"account_code": "6110", "debit": 500_000, "credit": 0},
                {"account_code": "1110", "debit": 0, "credit": 500_000},
            ],
        )
        out = asyncio.run(tool.run(ctx, payload))
        proposal = db.execute(
            select(AIProposal).where(
                AIProposal.confirmation_token == uuid.UUID(out["confirmation_token"])
            )
        ).scalar_one()
        assert proposal.tool_input["attachment_ids"] == [str(att.id)]

    def test_execute_links_attachment_to_transaction(self, db: Session) -> None:
        att = _make_attachment(db)
        ctx = ToolContext(
            db=db, user_id=USER, username="t",
            user_message="receipt", attachment_ids=[str(att.id)],
        )
        tool = ProposeCreateTransaction()
        payload = ProposeCreateTransactionInput(
            date="2026-05-20",
            description="Receipt entry",
            currency="IRR",
            lines=[
                {"account_code": "6110", "debit": 500_000, "credit": 0},
                {"account_code": "1110", "debit": 0, "credit": 500_000},
            ],
        )
        token = asyncio.run(tool.run(ctx, payload))["confirmation_token"]
        result = execute_proposal(
            db, confirmation_token=token, actor_user_id=USER, actor_username="t"
        )
        db.refresh(att)
        assert att.transaction_id is not None
        assert str(att.transaction_id) == result.transaction_id


# ---------------------------------------------------------------------------
# AI-1 / AI-2: graceful, localized partial when the model loops on entities
# ---------------------------------------------------------------------------


class _LoopingFindEntityClient:
    """Always asks for find_entity — simulates a model that never converges
    on the entity, so the orchestrator must hit MAX_TURNS and fall back."""

    shape = "fake"

    def __init__(self, query: str) -> None:
        self._query = query

    async def chat(self, *, system_prompt, tools, messages, model=None, max_tokens=8192):
        return LLMResponse(
            message=ChatMessage(
                role="assistant", text=None,
                tool_calls=[ToolCall(
                    id=f"call_{uuid.uuid4().hex[:8]}",
                    name="find_entity", input={"query": self._query},
                )],
            ),
            stop_reason="tool_use",
            usage=LLMUsage(input_tokens=10, output_tokens=5),
        )


class TestGracefulPartial:
    def test_candidates_offered_on_max_turns(self, db: Session) -> None:
        db.add(Entity(name="Kim Nguyen", type="employee"))
        db.commit()
        result = asyncio.run(run_chat_turn(
            db, user_id=USER, user_message="paid Kim 500 for supplies",
            client=_LoopingFindEntityClient("Kim"), lang="en",
        ))
        # No dead-end "simplify your request"; instead the candidate is named.
        assert "Kim Nguyen" in result.text
        assert "simplify your request" not in result.text

    def test_partial_localized_to_persian(self, db: Session) -> None:
        db.add(Entity(name="Kim Nguyen", type="employee"))
        db.commit()
        result = asyncio.run(run_chat_turn(
            db, user_id=USER, user_message="به کیم ۵۰۰ پرداختم",
            client=_LoopingFindEntityClient("Kim"), lang="fa",
        ))
        assert "Kim Nguyen" in result.text
        # Persian candidate-prompt contains Persian script.
        assert any("؀" <= ch <= "ۿ" for ch in result.text)

    def test_dead_end_localized_when_no_candidates(self, db: Session) -> None:
        # A model that loops on a non-entity tool produces no candidates →
        # the localized "add more detail" message (not the English default).
        class _LoopDefaults:
            shape = "fake"

            async def chat(self, *, system_prompt, tools, messages, model=None, max_tokens=8192):
                return LLMResponse(
                    message=ChatMessage(
                        role="assistant", text=None,
                        tool_calls=[ToolCall(
                            id=f"call_{uuid.uuid4().hex[:8]}",
                            name="get_company_defaults", input={},
                        )],
                    ),
                    stop_reason="tool_use",
                    usage=LLMUsage(input_tokens=10, output_tokens=5),
                )

        result = asyncio.run(run_chat_turn(
            db, user_id=USER, user_message="x", client=_LoopDefaults(), lang="es",
        ))
        assert any(ch in result.text for ch in "áéíóúñ¿")  # Spanish text


# ---------------------------------------------------------------------------
# AI-6: cash-on-hand is the same all-time balance for dashboard and CFO
# ---------------------------------------------------------------------------


def _post_cash_txn(db: Session, *, on: date, amount: int) -> None:
    """A simple cash receipt: DR 1110 cash / CR 4110 sales."""
    accounts = {a.code: a for a in db.query(Account).all()}
    txn = Transaction(date=on, description="cash sale", currency="IRR")
    db.add(txn)
    db.flush()
    db.add(TransactionLine(transaction_id=txn.id, account_id=accounts["1110"].id, debit=amount, credit=0))
    db.add(TransactionLine(transaction_id=txn.id, account_id=accounts["4110"].id, debit=0, credit=amount))
    db.commit()


class TestCashReconciliation:
    def test_cfo_cash_matches_shared_helper_and_includes_old_txns(self, db: Session) -> None:
        from app.services.cash_service import cash_on_hand
        from app.services.cfo_intelligence import build_cfo_report
        from app.services.locale_service import get_reporting_locale

        today = date.today()
        # One recent cash receipt and one OLDER than the CFO 12-month window.
        _post_cash_txn(db, on=today - timedelta(days=20), amount=40_000)
        _post_cash_txn(db, on=today - timedelta(days=420), amount=44_377)

        locale = get_reporting_locale(db)
        shared = cash_on_hand(db, locale=locale, currency=None, as_of=today)
        # All-time balance includes the >12-month-old receipt.
        assert shared == 84_377

        cfo = build_cfo_report(db)
        cfo_cash = next(k.value for k in cfo.kpis if k.key == "cash_on_hand")
        assert cfo_cash == shared  # CFO no longer windows cash (AI-6)
