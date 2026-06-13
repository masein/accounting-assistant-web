"""Execute and Undo services for AI-proposed actions.

These are **not** Claude tools — they're called by the FastAPI layer
when the user clicks Confirm or Undo in the chat UI. Keeping execute
and undo server-side (outside the tool catalogue) is the security
boundary: Claude proposes, the user authorises, the server commits.

Guarantees:

* **Idempotency.** Calling ``execute_proposal`` twice with the same
  ``confirmation_token`` returns the same audit-log entry without
  double-writing the ledger.
* **Expiry.** Proposals expire after 10 minutes (``PROPOSAL_TTL``);
  expired tokens raise ``ProposalExpired``.
* **Audit.** Every successful execute writes one ``audit_logs`` row
  with ``actor_source='ai-assistant'``, the original ``user_message``,
  the ``tool_name``, and the ``confirmation_token``. The orchestrator
  + chat UI never write to the audit log — only this module does.
* **Undo via compensating entry.** ``undo_action`` builds a reversing
  journal (via the existing ``LedgerService.reverse_journal_entry``)
  and writes a paired audit-log entry. The original transaction is
  never edited or deleted.
"""
from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.transactions import _create_transaction_from_payload, _load_transaction_with_lines
from app.models.ai_accountant import AIProposal
from app.models.audit_log import AuditLog
from app.models.transaction import Transaction
from app.schemas.entity import EntityLink
from app.schemas.transaction import TransactionCreate, TransactionLineCreate
from app.services.reporting.ledger_service import LedgerService

PROPOSAL_TTL = timedelta(minutes=10)
# The quick one-click undo window in the chat. Lengthened from 30s to 120s so
# the countdown doesn't expire while the user is still reading the receipt
# (AI-7). After it closes the user still has a persistent ``reverse_action``
# (no time limit) for recourse, so this is just the "instant" path.
UNDO_WINDOW = timedelta(seconds=120)


# ---------------------------------------------------------------------------
# Exceptions — surfaced as HTTP errors by the API layer
# ---------------------------------------------------------------------------


class ProposalNotFound(Exception): ...
class ProposalExpired(Exception): ...
class ProposalCancelled(Exception): ...
class PermissionDenied(Exception): ...
class UndoWindowClosed(Exception): ...
class UndoNotApplicable(Exception): ...


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------


@dataclass
class ExecutionResult:
    transaction_id: str | None
    audit_log_id: str
    confirmation_token: str
    tool_name: str
    idempotent: bool  # True when this call was a re-execute (no new write)


@dataclass
class UndoResult:
    original_transaction_id: str
    reversal_transaction_id: str
    audit_log_id: str


# ---------------------------------------------------------------------------
# Execute
# ---------------------------------------------------------------------------


def _resolve_proposal(db: Session, token: str) -> AIProposal:
    try:
        token_uuid = uuid.UUID(token)
    except (ValueError, TypeError):
        raise ProposalNotFound(f"Invalid confirmation_token format: {token!r}")
    row = db.execute(
        select(AIProposal).where(AIProposal.confirmation_token == token_uuid)
    ).scalar_one_or_none()
    if row is None:
        raise ProposalNotFound(f"No proposal found for token {token}")
    return row


def execute_proposal(
    db: Session,
    *,
    confirmation_token: str,
    actor_user_id: str,
    actor_username: str | None = None,
    ip_address: str | None = None,
) -> ExecutionResult:
    """Commit a pending proposal under the given user's authority.

    Server-side enforcement:
      * The proposal must belong to the requesting user (the brief says
        the AI inherits the calling user's permissions — we enforce that
        here, not in the system prompt).
      * Status must be ``pending``. A second call with the same token
        returns the original audit-log entry (idempotent).
      * Older than 10 minutes → ``ProposalExpired``.
    """
    proposal = _resolve_proposal(db, confirmation_token)

    if proposal.user_id != actor_user_id:
        raise PermissionDenied(
            "This proposal belongs to a different user. Only the user who "
            "created the proposal can execute it."
        )

    # Idempotent re-execute: return the original audit-log row.
    if proposal.status == "executed":
        existing_audit_id = str(proposal.executed_audit_id) if proposal.executed_audit_id else ""
        existing_audit = (
            db.execute(select(AuditLog).where(AuditLog.id == proposal.executed_audit_id))
            .scalar_one_or_none()
            if proposal.executed_audit_id else None
        )
        return ExecutionResult(
            transaction_id=(existing_audit.entity_id if existing_audit else None),
            audit_log_id=existing_audit_id,
            confirmation_token=str(proposal.confirmation_token),
            tool_name=proposal.tool_name,
            idempotent=True,
        )

    if proposal.status == "cancelled":
        raise ProposalCancelled("This proposal was cancelled — cannot execute.")

    # Expiry check (10 min from creation).
    created = proposal.created_at
    if created and created.tzinfo is None:
        created = created.replace(tzinfo=timezone.utc)
    if created and datetime.now(timezone.utc) - created > PROPOSAL_TTL:
        proposal.status = "expired"
        db.commit()
        raise ProposalExpired(
            f"Proposal expired (created {created.isoformat()}, TTL "
            f"{int(PROPOSAL_TTL.total_seconds())}s). Ask the assistant to draft a new one."
        )

    # Dispatch by tool_name. Only the v1 scope (create_transaction) is
    # implemented; add elif branches as new proposal tools land.
    if proposal.tool_name == "propose_create_transaction":
        txn_id, audit_id = _execute_create_transaction(
            db, proposal,
            actor_user_id=actor_user_id,
            actor_username=actor_username,
            ip_address=ip_address,
        )
    else:
        raise ProposalNotFound(
            f"No executor for tool {proposal.tool_name!r} — this tool's "
            f"execute path has not been implemented yet."
        )

    # Mark the proposal executed inside the same DB transaction.
    proposal.status = "executed"
    proposal.executed_at = datetime.now(timezone.utc)
    proposal.executed_audit_id = uuid.UUID(audit_id)
    db.commit()

    return ExecutionResult(
        transaction_id=txn_id,
        audit_log_id=audit_id,
        confirmation_token=str(proposal.confirmation_token),
        tool_name=proposal.tool_name,
        idempotent=False,
    )


