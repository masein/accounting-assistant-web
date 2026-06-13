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
from app.models.ai_accountant import AIChatMessage, AIChatSession, AIProposal
from app.services.ai_accountant.anthropic_client import AIAccountantError
from app.services.ai_accountant.execute_service import (
    PROPOSAL_TTL,
    PermissionDenied,
    ProposalCancelled,
    ProposalExpired,
    ProposalNotFound,
    UndoNotApplicable,
    UndoWindowClosed,
    execute_proposal,
    reverse_action,
    undo_action,
)
from app.services.ai_accountant.orchestrator import run_chat_turn

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


class ChatPayload(BaseModel):
    message: str
    session_id: str | None = None
    attachment_ids: list[str] = []


class ChatProposal(BaseModel):
    confirmation_token: str
    tool_name: str
    summary: str
    preview: dict
    expires_at: str | None = None


class ChatResponse(BaseModel):
    session_id: str
    text: str
    proposals: list[ChatProposal] = []
    tool_calls: list[dict] = []
    stop_reason: str | None = None
    turns: int


class ChatSessionRead(BaseModel):
    id: str
    title: str | None = None
    created_at: str
    updated_at: str
    message_count: int


class ChatMessageRead(BaseModel):
    id: str
    role: str
    content: dict
    created_at: str


_SUPPORTED_CHAT_LANGUAGES = ("en", "fa", "es", "ar")


def _user_language(db: Session, user: SessionUser) -> str:
    """The user's preferred UI language, used to localize the assistant's
    replies and status text (AI-2). Falls back to English."""
    from app.models.user import User

    try:
        row = db.get(User, user.user_id)
        lang = (row.preferred_language or "en") if row else "en"
    except Exception:
        lang = "en"
    lang = (lang or "en").strip().lower()
    return lang if lang in _SUPPORTED_CHAT_LANGUAGES else "en"


async def _build_ocr_context(db: Session, attachment_ids: list[str]) -> str:
    """OCR each attached document and render a compact text block the model
    can reason over. Failures degrade gracefully — an unreadable document
    is reported as such rather than raising, so the assistant can ask the
    user to type the details instead of erroring (feature acceptance)."""
    from app.models.transaction import TransactionAttachment
    from app.services.ocr_extract import extract_from_attachment

    blocks: list[str] = []
    for raw_id in attachment_ids:
        try:
            att_uuid = uuid.UUID(str(raw_id))
        except (ValueError, TypeError):
            continue
        row = db.get(TransactionAttachment, att_uuid)
        if row is None:
            continue
        name = row.file_name or "document"
        try:
            fields = await extract_from_attachment(row.file_path, row.content_type)
        except Exception:
            fields = {}
        raw_text = (fields.get("raw_text") or "").strip()
        has_fields = any(
            fields.get(k) for k in ("vendor_name", "date", "amount", "invoice_or_receipt_no")
        )
        if not has_fields and not raw_text:
            blocks.append(
                f"Attached document OCR (attachment_id={row.id}, file={name}): "
                f"the document could not be read automatically. Ask the user for the "
                f"key details (amount, date, vendor) instead of guessing."
            )
            continue
        lines = [f"Attached document OCR (attachment_id={row.id}, file={name}):"]
        if fields.get("vendor_name"):
            lines.append(f"  vendor: {fields['vendor_name']}")
        if fields.get("date"):
            lines.append(f"  date: {fields['date']}")
        if fields.get("amount") is not None:
            lines.append(f"  total amount: {fields['amount']} {fields.get('currency') or ''}".rstrip())
        if fields.get("invoice_or_receipt_no"):
            lines.append(f"  reference: {fields['invoice_or_receipt_no']}")
        if fields.get("confidence") is not None:
            lines.append(f"  confidence: {fields['confidence']}")
        if raw_text:
            lines.append("  raw text:\n" + raw_text[:2000])
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)


