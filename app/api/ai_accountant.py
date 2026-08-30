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

import json
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.auth import SessionUser, get_current_user
from app.core.permissions import Role
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
from app.services.audit_service import log_audit_event

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
    # Smart-intake card for spreadsheet drops: {kind: chart_export|transactions,
    # ...card data}. The UI renders it with its own Confirm targeting the
    # existing migration / excel-import confirm endpoints.
    intake: dict | None = None


class ChatSessionCreate(BaseModel):
    title: str | None = None


class ChatSessionUpdate(BaseModel):
    title: str | None = None
    archived: bool | None = None


class ChatSessionRead(BaseModel):
    id: str
    title: str | None = None
    created_at: str
    updated_at: str
    message_count: int
    match_snippet: str | None = None  # only set on q= searches


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


async def _build_ocr_context(db: Session, attachment_ids: list[str]) -> tuple[str, list[int]]:
    """OCR each attached document and render a compact text block the model
    can reason over. Returns (context_text, source_amounts) where the amounts
    are the documents' extracted totals — fed to the proposal sanity guard.

    Failures degrade gracefully — an unreadable document is reported as such
    rather than raising, so the assistant can ask the user to type the
    details instead of erroring (feature acceptance)."""
    from app.models.transaction import TransactionAttachment
    from app.services.ocr_extract import extract_from_attachment

    blocks: list[str] = []
    amounts: list[int] = []
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
        for _k in ("subtotal", "tax"):
            if fields.get(_k) is not None:
                lines.append(f"  {_k}: {fields[_k]}")
        if fields.get("amount") is not None:
            lines.append(f"  total amount: {fields['amount']} {fields.get('currency') or ''}".rstrip())
            try:
                amounts.append(int(fields["amount"]))
            except (TypeError, ValueError):
                pass
        if fields.get("invoice_or_receipt_no"):
            lines.append(f"  reference: {fields['invoice_or_receipt_no']}")
        if fields.get("confidence") is not None:
            lines.append(f"  confidence: {fields['confidence']}")
        if raw_text:
            lines.append("  raw text:\n" + raw_text[:2000])
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks), amounts


import re as _re

# A message that IS a confirmation and nothing else ("confirm", "تایید کن",
# "yes do it") — not a sentence that merely contains the word.
_CONFIRM_RE = _re.compile(
    r"^\s*(?:please\s+|لطفا\s+|لطفاً\s+)?"
    r"(?:confirm(?:\s+(?:it|the\s+transaction|the\s+entry))?|yes[,!\s]*(?:confirm|do\s+it|record\s+it)?|"
    r"ok(?:ay)?[,!\s]*(?:confirm|do\s+it)?|do\s+it|record\s+it|"
    r"تایید(?:\s*کن)?|تأیید(?:\s*کن)?|بله(?:\s*(?:تایید|تأیید|ثبت)\s*کن)?|ثبت\s*کن|باشه(?:\s*ثبت\s*کن)?)"
    r"\s*[.!؟?]*\s*$",
    _re.IGNORECASE,
)


def _is_bare_confirmation(message: str | None) -> bool:
    return bool(message) and bool(_CONFIRM_RE.match(message))


