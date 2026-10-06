from __future__ import annotations

import collections
import logging
import time as _time
import uuid
from datetime import date
from pathlib import Path
from uuid import UUID

from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse
import sqlalchemy as sa
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.core.http_headers import content_disposition
from app.core.messages import said
from app.db.session import get_db
from app.models.account import Account
from app.models.entity import Entity, TransactionEntity
from app.models.transaction import Transaction, TransactionAttachment, TransactionLine
from app.models.transaction_fee import FeeApplicationStatus, PaymentMethod, TransactionFee, TransactionFeeApplication
from app.schemas.transaction import (
    AttachmentRead,
    AttachmentOCRResponse,
    ImportTransactionsRequest,
    ImportTransactionsResponse,
    TransactionCreate,
    TransactionEntityLinkRead,
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
from app.services.ocr_extract import OCRExtractError, extract_from_attachment
from app.services.transaction_fee import (
    build_fee_line_items,
    canonical_method_name,
    get_active_fee_rule,
    calculate_total_with_fee,
    find_bank_entity_by_name,
    find_payment_method,
    get_or_create_bank_entity,
    recalculate_current_month_pending_entries,
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


# AI endpoints are limited per user and per company, with a 24-hour token
# budget (app/services/ai_usage.py, roadmap §2.5); the old single global
# chat bucket let one user lock everybody out.
from app.services.ai_usage import guard_ai_request  # noqa: E402
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
_STORED_EXTENSIONS = {
    "image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp", "application/pdf": ".pdf",
    "text/csv": ".csv", "text/tab-separated-values": ".tsv", "application/vnd.ms-excel": ".xls",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": ".xlsx",
}
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
    return f"/transactions/attachments/{p.stem}/file"  # legacy shape; callers pass the id below


def _attachment_to_read(a: TransactionAttachment) -> AttachmentRead:
    return AttachmentRead(
        id=a.id,
        file_name=a.file_name,
        content_type=a.content_type,
        size_bytes=a.size_bytes,
        url=f"/transactions/attachments/{a.id}/file",
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
            base_debit=line.base_debit,
            base_credit=line.base_credit,
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
        fx_rate=t.fx_rate,
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


@router.post("/attachments", response_model=AttachmentRead, status_code=201)
def upload_attachment(
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
    raw = file.file.read()
    if not raw:
        raise HTTPException(status_code=400, detail="Attachment is empty.")
    validate_file_magic(raw, content_type)
    if len(raw) > MAX_ATTACHMENT_SIZE_BYTES:
        raise HTTPException(status_code=400, detail="Attachment too large. Max size is 8 MB.")
    # The stored extension comes from the VALIDATED content type, never from
    # the user's file name: "evil.html" declared as text/csv used to be saved
    # as .html and served from our origin as HTML (stored XSS, review H2).
    ext = _STORED_EXTENSIONS.get(content_type, ".bin")
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


# Types a browser may render in-page; everything else downloads. The stored
# extension always comes from the validated type, so nothing here can be HTML.
_INLINE_TYPES = ("image/jpeg", "image/png", "image/webp", "application/pdf")


@router.get("/attachments/{attachment_id}/file")
def download_attachment(attachment_id: UUID, db: Session = Depends(get_db)) -> FileResponse:
    """Serve an attachment to a signed-in user of ITS company only (the row
    lookup is tenant-scoped, so another company's id is a 404). Replaces the
    public /uploads mount."""
    att = db.get(TransactionAttachment, attachment_id)
    if att is None:
        raise HTTPException(status_code=404, detail="Attachment not found")
    path = Path(str(att.file_path))
    if not path.is_absolute():
        path = UPLOADS_DIR / path.name
    path = path.resolve()
    try:
        path.relative_to(UPLOADS_DIR.resolve())
    except ValueError:
        raise HTTPException(status_code=404, detail="Attachment not found")
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Attachment file is missing")
    disposition = "inline" if att.content_type in _INLINE_TYPES else "attachment"
    return FileResponse(path, media_type=att.content_type,
                        headers={"Content-Disposition": content_disposition(path.name, inline=disposition == "inline"),
                                 "X-Content-Type-Options": "nosniff"})


@router.post("/attachments/{attachment_id}/ocr", response_model=AttachmentOCRResponse)
async def ocr_attachment(
    attachment_id: UUID,
    db: Session = Depends(get_db),
) -> AttachmentOCRResponse:
    row = db.get(TransactionAttachment, attachment_id)
    if not row:
        raise HTTPException(status_code=404, detail="Attachment not found")
    guard_ai_request(db)
    try:
        out = await extract_from_attachment(row.file_path, row.content_type)
    except OCRExtractError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return AttachmentOCRResponse(**out)


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
    from app.services.book_text import book_language
    line_items = build_fee_line_items(calc.fee_amount, method.name, bank.name, lang=book_language(db))
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
            # the serializer reads every link and its entity: two lazy loads
            # per row (98 queries for a page of 50) without these
            selectinload(Transaction.entity_links).selectinload(TransactionEntity.entity),
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
    # A closed period locks edits as well as postings (QA 2026-09-24: PATCH and
    # DELETE inside the lock used to succeed).
    from app.services.ledger_posting import assert_transaction_mutable
    assert_transaction_mutable(db, t, new_date=payload.date)
    from app.services.learned_preferences import learn_from_edit, snapshot
    before = snapshot(t)                       # correction memory (roadmap §5.4)
    if payload.date is not None:
        t.date = payload.date
    if payload.reference is not None:
        t.reference = payload.reference
    if payload.description is not None:
        t.description = payload.description
    if payload.currency is not None:
        from app.services.ledger_posting import default_currency
        t.currency = default_currency(db, payload.currency)
    if payload.fx_rate is not None:
        t.fx_rate = payload.fx_rate          # base amounts follow (app/services/fx_base.py)
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
    db.flush()
    db.expire(t, ["lines", "entity_links"])
    learn_from_edit(db, t.description, before, snapshot(t))
    # Edits are as auditable as creates and deletes (QA 2026-09-24 2.10: the
    # audit log showed no 'update' for a PATCH). Reload so the snapshot holds
    # the replaced lines, then record the event + a new version.
    db.expire(t)
    _log_transaction_audit(db, "update", t)
    db.commit()
    # an edited payment on a foreign invoice is settled again (roadmap §4.6)
    from app.services.fx_settlement import settle_waiting
    if settle_waiting(db):
        db.commit()
    db.refresh(t)
    _load_transaction_with_lines(db, t)
    from app.api.reports import invalidate_dashboard_cache
    invalidate_dashboard_cache()
    return _transaction_to_read(t)


def _soft_delete_transaction(db: Session, t: Transaction) -> None:
    """The one way an entry leaves the books: closed-period check, audit
    event + version, soft delete, statement rows released. Used by DELETE
    /transactions/{id} and by the chat's "undo" (which used to hard-delete)."""
    from datetime import datetime, timezone
    from app.services.ledger_posting import assert_transaction_mutable
    from app.services.statement_import import release_statement_rows

    assert_transaction_mutable(db, t)
    _log_transaction_audit(db, "delete", t)
    t.deleted_at = datetime.now(timezone.utc)
    release_statement_rows(db, t.id)
    db.commit()
    from app.api.reports import invalidate_dashboard_cache
    invalidate_dashboard_cache()


@router.delete("/{transaction_id}", status_code=204)
def delete_transaction(
    transaction_id: UUID,
    db: Session = Depends(get_db),
) -> None:
    from datetime import datetime, timezone
    t = db.get(Transaction, transaction_id)
    if not t or t.deleted_at is not None:
        raise HTTPException(status_code=404, detail="Transaction not found")
    _soft_delete_transaction(db, t)


@router.post("/import", response_model=ImportTransactionsResponse)
def import_transactions(
    payload: ImportTransactionsRequest,
    db: Session = Depends(get_db),
) -> ImportTransactionsResponse:
    """Import multiple transactions in one request. Each transaction must have balanced lines (sum debits = sum credits)."""
    from app.services.period_service import assert_period_open

    ids: list[UUID] = []
    created: list[Transaction] = []
    for imp in payload.transactions:
        total_debit = sum(l.debit for l in imp.lines)
        total_credit = sum(l.credit for l in imp.lines)
        if total_debit != total_credit:
            raise HTTPException(
                status_code=400,
                detail=f"Transaction dated {imp.date}: debits ({total_debit}) must equal credits ({total_credit})",
            )
        assert_period_open(db, imp.date)  # the lock applies to imports too (review H7)
        from app.services.ledger_posting import default_currency
        t = Transaction(
            date=imp.date,
            reference=imp.reference,
            description=imp.description,
            currency=default_currency(db, None),
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
        created.append(t)
    db.commit()
    for t in created:
        _log_transaction_audit(db, "create", t)  # imports leave a trail like any posting
    from app.api.reports import invalidate_dashboard_cache
    invalidate_dashboard_cache()
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

# Uploaded Excel files wait for "confirm" in the tenant-scoped upload_tokens
# table (roadmap §2.2): a preview on one worker can be confirmed on another,
# and a token only resolves inside the company that uploaded it.
EXCEL_UPLOAD_KIND = "excel_journal"
EXCEL_IMPORT_DIR = _Path(tempfile.gettempdir()) / "excel_imports"


def excel_upload_token(content: bytes, filename: str | None, suffix: str = ".xlsx") -> tuple[str, "_Path"]:
    """(token, temp path) for an uploaded sheet. The path is named by the
    content hash only; the client's filename is untrusted and used for display
    (the token tail) after sanitising. It used to be spliced into the path, so
    a name with "/" crashed the preview with a 500 (security review 2026-09-25)."""
    import re as _re
    digest = hashlib.sha256(content).hexdigest()[:32]
    base = _re.split(r"[\\/]", filename or "")[-1]
    safe = _re.sub(r"[^\w .()\-]", "_", base).strip(" .")[:120] or "import"
    EXCEL_IMPORT_DIR.mkdir(parents=True, exist_ok=True)
    return f"{digest}_{safe}", EXCEL_IMPORT_DIR / f"{digest}{suffix}"


def _excel_import_history(db: Session, sha: str) -> dict | None:
    """When this exact file was confirmed before, say so (QA 2026-09-24 2.12:
    a second upload showed no warning and would have imported duplicates).
    History lives in the audit log — action 'excel_import', entity_id = the
    file's SHA-256."""
    import json as _json

    from app.models.audit_log import AuditLog

    rows = db.execute(
        select(AuditLog).where(AuditLog.action == "excel_import", AuditLog.entity_id == sha)
        .order_by(AuditLog.timestamp.desc())
    ).scalars().all()
    if not rows:
        return None
    last = rows[0]
    try:
        detail = _json.loads(last.detail or "{}")
    except ValueError:
        detail = {}
    return {
        "at": last.timestamp.isoformat() if last.timestamp else None,
        "times": len(rows),
        "imported": int(detail.get("imported") or 0),
        "filename": detail.get("filename"),
    }


def _record_excel_import(db: Session, file_path: str, file_token: str, imported: int) -> None:
    import json as _json

    from app.services.audit_service import log_audit_event

    sha = hashlib.sha256(_Path(file_path).read_bytes()).hexdigest()
    filename = file_token.split("_", 1)[1] if "_" in file_token else file_token
    log_audit_event(db, action="excel_import", entity_type="excel_file", entity_id=sha,
                    detail=_json.dumps({"filename": filename, "imported": imported}))
    db.commit()


@router.post("/excel-import/preview", response_model=ExcelImportPreviewResponse)
def excel_import_preview(
    file: UploadFile = File(...),
    jalali_year: int | None = Query(None, description="Jalali year for date conversion (e.g. 1403)"),
    request: Request = None,
    db: Session = Depends(get_db),
):
    """Upload an Excel file and preview the parsed journal entries."""
    from app.services.excel_journal_parser import parse_excel_journal

    if not file.filename or not file.filename.lower().endswith(('.xlsx', '.xls')):
        raise HTTPException(status_code=400, detail="Only .xlsx/.xls files are supported")

    # Save to temp file
    content = file.file.read()
    if len(content) > 20 * 1024 * 1024:  # 20MB limit
        raise HTTPException(status_code=400, detail="File too large (max 20MB)")

    sha = hashlib.sha256(content).hexdigest()
    token, tmp_path = excel_upload_token(content, file.filename)
    tmp_path.write_bytes(content)
    from app.core.shared_state import store_upload
    store_upload(db, EXCEL_UPLOAD_KIND, token, str(tmp_path))

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
        file_sha256=sha,
        already_imported=_excel_import_history(db, sha),
        file_token=token,
        headers=result.headers,
        column_mapping=col_map,
        vouchers=preview_vouchers,
        unique_accounts=preview_accounts,
        jalali_year=result.jalali_year or 1403,
        total_rows=result.total_rows,
        total_vouchers=result.total_vouchers,
        errors=said(result.errors, request),       # in the page's language (#53)
        raw_preview=raw_preview,
    )


@router.post("/excel-import/confirm", response_model=ExcelImportConfirmResponse)
def excel_import_confirm(
    payload: ExcelImportConfirmRequest,
    request: Request = None,
    db: Session = Depends(get_db),
):
    """Confirm and import the previewed Excel journal entries."""
    from app.services.excel_journal_parser import parse_excel_journal

    # Retrieve file
    from app.core.shared_state import find_upload
    file_path = find_upload(db, EXCEL_UPLOAD_KIND, payload.file_token)
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

    # Refuse the whole file when a voucher falls inside the closed period —
    # importing the rest would leave a half-applied batch (review H7).
    from app.services.period_service import assert_period_open
    for v in result.vouchers:
        if getattr(v, "gregorian_date", None):
            assert_period_open(db, v.gregorian_date)
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

    # Record the history BEFORE removing the file: it hashes the file, and the
    # old order (delete first) made the history write fail every time, so the
    # "already imported" warning could never fire.
    try:
        _record_excel_import(db, file_path, payload.file_token, len(transaction_ids))
    except Exception as exc:  # the import itself succeeded; history is best-effort
        chat_logger.warning("excel_import_history_failed: %s", exc)

    # Clean up temp file
    try:
        _Path(file_path).unlink(missing_ok=True)
        from app.core.shared_state import drop_upload
        drop_upload(db, EXCEL_UPLOAD_KIND, payload.file_token)
        db.commit()
    except Exception:
        pass
    return ExcelImportConfirmResponse(
        imported=len(transaction_ids),
        transaction_ids=transaction_ids,
        accounts_created=accounts_created,
        errors=said(errors, request),
    )
