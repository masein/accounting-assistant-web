from __future__ import annotations

import base64
import collections
import logging
import re
import time as _time
import uuid
from datetime import date
from pathlib import Path
from uuid import UUID

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
import sqlalchemy as sa
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.db.session import get_db
from app.models.account import Account
from app.models.entity import Entity, TransactionEntity
from app.models.transaction import Transaction, TransactionAttachment, TransactionLine
from app.models.transaction_fee import FeeApplicationStatus, PaymentMethod, TransactionFee, TransactionFeeApplication
from app.schemas.transaction import (
    AttachmentRead,
    AttachmentOCRResponse,
    ChatRequest,
    ChatResponse,
    ImportTransactionsRequest,
    ImportTransactionsResponse,
    ResolvedEntityLink,
    SuggestTransactionRequest,
    SuggestTransactionResponse,
    TransactionCreate,
    TransactionEntityLinkRead,
    TransactionLineCreate,
    TransactionRead,
    TransactionLineRead,
    TransactionUpdate,
)
from app.schemas.transaction_fee import (
    PaymentMethodRead,
    TransactionFeeCalculateRequest,
    TransactionFeeCalculateResponse,
    TransactionFeeRead,
    TransactionFeeUpsertRequest,
    TransactionFeeUpsertResponse,
)
from app.services.ai_suggest import (
    AISuggestError,
    _infer_entity_mentions_from_text,
    chat_turn as ai_chat_turn,
    parse_transaction_edit_intent,
    suggest_transaction as ai_suggest_transaction,
)
from app.services.ocr_extract import OCRExtractError, extract_from_attachment
from app.services.reporting.report_intent import parse_report_intent
from app.services.transaction_fee import (
    build_fee_line_items,
    canonical_method_name,
    extract_payment_context,
    get_active_fee_rule,
    parse_fee_config_text,
    parse_fee_question_context,
    apply_fee_to_transaction_lines,
    calculate_total_with_fee,
    find_bank_entity_by_name,
    find_payment_method,
    get_or_create_bank_entity,
    recalculate_current_month_pending_entries,
    resolve_fee_rule,
    upsert_fee_rule,
)


# Transaction building moved to app/services/ledger_posting so services stop
# importing from the API layer. Re-exported under the old names: several
# modules and tests import these paths.
from app.services.ledger_posting import (  # noqa: E402
    create_transaction_from_payload as _create_transaction_from_payload,
    get_account_by_code as _get_account_by_code,
    get_or_create_entity as _get_or_create_entity,
    load_attachments as _load_attachments,
    upsert_role_link as _upsert_role_link,
    validate_balanced_lines as _validate_balanced_lines,
)


def _load_transaction_with_lines(db: Session, t: Transaction) -> None:
    """Ensure transaction lines and their accounts are loaded."""
    _ = t.lines
    for line in t.lines:
        _ = line.account
    _ = t.entity_links
    for link in t.entity_links:
        _ = link.entity
    _ = t.attachments

router = APIRouter(prefix="/transactions", tags=["transactions"])
chat_logger = logging.getLogger("app.chat")

UPLOADS_DIR = Path(__file__).resolve().parents[1] / "uploads" / "transactions"


class _RateLimiter:
    """Simple in-memory sliding-window rate limiter."""

    def __init__(self, max_requests: int = 10, window_seconds: int = 60):
        self._max = max_requests
        self._window = window_seconds
        self._buckets: dict[str, collections.deque] = {}

    def check(self, key: str) -> bool:
        """Return True if request is allowed, False if rate-limited."""
        now = _time.time()
        if key not in self._buckets:
            self._buckets[key] = collections.deque()
        q = self._buckets[key]
        while q and q[0] < now - self._window:
            q.popleft()
        if len(q) >= self._max:
            return False
        q.append(now)
        return True


_chat_limiter = _RateLimiter(max_requests=10, window_seconds=60)
MAX_ATTACHMENT_SIZE_BYTES = 8 * 1024 * 1024
ALLOWED_ATTACHMENT_TYPES = {
    "image/jpeg",
    "image/png",
    "image/webp",
    "application/pdf",
    # Spreadsheets — chat smart-intake (chart exports, transaction sheets, Q&A)
    "text/csv",
    "application/csv",
    "text/tab-separated-values",
    "application/vnd.ms-excel",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
}

# Extension fallback: browsers often send spreadsheet files with a blank or
# generic content type (e.g. application/octet-stream for .xls) — infer from
# the filename so the picker's accept list and the server agree.
_EXTENSION_CONTENT_TYPES = {
    ".csv": "text/csv",
    ".tsv": "text/tab-separated-values",
    ".xls": "application/vnd.ms-excel",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".pdf": "application/pdf",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
}

SPREADSHEET_ATTACHMENT_TYPES = {
    "text/csv",
    "application/csv",
    "text/tab-separated-values",
    "application/vnd.ms-excel",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
}


def _attachment_url(file_path: str) -> str:
    p = Path(file_path)
    return f"/uploads/transactions/{p.name}"


def _attachment_to_read(a: TransactionAttachment) -> AttachmentRead:
    return AttachmentRead(
        id=a.id,
        file_name=a.file_name,
        content_type=a.content_type,
        size_bytes=a.size_bytes,
        url=_attachment_url(a.file_path),
        transaction_id=a.transaction_id,
    )


def _transaction_to_read(t: Transaction) -> TransactionRead:
    lines = [
        TransactionLineRead(
            id=line.id,
            account_id=line.account_id,
            account_code=line.account.code,
            debit=line.debit,
            credit=line.credit,
            line_description=line.line_description,
        )
        for line in t.lines
    ]
    return TransactionRead(
        id=t.id,
        date=t.date,
        reference=t.reference,
        description=t.description,
        currency=t.currency or "IRR",
        lines=lines,
        entity_links=[
            TransactionEntityLinkRead(
                role=link.role,
                entity_id=link.entity_id,
                entity_name=(link.entity.name if link.entity else None),
                entity_type=(link.entity.type if link.entity else None),
                amount=link.amount,
            )
            for link in (t.entity_links or [])
        ],
        attachments=[_attachment_to_read(a) for a in (t.attachments or [])],
        created_at=t.created_at,
        updated_at=t.updated_at,
    )


def _transaction_brief(t: Transaction) -> str:
    from app.utils.jalali import format_jalali
    desc = (t.description or "No description").strip()
    if len(desc) > 48:
        desc = desc[:48] + "…"
    jalali = format_jalali(t.date) if t.date else ""
    return f"{t.date.isoformat()} ({jalali}) | ref: {(t.reference or '—')} | {desc}"


def _all_bank_names(db: Session) -> list[str]:
    return [
        n
        for n in db.execute(
            select(Entity.name).where(Entity.type == "bank").order_by(Entity.name)
        ).scalars().all()
        if n
    ]


def _log_transaction_audit(db: Session, action: str, txn: Transaction) -> None:
    """Log a transaction audit event and save a version snapshot."""
    try:
        import json as _json
        from app.services.audit_service import log_audit_event
        from app.models.audit_log import TransactionVersion

        snapshot = {
            "id": str(txn.id),
            "date": txn.date.isoformat() if txn.date else None,
            "reference": txn.reference,
            "description": txn.description,
            "lines": [
                {"account_code": getattr(ln.account, "code", ""), "debit": ln.debit, "credit": ln.credit, "desc": ln.line_description}
                for ln in (txn.lines or [])
            ] if hasattr(txn, "lines") and txn.lines else [],
        }

        log_audit_event(
            db, action=action, entity_type="transaction",
            entity_id=str(txn.id),
            detail=_json.dumps(snapshot, default=str),
        )

        existing_count = db.execute(
            select(func.count(TransactionVersion.id)).where(TransactionVersion.transaction_id == str(txn.id))
        ).scalar() or 0

        db.add(TransactionVersion(
            transaction_id=str(txn.id),
            version=existing_count + 1,
            snapshot=_json.dumps(snapshot, default=str),
            action=action,
        ))
        db.commit()
    except (OSError, sa.exc.SQLAlchemyError) as exc:
        chat_logger.warning("audit_log_failed: %s", exc, exc_info=True)
