def _deterministic_turn(
    db: Session,
    user: SessionUser,
    payload: ChatPayload,
    assistant_text: str,
    *,
    intake: dict | None,
) -> ChatResponse:
    """Persist a server-decided turn (path guard / smart-intake detection)
    into the session history — no LLM call, nothing written to the books."""
    from datetime import timedelta

    from app.services.ai_accountant.orchestrator import (
        _get_or_create_session,
        maybe_autotitle_session,
    )

    session = _get_or_create_session(db, user_id=user.user_id, session_id=payload.session_id)
    # Explicit microsecond timestamps: both rows land in the same DB second and
    # server_default now() has second precision on SQLite — ordering must hold.
    now = datetime.now(timezone.utc)
    db.add(AIChatMessage(session_id=session.id, role="user",
                         content={"role": "user", "text": payload.message}, created_at=now))
    db.add(AIChatMessage(session_id=session.id, role="assistant",
                         content={"role": "assistant", "text": assistant_text},
                         created_at=now + timedelta(milliseconds=1)))
    db.commit()
    maybe_autotitle_session(db, session, payload.message)
    return ChatResponse(
        session_id=str(session.id),
        text=assistant_text,
        proposals=[],
        tool_calls=[],
        stop_reason="intake",
        turns=0,
        intake=intake,
    )


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
    from app.services.ai_accountant.file_intake import (
        build_spreadsheet_intake,
        is_path_only_message,
    )

    # A bare "confirm" typed into the chat while a card is pending: the model
    # cannot execute proposals and (observed on gpt-4o-mini) re-creates an
    # IDENTICAL new card instead — which the user then also confirms →
    # double-posting. Deterministic reply pointing at the card's button.
    if _is_bare_confirmation(payload.message) and not payload.attachment_ids:
        from app.services.ai_accountant.proposal_tools import PROPOSAL_TTL

        cutoff = datetime.now(timezone.utc) - PROPOSAL_TTL
        pending_q = select(AIProposal).where(
            AIProposal.user_id == user.user_id,
            AIProposal.status == "pending",
            AIProposal.created_at >= cutoff,
        )
        if payload.session_id:
            pending_q = pending_q.where(AIProposal.session_id == payload.session_id)
        pending = db.execute(
            pending_q.order_by(AIProposal.created_at.desc()).limit(1)
        ).scalars().first()
        if pending is not None:
            text = (
                "برای ثبت، روی دکمهٔ «Confirm» روی همان کارت پیشنهاد بالا کلیک کنید "
                "(یا «Cancel» برای انصراف). من از پیام متنی چیزی ثبت نمی‌کنم و کارت "
                "جدیدی هم نمی‌سازم — کارت قبلی هنوز منتظر تأیید شماست."
                if _user_language(db, user) == "fa"
                else "To record it, click the Confirm button on the proposal card above "
                     "(or Cancel to discard). I never record from a typed message, and "
                     "I won't create a duplicate card — the existing one is still "
                     "waiting for your click."
            )
            return _deterministic_turn(db, user, payload, text, intake=None)

    # A message that is only a filesystem path (the old drag-drop failure
    # mode): don't let the model hallucinate file contents — ask for a real
    # upload. Deterministic, no LLM call.
    if not payload.attachment_ids and is_path_only_message(payload.message):
        text = (
            "آن فقط مسیر فایل است — من به محتوای فایل دسترسی ندارم. لطفاً خود فایل را "
            "با گیره 📎 پیوست کنید یا آن را داخل گفتگو بکشید و رها کنید."
            if _user_language(db, user) == "fa"
            else "That's just a file path — I can't read files from a path. Please attach "
                 "the file itself with the paperclip 📎 or drag it into the chat."
        )
        return _deterministic_turn(db, user, payload, text, intake=None)

    # Partition attachments: spreadsheets go through smart intake; images/PDFs
    # keep the OCR path.
    from app.api.transactions import SPREADSHEET_ATTACHMENT_TYPES
    from app.models.transaction import TransactionAttachment

    sheet_atts: list[TransactionAttachment] = []
    ocr_ids: list[str] = []
    for att_id in payload.attachment_ids or []:
        try:
            att = db.get(TransactionAttachment, uuid.UUID(str(att_id)))
        except (ValueError, TypeError):
            att = None
        if att is None:
            continue
        if (att.content_type or "").lower() in SPREADSHEET_ATTACHMENT_TYPES:
            sheet_atts.append(att)
        else:
            ocr_ids.append(att_id)

    intake_context = ""
    if sheet_atts:
        from app.core.permissions import Perm, role_can

        intake = build_spreadsheet_intake(
            db, sheet_atts, can_migrate=role_can(user.role, Perm.MIGRATION_WRITE)
        )
        if intake is not None and intake.kind in ("chart_export", "transactions"):
            # Deterministic detection + confirm card — no LLM call, no silent
            # writes: Confirm goes through the existing gated endpoints.
            if intake.kind == "chart_export":
                log_audit_event(
                    db, "migration_import_preview", "migration_batch",
                    entity_id=str(intake.payload.get("batch_id")),
                    detail=json.dumps({"via": "chat", "files": [a.file_name for a in sheet_atts]},
                                      ensure_ascii=False),
                )
            db.commit()
            return _deterministic_turn(
                db, user, payload, intake.detected,
                intake={"kind": intake.kind, **intake.payload},
            )
        if intake is not None:
            intake_context = "\n\n" + intake.context_text if intake.context_text else ""

    ocr_context = ""
    ocr_amounts: list[int] = []
    if ocr_ids:
        ocr_context, ocr_amounts = await _build_ocr_context(db, ocr_ids)
    ocr_context = (ocr_context or "") + intake_context
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
            source_amounts=ocr_amounts or None,
            mode="personal" if user.role == Role.PERSONAL else "default",
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


def _session_to_read(session: AIChatSession, count: int, snippet: str | None = None) -> ChatSessionRead:
    return ChatSessionRead(
        id=str(session.id),
        title=session.title,
        created_at=session.created_at.isoformat() if session.created_at else "",
        updated_at=session.updated_at.isoformat() if session.updated_at else "",
        message_count=count,
        match_snippet=snippet,
    )


