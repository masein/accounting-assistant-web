"""End-to-end tests for the propose → execute → undo loop.

These run entirely offline — no Anthropic calls. They cover the
acceptance scenarios from Section 6 of the design brief that are
expressible without a live LLM:

* idempotency  (same token twice → one ledger write)
* undo within window (compensating entry)
* undo outside window (rejected)
* expiry (>10 m old → 410)
* permission denied (other user's proposal → 403)
* execute writes an audit_log row with ``actor_source='ai-assistant'``
"""
from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.models.ai_accountant import AIProposal
from app.models.audit_log import AuditLog
from app.models.entity import TransactionEntity
from app.models.transaction import Transaction, TransactionLine
from app.services.ai_accountant.base import ToolContext
from app.services.ai_accountant.execute_service import (
    PermissionDenied,
    ProposalExpired,
    ProposalNotFound,
    UndoNotApplicable,
    UndoWindowClosed,
    execute_proposal,
    undo_action,
)
from app.services.ai_accountant.proposal_tools import (
    ProposeCreateTransaction,
    ProposeCreateTransactionInput,
)


USER = "test-user-1"
USER_ALT = "test-user-2"


@pytest.fixture(autouse=True)
def _isolate_ledger(db: Session):
    """Wipe any ledger / audit / proposal rows this test wrote so the
    session-scoped Iran chart stays clean for the rest of the suite.

    Read-only tests in this module also benefit — the cleanup is a
    no-op when nothing was written."""
    yield
    db.execute(delete(TransactionEntity))
    db.execute(delete(TransactionLine))
    db.execute(delete(Transaction))
    db.execute(delete(AuditLog))
    db.execute(delete(AIProposal))
    db.commit()


def _make_proposal(db: Session, user_id: str = USER) -> str:
    """Register a balanced 2-line proposal and return its token."""
    ctx = ToolContext(db=db, user_id=user_id, username="tester",
                      user_message="paid Kim 1k yesterday")
    tool = ProposeCreateTransaction()
    payload = ProposeCreateTransactionInput(
        date="2026-05-20",
        description="Test AI-generated journal",
        currency="IRR",
        lines=[
            {"account_code": "6110", "debit": 1_000_000, "credit": 0,
             "line_description": "salary"},
            {"account_code": "1110", "debit": 0, "credit": 1_000_000,
             "line_description": "cash out"},
        ],
        entity_links=[],
    )
    out = asyncio.run(tool.run(ctx, payload))
    return out["confirmation_token"]


# ---------------------------------------------------------------------------
# Proposal tool
# ---------------------------------------------------------------------------


class TestProposalTool:
    def test_creates_pending_proposal(self, db: Session) -> None:
        token = _make_proposal(db)
        row = db.execute(
            select(AIProposal).where(AIProposal.confirmation_token == uuid.UUID(token))
        ).scalar_one()
        assert row.status == "pending"
        assert row.tool_name == "propose_create_transaction"
        assert row.user_id == USER

    def test_unbalanced_payload_rejected_by_pydantic(self, db: Session) -> None:
        with pytest.raises(Exception):  # Pydantic ValidationError
            ProposeCreateTransactionInput(
                date="2026-05-20",
                description="bad",
                lines=[
                    {"account_code": "6110", "debit": 100, "credit": 0},
                    {"account_code": "1110", "debit": 0, "credit": 99},  # off by 1
                ],
            )

    def test_unknown_account_code_rejected(self, db: Session) -> None:
        from app.services.ai_accountant.base import ToolError
        ctx = ToolContext(db=db, user_id=USER)
        tool = ProposeCreateTransaction()
        payload = ProposeCreateTransactionInput(
            date="2026-05-20",
            description="x",
            lines=[
                {"account_code": "9999", "debit": 100, "credit": 0},
                {"account_code": "1110", "debit": 0, "credit": 100},
            ],
        )
        with pytest.raises(ToolError) as ei:
            asyncio.run(tool.run(ctx, payload))
        assert ei.value.code == "account_not_found"


# ---------------------------------------------------------------------------
# Execute
# ---------------------------------------------------------------------------