def _execute_create_transaction(
    db: Session,
    proposal: AIProposal,
    *,
    actor_user_id: str,
    actor_username: str | None,
    ip_address: str | None,
) -> tuple[str, str]:
    """Run the propose_create_transaction payload through the existing
    transaction-creation path. Returns (transaction_id, audit_log_id)."""
    payload_dict = dict(proposal.tool_input)  # JSONB → dict

    # Build a TransactionCreate from the saved dict. The schema fields
    # match the ProposeCreateTransactionInput we persisted.
    try:
        payload = TransactionCreate(
            date=payload_dict["date"],
            description=payload_dict.get("description"),
            reference=payload_dict.get("reference"),
            currency=payload_dict.get("currency", "IRR"),
            lines=[
                TransactionLineCreate(
                    account_code=ln["account_code"],
                    debit=int(ln.get("debit", 0)),
                    credit=int(ln.get("credit", 0)),
                    line_description=ln.get("line_description"),
                )
                for ln in payload_dict.get("lines", [])
            ],
            entity_links=[
                EntityLink(entity_id=link["entity_id"], role=link["role"])
                for link in payload_dict.get("entity_links", [])
            ],
            attachment_ids=payload_dict.get("attachment_ids", []) or [],
        )
    except Exception as e:
        # Catch any pydantic / KeyError; treat as a bad payload.
        raise HTTPException(
            status_code=400,
            detail=f"Stored proposal payload is malformed: {e}",
        ) from e

    transaction = _create_transaction_from_payload(db, payload)
    db.flush()
    db.refresh(transaction)
    _load_transaction_with_lines(db, transaction)

    # Audit row — actor_source='ai-assistant' so future filters can
    # pick out AI-initiated writes.
    audit = AuditLog(
        action="create",
        entity_type="transaction",
        entity_id=str(transaction.id),
        user_id=actor_user_id,
        username=actor_username,
        ip_address=ip_address,
        actor_source="ai-assistant",
        session_id=proposal.session_id,
        tool_name=proposal.tool_name,
        confirmation_token=proposal.confirmation_token,
        user_message=proposal.user_message,
        detail=json.dumps(
            {
                "transaction_id": str(transaction.id),
                "date": transaction.date.isoformat(),
                "description": transaction.description,
                "currency": transaction.currency,
                "tool_input": payload_dict,
            },
            default=str,
        ),
    )
    db.add(audit)
    db.flush()
    db.refresh(audit)

    return str(transaction.id), str(audit.id)


# ---------------------------------------------------------------------------
# Undo
# ---------------------------------------------------------------------------


def _resolve_undoable_audit(db: Session, audit_log_id: str, actor_user_id: str) -> AuditLog:
    """Load + authorise an audit row for reversal. Shared by the windowed
    undo and the persistent reverse paths."""
    try:
        audit_uuid = uuid.UUID(audit_log_id)
    except (ValueError, TypeError):
        raise UndoNotApplicable(f"Invalid audit_log_id: {audit_log_id!r}")
    audit = db.execute(
        select(AuditLog).where(AuditLog.id == audit_uuid)
    ).scalar_one_or_none()
    if audit is None:
        raise UndoNotApplicable(f"No audit row {audit_log_id}")
    if audit.user_id != actor_user_id:
        raise PermissionDenied("Only the user who initiated the write can reverse it.")
    if audit.actor_source != "ai-assistant":
        raise UndoNotApplicable(
            "Reverse is only supported for AI-initiated writes (actor_source='ai-assistant')."
        )
    if audit.entity_type != "transaction" or audit.action != "create":
        raise UndoNotApplicable(
            f"Reverse is only implemented for transaction creates (got "
            f"action={audit.action!r}, entity_type={audit.entity_type!r})."
        )
    return audit


