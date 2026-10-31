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
import time
import uuid
from uuid import UUID
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.auth import SessionUser, get_current_user
from app.core.permissions import Role
from app.core.http_headers import content_disposition
from app.db.session import get_db
from app.models.ai_accountant import AIChatMessage, AIChatSession, AIProposal
from app.services.ai_accountant.anthropic_client import AIAccountantError
from app.services.ai_accountant.execute_service import (
    PROPOSAL_TTL,
    ApprovalRequired,
    PermissionDenied,
    ProposalCancelled,
    ProposalExpired,
    ProposalNotFound,
    UndoNotApplicable,
    UndoWindowClosed,
    approve_proposal,
    execute_proposal,
    reject_proposal,
    reverse_action,
    undo_action,
)
from app.services.ai_accountant.orchestrator import run_chat_turn
from app.services.audit_service import log_audit_event

import logging
logger = logging.getLogger(__name__)

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
    reversal_transaction_id: str | None = None
    audit_log_id: str
    # "deleted" (quick undo removed the entry) | "reversed" (compensating entry)
    mode: str = "reversed"


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


class ReplyTo(BaseModel):
    """The message this one answers (scenarios AC4–AC7): whose it was, its words, a card's token."""
    role: str = Field(default="assistant", max_length=16)
    text: str = Field(default="", max_length=4000)
    token: str | None = Field(default=None, max_length=64)


class ChatPayload(BaseModel):
    message: str
    session_id: str | None = None
    attachment_ids: list[str] = []
    reply_to: ReplyTo | None = None


class ChatProposal(BaseModel):
    confirmation_token: str
    tool_name: str
    summary: str
    preview: dict
    expires_at: str | None = None
    # guardrails (§5.6): confirming sends it for a second person's approval
    needs_approval: bool = False
    approval_threshold: Money | None = None
    amount_in_base: Money | None = None
    # parties the card will create on Confirm ("Will create client: Aria")
    new_entities: list[dict] = []
    # the voucher as the phone draws it: what, when, how much, each line with its account's name
    # (scenarios AD1–AD5); None for a card that posts nothing to the books
    card: dict | None = None


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
    # «can I afford it?» as the review's card, in the reader's language (scenario AM9)
    review_cards: list[dict] = []


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
    # on an answer: the cards that came with it, each as it stands now (scenarios AE1–AE4)
    cards: list[dict] = []


_SUPPORTED_CHAT_LANGUAGES = ("en", "fa", "es", "ar")


def _user_language(db: Session, user: SessionUser) -> str:
    """The user's preferred UI language, used to localize the assistant's
    replies and status text (AI-2). Falls back to English."""
    from app.models.user import User

    try:
        row = db.get(User, uuid.UUID(str(user.user_id)))
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
from app.core.money import Money
from app.core.money import json_dumps

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


# Words that say what to do with a statement's rows, not only that it is one: «… مبالغی که علامت +
# دارند در بدهکار حساب بانک دی و بستانکار درآمد شهریه … ثبت شوند» (the owner's father, 2026-10-18).
# «ثبتشون کن» alone stays the import's own summary and its step-by-step review.
_SIDE_WORDS = ("بدهکار", "بستانکار", "علامت", "debit", "credit", "debe", "haber", "مدين", "دائن")
_POST_WORDS = ("ثبت", "سند", "بزن", "record", "post", "book", "registra", "asiento", "سجّل", "سجل", "قيد")


def _gives_rules(message: str | None) -> bool:
    low = " ".join((message or "").split()).lower()
    if not low:
        return False
    return any(w in low for w in _SIDE_WORDS) or (len(low.split()) >= 10 and any(w in low for w in _POST_WORDS))


def _statement_handoff(db: Session, turn, message: str | None) -> str | None:
    """The model's context for a statement just imported, when the user's words say what to do with its
    rows (scenario AF2); None when they don't, and the import's own summary answers."""
    sid = (turn.intake or {}).get("statement_id")
    if not sid or not _gives_rules(message) or (turn.intake or {}).get("status") == "reading":
        return None                  # still being read: «continue with your instructions» comes when it is (AJ6)
    from app.services.ai_accountant.statement_posting import context_for_model
    text = context_for_model(db, sid)
    return "\n\n" + text if text else None


_NO_AI_FOR_RULES = {
    "en": "I imported the statement, but the assistant isn't available to apply your rules right now. Try again in a few minutes, or post the rows from the Bank statements page.",
    "fa": "صورت‌حساب وارد شد، اما دستیار الان در دسترس نیست تا قاعده‌های شما را اعمال کند. چند دقیقهٔ دیگر دوباره امتحان کنید، یا ردیف‌ها را از صفحهٔ صورت‌حساب‌های بانکی ثبت کنید.",
    "es": "Importé el extracto, pero el asistente no está disponible para aplicar tus reglas ahora. Inténtalo de nuevo en unos minutos o registra las filas desde la página de extractos bancarios.",
    "ar": "استوردت الكشف، لكن المساعد غير متاح الآن لتطبيق قواعدك. حاول بعد دقائق، أو سجّل الصفوف من صفحة كشوف الحساب البنكية.",
}