class TestExecute:
    def test_execute_writes_transaction_and_audit(self, db: Session) -> None:
        token = _make_proposal(db)
        result = execute_proposal(
            db, confirmation_token=token, actor_user_id=USER, actor_username="tester"
        )
        assert result.idempotent is False
        assert result.transaction_id is not None

        # Transaction exists with the right line totals.
        txn = db.get(Transaction, uuid.UUID(result.transaction_id))
        assert txn is not None
        lines = db.execute(
            select(TransactionLine).where(TransactionLine.transaction_id == txn.id)
        ).scalars().all()
        assert sum(int(l.debit or 0) for l in lines) == 1_000_000
        assert sum(int(l.credit or 0) for l in lines) == 1_000_000

        # Audit row has the ai-assistant marker + payload context.
        audit = db.get(AuditLog, uuid.UUID(result.audit_log_id))
        assert audit is not None
        assert audit.actor_source == "ai-assistant"
        assert audit.tool_name == "propose_create_transaction"
        assert str(audit.confirmation_token) == token
        assert audit.user_message == "paid Kim 1k yesterday"

    def test_execute_is_idempotent(self, db: Session) -> None:
        """Second call with the same token returns the original
        audit_log_id and writes no second ledger row."""
        token = _make_proposal(db)
        first = execute_proposal(
            db, confirmation_token=token, actor_user_id=USER
        )
        # Count rows before the second call.
        before_count = db.execute(select(Transaction)).scalars().all()
        second = execute_proposal(
            db, confirmation_token=token, actor_user_id=USER
        )
        after_count = db.execute(select(Transaction)).scalars().all()
        assert second.idempotent is True
        assert second.audit_log_id == first.audit_log_id
        assert len(after_count) == len(before_count)

    def test_other_user_cannot_execute(self, db: Session) -> None:
        token = _make_proposal(db, user_id=USER)
        with pytest.raises(PermissionDenied):
            execute_proposal(db, confirmation_token=token, actor_user_id=USER_ALT)

    def test_unknown_token_returns_not_found(self, db: Session) -> None:
        with pytest.raises(ProposalNotFound):
            execute_proposal(
                db, confirmation_token=str(uuid.uuid4()), actor_user_id=USER
            )

    def test_expired_proposal_rejected(self, db: Session) -> None:
        token = _make_proposal(db)
        # Force the created_at backwards by 20 minutes.
        prop = db.execute(
            select(AIProposal).where(AIProposal.confirmation_token == uuid.UUID(token))
        ).scalar_one()
        prop.created_at = datetime.now(timezone.utc) - timedelta(minutes=20)
        db.commit()
        with pytest.raises(ProposalExpired):
            execute_proposal(db, confirmation_token=token, actor_user_id=USER)
        # Status flips to "expired" so future calls don't waste effort.
        db.refresh(prop)
        assert prop.status == "expired"


# ---------------------------------------------------------------------------
# Undo
# ---------------------------------------------------------------------------


class TestUndo:
    def test_undo_creates_reversing_entry(self, db: Session) -> None:
        token = _make_proposal(db)
        ex = execute_proposal(db, confirmation_token=token, actor_user_id=USER)

        result = undo_action(
            db, audit_log_id=ex.audit_log_id, actor_user_id=USER,
        )

        # Reversal must be a distinct transaction.
        assert result.reversal_transaction_id != result.original_transaction_id
        reversal_txn = db.get(Transaction, uuid.UUID(result.reversal_transaction_id))
        assert reversal_txn is not None

        # Reversal lines mirror the original (debits ↔ credits).
        orig_lines = db.execute(
            select(TransactionLine).where(
                TransactionLine.transaction_id == uuid.UUID(result.original_transaction_id)
            )
        ).scalars().all()
        rev_lines = db.execute(
            select(TransactionLine).where(
                TransactionLine.transaction_id == reversal_txn.id
            )
        ).scalars().all()
        assert sum(int(l.debit or 0) for l in orig_lines) == sum(
            int(l.credit or 0) for l in rev_lines
        )

        # Both audit rows visible (original 'create' + undo).
        audits = db.execute(
            select(AuditLog).where(AuditLog.entity_id == result.original_transaction_id)
        ).scalars().all()
        actions = {a.action for a in audits}
        assert "create" in actions
        assert "undo" in actions

    def test_undo_outside_window_rejected(self, db: Session) -> None:
        token = _make_proposal(db)
        ex = execute_proposal(db, confirmation_token=token, actor_user_id=USER)
        # Push the audit timestamp back so the window has closed.
        audit = db.get(AuditLog, uuid.UUID(ex.audit_log_id))
        audit.timestamp = datetime.now(timezone.utc) - timedelta(minutes=5)
        db.commit()
        with pytest.raises(UndoWindowClosed):
            undo_action(db, audit_log_id=ex.audit_log_id, actor_user_id=USER)

    def test_undo_rejects_non_ai_audit_rows(self, db: Session) -> None:
        # Create an audit row not from the AI assistant.
        manual = AuditLog(
            action="create",
            entity_type="transaction",
            entity_id=str(uuid.uuid4()),
            user_id=USER,
            actor_source="manual",
        )
        db.add(manual)
        db.commit()
        db.refresh(manual)
        with pytest.raises(UndoNotApplicable):
            undo_action(db, audit_log_id=str(manual.id), actor_user_id=USER)

    def test_other_user_cannot_undo(self, db: Session) -> None:
        token = _make_proposal(db, user_id=USER)
        ex = execute_proposal(db, confirmation_token=token, actor_user_id=USER)
        with pytest.raises(PermissionDenied):
            undo_action(db, audit_log_id=ex.audit_log_id, actor_user_id=USER_ALT)
