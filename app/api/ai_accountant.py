"""HTTP endpoints for the AI accountant feature.

Three primary endpoints:

* ``POST /ai-accountant/execute`` — commit a pending AI proposal. Body
  carries the ``confirmation_token`` issued by a proposal tool. Idempotent.
* ``POST /ai-accountant/undo`` — reverse an AI-initiated transaction
  within the 30-second undo window via a compensating journal entry.
* ``GET /ai-accountant/proposals/{token}`` — fetch the current state of
  a proposal (status / payload / expires_at) so the UI can render the
  action card without re-asking Claude.

The chat-loop endpoint (``POST /ai-accountant/chat``) lives in a
separate module wired in via the orchestrator (next chunk of work).
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.auth import SessionUser, get_current_user
from app.db.session import get_db
from app.models.ai_accountant import AIProposal
from app.services.ai_accountant.execute_service import (
    PROPOSAL_TTL,
    PermissionDenied,
    ProposalCancelled,
    ProposalExpired,
    ProposalNotFound,
    UndoNotApplicable,
    UndoWindowClosed,
    execute_proposal,
    undo_action,
)

router = APIRouter(prefix="/ai-accountant", tags=["ai-accountant"])


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class ExecutePayload(BaseModel):
    confirmation_token: str


class ExecuteResponse(BaseModel):
    transaction_id: str | None
    audit_log_id: str
    confirmation_token: str
    tool_name: str
    idempotent: bool


class UndoPayload(BaseModel):
    audit_log_id: str


class UndoResponse(BaseModel):
    original_transaction_id: str
    reversal_transaction_id: str
    audit_log_id: str


class ProposalRead(BaseModel):
    confirmation_token: str
    status: str
    tool_name: str
    tool_input: dict
    created_at: str
    expires_at: str
    user_message: str | None = None


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.post("/execute", response_model=ExecuteResponse)
def execute(
    payload: ExecutePayload,
    db: Session = Depends(get_db),
    user: SessionUser = Depends(get_current_user),
) -> ExecuteResponse:
    """Confirm and commit a pending AI proposal.

    Server-side authorization: the proposal's user_id must match the
    requesting user. Idempotent on the confirmation_token — a second
    call with the same token returns the existing audit_log_id without
    double-writing.
    """
    try:
        result = execute_proposal(
            db,
            confirmation_token=payload.confirmation_token,
            actor_user_id=user.user_id,
            actor_username=user.username,
        )
    except ProposalNotFound as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ProposalExpired as e:
        raise HTTPException(status_code=410, detail=str(e))  # 410 Gone
    except ProposalCancelled as e:
        raise HTTPException(status_code=409, detail=str(e))
    except PermissionDenied as e:
        raise HTTPException(status_code=403, detail=str(e))
    return ExecuteResponse(
        transaction_id=result.transaction_id,
        audit_log_id=result.audit_log_id,
        confirmation_token=result.confirmation_token,
        tool_name=result.tool_name,
        idempotent=result.idempotent,
    )


@router.post("/undo", response_model=UndoResponse)
def undo(
    payload: UndoPayload,
    db: Session = Depends(get_db),
    user: SessionUser = Depends(get_current_user),
) -> UndoResponse:
    """Reverse a recent AI-initiated transaction via a compensating entry.

    Allowed only:
      * Within ``UNDO_WINDOW`` seconds of the original audit timestamp
        (30s by default).
      * For audit rows with ``actor_source='ai-assistant'``.
      * By the same user who initiated the write.
    """
    try:
        result = undo_action(
            db,
            audit_log_id=payload.audit_log_id,
            actor_user_id=user.user_id,
            actor_username=user.username,
        )
    except UndoNotApplicable as e:
        raise HTTPException(status_code=400, detail=str(e))
    except UndoWindowClosed as e:
        raise HTTPException(status_code=410, detail=str(e))
    except PermissionDenied as e:
        raise HTTPException(status_code=403, detail=str(e))
    return UndoResponse(
        original_transaction_id=result.original_transaction_id,
        reversal_transaction_id=result.reversal_transaction_id,
        audit_log_id=result.audit_log_id,
    )


@router.get("/proposals/{token}", response_model=ProposalRead)
def get_proposal(
    token: str,
    db: Session = Depends(get_db),
    user: SessionUser = Depends(get_current_user),
) -> ProposalRead:
    """Look up a proposal by ``confirmation_token``. The frontend uses
    this to render the action card after parsing a tool_use block from
    the chat response."""
    try:
        token_uuid = uuid.UUID(token)
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="Invalid confirmation_token format")
    row = db.execute(
        select(AIProposal).where(AIProposal.confirmation_token == token_uuid)
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="Proposal not found")
    if row.user_id != user.user_id:
        raise HTTPException(status_code=403, detail="This proposal belongs to a different user")

    created = row.created_at
    if created and created.tzinfo is None:
        created = created.replace(tzinfo=timezone.utc)
    return ProposalRead(
        confirmation_token=str(row.confirmation_token),
        status=row.status,
        tool_name=row.tool_name,
        tool_input=row.tool_input,
        created_at=created.isoformat() if created else "",
        expires_at=(created + PROPOSAL_TTL).isoformat() if created else "",
        user_message=row.user_message,
    )