def _deterministic_turn(
    db: Session,
    user: SessionUser,
    payload: ChatPayload,
    assistant_text: str,
    *,
    intake: dict | None,
    tool_calls: list[dict] | None = None,
    quote: dict | None = None,
    user_saved: bool = False,
) -> ChatResponse:
    """Persist a server-decided turn (path guard / smart-intake detection)
    into the session history — no LLM call, nothing written to the books."""
    from datetime import timedelta

    from app.services.ai_accountant.orchestrator import (
        _get_or_create_session,
        maybe_autotitle_session,
        sent_files,
    )

    session = _get_or_create_session(db, user_id=user.user_id, session_id=payload.session_id)
    # Explicit microsecond timestamps: both rows land in the same DB second and
    # server_default now() has second precision on SQLite — ordering must hold.
    now = datetime.now(timezone.utc)
    said = {"role": "user", "text": payload.message}
    if payload.attachment_ids:
        said["files"] = sent_files(db, payload.attachment_ids)
    if quote:
        said["reply_to"] = quote
    if not user_saved:          # a turn the model began has kept the user's words already
        db.add(AIChatMessage(session_id=session.id, role="user", content=said, created_at=now))
    db.add(AIChatMessage(session_id=session.id, role="assistant",
                         content={"role": "assistant", "text": assistant_text},
                         created_at=now + timedelta(milliseconds=1)))
    db.commit()
    maybe_autotitle_session(db, session, payload.message)
    return ChatResponse(
        session_id=str(session.id),
        text=assistant_text,
        proposals=[],
        tool_calls=tool_calls or [],
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

    # Nothing to answer: refuse before any AI call (a blank turn still cost a
    # model request).
    if not (payload.message or "").strip() and not payload.attachment_ids:
        raise HTTPException(status_code=422, detail="Type a message or attach a file.")
    from app.services.ai_accountant import replies
    reply = payload.reply_to
    quote = replies.quoted(db, user_id=user.user_id, role=reply.role, text=reply.text,
                           token=reply.token) if reply else None

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
                "برای ثبت، روی دکمهٔ «تأیید» در همان کارت پیشنهادی بالا بزنید "
                "(یا «انصراف» اگر نمی‌خواهید ثبت شود). من از پیام متنی چیزی ثبت نمی‌کنم و کارت "
                "جدیدی هم نمی‌سازم — کارت قبلی هنوز منتظر تأیید شماست."
                if _user_language(db, user) == "fa"
                else "To record it, click the Confirm button on the proposal card above "
                     "(or Cancel to discard). I never record from a typed message, and "
                     "I won't create a duplicate card — the existing one is still "
                     "waiting for your click."
            )
            return _deterministic_turn(db, user, payload, text, intake=None, quote=quote)

    # A message that is only a filesystem path (the old drag-drop failure
    # mode): don't let the model hallucinate file contents — ask for a real
    # upload. Deterministic, no LLM call.
    if not payload.attachment_ids and is_path_only_message(payload.message):
        text = (
            "این فقط مسیر فایل است — من به محتوای فایل دسترسی ندارم. لطفاً خود فایل را "
            "با گیره 📎 پیوست کنید یا آن را داخل گفتگو بکشید و رها کنید."
            if _user_language(db, user) == "fa"
            else "That's just a file path — I can't read files from a path. Please attach "
                 "the file itself with the paperclip 📎 or drag it into the chat."
        )
        return _deterministic_turn(db, user, payload, text, intake=None, quote=quote)

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
    stmt_handoff = None          # a statement imported from the file, handed to the model with the user's rules
    if sheet_atts or ocr_ids:
        from app.services.ai_accountant import progress
        progress.emit("reading_files", lang=_user_language(db, user))
    if sheet_atts:
        from app.core.permissions import Perm, role_can

        from app.services.ai_accountant.orchestrator import _message_language as _said_in
        intake = build_spreadsheet_intake(
            db, sheet_atts, can_migrate=role_can(user.role, Perm.MIGRATION_WRITE),
            lang=_said_in(payload.message, _user_language(db, user)),     # «به نظر فهرست سندهاست …» (AF8)
        )
        if intake is not None and intake.kind in ("chart_export", "journals"):
            # Deterministic detection + confirm card — no LLM call, no silent
            # writes: Confirm goes through the existing gated endpoints.
            if intake.kind == "chart_export":
                log_audit_event(
                    db, "migration_import_preview", "migration_batch",
                    entity_id=str(intake.payload.get("batch_id")),
                    detail=json_dumps({"via": "chat", "files": [a.file_name for a in sheet_atts]},
                                      ensure_ascii=False),
                )
            db.commit()
            from app.core.messages import localize_lists
            # the summary's errors and warnings in the user's language (#53)
            return _deterministic_turn(
                db, user, payload, intake.detected,
                intake=localize_lists({"kind": intake.kind, **intake.payload}, _user_language(db, user)),
                quote=quote,
            )
        if intake is not None:
            intake_context = "\n\n" + intake.context_text if intake.context_text else ""
        # Not a chart or a journal: a bank statement is imported as it is,
        # before (and without) the AI.
        from app.services.ai_accountant.statement_intake import maybe_statement_intake
        sheet_turn = await maybe_statement_intake(
            db, user_role=user.role, attachments=sheet_atts,
            message=payload.message, lang=_user_language(db, user),
        )
        if sheet_turn is not None:
            log_audit_event(
                db, "bank_statement_import", "bank_statement",
                entity_id=str(sheet_turn.intake.get("statement_id") or ""),
                detail=json_dumps({"via": "chat", "file": sheet_turn.intake.get("file_name"),
                                   "status": sheet_turn.intake.get("status")}, ensure_ascii=False),
            )
            db.commit()
            handoff = _statement_handoff(db, sheet_turn, payload.message)
            if handoff is None:
                from app.core.messages import localize_lists
                return _deterministic_turn(db, user, payload, sheet_turn.text,
                                           intake=localize_lists(sheet_turn.intake, _user_language(db, user)), quote=quote)
            stmt_handoff, intake_context = sheet_turn, handoff

    # A common question the books answer directly ("how much cash?", "who owes
    # us?"): the read tool runs without the model — exact, instant, no tokens,
    # and it works when the AI is down (roadmap ROADMAP_ANDROID_CHAT P0.6).
    # Not a reply: «و به دلار؟» under an answer is about that answer, which only the model reads.
    if not payload.attachment_ids and quote is None:
        from app.services.ai_accountant import fast_paths
        from app.services.ai_accountant.orchestrator import _message_language
        fast = await fast_paths.answer(db, payload.message, user_id=user.user_id, username=user.username,
                                       lang=_message_language(payload.message, _user_language(db, user)),
                                       mode="personal" if user.role == Role.PERSONAL else "default")
        if fast is not None:
            return _deterministic_turn(db, user, payload, fast.text, intake=None, tool_calls=fast.tool_calls)

    # From here on the turn uses the AI (OCR, statement vision, the model
    # loop): per-user / per-company limits and the 24-hour token budget.
    from app.services.ai_usage import guard_ai_request
    try:
        guard_ai_request(db)
    except Exception:
        if stmt_handoff is None:
            raise
        # the statement is in; the rules wait for the assistant (scenario AF7)
        from app.services.ai_accountant.orchestrator import _message_language as _said_in
        lang_ = _said_in(payload.message, _user_language(db, user))
        return _deterministic_turn(db, user, payload, stmt_handoff.text + "\n\n" + _NO_AI_FOR_RULES.get(lang_, _NO_AI_FOR_RULES["en"]),
                                   intake=stmt_handoff.intake, quote=quote)

    ocr_context = ""
    ocr_amounts: list[int] = []
    if ocr_ids:
        # A bank statement PDF/image is a table of many rows, not a receipt:
        # import it through the statement pipeline and answer with a review
        # card instead of asking the user to type the rows (QA finding 1).
        from app.services.ai_accountant.statement_intake import maybe_statement_intake

        ocr_atts = [a for a in (db.get(TransactionAttachment, uuid.UUID(str(i))) for i in ocr_ids) if a is not None]
        stmt_turn = await maybe_statement_intake(
            db, user_role=user.role, attachments=ocr_atts,
            message=payload.message, lang=_user_language(db, user),
        )
        if stmt_turn is not None:
            log_audit_event(
                db, "bank_statement_import", "bank_statement",
                entity_id=str(stmt_turn.intake.get("statement_id") or ""),
                detail=json_dumps({"via": "chat", "file": stmt_turn.intake.get("file_name"),
                                   "status": stmt_turn.intake.get("status")}, ensure_ascii=False),
            )
            db.commit()
            handoff = _statement_handoff(db, stmt_turn, payload.message)
            if handoff is None:
                return _deterministic_turn(db, user, payload, stmt_turn.text, intake=stmt_turn.intake, quote=quote)
            stmt_handoff, intake_context = stmt_turn, handoff
        else:
            ocr_context, ocr_amounts = await _build_ocr_context(db, ocr_ids)
    ocr_context = (ocr_context or "") + intake_context
    turn_started = time.perf_counter()
    if stmt_handoff is not None and not payload.session_id:
        # the conversation first, so a turn the model can't finish still answers in it (AF7)
        from app.services.ai_accountant.orchestrator import _get_or_create_session
        payload = payload.model_copy(update={"session_id": str(_get_or_create_session(db, user_id=user.user_id, session_id=None).id)})
        db.commit()
    try:
        result = await run_chat_turn(
            db,
            user_id=user.user_id,
            username=user.username,
            user_message=payload.message,
            session_id=payload.session_id,
            lang=_user_language(db, user),
            ocr_context=ocr_context or None,
            # a statement's file isn't a receipt to pin on each of its vouchers
            attachment_ids=(payload.attachment_ids or None) if stmt_handoff is None else None,
            source_amounts=ocr_amounts or None,
            mode="personal" if user.role == Role.PERSONAL else "default",
            reply_to=quote,
        )
    except AIAccountantError as e:
        if stmt_handoff is not None:
            # the statement is in; the rules wait for the assistant (scenario AF7)
            from app.services.ai_accountant.orchestrator import _message_language as _said_in
            lang_ = _said_in(payload.message, _user_language(db, user))
            return _deterministic_turn(db, user, payload, stmt_handoff.text + "\n\n" + _NO_AI_FOR_RULES.get(lang_, _NO_AI_FOR_RULES["en"]),
                                       intake=stmt_handoff.intake, quote=quote, user_saved=True)
        # the detail is for whoever configures the AI; the code lets the chat say it plainly.
        # An empty provider account isn't "try again in a moment" (Metis 402 "Insufficient
        # credit, please top up", model benchmark 2026-10-12): the reason says so.
        low = str(e).lower()
        reason = {"X-Error-Reason": "out_of_credit"} if any(
            w in low for w in ("402", "insufficient credit", "payment required", "insufficient_quota", "top up")) else {}
        raise HTTPException(status_code=502, detail=str(e), headers={"X-Error-Code": "ai_unavailable", **reason})
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))

    # Step-by-step statement review: the model described a finding but
    # didn't raise the card (observed on gpt-4o-mini) → raise it for it.
    def _statement_turn(tc: dict) -> bool:
        if tc.get("name") == "review_bank_statement":
            return True
        inp = tc.get("input") or {}
        return isinstance(inp, dict) and bool(inp.get("bank_statement_row_id"))

    if not result.proposals and any(_statement_turn(tc) for tc in (result.tool_calls or [])):
        from app.services.ai_accountant.statement_intake import ensure_statement_row_proposal

        try:
            extra = await ensure_statement_row_proposal(
                db, user_id=user.user_id, username=user.username,
                session_id=result.session_id, user_message=payload.message,
                tool_calls=result.tool_calls or [], assistant_text=result.text,
            )
        except Exception:  # noqa: BLE001 — never fail the turn over the safety net
            logger.warning("statement row proposal safety net failed", exc_info=True)
            extra = None
        if extra:
            result.proposals.append(extra)
            hint = (
                "👉 برای ثبت این ردیف، روی «تأیید» در کارت زیر بزنید (یا «انصراف»)."
                if _user_language(db, user) == "fa"
                else "👉 Click Confirm on the card below to record this row (or Cancel to skip it)."
            )
            result.text = ((result.text or "").rstrip() + "\n\n" + hint).strip()
    # about one turn in ten, for the owner's review queue (roadmap §5.5)
    from app.services import ai_review
    ai_review.maybe_sample(db, result, user_id=user.user_id, username=user.username, message=payload.message,
                           lang=_user_language(db, user), channel="web",
                           latency_ms=int((time.perf_counter() - turn_started) * 1000),
                           personal=user.role == Role.PERSONAL)
    from app.services.ai_accountant.orchestrator import _message_language
    reply_lang = _message_language(payload.message, _user_language(db, user))
    cards = _voucher_cards(db, result.proposals, lang=reply_lang)
    review_cards = _review_cards(db, result.tool_calls, lang=reply_lang)
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
                needs_approval=bool(p.get("needs_approval")),
                approval_threshold=p.get("approval_threshold"),
                amount_in_base=p.get("amount_in_base"),
                new_entities=list(p.get("new_entities") or []),
                card=cards.get(str(p["confirmation_token"])),
            )
            for p in result.proposals
        ],
        tool_calls=result.tool_calls,
        stop_reason=result.stop_reason,
        turns=result.turns,
        # the statement imported from the file stays on screen beside the card made of it (AF2)
        intake=_localized_intake(db, user, stmt_handoff.intake) if stmt_handoff is not None else None,
        review_cards=review_cards,
    )