def _transaction_fee_to_read(rule: TransactionFee) -> TransactionFeeRead:
    method = getattr(rule, "method", None)
    bank = getattr(rule, "bank", None)
    return TransactionFeeRead(
        id=rule.id,
        method_id=rule.method_id,
        method_name=(method.name if method else ""),
        bank_id=rule.bank_id,
        bank_name=(bank.name if bank else ""),
        fee_type=rule.fee_type.value,
        fee_value=rule.fee_value or 0,
        flat_fee=rule.flat_fee or 0,
        percent_bps=rule.percent_bps or 0,
        max_fee=rule.max_fee,
        effective_from=rule.effective_from,
        is_active=bool(rule.is_active),
    )
































# The legacy chat's supporting logic (intent detection, entity/bank matching,
# fee-context parsing, report building) lives in app/services/transaction_chat.
# Imported under the original private names so the chat handler below is
# unchanged by the move.
from app.services.transaction_chat import (  # noqa: E402
    _align_payment_amount_with_context,
    _build_report_from_intent,
    _canonical_bank_key,
    _fee_question_message,
    _find_bank_entity_by_text,
    _find_last_voucher_assistant_idx,
    _find_transactions_for_ai_edit,
    _format_entity_transaction_results,
    _friendly_ai_error,
    _infer_followup_report_intent,
    _level_for_code,
    _looks_like_edit_request,
    _looks_like_fee_correction,
    _looks_like_non_payment_query,
    _looks_like_transaction_user_text,
    _normalize_employee_payment_account,
    _normalize_entity_mentions_for_context,
    _normalize_for_match,
    _parent_code_for,
    _parse_entity_transaction_query,
    _parse_included_fee_context,
    _resolve_bank_account_code,
    _search_transactions_by_entity,
    _select_transaction_context_text,
    _transaction_window_messages,
    _user_says_unknown_method,
)


