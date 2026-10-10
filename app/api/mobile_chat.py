"""The phone's chat (roadmap ROADMAP_ANDROID_CHAT P0.3, P0.4).

The same accountant as the web chat (``/ai-accountant/chat``), answering in
typed blocks the app draws natively; the cards a reply raised can be
confirmed, cancelled and undone from here, and a thread's history comes back
with its cards, not only its words.
"""
from __future__ import annotations

import asyncio
import json
import logging
import uuid

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.ai_accountant import ChatPayload, _user_language
from app.api.ai_accountant import chat as web_chat
from app.api.mobile import PREFIX, check_app_version
from app.core.auth import SessionUser, get_current_user
from app.db.session import get_db
from app.models.ai_accountant import AIChatMessage, AIChatSession, AIProposal
from app.services.ai_accountant import blocks as B
from app.services.ai_accountant import progress
from app.services.ai_usage import AIBudgetExceeded, AIRateLimited
from app.services.ai_accountant.execute_service import (
    UNDO_WINDOW,
    ApprovalRequired,
    PermissionDenied,
    ProposalCancelled,
    ProposalExpired,
    ProposalNotFound,
    UndoNotApplicable,
    UndoWindowClosed,
    cancel_proposal,
    execute_proposal,
    undo_action,
)

router = APIRouter(prefix=PREFIX, tags=["mobile"], dependencies=[Depends(check_app_version)])
logger = logging.getLogger(__name__)


class MobileChatPayload(BaseModel):
    message: str = Field(default="", max_length=8000)
    thread_id: str | None = None
    attachment_ids: list[str] = []
    # The phone's own id for this message: a retry after a dropped connection
    # gets the reply it already had instead of a second turn (P0.5).
    client_message_id: str | None = Field(default=None, max_length=64)


def _calendar(db: Session) -> str:
    from app.services.locale_service import get_display_calendar
    return get_display_calendar(db)


def _store_blocks(db: Session, thread_id: str, blocks: list[dict]) -> None:
    """Keep the reply's blocks on its assistant message, so reopening the
    thread redraws the cards (the web redrew only the text)."""
    try:
        sid = uuid.UUID(str(thread_id))
    except (ValueError, TypeError):
        return
    row = db.execute(
        select(AIChatMessage).where(AIChatMessage.session_id == sid, AIChatMessage.role == "assistant")
        .order_by(AIChatMessage.created_at.desc(), AIChatMessage.id.desc()).limit(1)
    ).scalars().first()
    if row is not None:
        content = dict(row.content or {})
        content["blocks"] = blocks
        row.content = content
        db.commit()


def _already_answered(db: Session, thread_id: str | None, client_message_id: str | None) -> dict | None:
    """The reply a message with this client id already got in this thread."""
    if not (thread_id and client_message_id):
        return None
    try:
        sid = uuid.UUID(str(thread_id))
    except (ValueError, TypeError):
        return None
    rows = db.execute(
        select(AIChatMessage).where(AIChatMessage.session_id == sid, AIChatMessage.role.in_(("user", "assistant")))
        .order_by(AIChatMessage.created_at.desc(), AIChatMessage.id.desc()).limit(20)
    ).scalars().all()
    rows = list(reversed(rows))
    for i, m in enumerate(rows):
        if m.role == "user" and (m.content or {}).get("client_message_id") == client_message_id:
            reply = next((r for r in rows[i + 1:] if r.role == "assistant" and (r.content or {}).get("blocks")), None)
            if reply is not None:
                return {"thread_id": str(sid), "blocks": reply.content["blocks"], "stop_reason": "repeat"}
    return None


def _mark_client_message(db: Session, thread_id: str, client_message_id: str | None) -> None:
    """Note the phone's id on the user's message just answered."""
    if not client_message_id:
        return
    try:
        sid = uuid.UUID(str(thread_id))
    except (ValueError, TypeError):
        return
    row = db.execute(
        select(AIChatMessage).where(AIChatMessage.session_id == sid, AIChatMessage.role == "user")
        .order_by(AIChatMessage.created_at.desc(), AIChatMessage.id.desc()).limit(1)
    ).scalars().first()
    if row is not None:
        row.content = {**(row.content or {}), "client_message_id": client_message_id}
        db.commit()