def _review_cards(db: Session, tool_calls: list[dict] | None, *, lang: str) -> list[dict]:
    """The cards the turn's read tools draw as a review would (check_affordability), for the web."""
    try:
        from app.services.ai_accountant import blocks as B
        from app.services.locale_service import get_display_calendar
        return [{k: v for k, v in b.items() if k not in ("type", "id", "tool", "fallback_text")}
                for b in B.data_blocks(tool_calls, lang=lang, calendar=get_display_calendar(db)) if b.get("type") == "review"]
    except Exception:  # noqa: BLE001 — the words still say it
        logger.warning("review cards for the web chat failed", exc_info=True)
        return []


def _localized_intake(db: Session, user: SessionUser, intake: dict) -> dict:
    from app.core.messages import localize_lists
    return localize_lists(intake, _user_language(db, user))


def _voucher_cards(db: Session, proposals: list[dict], *, lang: str) -> dict[str, dict]:
    """Each voucher card's title, date, amount and lines, by token, as the phone's
    card says them (blocks.proposal_block): a person's card on the web showed the
    server's English summary, «DR 112001 کیف پول سبز: 77.5» (scenario AD1)."""
    out: dict[str, dict] = {}
    try:
        from app.services.ai_accountant import blocks as B
        from app.services.locale_service import get_display_calendar
        calendar = get_display_calendar(db)
        names = B.card_names(db, proposals)
        for p in proposals or []:
            if not ((p.get("preview") or {}).get("lines") if isinstance(p.get("preview"), dict) else None):
                continue
            b = B.proposal_block(db, p, calendar=calendar, lang=lang, names=names)
            out[str(p["confirmation_token"])] = {k: b[k] for k in ("title", "amount", "date", "lines", "people")}
    except Exception:  # noqa: BLE001 — the summary still says it
        logger.warning("voucher cards for the web chat failed", exc_info=True)
    return out


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
        variants = {term.lower(), json_dumps(term, ensure_ascii=True)[1:-1].lower()}
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
    cards = _history_cards(db, sid, user, rows)
    return [
        ChatMessageRead(
            id=str(m.id),
            role=m.role,
            content=m.content,
            created_at=m.created_at.isoformat() if m.created_at else "",
            cards=cards.get(str(m.id), []),
        )
        for m in rows
    ]