@router.post("/chat", response_model=ChatResponse)
async def chat(
    payload: ChatPayload,
    db: Session = Depends(get_db),
    user: SessionUser = Depends(get_current_user),
) -> ChatResponse:
    """Send a single user message through the AI accountant.

    Runs the entire tool-use loop server-side and returns the final
    text + any proposals registered along the way. The frontend
    renders each proposal as an inline action card; clicking Confirm
    hits ``POST /ai-accountant/execute``.

    When ``attachment_ids`` are present (an invoice/receipt uploaded in
    the chat), each file is OCR'd and its extracted fields are fed to the
    model as context for the turn; the files are linked onto whatever
    transaction the model proposes.
    """
    ocr_context = ""
    if payload.attachment_ids:
        ocr_context = await _build_ocr_context(db, payload.attachment_ids)
    try:
        result = await run_chat_turn(
            db,
            user_id=user.user_id,
            username=user.username,
            user_message=payload.message,
            session_id=payload.session_id,
            lang=_user_language(db, user),
            ocr_context=ocr_context or None,
            attachment_ids=payload.attachment_ids or None,
        )
    except AIAccountantError as e:
        raise HTTPException(status_code=502, detail=str(e))
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))
    return ChatResponse(
        session_id=result.session_id,
        text=result.text,
        proposals=[
            ChatProposal(
                confirmation_token=p["confirmation_token"],
                tool_name=p.get("tool_name", ""),
                summary=p.get("summary", ""),
                preview=p.get("preview", {}),
                expires_at=p.get("expires_at"),
            )
            for p in result.proposals
        ],
        tool_calls=result.tool_calls,
        stop_reason=result.stop_reason,
        turns=result.turns,
    )


@router.get("/sessions", response_model=list[ChatSessionRead])
def list_sessions(
    db: Session = Depends(get_db),
    user: SessionUser = Depends(get_current_user),
) -> list[ChatSessionRead]:
    """List the calling user's chat sessions, newest first."""
    from sqlalchemy import func
    rows = (
        db.execute(
            select(
                AIChatSession,
                func.count(AIChatMessage.id),
            )
            .outerjoin(AIChatMessage, AIChatMessage.session_id == AIChatSession.id)
            .where(AIChatSession.user_id == user.user_id)
            .group_by(AIChatSession.id)
            .order_by(AIChatSession.updated_at.desc())
            .limit(50)
        )
        .all()
    )
    return [
        ChatSessionRead(
            id=str(row[0].id),
            title=row[0].title,
            created_at=row[0].created_at.isoformat() if row[0].created_at else "",
            updated_at=row[0].updated_at.isoformat() if row[0].updated_at else "",
            message_count=int(row[1] or 0),
        )
        for row in rows
    ]


@router.get("/sessions/{session_id}/messages", response_model=list[ChatMessageRead])
def list_messages(
    session_id: str,
    db: Session = Depends(get_db),
    user: SessionUser = Depends(get_current_user),
) -> list[ChatMessageRead]:
    """Return every message in a chat session in chronological order."""
    try:
        sid = uuid.UUID(session_id)
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="Invalid session_id")
    session = db.execute(
        select(AIChatSession).where(AIChatSession.id == sid)
    ).scalar_one_or_none()
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")
    if session.user_id != user.user_id:
        raise HTTPException(status_code=403, detail="Session belongs to a different user")
    rows = (
        db.execute(
            select(AIChatMessage)
            .where(AIChatMessage.session_id == sid)
            .order_by(AIChatMessage.created_at, AIChatMessage.id)
        )
        .scalars()
        .all()
    )
    return [
        ChatMessageRead(
            id=str(m.id),
            role=m.role,
            content=m.content,
            created_at=m.created_at.isoformat() if m.created_at else "",
        )
        for m in rows
    ]


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


@router.post("/reverse", response_model=UndoResponse)
def reverse(
    payload: UndoPayload,
    db: Session = Depends(get_db),
    user: SessionUser = Depends(get_current_user),
) -> UndoResponse:
    """Persistent reversal of an AI-initiated transaction (AI-7).

    Same compensating-entry mechanism as ``/undo`` but with **no time
    limit** — the recourse after the quick undo window closes, so the user
    never has to fall back to manual deletion. Allowed only for the user
    who created the AI write, and only once per entry.
    """
    try:
        result = reverse_action(
            db,
            audit_log_id=payload.audit_log_id,
            actor_user_id=user.user_id,
            actor_username=user.username,
        )
    except UndoNotApplicable as e:
        raise HTTPException(status_code=400, detail=str(e))
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