@router.post("/attachments", response_model=AttachmentRead, status_code=201)
async def upload_attachment(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
) -> AttachmentRead:
    from app.core.file_validation import validate_file_magic

    content_type = (file.content_type or "").strip().lower()
    if content_type not in ALLOWED_ATTACHMENT_TYPES:
        # Browsers send blank/generic types for spreadsheets — infer from name.
        inferred = _EXTENSION_CONTENT_TYPES.get(Path(file.filename or "").suffix.lower())
        if inferred:
            content_type = inferred
        else:
            raise HTTPException(
                status_code=400,
                detail="Unsupported file type. Use JPG, PNG, WEBP, PDF, CSV, TSV, XLS, or XLSX.",
            )
    raw = await file.read()
    if not raw:
        raise HTTPException(status_code=400, detail="Attachment is empty.")
    validate_file_magic(raw, content_type)
    if len(raw) > MAX_ATTACHMENT_SIZE_BYTES:
        raise HTTPException(status_code=400, detail="Attachment too large. Max size is 8 MB.")
    ext = Path(file.filename or "file").suffix or {
        "image/jpeg": ".jpg",
        "image/png": ".png",
        "image/webp": ".webp",
        "application/pdf": ".pdf",
    }.get(content_type, "")
    UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
    stored_name = f"{uuid.uuid4().hex}{ext}"
    path = UPLOADS_DIR / stored_name
    path.write_bytes(raw)
    row = TransactionAttachment(
        file_name=(file.filename or stored_name).strip()[:256],
        file_path=str(path),
        content_type=content_type,
        size_bytes=len(raw),
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return _attachment_to_read(row)


@router.delete("/attachments/{attachment_id}", status_code=204)
def delete_attachment(
    attachment_id: UUID,
    db: Session = Depends(get_db),
) -> None:
    row = db.get(TransactionAttachment, attachment_id)
    if not row:
        raise HTTPException(status_code=404, detail="Attachment not found")
    if row.transaction_id:
        raise HTTPException(status_code=400, detail="Attachment is already linked to a transaction")
    try:
        p = Path(row.file_path)
        if p.exists():
            p.unlink()
    except OSError:
        pass
    db.delete(row)
    db.commit()


@router.post("/attachments/{attachment_id}/ocr", response_model=AttachmentOCRResponse)
async def ocr_attachment(
    attachment_id: UUID,
    db: Session = Depends(get_db),
) -> AttachmentOCRResponse:
    row = db.get(TransactionAttachment, attachment_id)
    if not row:
        raise HTTPException(status_code=404, detail="Attachment not found")
    try:
        out = await extract_from_attachment(row.file_path, row.content_type)
    except OCRExtractError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return AttachmentOCRResponse(**out)


@router.post("/chat", response_model=ChatResponse)
async def chat(
    payload: ChatRequest,
    db: Session = Depends(get_db),
) -> ChatResponse:
    """
    Conversational flow: send messages (user/assistant history). AI may ask which client,
    which bank account, what for. When it has enough info, returns message + transaction to fill the form.
    """
    rate_key = "global"
    if not _chat_limiter.check(rate_key):
        return ChatResponse(
            message="You're sending messages too quickly. Please wait a moment before trying again.",
            transaction=None,
        )
    accounts = db.execute(select(Account).order_by(Account.code)).scalars().all()
    account_list = [{"code": a.code, "name": a.name} for a in accounts]
    if not account_list:
        raise HTTPException(status_code=400, detail="No accounts. Run the app so the seed runs.")
    messages = [{"role": m.role, "content": m.content} for m in payload.messages]
    last_user_message = next((m.get("content") or "" for m in reversed(messages) if m.get("role") == "user"), "")
    last_assistant_message = next((m.get("content") or "" for m in reversed(messages) if m.get("role") == "assistant"), "")

    # Undo last voucher: "undo", "delete last", "لغو آخری"
    _undo_low = last_user_message.strip().lower()
    if _undo_low in ("undo", "undo last", "delete last", "لغو", "لغو آخری", "حذف آخری", "برگرد"):
        last_txn = db.execute(
            select(Transaction).order_by(Transaction.created_at.desc()).limit(1)
        ).scalar_one_or_none()
        if last_txn:
            txn_brief = f"{last_txn.date.isoformat()} - {last_txn.description or last_txn.reference or str(last_txn.id)[:8]}"
            db.delete(last_txn)
            db.commit()
            return ChatResponse(
                message=f"Deleted the last voucher: {txn_brief}",
                transaction=None,
            )
        return ChatResponse(
            message="No vouchers found to undo.",
            transaction=None,
        )

    # CFO-mode questions: detect and route to CFO intelligence
    _cfo_keywords = (
        "financially healthy", "financial health", "survive", "runway", "burn rate",
        "profit drop", "profit fell", "cost driver", "cash leak", "cash runway",
        "can we survive", "can we last", "how long can we", "are we healthy",
        "سلامت مالی", "وضعیت مالی", "نرخ سوخت", "بقا", "چقدر دوام",
        "هزینه اصلی", "نشت نقدینگی", "سود کاهش",
    )
    if any(k in last_user_message.lower() for k in _cfo_keywords):
        try:
            from app.services.cfo_intelligence import answer_cfo_question
            answer = answer_cfo_question(db, last_user_message)
            return ChatResponse(message=answer, transaction=None)
        except Exception as exc:
            chat_logger.warning("cfo_question_failed: %s", exc, exc_info=True)

    # Post-voucher date/field corrections: user says "the date was 4th of Esfand" after voucher shown
    _voucher_just_suggested = any(
        k in (last_assistant_message or "").lower()
        for k in ("voucher", "here's the", "here is the", "transaction ready", "voucher ready")
    )
    if _voucher_just_suggested:
        from app.utils.jalali import try_parse_jalali, find_and_replace_jalali_dates, format_jalali
        low_msg = last_user_message.lower().strip()
        date_correction = None
        # "the date was X", "date is X", "date: X", "on X"
        m_date = re.search(
            r"(?:the\s+)?date\s+(?:was|is|should be|=|:)\s*(.+?)$",
            low_msg, re.IGNORECASE,
        )
        if m_date:
            raw = m_date.group(1).strip()
            date_correction = try_parse_jalali(raw)
            if date_correction is None:
                try:
                    date_correction = date.fromisoformat(raw)
                except ValueError:
                    pass
        if date_correction is None:
            # Try whole message as a date
            date_correction = try_parse_jalali(last_user_message)
            if date_correction is not None:
                # Only accept if message is short (just a date, not a new transaction)
                if len(last_user_message.split()) > 6:
                    date_correction = None
        if date_correction is not None:
            jalali_str = format_jalali(date_correction)
            return ChatResponse(
                message=f"Date updated to **{date_correction.isoformat()} ({jalali_str})**. Please update the date field in the form above.",
                transaction=None,
                form_updates={"date": date_correction.isoformat()},
            )

    # Entity transaction search: "transactions with Nikzade", "have I had dealings with Ali?"
    entity_query_name = _parse_entity_transaction_query(last_user_message)
    if entity_query_name:
        txns = _search_transactions_by_entity(db, entity_query_name)
        msg, report = _format_entity_transaction_results(entity_query_name, txns)
        return ChatResponse(message=msg, report=report, transaction=None)

    non_payment_query = _looks_like_non_payment_query(last_user_message)
    report_intent = parse_report_intent(last_user_message)
    # Only infer report follow-up context when current user text looks like a report query
    # OR when the message is short (likely just a bank/entity name answering a previous balance question).
    if report_intent is None and (non_payment_query or len(last_user_message.split()) <= 4):
        report_intent = _infer_followup_report_intent(messages, db)
    if report_intent is not None:
        try:
            msg, report = _build_report_from_intent(db, report_intent)
            chat_logger.info(
                "chat_report_intent user=%r intent=%s from=%s to=%s",
                (last_user_message[:120] if last_user_message else ""),
                report_intent.key,
                report_intent.from_date,
                report_intent.to_date,
            )
            return ChatResponse(message=msg, report=report, transaction=None)
        except HTTPException:
            raise
        except Exception:
            chat_logger.exception("chat_report_intent_failed intent=%s", getattr(report_intent, "key", None))
            return ChatResponse(
                message="I couldn't generate that report right now. Please try with a date range, e.g. 'balance sheet this month'.",
                transaction=None,
            )
    attachments = _load_attachments(db, payload.attachment_ids)
    attachment_context: list[dict[str, str]] = []
    for a in attachments:
        item = {"file_name": a.file_name, "content_type": a.content_type}
        if a.content_type.startswith("image/"):
            try:
                encoded = base64.b64encode(Path(a.file_path).read_bytes()).decode("ascii")
                item["data_url"] = f"data:{a.content_type};base64,{encoded}"
            except OSError:
                pass
        attachment_context.append(item)
    # Fee correction after a generated voucher:
    # user says e.g. "fee is wrong, it should be 0.01%" and expects current voucher to update.
    if _looks_like_fee_correction(last_user_message):
        parsed_cfg = parse_fee_config_text(last_user_message)
        fee_ctx = parse_fee_question_context(last_assistant_message) or _parse_included_fee_context(last_assistant_message)
        if fee_ctx is None:
            hist_ctx = extract_payment_context(messages, _all_bank_names(db))
            if hist_ctx.is_payment and hist_ctx.method_name and hist_ctx.bank_name:
                fee_ctx = (hist_ctx.method_name, hist_ctx.bank_name)
        if parsed_cfg is not None and fee_ctx is not None:
            method_name_q, bank_name_q = fee_ctx
            upsert_fee_rule(
                db,
                method_name=method_name_q,
                bank_name=bank_name_q,
                fee_type=parsed_cfg["fee_type"],
                fee_value=parsed_cfg["fee_value"],
                flat_fee=parsed_cfg["flat_fee"],
                percent_bps=parsed_cfg["percent_bps"],
                max_fee=parsed_cfg["max_fee"],
                effective_from=date.today(),
            )
            db.commit()
            # Rebuild the latest voucher using updated rule and the latest voucher context window.
            voucher_idx = _find_last_voucher_assistant_idx(messages)
            msg_scope = messages if voucher_idx < 0 else messages[: voucher_idx + 1]
            base_ctx_text = _select_transaction_context_text(msg_scope)
            if base_ctx_text:
                try:
                    suggested = await ai_suggest_transaction(base_ctx_text, account_list)
                    inferred_mentions = _infer_entity_mentions_from_text(suggested, base_ctx_text)
                    inferred_mentions = _normalize_entity_mentions_for_context(
                        inferred_mentions or [],
                        context_text=base_ctx_text,
                    )
                    payment_ctx_full = extract_payment_context(msg_scope, _all_bank_names(db))
                    if payment_ctx_full.is_payment and payment_ctx_full.amount > 0:
                        suggested = _align_payment_amount_with_context(suggested, payment_ctx_full.amount)
                    _, _, fee_rule = resolve_fee_rule(
                        db,
                        method_name=method_name_q,
                        bank_name=bank_name_q,
                        as_of=date.today(),
                    )
                    if fee_rule is not None:
                        suggested, fee_calc = apply_fee_to_transaction_lines(
                            suggested,
                            method_name=method_name_q,
                            bank_name=bank_name_q,
                            rule=fee_rule,
                            amount_mode=payment_ctx_full.amount_mode if payment_ctx_full else "net",
                        )
                    else:
                        fee_calc = None
                    return ChatResponse(
                        message=(
                            f"Updated fee rule for {canonical_method_name(method_name_q)} via {bank_name_q}. "
                            + (
                                f"Recalculated fee: {(fee_calc.fee_amount if fee_calc else 0):,} IRR."
                                if fee_calc is not None
                                else "Fee rule saved."
                            )
                        ),
                        transaction=SuggestTransactionResponse(
                            date=suggested["date"],
                            reference=suggested.get("reference"),
                            description=suggested.get("description"),
                            lines=[
                                TransactionLineCreate(
                                    account_code=ln["account_code"],
                                    debit=ln["debit"],
                                    credit=ln["credit"],
                                    line_description=ln.get("line_description"),
                                )
                                for ln in suggested["lines"]
                            ],
                        ),
                        entity_mentions=inferred_mentions or None,
                        resolved_entities=None,
                    )
                except AISuggestError:
                    # If regeneration fails, still persist fee rule change and return clear message.
                    return ChatResponse(
                        message=(
                            f"Updated fee rule for {canonical_method_name(method_name_q)} via {bank_name_q}. "
                            "Please resend the transaction message to regenerate the voucher."
                        ),
                        transaction=None,
                    )
    edit_intent = {"intent": "other", "search": {}, "changes": {}, "entity_updates": []}
    if _looks_like_edit_request(messages):
        edit_intent = await parse_transaction_edit_intent(messages)
    if edit_intent.get("intent") == "edit_transaction":
        search = edit_intent.get("search") or {}
        changes = edit_intent.get("changes") or {}
        entity_updates = edit_intent.get("entity_updates") or []
        has_any_criteria = bool(search) or bool(changes) or bool(entity_updates)
        if not has_any_criteria:
            return ChatResponse(
                message=(
                    "Sure. Tell me at least one identifier so I can find it: "
                    "date, reference, description text, client/bank name, or transaction id."
                ),
                transaction=None,
            )
        matched = _find_transactions_for_ai_edit(db, search)
        if not matched:
            return ChatResponse(
                message=(
                    "I couldn't find a matching transaction to edit. "
                    "Please include at least one identifier like date (YYYY-MM-DD), reference, description text, or transaction id."
                ),
                transaction=None,
            )
        if len(matched) > 1:
            items = "\n".join([f"- {str(t.id)} | {_transaction_brief(t)}" for t in matched[:5]])
            return ChatResponse(
                message=(
                    "I found multiple matches. Please reply with transaction id (or a tighter reference/date).\n"
                    f"{items}"
                ),
                transaction=None,
            )
        target = matched[0]
        if "amount" in changes:
            return ChatResponse(
                message=(
                    "I found the transaction, but amount/line edits by AI are not enabled yet. "
                    "Use the Edit button in the transactions modal for line/amount changes."
                ),
                transaction=None,
            )
        changed_fields: list[str] = []
        if "date" in changes:
            v = changes.get("date")
            if isinstance(v, str) and v.strip():
                new_date: date | None = None
                # Try ISO first
                try:
                    new_date = date.fromisoformat(v.strip())
                except ValueError:
                    pass
                # Try Jalali
                if new_date is None:
                    from app.utils.jalali import try_parse_jalali
                    new_date = try_parse_jalali(v.strip())
                if new_date is None:
                    return ChatResponse(
                        message="Invalid date format. Use YYYY-MM-DD or Jalali (e.g. 1404/11/27).",
                        transaction=None,
                    )
                target.date = new_date
                changed_fields.append("date")
            elif v is None:
                return ChatResponse(message="Date cannot be empty.", transaction=None)
        if "reference" in changes:
            v = changes.get("reference")
            target.reference = (str(v).strip() if isinstance(v, str) and str(v).strip() else None)
            changed_fields.append("reference")
        if "description" in changes:
            v = changes.get("description")
            target.description = (str(v).strip() if isinstance(v, str) and str(v).strip() else None)
            changed_fields.append("description")
        for item in entity_updates:
            role = (item.get("role") or "").strip().lower()
            name = (item.get("name") or "").strip()
            if role in ("client", "bank", "payee", "supplier") and name:
                entity = _get_or_create_entity(db, role, name)
                _upsert_role_link(db, target, role, entity)
                changed_fields.append(role)
        if not changed_fields:
            return ChatResponse(
                message=(
                    f"I found 1 transaction ({target.id}) but no specific change was detected. "
                    "Tell me what to change, e.g. 'set client to Innotech' or 'set reference to INV-21'."
                ),
                transaction=None,
            )
        db.commit()
        db.refresh(target)
        _load_transaction_with_lines(db, target)
        changed = ", ".join(sorted(set(changed_fields)))
        from app.utils.jalali import format_jalali
        jalali_str = format_jalali(target.date) if target.date else ""
        date_info = f" New date: {target.date.isoformat()} ({jalali_str})" if "date" in changed_fields else ""
        return ChatResponse(
            message=f"Updated transaction {target.id}. Changed: {changed}.{date_info}",
            transaction=None,
        )

    working_messages = _transaction_window_messages(messages)
    working_last_user_message = next((m.get("content") or "" for m in reversed(working_messages) if m.get("role") == "user"), "")
    if working_last_user_message:
        last_user_message = working_last_user_message
    bank_names = _all_bank_names(db)
    payment_ctx = extract_payment_context(working_messages, bank_names)

    # Conversational fee learning flow: if the assistant previously asked for fee mapping,
    # parse the user's answer and store it for future transactions.
    fee_context = parse_fee_question_context(last_assistant_message)
    learned_fee_prefix = ""
    if fee_context and not non_payment_query:
        # User started a new transaction instead of answering fee; ignore stale fee prompt context.
        if _looks_like_transaction_user_text(last_user_message):
            fee_context = None
        else:
            method_name_q, bank_name_q = fee_context
            parsed_cfg = parse_fee_config_text(last_user_message)
            if parsed_cfg is None:
                return ChatResponse(
                    message=(
                        f"I couldn't parse the fee format for {method_name_q} via {bank_name_q}. "
                        "Please answer like: '5000 toman', '1%', or '1% + 5000 with max 30000'."
                    ),
                    transaction=None,
                )
            if bool(parsed_cfg.get("from_bare_number")):
                flat_candidate = int(parsed_cfg.get("flat_fee") or parsed_cfg.get("fee_value") or 0)
                if (
                    flat_candidate >= 1_000_000
                    and payment_ctx.amount > 0
                    and flat_candidate >= max(1_000_000, payment_ctx.amount // 2)
                ):
                    return ChatResponse(
                        message=(
                            f"I read {flat_candidate:,} IRR as the fee for {canonical_method_name(method_name_q)} via {bank_name_q}, "
                            "which looks unusually high. If this is intentional, reply with explicit format like "
                            f"'fee is {flat_candidate} rial'; otherwise send the correct fee (e.g. '5000 toman' or '1%')."
                        ),
                        transaction=None,
                    )
            rule = upsert_fee_rule(
                db,
                method_name=method_name_q,
                bank_name=bank_name_q,
                fee_type=parsed_cfg["fee_type"],
                fee_value=parsed_cfg["fee_value"],
                flat_fee=parsed_cfg["flat_fee"],
                percent_bps=parsed_cfg["percent_bps"],
                max_fee=parsed_cfg["max_fee"],
                effective_from=date.today(),
            )
            db.commit()
            learned_fee_prefix = (
                f"Saved fee rule for {canonical_method_name(method_name_q)} via {bank_name_q}. "
            )

    # Conversational "fill in the blanks" for dynamic fee logic:
    # if payment method is missing for a payment, ask it before generating voucher.
    user_unknown_method = _user_says_unknown_method(last_user_message)
    if payment_ctx.is_payment and payment_ctx.amount <= 0 and not non_payment_query:
        return ChatResponse(
            message="What was the transaction amount (in IRR)?",
            transaction=None,
        )
    if (
        payment_ctx.is_payment
        and payment_ctx.amount > 0
        and not payment_ctx.method_name
        and not non_payment_query
        and not user_unknown_method
    ):
        return ChatResponse(
            message="Which payment method did you use for this transaction?",
            transaction=None,
        )
    if payment_ctx.is_payment and payment_ctx.amount > 0 and payment_ctx.method_name and not payment_ctx.bank_name and not non_payment_query:
        return ChatResponse(
            message="Which bank account did you use for this transaction?",
            transaction=None,
        )
    if (
        payment_ctx.is_payment
        and payment_ctx.amount > 0
        and payment_ctx.method_name
        and payment_ctx.bank_name
        and not non_payment_query
    ):
        _, _, mapped_rule = resolve_fee_rule(
            db,
            method_name=payment_ctx.method_name,
            bank_name=payment_ctx.bank_name,
            as_of=date.today(),
        )
        if mapped_rule is not None:
            preview = calculate_total_with_fee(payment_ctx.amount, mapped_rule, amount_mode=payment_ctx.amount_mode)
            if preview.fee_amount > 0 and payment_ctx.amount > 0 and preview.fee_amount >= payment_ctx.amount:
                return ChatResponse(
                    message=_fee_question_message(
                        payment_ctx.method_name,
                        payment_ctx.bank_name,
                        prefix=(
                            f"Current saved rule would charge {preview.fee_amount:,} IRR fee on {payment_ctx.amount:,} IRR, "
                            "which looks unusually high."
                        ),
                    ),
                    transaction=None,
                )
        if mapped_rule is None and not fee_context:
            return ChatResponse(
                message=_fee_question_message(payment_ctx.method_name, payment_ctx.bank_name),
                transaction=None,
            )
    chat_logger.info(
        "chat_flow user=%r non_payment_query=%s payment_ctx={is_payment:%s,amount:%s,method:%r,bank:%r}",
        (last_user_message[:120] if last_user_message else ""),
        non_payment_query,
        payment_ctx.is_payment,
        payment_ctx.amount,
        payment_ctx.method_name,
        payment_ctx.bank_name,
    )
    combined_user_text = " . ".join(
        [(m.get("content") or "").strip() for m in working_messages if m.get("role") == "user" and (m.get("content") or "").strip()]
    )
    transaction_context_text = _select_transaction_context_text(working_messages) or combined_user_text

    result: dict | None = None
    # After learning a new fee rule, prefer a direct single-shot suggestion from full user history
    # to avoid another clarification loop.
    if (
        learned_fee_prefix
        and payment_ctx.is_payment
        and payment_ctx.amount > 0
        and payment_ctx.method_name
        and payment_ctx.bank_name
    ):
        try:
            suggested = await ai_suggest_transaction(transaction_context_text, account_list)
            inferred_mentions = _infer_entity_mentions_from_text(suggested, transaction_context_text)
            result = {
                "message": "Here's the voucher based on what you said.",
                "transaction": suggested,
                "entity_mentions": inferred_mentions or [],
            }
        except AISuggestError:
            result = None
    if result is None:
        try:
            result = await ai_chat_turn(working_messages, account_list, attachment_context=attachment_context)
        except AISuggestError as e:
            chat_logger.warning("chat_ai_error: %s", e)
            friendly = _friendly_ai_error(str(e))
            return ChatResponse(message=friendly, transaction=None)
    transaction = result.get("transaction")
    if transaction:
        code_to_id = {a.code: a.id for a in accounts}
        existing_codes = set(code_to_id.keys())
        for na in transaction.get("new_accounts") or []:
            code = (na.get("code") or "").strip()
            name = (na.get("name") or "").strip()
            if not code or not name or code in code_to_id:
                continue
            parent_code = _parent_code_for(code, existing_codes)
            parent_id = code_to_id.get(parent_code) if parent_code else None
            new_acc = Account(code=code, name=name, level=_level_for_code(code), parent_id=parent_id)
            db.add(new_acc)
            db.flush()
            code_to_id[code] = new_acc.id
            existing_codes.add(code)
        db.commit()
        # Resolve mentions to entities (get-or-create) so they appear in Entities and we can return ids for dropdowns
        entity_mentions = list(result.get("entity_mentions") or [])
        inferred_mentions = _infer_entity_mentions_from_text(transaction, transaction_context_text)
        for m in inferred_mentions or []:
            if not isinstance(m, dict):
                continue
            role = (m.get("role") or "").strip().lower()
            name = (m.get("name") or "").strip()
            if role in ("client", "bank", "payee", "supplier") and name:
                if not any(
                    isinstance(existing, dict)
                    and (existing.get("role") or "").strip().lower() == role
                    and (existing.get("name") or "").strip().lower() == name.lower()
                    for existing in entity_mentions
                ):
                    entity_mentions.append({"role": role, "name": name})
        entity_mentions = _normalize_entity_mentions_for_context(
            entity_mentions,
            context_text=transaction_context_text,
        )
        # Ensure detected payment bank is returned as an entity mention so UI can auto-link it on save.
        if payment_ctx.bank_name:
            has_bank_mention = any((m.get("role") or "").strip().lower() == "bank" for m in entity_mentions if isinstance(m, dict))
            if not has_bank_mention:
                bank_existing = find_bank_entity_by_name(db, payment_ctx.bank_name)
                entity_mentions.append(
                    {
                        "role": "bank",
                        "name": (bank_existing.name if bank_existing else payment_ctx.bank_name.strip()),
                    }
                )
        resolved_entities: list[ResolvedEntityLink] = []
        for m in entity_mentions:
            role = (m.get("role") or "").strip().lower()
            name = (m.get("name") or "").strip()
            if role and name and role in ("client", "bank", "payee", "supplier"):
                try:
                    entity = _get_or_create_entity(db, role, name)
                except HTTPException:
                    continue
                resolved_entities.append(ResolvedEntityLink(role=role, entity_id=entity.id))
        if entity_mentions:
            db.commit()
        transaction = _normalize_employee_payment_account(
            db,
            transaction,
            resolved_entities=resolved_entities,
            entity_mentions=entity_mentions,
            user_text=transaction_context_text,
        )
        if payment_ctx.is_payment and payment_ctx.amount > 0:
            transaction = _align_payment_amount_with_context(transaction, payment_ctx.amount)
        line_creates = [
            TransactionLineCreate(
                account_code=ln["account_code"],
                debit=ln["debit"],
                credit=ln["credit"],
                line_description=ln.get("line_description"),
            )
            for ln in transaction["lines"]
        ]
        # Apply dynamic transaction fee as separate traceable line items when method+bank mapping exists.
        bank_mention = next((m.get("name") for m in (entity_mentions or []) if (m.get("role") or "").lower() == "bank"), None)
        effective_bank_name = payment_ctx.bank_name or (bank_mention.strip() if isinstance(bank_mention, str) else None)
        effective_method_name = payment_ctx.method_name
        if payment_ctx.is_payment and effective_method_name and effective_bank_name:
            method_obj, bank_obj, fee_rule = resolve_fee_rule(
                db,
                method_name=effective_method_name,
                bank_name=effective_bank_name,
                as_of=date.today(),
            )
            if fee_rule is not None and method_obj and bank_obj:
                transaction, fee_calc = apply_fee_to_transaction_lines(
                    transaction,
                    method_name=method_obj.name,
                    bank_name=bank_obj.name,
                    rule=fee_rule,
                    amount_mode=payment_ctx.amount_mode,
                )
                if fee_calc is not None and fee_calc.fee_amount > 0:
                    line_creates = [
                        TransactionLineCreate(
                            account_code=ln["account_code"],
                            debit=ln["debit"],
                            credit=ln["credit"],
                            line_description=ln.get("line_description"),
                        )
                        for ln in transaction["lines"]
                    ]
                    fee_msg = (
                        f"Included transaction fee {fee_calc.fee_amount:,} IRR "
                        f"({canonical_method_name(method_obj.name)} via {bank_obj.name})."
                    )
                    result["message"] = (learned_fee_prefix + result.get("message", "") + " " + fee_msg).strip()
                elif learned_fee_prefix:
                    result["message"] = (learned_fee_prefix + result.get("message", "")).strip()
            elif payment_ctx.method_name and payment_ctx.bank_name:
                return ChatResponse(
                    message=_fee_question_message(payment_ctx.method_name, payment_ctx.bank_name),
                    transaction=None,
                )
        elif learned_fee_prefix:
            result["message"] = (learned_fee_prefix + result.get("message", "")).strip()

        txn_response = SuggestTransactionResponse(
            date=transaction["date"],
            reference=transaction.get("reference"),
            description=transaction.get("description"),
            lines=line_creates,
        )
        chat_logger.info(
            "chat_entities mentions=%s resolved=%s",
            entity_mentions,
            [{"role": r.role, "entity_id": str(r.entity_id)} for r in (resolved_entities or [])],
        )
        raw_confidence = result.get("confidence")
        confidence_val = None
        if isinstance(raw_confidence, (int, float)):
            confidence_val = max(0.0, min(1.0, float(raw_confidence)))
        reasoning_val = result.get("reasoning") if isinstance(result.get("reasoning"), str) else None

        return ChatResponse(
            message=result["message"],
            transaction=txn_response,
            confidence=confidence_val,
            reasoning=reasoning_val,
            entity_mentions=entity_mentions,
            resolved_entities=resolved_entities if resolved_entities else None,
        )
    # Deterministic recovery: if chat model fails but payment context is complete,
    # fall back to single-shot suggestion from full user history.
    if (
        payment_ctx.is_payment
        and payment_ctx.amount > 0
        and payment_ctx.method_name
        and payment_ctx.bank_name
    ):
        try:
            suggested = await ai_suggest_transaction(transaction_context_text, account_list)
            if payment_ctx.is_payment and payment_ctx.amount > 0:
                suggested = _align_payment_amount_with_context(suggested, payment_ctx.amount)
            method_obj, bank_obj, fee_rule = resolve_fee_rule(
                db,
                method_name=payment_ctx.method_name,
                bank_name=payment_ctx.bank_name,
                as_of=date.today(),
            )
            if fee_rule is not None and method_obj and bank_obj:
                suggested, fee_calc = apply_fee_to_transaction_lines(
                    suggested,
                    method_name=method_obj.name,
                    bank_name=bank_obj.name,
                    rule=fee_rule,
                    amount_mode=payment_ctx.amount_mode,
                )
                extra_msg = (
                    f" Included transaction fee {fee_calc.fee_amount:,} IRR "
                    f"({canonical_method_name(method_obj.name)} via {bank_obj.name})."
                    if fee_calc and fee_calc.fee_amount > 0
                    else ""
                )
            else:
                extra_msg = ""
            txn_response = SuggestTransactionResponse(
                date=suggested["date"],
                reference=suggested.get("reference"),
                description=suggested.get("description"),
                lines=[
                    TransactionLineCreate(
                        account_code=ln["account_code"],
                        debit=ln["debit"],
                        credit=ln["credit"],
                        line_description=ln.get("line_description"),
                    )
                    for ln in suggested["lines"]
                ],
            )
            fallback_entity_mentions: list[dict[str, str]] = list(
                _infer_entity_mentions_from_text(suggested, transaction_context_text) or []
            )
            fallback_entity_mentions = _normalize_entity_mentions_for_context(
                fallback_entity_mentions,
                context_text=transaction_context_text,
            )
            fallback_resolved: list[ResolvedEntityLink] = []
            if payment_ctx.bank_name:
                bank_existing = find_bank_entity_by_name(db, payment_ctx.bank_name)
                bank_name_value = (bank_existing.name if bank_existing else payment_ctx.bank_name.strip())
                has_bank = any(
                    isinstance(m, dict)
                    and (m.get("role") or "").strip().lower() == "bank"
                    and (m.get("name") or "").strip()
                    for m in fallback_entity_mentions
                )
                if not has_bank:
                    fallback_entity_mentions.append({"role": "bank", "name": bank_name_value})
                bank_entity = _get_or_create_entity(db, "bank", bank_name_value)
                fallback_resolved.append(ResolvedEntityLink(role="bank", entity_id=bank_entity.id))
            for m in fallback_entity_mentions:
                role = (m.get("role") or "").strip().lower() if isinstance(m, dict) else ""
                name = (m.get("name") or "").strip() if isinstance(m, dict) else ""
                if role in ("client", "bank", "payee", "supplier") and name:
                    try:
                        entity = _get_or_create_entity(db, role, name)
                    except HTTPException:
                        continue
                    if not any((r.role == role and r.entity_id == entity.id) for r in fallback_resolved):
                        fallback_resolved.append(ResolvedEntityLink(role=role, entity_id=entity.id))
            if fallback_entity_mentions:
                db.commit()
            chat_logger.info(
                "chat_entities_fallback mentions=%s resolved=%s",
                fallback_entity_mentions,
                [{"role": r.role, "entity_id": str(r.entity_id)} for r in (fallback_resolved or [])],
            )
            return ChatResponse(
                message=(learned_fee_prefix + "Here's the voucher based on what you said." + extra_msg).strip(),
                transaction=txn_response,
                entity_mentions=fallback_entity_mentions or None,
                resolved_entities=fallback_resolved or None,
            )
        except AISuggestError:
            pass
    return ChatResponse(message=result["message"], transaction=None)


@router.post("/suggest", response_model=SuggestTransactionResponse)
async def suggest_transaction(
    payload: SuggestTransactionRequest,
    db: Session = Depends(get_db),
) -> SuggestTransactionResponse:
    """
    Let the user describe a transaction in plain language (e.g. "I paid 500,000 for rent").
    LM Studio suggests date, description, and balanced debit/credit lines. If the chart has no
    fitting account, the AI may suggest new_accounts; they are created and the transaction uses them.
    """
    accounts = db.execute(select(Account).order_by(Account.code)).scalars().all()
    account_list = [{"code": a.code, "name": a.name} for a in accounts]
    if not account_list:
        raise HTTPException(
            status_code=400,
            detail="No accounts in the chart of accounts. Run the app so the seed runs, or add accounts first.",
        )
    try:
        suggested = await ai_suggest_transaction(payload.user_message, account_list)
    except AISuggestError as e:
        raise HTTPException(status_code=503, detail=str(e)) from e
    # Create any new_accounts the AI suggested (chart wasn't related to what user asked)
    code_to_id = {a.code: a.id for a in accounts}
    existing_codes = set(code_to_id.keys())
    for na in suggested.get("new_accounts") or []:
        code = (na.get("code") or "").strip()
        name = (na.get("name") or "").strip()
        if not code or not name or code in code_to_id:
            continue
        parent_code = _parent_code_for(code, existing_codes)
        parent_id = code_to_id.get(parent_code) if parent_code else None
        level = _level_for_code(code)
        new_acc = Account(code=code, name=name, level=level, parent_id=parent_id)
        db.add(new_acc)
        db.flush()
        code_to_id[code] = new_acc.id
        existing_codes.add(code)
    db.commit()
    line_creates = [
        TransactionLineCreate(
            account_code=ln["account_code"],
            debit=ln["debit"],
            credit=ln["credit"],
            line_description=ln.get("line_description"),
        )
        for ln in suggested["lines"]
    ]
    return SuggestTransactionResponse(
        date=suggested["date"],
        reference=suggested.get("reference"),
        description=suggested.get("description"),
        lines=line_creates,
    )


@router.get("/fees/methods", response_model=list[PaymentMethodRead])
def list_payment_methods(
    db: Session = Depends(get_db),
    active_only: bool = Query(True),
) -> list[PaymentMethodRead]:
    q = select(PaymentMethod).order_by(PaymentMethod.name)
    if active_only:
        q = q.where(PaymentMethod.is_active.is_(True))
    rows = db.execute(q).scalars().all()
    return [PaymentMethodRead.model_validate(r) for r in rows]


@router.get("/fees", response_model=list[TransactionFeeRead])
def list_transaction_fees(
    db: Session = Depends(get_db),
    method_name: str | None = Query(None),
    bank_id: UUID | None = Query(None),
    bank_name: str | None = Query(None),
    active_only: bool = Query(True),
) -> list[TransactionFeeRead]:
    q = (
        select(TransactionFee)
        .options(selectinload(TransactionFee.method), selectinload(TransactionFee.bank))
        .order_by(TransactionFee.effective_from.desc(), TransactionFee.created_at.desc())
    )
    if active_only:
        q = q.where(TransactionFee.is_active.is_(True))
    if bank_id:
        q = q.where(TransactionFee.bank_id == bank_id)
    if bank_name and bank_name.strip():
        bank = find_bank_entity_by_name(db, bank_name.strip())
        if not bank:
            return []
        q = q.where(TransactionFee.bank_id == bank.id)
    if method_name and method_name.strip():
        method = find_payment_method(db, method_name.strip())
        if not method:
            return []
        q = q.where(TransactionFee.method_id == method.id)
    rows = db.execute(q).scalars().all()
    return [_transaction_fee_to_read(r) for r in rows]


@router.put("/fees", response_model=TransactionFeeUpsertResponse)
def upsert_transaction_fee(
    payload: TransactionFeeUpsertRequest,
    db: Session = Depends(get_db),
) -> TransactionFeeUpsertResponse:
    bank: Entity | None = None
    if payload.bank_id:
        bank = db.get(Entity, payload.bank_id)
        if not bank or bank.type != "bank":
            raise HTTPException(status_code=400, detail="bank_id must reference an existing bank entity.")
    elif payload.bank_name and payload.bank_name.strip():
        bank = get_or_create_bank_entity(db, payload.bank_name.strip())
    if not bank:
        raise HTTPException(status_code=400, detail="bank_name or bank_id is required.")

    fee_type = payload.fee_type
    fee_value = max(0, int(payload.fee_value or 0))
    flat_fee = max(0, int(payload.flat_fee or 0))
    percent_bps = max(0, int(payload.percent_bps or 0))
    max_fee = payload.max_fee if payload.max_fee is None else max(0, int(payload.max_fee))
    if fee_type == "free":
        fee_value = 0
        flat_fee = 0
        percent_bps = 0
        max_fee = None
    elif fee_type == "flat":
        if flat_fee <= 0:
            flat_fee = fee_value
        fee_value = flat_fee
        percent_bps = 0
    elif fee_type == "percent":
        if percent_bps <= 0:
            percent_bps = fee_value
        fee_value = percent_bps
        flat_fee = 0
    elif fee_type == "hybrid":
        # compatibility field is not meaningful for hybrid.
        fee_value = 0

    rule = upsert_fee_rule(
        db,
        method_name=payload.method_name,
        bank_name=bank.name,
        fee_type=fee_type,
        fee_value=fee_value,
        flat_fee=flat_fee,
        percent_bps=percent_bps,
        max_fee=max_fee,
        effective_from=payload.effective_from,
    )

    recalculated = 0
    if payload.update_scope == "recalculate_current_month_pending":
        recalculated = recalculate_current_month_pending_entries(
            db,
            method_id=rule.method_id,
            bank_id=rule.bank_id,
            as_of=payload.effective_from or date.today(),
        )
    db.commit()

    fresh = db.execute(
        select(TransactionFee)
        .options(selectinload(TransactionFee.method), selectinload(TransactionFee.bank))
        .where(TransactionFee.id == rule.id)
    ).scalars().one()
    return TransactionFeeUpsertResponse(
        rule=_transaction_fee_to_read(fresh),
        recalculated_pending_entries=recalculated,
    )


@router.post("/fees/calculate", response_model=TransactionFeeCalculateResponse)
def calculate_transaction_fee(
    payload: TransactionFeeCalculateRequest,
    db: Session = Depends(get_db),
) -> TransactionFeeCalculateResponse:
    method = find_payment_method(db, payload.method_name)
    if not method:
        raise HTTPException(status_code=404, detail=f"Payment method not found: {payload.method_name}")

    bank: Entity | None = None
    if payload.bank_id:
        bank = db.get(Entity, payload.bank_id)
        if not bank or bank.type != "bank":
            raise HTTPException(status_code=400, detail="bank_id must reference an existing bank entity.")
    elif payload.bank_name:
        bank = find_bank_entity_by_name(db, payload.bank_name)
    if not bank:
        raise HTTPException(status_code=404, detail="Bank not found for fee calculation.")

    rule = get_active_fee_rule(db, method.id, bank.id, as_of=payload.as_of_date)
    if not rule:
        raise HTTPException(
            status_code=404,
            detail=f"No fee rule mapped for {canonical_method_name(method.name)} via {bank.name}.",
        )
    calc = calculate_total_with_fee(payload.amount, rule, amount_mode=payload.amount_mode)
    line_items = build_fee_line_items(calc.fee_amount, method.name, bank.name)
    if payload.track_pending:
        tx_id = payload.transaction_id
        if tx_id is not None:
            tx = db.get(Transaction, tx_id)
            if not tx:
                raise HTTPException(status_code=404, detail=f"Transaction not found: {tx_id}")
        existing = None
        if tx_id is not None:
            existing = db.execute(
                select(TransactionFeeApplication).where(TransactionFeeApplication.transaction_id == tx_id)
            ).scalars().first()
        app_row = existing or TransactionFeeApplication(transaction_id=tx_id)
        app_row.method_id = method.id
        app_row.bank_id = bank.id
        app_row.fee_rule_id = rule.id
        app_row.status = FeeApplicationStatus.PENDING
        app_row.direction = "payment"
        app_row.amount_mode = payload.amount_mode
        app_row.base_amount = calc.base_amount
        app_row.fee_amount = calc.fee_amount
        app_row.gross_amount = calc.gross_amount
        app_row.net_amount = calc.net_amount
        app_row.note = "Pending fee application snapshot"
        if existing is None:
            db.add(app_row)
        db.commit()
    return TransactionFeeCalculateResponse(
        amount_mode=calc.amount_mode,
        input_amount=calc.input_amount,
        base_amount=calc.base_amount,
        fee_amount=calc.fee_amount,
        gross_amount=calc.gross_amount,
        net_amount=calc.net_amount,
        applied_cap=calc.applied_cap,
        fee_type=rule.fee_type.value,
        method_name=method.name,
        bank_name=bank.name,
        line_items=line_items,
    )


@router.get("", response_model=list[TransactionRead])
def list_transactions(
    db: Session = Depends(get_db),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
) -> list[TransactionRead]:
    q = (
        select(Transaction)
        .where(Transaction.deleted_at.is_(None))
        .options(
            selectinload(Transaction.lines).selectinload(TransactionLine.account),
            selectinload(Transaction.attachments),
        )
        .order_by(Transaction.date.desc(), Transaction.created_at.desc())
        .offset(skip)
        .limit(limit)
    )
    result = db.execute(q)
    transactions = result.unique().scalars().all()
    return [_transaction_to_read(t) for t in transactions]


@router.get("/{transaction_id}", response_model=TransactionRead)
def get_transaction(
    transaction_id: UUID,
    db: Session = Depends(get_db),
) -> TransactionRead:
    t = db.get(Transaction, transaction_id)
    if not t or t.deleted_at is not None:
        raise HTTPException(status_code=404, detail="Transaction not found")
    _load_transaction_with_lines(db, t)
    return _transaction_to_read(t)


@router.post("", response_model=TransactionRead, status_code=201)
def create_transaction(
    payload: TransactionCreate,
    db: Session = Depends(get_db),
) -> TransactionRead:
    transaction = _create_transaction_from_payload(db, payload)
    db.commit()
    db.refresh(transaction)
    _load_transaction_with_lines(db, transaction)
    from app.api.reports import invalidate_dashboard_cache
    invalidate_dashboard_cache()
    _log_transaction_audit(db, "create", transaction)
    return _transaction_to_read(transaction)


@router.patch("/{transaction_id}", response_model=TransactionRead)
def update_transaction(
    transaction_id: UUID,
    payload: TransactionUpdate,
    db: Session = Depends(get_db),
) -> TransactionRead:
    t = db.get(Transaction, transaction_id)
    if not t:
        raise HTTPException(status_code=404, detail="Transaction not found")
    if payload.date is not None:
        t.date = payload.date
    if payload.reference is not None:
        t.reference = payload.reference
    if payload.description is not None:
        t.description = payload.description
    if payload.currency is not None:
        t.currency = payload.currency.strip() or "IRR"
    if payload.lines is not None:
        total_debit = sum(l.debit for l in payload.lines)
        total_credit = sum(l.credit for l in payload.lines)
        if total_debit != total_credit:
            raise HTTPException(
                status_code=400,
                detail=f"Debits ({total_debit}) must equal credits ({total_credit})",
            )
        # Replace lines
        for line in t.lines:
            db.delete(line)
        db.flush()
        for line in payload.lines:
            acc = _get_account_by_code(db, line.account_code)
            db.add(
                TransactionLine(
                    transaction_id=t.id,
                    account_id=acc.id,
                    debit=line.debit,
                    credit=line.credit,
                    line_description=line.line_description,
                )
            )
    if payload.entity_links is not None:
        for link in list(t.entity_links or []):
            db.delete(link)
        db.flush()
        for link in payload.entity_links:
            role = link.role.strip().lower()
            if link.entity_id:
                entity = db.get(Entity, link.entity_id)
                if not entity:
                    raise HTTPException(status_code=400, detail=f"Entity not found: {link.entity_id}")
            else:
                entity = _get_or_create_entity(db, role, link.name or "")
            db.add(
                TransactionEntity(
                    transaction_id=t.id,
                    entity_id=entity.id,
                    role=role,
                )
            )
    if payload.attachment_ids is not None:
        keep_ids = set(payload.attachment_ids)
        for a in list(t.attachments or []):
            if a.id not in keep_ids:
                a.transaction_id = None
        if keep_ids:
            selected = _load_attachments(db, list(keep_ids))
            for a in selected:
                if a.transaction_id and a.transaction_id != t.id:
                    raise HTTPException(status_code=400, detail=f"Attachment already linked: {a.id}")
                a.transaction_id = t.id
    db.commit()
    db.refresh(t)
    _load_transaction_with_lines(db, t)
    from app.api.reports import invalidate_dashboard_cache
    invalidate_dashboard_cache()
    return _transaction_to_read(t)


@router.delete("/{transaction_id}", status_code=204)
def delete_transaction(
    transaction_id: UUID,
    db: Session = Depends(get_db),
) -> None:
    from datetime import datetime, timezone
    t = db.get(Transaction, transaction_id)
    if not t:
        raise HTTPException(status_code=404, detail="Transaction not found")
    _log_transaction_audit(db, "delete", t)
    # Soft delete: mark as deleted instead of removing from DB
    t.deleted_at = datetime.now(timezone.utc)
    db.commit()
    from app.api.reports import invalidate_dashboard_cache
    invalidate_dashboard_cache()


@router.post("/import", response_model=ImportTransactionsResponse)
def import_transactions(
    payload: ImportTransactionsRequest,
    db: Session = Depends(get_db),
) -> ImportTransactionsResponse:
    """Import multiple transactions in one request. Each transaction must have balanced lines (sum debits = sum credits)."""
    ids: list[UUID] = []
    for imp in payload.transactions:
        total_debit = sum(l.debit for l in imp.lines)
        total_credit = sum(l.credit for l in imp.lines)
        if total_debit != total_credit:
            raise HTTPException(
                status_code=400,
                detail=f"Transaction dated {imp.date}: debits ({total_debit}) must equal credits ({total_credit})",
            )
        t = Transaction(
            date=imp.date,
            reference=imp.reference,
            description=imp.description,
        )
        db.add(t)
        db.flush()
        for line in imp.lines:
            acc = _get_account_by_code(db, line.account_code)
            db.add(
                TransactionLine(
                    transaction_id=t.id,
                    account_id=acc.id,
                    debit=line.debit,
                    credit=line.credit,
                    line_description=line.line_description,
                )
            )
        ids.append(t.id)
    db.commit()
    return ImportTransactionsResponse(imported=len(ids), ids=ids)


# ---------------------------------------------------------------------------
# Excel journal import
# ---------------------------------------------------------------------------

import hashlib
import json
import tempfile
from pathlib import Path as _Path

from app.schemas.transaction import (
    ExcelAccountMapping,
    ExcelImportConfirmRequest,
    ExcelImportConfirmResponse,
    ExcelImportPreviewAccount,
    ExcelImportPreviewLine,
    ExcelImportPreviewResponse,
    ExcelImportPreviewVoucher,
)

# Temp storage for uploaded Excel files (token → file path)
_EXCEL_UPLOAD_STORE: dict[str, str] = {}


@router.post("/excel-import/preview", response_model=ExcelImportPreviewResponse)
async def excel_import_preview(
    file: UploadFile = File(...),
    jalali_year: int | None = Query(None, description="Jalali year for date conversion (e.g. 1403)"),
    db: Session = Depends(get_db),
):
    """Upload an Excel file and preview the parsed journal entries."""
    from app.services.excel_journal_parser import parse_excel_journal

    if not file.filename or not file.filename.lower().endswith(('.xlsx', '.xls')):
        raise HTTPException(status_code=400, detail="Only .xlsx/.xls files are supported")

    # Save to temp file
    content = await file.read()
    if len(content) > 20 * 1024 * 1024:  # 20MB limit
        raise HTTPException(status_code=400, detail="File too large (max 20MB)")

    token = hashlib.sha256(content).hexdigest()[:16] + "_" + (file.filename or "import")
    tmp_dir = _Path(tempfile.gettempdir()) / "excel_imports"
    tmp_dir.mkdir(exist_ok=True)
    tmp_path = tmp_dir / f"{token}.xlsx"
    tmp_path.write_bytes(content)
    _EXCEL_UPLOAD_STORE[token] = str(tmp_path)

    # Parse
    result = parse_excel_journal(str(tmp_path), jalali_year=jalali_year)

    # Check which accounts exist in our chart
    existing_codes = {
        a.code
        for a in db.execute(select(Account)).scalars().all()
    }

    preview_accounts = []
    for acct in result.unique_accounts:
        preview_accounts.append(ExcelImportPreviewAccount(
            title1=acct.title1,
            title2=acct.title2,
            title3=acct.title3,
            suggested_code=acct.suggested_code,
            suggested_name=acct.suggested_name,
            exists_in_chart=acct.suggested_code in existing_codes if acct.suggested_code else False,
        ))

    preview_vouchers = []
    for v in result.vouchers:
        lines = []
        for l in v.lines:
            from app.services.excel_journal_parser import _suggest_account_code
            suggested = _suggest_account_code(l.title1, l.title2, l.title3)
            lines.append(ExcelImportPreviewLine(
                title1=l.title1, title2=l.title2, title3=l.title3,
                description=l.description,
                debit=l.debit, credit=l.credit,
                suggested_code=suggested,
                project_group=l.project_group,
                project=l.project,
                project_name=l.project_name,
            ))
        preview_vouchers.append(ExcelImportPreviewVoucher(
            voucher_number=str(v.voucher_number),
            date_code=str(v.date_code) if v.date_code else None,
            gregorian_date=v.gregorian_date,
            lines=lines,
            total_debit=v.total_debit,
            total_credit=v.total_credit,
            is_balanced=v.is_balanced,
        ))

    # Serialize raw_preview: convert all cells to strings
    raw_preview = []
    for row in result.raw_preview:
        raw_preview.append([str(c) if c is not None else None for c in row])

    col_map = {}
    m = result.column_mapping
    for f in ['row_num', 'voucher_num', 'day', 'title1', 'title2', 'title3',
              'notes', 'debit', 'credit', 'balance', 'project_group', 'project', 'project_name']:
        col_map[f] = getattr(m, f)

    return ExcelImportPreviewResponse(
        file_token=token,
        headers=result.headers,
        column_mapping=col_map,
        vouchers=preview_vouchers,
        unique_accounts=preview_accounts,
        jalali_year=result.jalali_year or 1403,
        total_rows=result.total_rows,
        total_vouchers=result.total_vouchers,
        errors=result.errors,
        raw_preview=raw_preview,
    )


@router.post("/excel-import/confirm", response_model=ExcelImportConfirmResponse)
def excel_import_confirm(
    payload: ExcelImportConfirmRequest,
    db: Session = Depends(get_db),
):
    """Confirm and import the previewed Excel journal entries."""
    from app.services.excel_journal_parser import parse_excel_journal

    # Retrieve file
    file_path = _EXCEL_UPLOAD_STORE.get(payload.file_token)
    if not file_path or not _Path(file_path).exists():
        raise HTTPException(status_code=400, detail="Upload expired or not found. Please re-upload the file.")

    # Re-parse with possibly updated column mapping
    col_map = None
    if payload.column_mapping:
        col_map = payload.column_mapping

    result = parse_excel_journal(file_path, jalali_year=payload.jalali_year, column_mapping=col_map)

    # Build account mapping lookup: "title1||title2||title3" → account_code
    acct_map: dict[str, str] = {}
    for am in payload.account_mappings:
        key = f"{am.title1.strip()}||{am.title2.strip()}||{am.title3.strip()}"
        acct_map[key] = am.account_code.strip()

    # Verify all mapped codes exist (or create sub-accounts if needed)
    existing_accounts = {a.code: a for a in db.execute(select(Account)).scalars().all()}
    accounts_created = 0

    # Validate all account codes in mappings exist
    needed_codes = set(acct_map.values())
    missing_codes = needed_codes - set(existing_accounts.keys())
    if missing_codes:
        raise HTTPException(
            status_code=400,
            detail=f"Account codes not found in chart: {', '.join(sorted(missing_codes))}. "
                   f"Please create them first or adjust the mapping.",
        )

    multiplier = payload.amount_multiplier
    currency = payload.currency or "IRR"
    transaction_ids: list[UUID] = []
    errors: list[str] = []

    for v in result.vouchers:
        if not v.gregorian_date:
            errors.append(f"Voucher {v.voucher_number}: could not determine date (day code: {v.date_code})")
            continue

        if not v.is_balanced:
            errors.append(f"Voucher {v.voucher_number}: unbalanced (debit={v.total_debit}, credit={v.total_credit})")
            continue

        # Build description from first line
        descriptions = [l.description for l in v.lines if l.description]
        desc = descriptions[0] if descriptions else f"Excel import voucher {v.voucher_number}"

        # Currency tag
        currency = payload.currency or "IRR"
        if currency != "IRR":
            orig_total = v.total_debit  # original amount before multiplier
            desc += f" [{currency} {orig_total:,.2f}]"

        # Project info
        projects = set()
        for l in v.lines:
            if l.project_group or l.project_name:
                parts = [p for p in [l.project_group, l.project, l.project_name] if p]
                if parts:
                    projects.add(" / ".join(parts))
        if projects:
            desc += " [" + "; ".join(projects) + "]"

        t = Transaction(
            date=v.gregorian_date,
            reference=f"V{v.voucher_number}",
            description=desc[:2000],
            currency=currency,
        )
        db.add(t)
        db.flush()

        for line in v.lines:
            acct_key = f"{line.title1}||{line.title2}||{line.title3}"
            code = acct_map.get(acct_key)
            if not code:
                errors.append(
                    f"Voucher {v.voucher_number}, row {line.row_index}: "
                    f"no account mapping for [{line.title1} > {line.title2} > {line.title3}]"
                )
                continue

            debit_amt = int(round(line.debit * multiplier))
            credit_amt = int(round(line.credit * multiplier))

            acc = existing_accounts.get(code)
            if not acc:
                errors.append(f"Account code {code} not found")
                continue

            # Build rich line description with all metadata for searchability
            line_desc_parts = []
            # Original notes/description
            if line.description:
                line_desc_parts.append(line.description)
            # Account hierarchy (Title 1 > 2 > 3)
            titles = [t for t in [line.title1, line.title2, line.title3] if t]
            if titles:
                line_desc_parts.append("(" + " > ".join(titles) + ")")
            # Project info
            proj_parts = [p for p in [line.project_group, line.project, line.project_name] if p]
            if proj_parts:
                line_desc_parts.append("[" + " / ".join(proj_parts) + "]")
            # Original amount if foreign currency
            if currency != "IRR" and (line.debit or line.credit):
                orig_amt = line.debit if line.debit else line.credit
                line_desc_parts.append(f"{{{currency} {orig_amt:,.2f}}}")

            full_line_desc = " ".join(line_desc_parts)

            db.add(TransactionLine(
                transaction_id=t.id,
                account_id=acc.id,
                debit=debit_amt,
                credit=credit_amt,
                line_description=full_line_desc[:512] if full_line_desc else None,
            ))

        transaction_ids.append(t.id)

    if transaction_ids:
        db.commit()
        # Invalidate dashboard cache
        try:
            from app.api.reports import invalidate_dashboard_cache
            invalidate_dashboard_cache()
        except Exception:
            pass

    # Clean up temp file
    try:
        _Path(file_path).unlink(missing_ok=True)
        _EXCEL_UPLOAD_STORE.pop(payload.file_token, None)
    except Exception:
        pass

    return ExcelImportConfirmResponse(
        imported=len(transaction_ids),
        transaction_ids=transaction_ids,
        accounts_created=accounts_created,
        errors=errors,
    )