def _history_cards(db: Session, sid: uuid.UUID, user: SessionUser, rows: list[AIChatMessage]) -> dict[str, list[dict]]:
    """The cards this user made in this conversation, by the answer each came
    with (the first answer after it), each as it stands now: pending, posted
    with its reference and date, cancelled, replaced, rejected or expired. A web
    conversation opened again brought back only its words (scenarios AE1–AE4)."""
    proposals = db.execute(select(AIProposal).where(AIProposal.session_id == str(sid),
                                                    AIProposal.user_id == str(user.user_id))
                           .order_by(AIProposal.created_at)).scalars().all()
    answers = [m for m in rows if m.role == "assistant" and str((m.content or {}).get("text") or "").strip()]
    if not proposals or not answers:
        return {}
    from app.api.mobile_chat import _stamps
    from app.models.audit_log import AuditLog
    from app.services.ai_accountant import blocks as B
    from app.services.ai_accountant.proposal_tools import PROPOSAL_TTL
    from app.services.locale_service import get_display_calendar

    items = [{"confirmation_token": str(p.confirmation_token), "tool_name": p.tool_name, "summary": p.summary or "",
              "preview": p.tool_input if isinstance(p.tool_input, dict) else {}} for p in proposals]
    names = B.card_names(db, items)
    stamps = _stamps(db, [p for p in proposals if p.status == "executed"], user)
    cancelled = [str(p.id) for p in proposals if p.status == "cancelled"]
    replaced = set()
    if cancelled:
        for entity_id, detail in db.execute(select(AuditLog.entity_id, AuditLog.detail).where(
                AuditLog.entity_type == "ai_proposal", AuditLog.entity_id.in_(cancelled))).all():
            if detail and "replaced_by" in detail:
                replaced.add(str(entity_id))
    calendar, lang = get_display_calendar(db), _user_language(db, user)
    now = datetime.now(timezone.utc)

    def utc(dt: datetime) -> datetime:                # SQLite gives them back without their zone
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)

    out: dict[str, list[dict]] = {}
    for p, item in zip(proposals, items):
        made = utc(p.created_at)
        state = p.status
        if state == "pending" and p.approval_status != "requested" and made + PROPOSAL_TTL < now:
            state = "expired"
        elif state == "cancelled" and p.approval_status == "rejected":
            state = "rejected"
        elif state == "cancelled" and str(p.id) in replaced:
            state = "replaced"
        card = None
        if item["preview"].get("lines"):
            b = B.proposal_block(db, item, calendar=calendar, lang=lang, names=names)
            card = {k: b[k] for k in ("title", "amount", "date", "lines", "people")}
        parties = [e for e in (item["preview"].get("new_entities") or []) if isinstance(e, dict) and e.get("name")]
        answer = next((m for m in answers if utc(m.created_at) >= made), answers[-1])
        out.setdefault(str(answer.id), []).append({
            **item, "card": card, "state": state, "approval_status": p.approval_status,
            "posted": stamps.get(item["confirmation_token"]), "new_entities": parties,
        })
    return out


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
    except ApprovalRequired as e:
        # above the company's threshold: waiting for someone else (§5.6)
        from fastapi.responses import JSONResponse
        return JSONResponse(status_code=202, content={**e.info, "confirmation_token": payload.confirmation_token})
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