@router.post("/chat")
async def mobile_chat(payload: MobileChatPayload, db: Session = Depends(get_db),
                      user: SessionUser = Depends(get_current_user)) -> dict:
    """One message to the accountant; the reply as blocks, cards first."""
    repeat = _already_answered(db, payload.thread_id, payload.client_message_id)
    if repeat is not None:
        return repeat
    resp = await web_chat(ChatPayload(message=payload.message, session_id=payload.thread_id,
                                      attachment_ids=payload.attachment_ids), db=db, user=user)
    lang = _user_language(db, user)
    blocks = B.build_blocks(db, text=resp.text, proposals=resp.proposals, tool_calls=resp.tool_calls,
                            intake=resp.intake, lang=lang, calendar=_calendar(db))
    _store_blocks(db, resp.session_id, blocks)
    _mark_client_message(db, resp.session_id, payload.client_message_id)
    return {"thread_id": resp.session_id, "blocks": blocks, "stop_reason": resp.stop_reason}


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False, default=str)}\n\n"


@router.post("/chat/stream")
async def mobile_chat_stream(payload: MobileChatPayload, db: Session = Depends(get_db),
                             user: SessionUser = Depends(get_current_user)) -> StreamingResponse:
    """The same turn as ``/chat``, streamed as server-sent events: a
    ``status`` for each step (thinking, a tool, reading the files) as it
    happens, then the ``reply`` with its blocks — or an ``error`` with the
    status and code ``/chat`` would have answered — then ``done``. The phone
    shows what the accountant is doing instead of nine silent seconds."""
    queue: asyncio.Queue = asyncio.Queue()

    async def run() -> None:
        token = progress.listen(lambda event: queue.put_nowait(("status", event)))
        try:
            await queue.put(("reply", await mobile_chat(payload, db=db, user=user)))
        except HTTPException as e:
            from app.core.messages import localize_detail
            detail = localize_detail(e.detail, _user_language(db, user)) if isinstance(e.detail, str) else e.detail
            await queue.put(("error", {"status": e.status_code, "detail": detail,
                                       "code": (e.headers or {}).get("X-Error-Code")}))
        except (AIBudgetExceeded, AIRateLimited) as e:
            code = "ai_budget_exceeded" if isinstance(e, AIBudgetExceeded) else "ai_rate_limited"
            await queue.put(("error", {"status": 429, "detail": str(e), "code": code}))
        except Exception:  # noqa: BLE001 — the stream must end with an answer
            logger.exception("mobile chat stream failed")
            await queue.put(("error", {"status": 500, "detail": "Something went wrong. Try again.", "code": None}))
        finally:
            progress.stop(token)
            await queue.put(None)

    task = asyncio.create_task(run())

    async def events():
        try:
            while (item := await queue.get()) is not None:
                yield _sse(*item)
            yield _sse("done", {})
        finally:
            if not task.done():                  # the phone hung up: let the turn finish on its own
                await asyncio.shield(task)

    return StreamingResponse(events(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@router.get("/threads")
def mobile_threads(db: Session = Depends(get_db), user: SessionUser = Depends(get_current_user)) -> list[dict]:
    """The user's conversations, most recent first."""
    counts = (select(AIChatMessage.session_id, func.count(AIChatMessage.id).label("n"))
              .where(AIChatMessage.role.in_(("user", "assistant"))).group_by(AIChatMessage.session_id).subquery())
    rows = db.execute(
        select(AIChatSession, func.coalesce(counts.c.n, 0))
        .outerjoin(counts, counts.c.session_id == AIChatSession.id)
        .where(AIChatSession.user_id == user.user_id)
        .order_by(AIChatSession.updated_at.desc()).limit(100)
    ).all()
    return [{"id": str(s.id), "title": s.title, "updated_at": s.updated_at.isoformat() if s.updated_at else None,
             "message_count": int(n)} for s, n in rows]


def _proposal_states(db: Session, tokens: list[str]) -> dict[str, AIProposal]:
    keys = []
    for t in tokens:
        try:
            keys.append(uuid.UUID(t))
        except (ValueError, TypeError):
            pass
    if not keys:
        return {}
    rows = db.execute(select(AIProposal).where(AIProposal.confirmation_token.in_(keys))).scalars().all()
    return {str(r.confirmation_token): r for r in rows}


@router.get("/threads/{thread_id}/messages")
def mobile_thread_messages(thread_id: str, db: Session = Depends(get_db),
                           user: SessionUser = Depends(get_current_user)) -> list[dict]:
    """A thread as the phone draws it: the user's words and the replies'
    blocks, each voucher with where it stands now (pending, posted, cancelled)."""
    try:
        sid = uuid.UUID(thread_id)
    except (ValueError, TypeError):
        raise HTTPException(status_code=404, detail="Session not found")
    session = db.get(AIChatSession, sid)
    if session is None or str(session.user_id) != str(user.user_id):
        raise HTTPException(status_code=404, detail="Session not found")
    rows = db.execute(
        select(AIChatMessage).where(AIChatMessage.session_id == sid, AIChatMessage.role.in_(("user", "assistant")))
        .order_by(AIChatMessage.created_at, AIChatMessage.id)
    ).scalars().all()
    out = []
    tokens = []
    for m in rows:
        content = m.content or {}
        if m.role == "assistant" and not content.get("blocks") and not (content.get("text") or "").strip():
            continue                                   # a tool-calling step with nothing to show
        item = {"id": str(m.id), "role": m.role, "created_at": m.created_at.isoformat() if m.created_at else None}
        if m.role == "user":
            item["text"] = content.get("text") or ""
        else:
            item["blocks"] = content.get("blocks") or [{"type": "text", "id": f"text:{m.id}", "text": content.get("text") or "",
                                                        "fallback_text": content.get("text") or ""}]
            tokens += [b["token"] for b in item["blocks"] if b.get("type") == "proposal" and b.get("token")]
        out.append(item)
    states = _proposal_states(db, tokens)
    for item in out:
        for b in item.get("blocks") or []:
            if b.get("type") == "proposal" and b.get("token") in states:
                b["state"] = states[b["token"]].status
    return out


def _execute_errors(exc: Exception) -> HTTPException:
    if isinstance(exc, ProposalNotFound):
        return HTTPException(status_code=404, detail=str(exc))
    if isinstance(exc, ProposalExpired):
        return HTTPException(status_code=410, detail=str(exc))
    if isinstance(exc, ProposalCancelled):
        return HTTPException(status_code=409, detail=str(exc))
    if isinstance(exc, PermissionDenied):
        return HTTPException(status_code=403, detail=str(exc))
    raise exc


@router.post("/proposals/{token}/confirm")
def mobile_confirm(token: str, db: Session = Depends(get_db), user: SessionUser = Depends(get_current_user)) -> dict:
    """Post the voucher. The answer is the stamped receipt with its undo
    window, or, above the company's threshold, a note that it waits for a
    second person."""
    try:
        result = execute_proposal(db, confirmation_token=token, actor_user_id=user.user_id,
                                  actor_username=user.username)
    except ApprovalRequired as e:
        return {"state": "waiting_for_approval", "approval": e.info}
    except (ProposalNotFound, ProposalExpired, ProposalCancelled, PermissionDenied) as e:
        raise _execute_errors(e)
    voucher = date_iso = None
    if result.transaction_id:
        from app.models.transaction import Transaction
        try:
            txn = db.get(Transaction, uuid.UUID(str(result.transaction_id)))
        except (ValueError, TypeError):
            txn = None
        if txn is not None:
            # the entry's own reference; a database id is no number to stamp
            voucher = txn.reference or None
            date_iso = txn.date.isoformat() if txn.date else None
    undo = 0 if result.idempotent else int(UNDO_WINDOW.total_seconds())
    return {"state": "posted", "block": B.posted_block(
        token=token, transaction_id=result.transaction_id, audit_log_id=result.audit_log_id, voucher=voucher,
        date_iso=date_iso, calendar=_calendar(db), lang=_user_language(db, user), undo_seconds=undo,
        file=_made_document(db, result.audit_log_id))}


def _made_document(db: Session, audit_log_id: str | None) -> dict | None:
    """The document a posting made, for the phone to open or share: an
    invoice's PDF (its audit row names the invoice)."""
    from app.models.audit_log import AuditLog
    from app.models.invoice import Invoice
    try:
        row = db.get(AuditLog, uuid.UUID(str(audit_log_id)))
    except (ValueError, TypeError):
        return None
    if row is None or row.entity_type != "invoice" or not row.entity_id:
        return None
    try:
        inv = db.get(Invoice, uuid.UUID(str(row.entity_id)))
    except (ValueError, TypeError):
        return None
    return B.invoice_file(inv.id, inv.number) if inv is not None else None


@router.get("/documents/invoices/{invoice_id}")
def mobile_invoice_pdf(invoice_id: uuid.UUID, db: Session = Depends(get_db)):
    """An invoice's PDF, to open on the phone or share to Telegram, WhatsApp,
    Eitaa or e-mail: the same document the web prints."""
    from app.api.invoices import invoice_pdf
    return invoice_pdf(invoice_id=invoice_id, db=db)


@router.post("/proposals/{token}/cancel")
def mobile_cancel(token: str, db: Session = Depends(get_db), user: SessionUser = Depends(get_current_user)) -> dict:
    """Discard the card; a confirmed one is undone or reversed instead."""
    try:
        cancel_proposal(db, confirmation_token=token, actor_user_id=user.user_id)
    except (ProposalNotFound, ProposalCancelled, PermissionDenied) as e:
        raise _execute_errors(e)
    return {"state": "cancelled"}


@router.post("/postings/{audit_log_id}/undo")
def mobile_undo(audit_log_id: str, db: Session = Depends(get_db), user: SessionUser = Depends(get_current_user)) -> dict:
    """Take a posting back within its two minutes: the books look as if it
    was never confirmed."""
    try:
        result = undo_action(db, audit_log_id=audit_log_id, actor_user_id=user.user_id, actor_username=user.username)
    except UndoNotApplicable as e:
        raise HTTPException(status_code=400, detail=str(e))
    except UndoWindowClosed as e:
        raise HTTPException(status_code=410, detail=str(e))
    except PermissionDenied as e:
        raise HTTPException(status_code=403, detail=str(e))
    return {"state": "undone", "mode": result.mode}


# --- files, voice and the briefing (roadmap ROADMAP_ANDROID_CHAT P0.7) ------------------

@router.post("/uploads", status_code=201)
def mobile_upload(file: UploadFile = File(...), db: Session = Depends(get_db)) -> dict:
    """A photo of a receipt, a PDF, a statement: stored like the web's
    attachments (types checked by their bytes, 8 MB), its id then sent with
    the chat message."""
    from app.api.transactions import upload_attachment
    a = upload_attachment(file=file, db=db)
    return {"id": str(a.id), "file_name": a.file_name, "content_type": a.content_type, "size_bytes": a.size_bytes}


@router.post("/transcribe")
async def mobile_transcribe(file: UploadFile = File(...), db: Session = Depends(get_db)) -> dict:
    """A voice note's words, for the user to check before sending."""
    from app.api.ai_accountant import transcribe_voice_note
    return await transcribe_voice_note(file=file, db=db)


class BriefingPayload(BaseModel):
    thread_id: str | None = None


@router.post("/briefing")
def mobile_briefing(payload: BriefingPayload, db: Session = Depends(get_db),
                    user: SessionUser = Depends(get_current_user)) -> dict:
    """The accountant speaks first when the app opens: what needs attention
    today, without a model call. No blocks when there is nothing to say."""
    from app.api.ai_accountant import BriefingPayload as WebBriefing, briefing
    r = briefing(WebBriefing(session_id=payload.thread_id), db=db, user=user)
    if not r.text:
        return {"thread_id": payload.thread_id, "blocks": []}
    blocks = [{"type": "text", "id": f"briefing:{r.session_id}", "kind": "briefing", "text": r.text,
               "fallback_text": r.text}]
    _store_blocks(db, r.session_id, blocks)
    return {"thread_id": r.session_id, "blocks": blocks}