def _already_reversed(db: Session, transaction_id: str) -> bool:
    """True if a prior undo/reverse already compensated this transaction —
    guards against stacking multiple reversals on one entry."""
    return db.execute(
        select(AuditLog.id).where(
            AuditLog.action == "undo",
            AuditLog.entity_type == "transaction",
            AuditLog.entity_id == transaction_id,
        )
    ).first() is not None


def _perform_reversal(
    db: Session,
    audit: AuditLog,
    *,
    actor_user_id: str,
    actor_username: str | None,
    ip_address: str | None,
    tool_name: str,
) -> UndoResult:
    """Build the compensating journal entry for ``audit``'s transaction and
    write a paired audit row. The original is never edited or deleted."""
    if not audit.entity_id:
        raise UndoNotApplicable("Audit row is missing entity_id.")
    try:
        original_txn_uuid = uuid.UUID(audit.entity_id)
    except (ValueError, TypeError) as e:
        raise UndoNotApplicable(f"Invalid transaction ID on audit row: {e}") from e

    if _already_reversed(db, str(original_txn_uuid)):
        raise UndoNotApplicable("This entry has already been reversed.")

    original = db.execute(
        select(Transaction).where(Transaction.id == original_txn_uuid)
    ).scalar_one_or_none()
    if original is None:
        raise UndoNotApplicable("Original transaction no longer exists (already reversed?).")

    svc = LedgerService(db)
    reversal = svc.reverse_journal_entry(
        transaction_id=original_txn_uuid,
        reverse_date=None,
        reference=f"REVERSAL of {original.reference or original.id}",
        description=f"AI reversal — reverses {original.description or original.id}",
    )

    undo_audit = AuditLog(
        action="undo",
        entity_type="transaction",
        entity_id=str(original_txn_uuid),
        user_id=actor_user_id,
        username=actor_username,
        ip_address=ip_address,
        actor_source="ai-assistant",
        session_id=audit.session_id,
        tool_name=tool_name,
        confirmation_token=audit.confirmation_token,
        user_message=audit.user_message,
        detail=json.dumps(
            {
                "reversed_audit_id": str(audit.id),
                "reversal_transaction_id": str(reversal.transaction_id),
            },
            default=str,
        ),
    )
    db.add(undo_audit)
    db.commit()
    db.refresh(undo_audit)

    return UndoResult(
        original_transaction_id=str(original_txn_uuid),
        reversal_transaction_id=str(reversal.transaction_id),
        audit_log_id=str(undo_audit.id),
    )


def undo_action(
    db: Session,
    *,
    audit_log_id: str,
    actor_user_id: str,
    actor_username: str | None = None,
    ip_address: str | None = None,
    undo_window: timedelta | None = None,
) -> UndoResult:
    """Reverse an AI-initiated write via a compensating entry, within the
    quick one-click undo window.

    The original transaction is **never edited or deleted** — the
    reversal is a new transaction with opposite-sign lines and a
    descriptive reference. Both transactions remain visible in the
    audit log forever.

    Constraints:
      * The audit row must be ``actor_source='ai-assistant'`` and
        ``entity_type='transaction'``.
      * Must be within the undo window (``UNDO_WINDOW`` default).
      * The original row's transaction must still exist and not already
        be reversed.

    Once the window closes use ``reverse_action`` instead — same
    mechanism, no time limit (AI-7).
    """
    audit = _resolve_undoable_audit(db, audit_log_id, actor_user_id)

    window = undo_window or UNDO_WINDOW
    ts = audit.timestamp
    if ts and ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    if ts and datetime.now(timezone.utc) - ts > window:
        raise UndoWindowClosed(
            f"Undo window of {int(window.total_seconds())}s has closed. "
            f"Reverse the entry via the Reverse action instead."
        )

    return _perform_reversal(
        db, audit,
        actor_user_id=actor_user_id,
        actor_username=actor_username,
        ip_address=ip_address,
        tool_name="undo_action",
    )


def reverse_action(
    db: Session,
    *,
    audit_log_id: str,
    actor_user_id: str,
    actor_username: str | None = None,
    ip_address: str | None = None,
) -> UndoResult:
    """Persistent reversal of an AI-initiated write — the same compensating
    entry as ``undo_action`` but with **no time limit** (AI-7). This is the
    recourse after the quick undo window closes, so the user never has to
    fall back to manual deletion. Guarded so an entry can't be reversed
    twice.
    """
    audit = _resolve_undoable_audit(db, audit_log_id, actor_user_id)
    return _perform_reversal(
        db, audit,
        actor_user_id=actor_user_id,
        actor_username=actor_username,
        ip_address=ip_address,
        tool_name="reverse_action",
    )