# --- Guardrails: two-person approval (roadmap §5.6) -------------------------------------------------

class GuardrailsUpdate(BaseModel):
    approval_threshold: Money | None = Field(default=None, ge=0, description="Base-currency amount; empty = off")


@router.get("/guardrails")
def read_guardrails(db: Session = Depends(get_db), user: SessionUser = Depends(get_current_user)) -> dict:
    from app.services.ai_accountant import guardrails
    return {**guardrails.get_settings(db), "approvers": guardrails.approvers(db)}


@router.put("/guardrails")
def save_guardrails(payload: GuardrailsUpdate, db: Session = Depends(get_db),
                    user: SessionUser = Depends(get_current_user)) -> dict:
    from app.services.ai_accountant import guardrails
    before = guardrails.get_settings(db)
    out = guardrails.save_settings(db, approval_threshold=payload.approval_threshold)
    log_audit_event(db, action="update", entity_type="ai_guardrails", entity_id="approval_threshold",
                    user_id=user.user_id, username=user.username,
                    detail=json_dumps({"from": before["approval_threshold"], "to": out["approval_threshold"]}))
    db.commit()
    return {**out, "approvers": guardrails.approvers(db)}


# --- the review queue (roadmap §5.5): owner only ----------------------------------------------------------------

class ReviewUpdate(BaseModel):
    verdict: str | None = Field(default=None, description="good | bad | null (back to the queue)")
    note: str | None = Field(default=None, max_length=2000)


class ReviewSettingsUpdate(BaseModel):
    enabled: bool


def _sample_or_404(db: Session, sample_id: str):
    from app.services import ai_review
    s = ai_review.get_sample(db, sample_id)
    if s is None:
        raise HTTPException(status_code=404, detail="Review sample not found.")
    return s


@router.get("/review-samples")
def list_review_samples(status: str = Query("new", pattern="^(new|good|bad|all)$"),
                        limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0),
                        db: Session = Depends(get_db), user: SessionUser = Depends(get_current_user)) -> dict:
    from app.services import ai_review
    return ai_review.list_samples(db, status=status, limit=limit, offset=offset)


@router.patch("/review-samples/{sample_id}")
def review_sample(sample_id: str, payload: ReviewUpdate, db: Session = Depends(get_db),
                  user: SessionUser = Depends(get_current_user)) -> dict:
    from app.services import ai_review
    s = _sample_or_404(db, sample_id)
    try:
        ai_review.review(db, s, verdict=payload.verdict, note=payload.note, reviewer=user.username)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    db.commit()
    return {"sample": ai_review.to_dict(s), "counts": ai_review.counts(db)}


@router.delete("/review-samples/{sample_id}")
def discard_review_sample(sample_id: str, db: Session = Depends(get_db),
                          user: SessionUser = Depends(get_current_user)) -> dict:
    from app.services import ai_review
    db.delete(_sample_or_404(db, sample_id))
    db.commit()
    return {"deleted": True, "counts": ai_review.counts(db)}


@router.get("/review-samples/{sample_id}/scenario")
def review_sample_as_scenario(sample_id: str, db: Session = Depends(get_db),
                              user: SessionUser = Depends(get_current_user)):
    from fastapi.responses import JSONResponse

    from app.services import ai_review
    s = _sample_or_404(db, sample_id)
    body = ai_review.as_scenario(s)
    return JSONResponse(body, headers={"Content-Disposition": content_disposition(f'{body["id"]}.json')})