@router.get("/sessions", response_model=list[ChatSessionRead])
def list_sessions(
    q: str | None = None,
    db: Session = Depends(get_db),
    user: SessionUser = Depends(get_current_user),
) -> list[ChatSessionRead]:
    """List the calling user's chat sessions, newest first (archived hidden).

    ``q`` filters by title AND message content; matched sessions carry a
    ``match_snippet`` around the first content hit so the UI can highlight it.
    """
    from sqlalchemy import Text, cast, func, or_

    query = (
        select(AIChatSession, func.count(AIChatMessage.id))
        .outerjoin(AIChatMessage, AIChatMessage.session_id == AIChatSession.id)
        .where(AIChatSession.user_id == user.user_id, AIChatSession.archived.is_(False))
        .group_by(AIChatSession.id)
        .order_by(AIChatSession.updated_at.desc())
        .limit(50)
    )
    term = (q or "").strip()
    # Non-ASCII terms: SQLite's JSON serializer stores \uXXXX escapes while
    # Postgres JSONB keeps raw Unicode — search both forms of the term.
    _term_likes = []
    if term:
        variants = {term.lower(), json.dumps(term, ensure_ascii=True)[1:-1].lower()}
        _term_likes = [f"%{v}%" for v in variants]
    if term:
        from sqlalchemy.orm import aliased

        msg = aliased(AIChatMessage)  # outer query already joins AIChatMessage
        content_col = func.lower(cast(msg.content, Text))
        content_match = (
            select(msg.id)
            .where(
                msg.session_id == AIChatSession.id,
                or_(*[content_col.like(lk) for lk in _term_likes]),
            )
            .exists()
        )
        query = query.where(or_(
            func.lower(AIChatSession.title).like(f"%{term.lower()}%"), content_match
        ))

    rows = db.execute(query).all()
    out: list[ChatSessionRead] = []
    for session, count in rows:
        snippet = None
        if term:
            hit_col = func.lower(cast(AIChatMessage.content, Text))
            hit = db.execute(
                select(AIChatMessage)
                .where(
                    AIChatMessage.session_id == session.id,
                    or_(*[hit_col.like(lk) for lk in _term_likes]),
                )
                .order_by(AIChatMessage.created_at)
                .limit(1)
            ).scalars().first()
            if hit is not None:
                text = str((hit.content or {}).get("text") or "")
                pos = text.lower().find(term.lower())
                if pos >= 0:
                    start = max(0, pos - 40)
                    snippet = ("…" if start else "") + text[start: pos + len(term) + 40]
        out.append(_session_to_read(session, int(count or 0), snippet))
    return out


@router.post("/sessions", response_model=ChatSessionRead, status_code=201)
def create_session(
    payload: ChatSessionCreate,
    db: Session = Depends(get_db),
    user: SessionUser = Depends(get_current_user),
) -> ChatSessionRead:
    """Start a fresh, empty chat session (the sidebar's New chat)."""
    session = AIChatSession(user_id=user.user_id, title=(payload.title or "").strip() or None)
    db.add(session)
    db.commit()
    db.refresh(session)
    log_audit_event(db, "create", "ai_chat_session", entity_id=str(session.id))
    db.commit()
    return _session_to_read(session, 0)


def _get_own_session(db: Session, user: SessionUser, session_id: str) -> AIChatSession:
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
    return session


@router.patch("/sessions/{session_id}", response_model=ChatSessionRead)
def update_session(
    session_id: str,
    payload: ChatSessionUpdate,
    db: Session = Depends(get_db),
    user: SessionUser = Depends(get_current_user),
) -> ChatSessionRead:
    """Rename and/or archive (soft-delete) a chat session."""
    session = _get_own_session(db, user, session_id)
    changes: dict[str, object] = {}
    if payload.title is not None:
        session.title = payload.title.strip()[:256] or None
        changes["title"] = session.title
    if payload.archived is not None:
        session.archived = bool(payload.archived)
        changes["archived"] = session.archived
    if not changes:
        raise HTTPException(status_code=400, detail="Nothing to change")
    db.commit()
    log_audit_event(db, "update", "ai_chat_session", entity_id=str(session.id),
                    detail=json.dumps(changes, ensure_ascii=False, default=str))
    db.commit()
    return _session_to_read(session, len(session.messages))


@router.delete("/sessions/{session_id}", response_model=ChatSessionRead)
def delete_session(
    session_id: str,
    db: Session = Depends(get_db),
    user: SessionUser = Depends(get_current_user),
) -> ChatSessionRead:
    """Soft-delete: archive the session (history kept for audit)."""
    session = _get_own_session(db, user, session_id)
    session.archived = True
    db.commit()
    log_audit_event(db, "archive", "ai_chat_session", entity_id=str(session.id))
    db.commit()
    return _session_to_read(session, len(session.messages))


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