@router.put("/review-settings")
def save_review_settings(payload: ReviewSettingsUpdate, db: Session = Depends(get_db),
                         user: SessionUser = Depends(get_current_user)) -> dict:
    from app.services import ai_review
    before = ai_review.get_settings(db)["enabled"]
    out = ai_review.save_settings(db, enabled=payload.enabled)
    log_audit_event(db, action="update", entity_type="ai_review", entity_id="enabled",
                    user_id=user.user_id, username=user.username,
                    detail=json_dumps({"from": before, "to": out["enabled"]}))
    db.commit()
    return out


@router.get("/approvals")
def list_approvals(db: Session = Depends(get_db), user: SessionUser = Depends(get_current_user)) -> dict:
    """Proposals waiting for a second person; yours are listed but not yours to approve."""
    from app.services.ai_accountant import guardrails
    items = [guardrails.describe(db, p) for p in guardrails.waiting(db)]
    for it in items:
        it["mine"] = str(it["requested_by_id"]) == str(user.user_id)
    return {"items": items}


class RejectPayload(BaseModel):
    note: str | None = Field(default=None, max_length=1000)


@router.post("/approvals/{token}/approve", response_model=ExecuteResponse)
def approve(token: str, db: Session = Depends(get_db), user: SessionUser = Depends(get_current_user)) -> ExecuteResponse:
    try:
        result = approve_proposal(db, confirmation_token=token, approver_user_id=user.user_id,
                                  approver_username=user.username)
    except ProposalNotFound as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ProposalExpired as e:
        raise HTTPException(status_code=410, detail=str(e))
    except ProposalCancelled as e:
        raise HTTPException(status_code=409, detail=str(e))
    except PermissionDenied as e:
        raise HTTPException(status_code=403, detail=str(e))
    return ExecuteResponse(transaction_id=result.transaction_id, audit_log_id=result.audit_log_id,
                           confirmation_token=result.confirmation_token, tool_name=result.tool_name,
                           idempotent=result.idempotent)


@router.post("/approvals/{token}/reject")
def reject(token: str, payload: RejectPayload, db: Session = Depends(get_db),
           user: SessionUser = Depends(get_current_user)) -> dict:
    try:
        row = reject_proposal(db, confirmation_token=token, approver_user_id=user.user_id,
                              approver_username=user.username, note=payload.note)
    except ProposalNotFound as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ProposalCancelled as e:
        raise HTTPException(status_code=409, detail=str(e))
    except PermissionDenied as e:
        raise HTTPException(status_code=403, detail=str(e))
    return {"status": "rejected", "confirmation_token": str(row.confirmation_token)}


class BriefingPayload(BaseModel):
    session_id: str | None = None


class BriefingResponse(BaseModel):
    session_id: str | None = None
    text: str | None = None
    count: int = 0


def _briefed_today(db: Session, session_id, text: str) -> bool:
    """This briefing was already said in this conversation today."""
    since = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    rows = db.execute(select(AIChatMessage.content).where(
        AIChatMessage.session_id == session_id, AIChatMessage.role == "assistant",
        AIChatMessage.created_at >= since).order_by(AIChatMessage.created_at.desc()).limit(50)).scalars().all()
    return any((c or {}).get("briefing") and (c or {}).get("text") == text for c in rows)


@router.post("/briefing", response_model=BriefingResponse)
def briefing(
    payload: BriefingPayload,
    db: Session = Depends(get_db),
    user: SessionUser = Depends(get_current_user),
) -> BriefingResponse:
    """The assistant speaks first: a short, deterministic briefing of the
    proactive insights (no LLM call), persisted as an assistant message in
    the chat session so it survives reload and shows in the transcript.
    Returns ``text=None`` — and writes nothing — when there is nothing to say."""
    from app.services.ai_accountant.orchestrator import _get_or_create_session
    from app.services.insight_service import briefing_text, compute_insights

    from app.services.ai_accountant.orchestrator import thread_language
    insights = compute_insights(db)
    text = briefing_text(insights, thread_language(db, payload.session_id, _user_language(db, user)))
    if not text:
        return BriefingResponse(session_id=payload.session_id, text=None, count=0)
    try:
        session = _get_or_create_session(db, user_id=user.user_id, session_id=payload.session_id)
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))
    if _briefed_today(db, session.id, text):
        # the page remembers its own day, so a second browser or phone asked again: once a day in a
        # conversation, unless it changed, as the phone's briefing (deep test 2026-10-20)
        return BriefingResponse(session_id=str(session.id), text=None, count=0)
    db.add(AIChatMessage(session_id=session.id, role="assistant",
                         content={"role": "assistant", "text": text, "briefing": True},
                         created_at=datetime.now(timezone.utc)))
    db.commit()
    return BriefingResponse(session_id=str(session.id), text=text, count=len(insights))


class ReviewsResponse(BaseModel):
    session_id: str | None = None
    reviews: list[dict] = []


@router.post("/reviews", response_model=ReviewsResponse)
def reviews(
    payload: BriefingPayload,
    db: Session = Depends(get_db),
    user: SessionUser = Depends(get_current_user),
) -> ReviewsResponse:
    """The app's own weekly and monthly reviews and its nudge, not yet seen by
    this reader (scenarios AL1–AL13): each becomes an assistant message of the
    conversation — kept, so it is in its history — with its suggestions' buttons.
    Unlike the briefing, not once a day: a review made after the day's briefing
    still comes the next time the chat is opened (AL4)."""
    from app.services.ai_accountant.orchestrator import _get_or_create_session
    from app.services.proactive_reviews import take_for_chat
    said = take_for_chat(db, user, _user_language(db, user))
    if not said:
        return ReviewsResponse(session_id=payload.session_id, reviews=[])
    try:
        session = _get_or_create_session(db, user_id=user.user_id, session_id=payload.session_id)
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))
    for r in said:
        db.add(AIChatMessage(session_id=session.id, role="assistant",
                             content={"role": "assistant", "text": r["text"], "review": r["review_id"],
                                      "choices": r["choices"], "blocks": r["blocks"], "card": r["card"],
                                      "cards": r["cards"]},
                             created_at=datetime.now(timezone.utc)))
    db.commit()
    return ReviewsResponse(session_id=str(session.id),
                           reviews=[{"kind": r["kind"], "text": r["text"], "choices": r["choices"], "card": r["card"],
                                     "cards": r["cards"]}
                                    for r in said])


class ReviewPrefs(BaseModel):
    weekly: bool = True
    monthly: bool = True
    suggestions: bool = True
    nudge: bool = True


@router.get("/review-prefs", response_model=ReviewPrefs)
def get_review_prefs(db: Session = Depends(get_db), user: SessionUser = Depends(get_current_user)) -> ReviewPrefs:
    """Which of the app's own messages this person gets (scenario AL14)."""
    from app.services.proactive_reviews import switched_off, user_row
    off = switched_off(user_row(db, user))
    return ReviewPrefs(**{k: k not in off for k in ReviewPrefs.model_fields})


@router.put("/review-prefs", response_model=ReviewPrefs)
def put_review_prefs(payload: ReviewPrefs, db: Session = Depends(get_db),
                     user: SessionUser = Depends(get_current_user)) -> ReviewPrefs:
    from app.services.proactive_reviews import set_switch, user_row
    row = user_row(db, user)
    if row is None:
        raise HTTPException(status_code=404, detail="User not found")
    for kind, on in payload.model_dump().items():
        set_switch(row, kind, on)
    db.commit()
    return payload


@router.post("/proposals/{token}/cancel")
def cancel(token: str, db: Session = Depends(get_db), user: SessionUser = Depends(get_current_user)) -> dict:
    """Discard a card the user asked for: it stops being pending on the server
    too, so nothing can confirm it later (roadmap ROADMAP_ANDROID_CHAT P0.4)."""
    from app.services.ai_accountant.execute_service import cancel_proposal
    try:
        cancel_proposal(db, confirmation_token=token, actor_user_id=user.user_id)
    except ProposalNotFound as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ProposalCancelled as e:
        raise HTTPException(status_code=409, detail=str(e))
    except PermissionDenied as e:
        raise HTTPException(status_code=403, detail=str(e))
    return {"ok": True, "status": "cancelled"}


@router.post("/undo", response_model=UndoResponse)
def undo(
    payload: UndoPayload,
    db: Session = Depends(get_db),
    user: SessionUser = Depends(get_current_user),
) -> UndoResponse:
    """Quick undo of a recent AI-initiated transaction: the entry is removed
    (soft-deleted) so the books look as if it was never confirmed. Only when
    the entry sits in a closed period is a compensating entry posted instead
    (``mode="reversed"``).

    Allowed only:
      * Within ``UNDO_WINDOW`` seconds of the original audit timestamp.
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
        mode=result.mode,
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
        mode=result.mode,
    )


# ─── Voice notes (roadmap §5.7) ─────────────────────────────────────────

@router.post("/transcribe")
async def transcribe_voice_note(file: UploadFile = File(...), db: Session = Depends(get_db)) -> dict:
    """A recorded voice note → its text, for the user to check and send.
    Metered and budgeted like a chat turn; the audio is not kept."""
    from app.services.ai_usage import guard_ai_request
    from app.services.speech import MAX_BYTES, SpeechError, transcribe
    data = await file.read(MAX_BYTES + 1)
    guard_ai_request(db)
    try:
        return await transcribe(data)
    except SpeechError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e


# ─── Messenger bots (roadmap §5.7) ─────────────────────────────────────

class MessengerLinkRequest(BaseModel):
    platform: str = Field(..., pattern="^(telegram|bale)$")


@router.get("/messenger")
def messenger_status(db: Session = Depends(get_db), user: SessionUser = Depends(get_current_user)) -> dict:
    """Which bots this server has, and the caller's linked chats."""
    from app.services.messenger import links_of, public_status
    return {"platforms": public_status(db), "links": links_of(db, user.user_id)}


@router.post("/messenger/link", status_code=201)
def messenger_link(payload: MessengerLinkRequest, db: Session = Depends(get_db),
                   user: SessionUser = Depends(get_current_user)) -> dict:
    """A one-time link (10 minutes) that ties a Telegram/Bale chat to this login."""
    from app.services.messenger import start_link
    try:
        out = start_link(db, user_id=user.user_id, platform=payload.platform)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    db.commit()
    return out


@router.delete("/messenger/links/{link_id}", status_code=204)
def messenger_unlink(link_id: UUID, db: Session = Depends(get_db),
                     user: SessionUser = Depends(get_current_user)) -> None:
    from app.models.messenger import MessengerLink
    link = db.get(MessengerLink, link_id)
    if link is None or str(link.user_id) != user.user_id:
        raise HTTPException(status_code=404, detail="Not found")
    db.delete(link)
    db.commit()


# ─── Correction memory (roadmap §5.4) ─────────────────────────────────

class PreferenceCreate(BaseModel):
    description: str = Field(..., min_length=2, max_length=256)
    account_code: str | None = Field(None, max_length=64)
    entity_id: UUID | None = None


@router.get("/preferences")
def list_preferences(db: Session = Depends(get_db)) -> list[dict]:
    """What was learned from the user's corrections: wording → account/party."""
    from app.services.learned_preferences import describe, list_all
    return [describe(db, p) for p in list_all(db)]


@router.post("/preferences", status_code=201)
def add_preference(payload: PreferenceCreate, db: Session = Depends(get_db)) -> dict:
    from app.models.account import Account, AccountLevel
    from app.models.entity import Entity
    from app.services.learned_preferences import describe, remember
    if payload.account_code:
        acc = db.execute(select(Account).where(Account.code == payload.account_code.strip())).scalars().first()
        if acc is None or acc.level == AccountLevel.GROUP or acc.is_active is False:
            raise HTTPException(status_code=422, detail=f"Account {payload.account_code} is not a postable account.")
    if payload.entity_id and db.get(Entity, payload.entity_id) is None:
        raise HTTPException(status_code=422, detail="Entity not found.")
    pref = remember(db, payload.description, account_code=(payload.account_code or "").strip() or None,
                    entity_id=payload.entity_id, source="manual")
    if pref is None:
        raise HTTPException(status_code=422, detail="Give an account or a party, and wording that is more than "
                                                    "numbers and common words.")
    db.commit()
    return describe(db, pref)


@router.delete("/preferences/{pref_id}", status_code=204)
def delete_preference(pref_id: UUID, db: Session = Depends(get_db)) -> None:
    from app.services.learned_preferences import forget
    if not forget(db, pref_id):
        raise HTTPException(status_code=404, detail="Not found")
    db.commit()


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


class StatementNextPayload(BaseModel):
    session_id: str | None = None


class StatementNextResponse(BaseModel):
    session_id: str
    statement_id: str
    text: str
    proposals: list[ChatProposal] = []
    left: int = 0
    # the statement's account is only a guess: choose it first (scenario AJ12)
    choose: bool = False
    choices: list[dict] = []


@router.post("/statements/{statement_id}/next", response_model=StatementNextResponse)
async def statement_next(statement_id: UUID, payload: StatementNextPayload, db: Session = Depends(get_db),
                         user: SessionUser = Depends(get_current_user)) -> StatementNextResponse:
    """«رفع قدم‌به‌قدم» on the web, as the phone's Next: the next row the books don't have, as a card, without
    the model, and how many are left. The button used to ask the model, which raised the first card and stopped
    there (the owner, 2026-10-21, scenario AJ13). Kept in the conversation like any answer."""
    from app.models.bank_statement import BankStatement
    from app.services.ai_accountant.orchestrator import _get_or_create_session
    from app.services.ai_accountant.statement_next import next_difference
    stmt = db.get(BankStatement, statement_id)
    if stmt is None:
        raise HTTPException(status_code=404, detail="Statement not found")
    try:
        session = _get_or_create_session(db, user_id=user.user_id, session_id=payload.session_id)
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))
    lang = _user_language(db, user)
    nxt = await next_difference(db, stmt=stmt, session=session, user_id=str(user.user_id), username=user.username,
                                lang=lang)
    proposals: list[ChatProposal] = []
    if nxt.proposal is not None:
        p = nxt.proposal
        cards = _voucher_cards(db, [p], lang=lang)
        proposals.append(ChatProposal(
            confirmation_token=p["confirmation_token"], tool_name=p.get("tool_name", ""), summary=p.get("summary", ""),
            preview=p.get("preview", {}), expires_at=p.get("expires_at"), needs_approval=bool(p.get("needs_approval")),
            approval_threshold=p.get("approval_threshold"), amount_in_base=p.get("amount_in_base"),
            new_entities=list(p.get("new_entities") or []), card=cards.get(str(p["confirmation_token"]))))
    # after the card, so the conversation's history draws the card with these words (scenario AE1)
    db.add(AIChatMessage(session_id=session.id, role="assistant",
                         content={"role": "assistant", "text": nxt.text,
                                  "statement_next": {"statement_id": nxt.statement_id, "left": nxt.left,
                                                     "choose": nxt.choose}},
                         created_at=datetime.now(timezone.utc)))
    db.commit()
    return StatementNextResponse(session_id=nxt.session_id, statement_id=nxt.statement_id, text=nxt.text,
                                 proposals=proposals, left=nxt.left if nxt.proposal is not None else 0,
                                 choose=nxt.choose, choices=nxt.choices)


@router.get("/statements/{statement_id}/card")
def statement_card(statement_id: UUID, db: Session = Depends(get_db),
                   user: SessionUser = Depends(get_current_user)) -> dict:
    """A statement dropped in the chat, as it stands: still being read (how far), read (its card and
    what was read), or stopped. The chat's «reading» card asks every few seconds (scenarios AJ1, AJ6)."""
    from app.models.bank_statement import BankStatement
    from app.services.ai_accountant import statement_intake as si
    from app.services.locale_service import get_display_calendar
    from app.services.statement_import import read_report, settle_stale_reading
    stmt = db.get(BankStatement, statement_id)
    if stmt is None:
        raise HTTPException(status_code=404, detail="Statement not found")
    settle_stale_reading(db, stmt)
    lang = _user_language(db, user)
    report = read_report(stmt)
    out = {"status": stmt.status, "pages_total": stmt.pages_total, "pages_read": stmt.pages_read}
    if stmt.status == "reading":
        return out
    if stmt.status == "failed":
        return {**out, "text": si.reading_said(report or {"error": True}, lang)}
    intake = {"kind": "bank_statement", "status": "imported", "statement_id": str(stmt.id),
              "bank_name": stmt.bank_name, "bank_label": si.bank_label(stmt.bank_name, lang),
              "file_name": stmt.source_filename, **si._review_fields(db, stmt.id)}
    text = si._reply(lang, intake, jalali=get_display_calendar(db) == "jalali")
    if report:
        intake["read_report"] = report
        text += " " + si.reading_said(report, lang)
    return {**out, "intake": intake, "text": text}
