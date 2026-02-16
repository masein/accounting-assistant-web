from __future__ import annotations

import base64
import uuid
from datetime import date, timedelta
from pathlib import Path
from uuid import UUID

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.db.session import get_db
from app.models.account import Account, AccountLevel
from app.models.entity import Entity, TransactionEntity
from app.models.transaction import Transaction, TransactionAttachment, TransactionLine
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
from app.services.ai_suggest import (
    AISuggestError,
    chat_turn as ai_chat_turn,
    parse_transaction_edit_intent,
    suggest_transaction as ai_suggest_transaction,
)
from app.services.ocr_extract import OCRExtractError, extract_from_attachment


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

UPLOADS_DIR = Path(__file__).resolve().parents[1] / "uploads" / "transactions"
MAX_ATTACHMENT_SIZE_BYTES = 8 * 1024 * 1024
ALLOWED_ATTACHMENT_TYPES = {
    "image/jpeg",
    "image/png",
    "image/webp",
    "application/pdf",
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


def _load_attachments(db: Session, attachment_ids: list[UUID]) -> list[TransactionAttachment]:
    if not attachment_ids:
        return []
    found = db.execute(
        select(TransactionAttachment).where(TransactionAttachment.id.in_(attachment_ids))
    ).scalars().all()
    by_id = {a.id: a for a in found}
    missing = [str(i) for i in attachment_ids if i not in by_id]
    if missing:
        raise HTTPException(status_code=400, detail=f"Attachment not found: {', '.join(missing)}")
    return [by_id[i] for i in attachment_ids if i in by_id]


def _get_account_by_code(db: Session, code: str) -> Account:
    code = code.strip()
    acc = db.execute(select(Account).where(Account.code == code)).scalars().one_or_none()
    if not acc:
        raise HTTPException(status_code=400, detail=f"Account not found: {code}")
    return acc


def _get_or_create_entity(db: Session, role: str, name: str) -> Entity:
    """Find entity by type and name (case-insensitive), or create it."""
    name = name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="Entity name is empty")
    role = role.strip().lower()
    entity_type = role if role in ("client", "bank", "employee", "supplier") else "employee"
    existing = (
        db.execute(
            select(Entity).where(
                Entity.type == entity_type,
                Entity.name.ilike(name),
            )
        )
        .scalars().first()
    )
    if existing:
        return existing
    entity = Entity(type=entity_type, name=name)
    db.add(entity)
    db.flush()
    return entity


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
        lines=lines,
        entity_links=[
            TransactionEntityLinkRead(
                role=link.role,
                entity_id=link.entity_id,
                entity_name=(link.entity.name if link.entity else None),
                entity_type=(link.entity.type if link.entity else None),
            )
            for link in (t.entity_links or [])
        ],
        attachments=[_attachment_to_read(a) for a in (t.attachments or [])],
        created_at=t.created_at,
        updated_at=t.updated_at,
    )


def _transaction_brief(t: Transaction) -> str:
    desc = (t.description or "No description").strip()
    if len(desc) > 48:
        desc = desc[:48] + "…"
    return f"{t.date.isoformat()} | ref: {(t.reference or '—')} | {desc}"


def _upsert_role_link(db: Session, t: Transaction, role: str, entity: Entity) -> None:
    role_key = (role or "").strip().lower()
    existing = next((ln for ln in (t.entity_links or []) if (ln.role or "").strip().lower() == role_key), None)
    if existing:
        existing.entity_id = entity.id
    else:
        db.add(TransactionEntity(transaction_id=t.id, entity_id=entity.id, role=role_key))


def _find_transactions_for_ai_edit(db: Session, search: dict) -> list[Transaction]:
    txid = (search.get("transaction_id") or "").strip() if isinstance(search.get("transaction_id"), str) else ""
    if txid:
        try:
            txn_uuid = UUID(txid)
        except ValueError:
            return []
        t = db.get(Transaction, txn_uuid)
        if not t:
            return []
        _load_transaction_with_lines(db, t)
        return [t]
    date_val = (search.get("date") or "").strip() if isinstance(search.get("date"), str) else ""
    ref = (search.get("reference") or "").strip() if isinstance(search.get("reference"), str) else ""
    desc = (search.get("description_contains") or "").strip() if isinstance(search.get("description_contains"), str) else ""
    entity_name = (search.get("entity_name") or "").strip() if isinstance(search.get("entity_name"), str) else ""
    q = (
        select(Transaction)
        .options(
            selectinload(Transaction.lines).selectinload(TransactionLine.account),
            selectinload(Transaction.entity_links).selectinload(TransactionEntity.entity),
            selectinload(Transaction.attachments),
        )
        .order_by(Transaction.date.desc(), Transaction.created_at.desc())
    )
    has_filter = False
    if date_val:
        low = date_val.lower()
        try:
            q = q.where(Transaction.date == date.fromisoformat(date_val))
            has_filter = True
        except ValueError:
            today = date.today()
            if low == "today":
                q = q.where(Transaction.date == today)
                has_filter = True
            elif low == "yesterday":
                q = q.where(Transaction.date == (today - timedelta(days=1)))
                has_filter = True
            elif low == "last week":
                q = q.where(Transaction.date >= (today - timedelta(days=7))).where(Transaction.date <= today)
                has_filter = True
            elif low == "this week":
                week_start = today - timedelta(days=today.weekday())
                q = q.where(Transaction.date >= week_start).where(Transaction.date <= today)
                has_filter = True
            elif low == "last month":
                q = q.where(Transaction.date >= (today - timedelta(days=30))).where(Transaction.date <= today)
                has_filter = True
    if ref:
        q = q.where(Transaction.reference.ilike(f"%{ref}%"))
        has_filter = True
    if desc:
        q = q.where(Transaction.description.ilike(f"%{desc}%"))
        has_filter = True
    if entity_name:
        q = q.join(TransactionEntity, Transaction.id == TransactionEntity.transaction_id)
        q = q.join(Entity, Entity.id == TransactionEntity.entity_id)
        q = q.where(Entity.name.ilike(f"%{entity_name}%"))
        has_filter = True
    if not has_filter:
        return []
    return db.execute(q.limit(10)).scalars().unique().all()


def _parent_code_for(code: str, existing_codes: set[str]) -> str | None:
    """Best parent code that exists: 6-digit -> try 4-digit then 2-digit; 4-digit -> 2-digit."""
    if len(code) <= 2:
        return None
    if len(code) >= 4 and code[:4] in existing_codes:
        return code[:4]
    if code[:2] in existing_codes:
        return code[:2]
    return None


def _level_for_code(code: str) -> AccountLevel:
    if len(code) == 2:
        return AccountLevel.GROUP
    if len(code) == 4:
        return AccountLevel.GENERAL
    return AccountLevel.SUB


@router.post("/attachments", response_model=AttachmentRead, status_code=201)
async def upload_attachment(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
) -> AttachmentRead:
    content_type = (file.content_type or "").strip().lower()
    if content_type not in ALLOWED_ATTACHMENT_TYPES:
        raise HTTPException(status_code=400, detail="Unsupported file type. Use JPG, PNG, WEBP, or PDF.")
    raw = await file.read()
    if not raw:
        raise HTTPException(status_code=400, detail="Attachment is empty.")
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
    accounts = db.execute(select(Account).order_by(Account.code)).scalars().all()
    account_list = [{"code": a.code, "name": a.name} for a in accounts]
    if not account_list:
        raise HTTPException(status_code=400, detail="No accounts. Run the app so the seed runs.")
    messages = [{"role": m.role, "content": m.content} for m in payload.messages]
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
                try:
                    target.date = date.fromisoformat(v.strip())
                    changed_fields.append("date")
                except ValueError:
                    return ChatResponse(message="Invalid date format. Use YYYY-MM-DD.", transaction=None)
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
        return ChatResponse(
            message=f"Updated transaction {target.id}. Changed: {changed}.",
            transaction=None,
        )

    try:
        result = await ai_chat_turn(messages, account_list, attachment_context=attachment_context)
    except AISuggestError as e:
        raise HTTPException(status_code=503, detail=str(e)) from e
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
        entity_mentions = result.get("entity_mentions") or []
        resolved_entities: list[ResolvedEntityLink] = []
        for m in entity_mentions:
            role = (m.get("role") or "").strip().lower()
            name = (m.get("name") or "").strip()
            if role and name and role in ("client", "bank", "payee", "supplier"):
                entity = _get_or_create_entity(db, role, name)
                resolved_entities.append(ResolvedEntityLink(role=role, entity_id=entity.id))
        if entity_mentions:
            db.commit()
        line_creates = [
            TransactionLineCreate(
                account_code=ln["account_code"],
                debit=ln["debit"],
                credit=ln["credit"],
                line_description=ln.get("line_description"),
            )
            for ln in transaction["lines"]
        ]
        txn_response = SuggestTransactionResponse(
            date=transaction["date"],
            reference=transaction.get("reference"),
            description=transaction.get("description"),
            lines=line_creates,
        )
        return ChatResponse(
            message=result["message"],
            transaction=txn_response,
            entity_mentions=entity_mentions,
            resolved_entities=resolved_entities if resolved_entities else None,
        )
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


@router.get("", response_model=list[TransactionRead])
def list_transactions(
    db: Session = Depends(get_db),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
) -> list[TransactionRead]:
    q = (
        select(Transaction)
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
    if not t:
        raise HTTPException(status_code=404, detail="Transaction not found")
    _load_transaction_with_lines(db, t)
    return _transaction_to_read(t)


def _create_transaction_from_payload(db: Session, payload: TransactionCreate) -> Transaction:
    lines_data = payload.lines
    total_debit = sum(l.debit for l in lines_data)
    total_credit = sum(l.credit for l in lines_data)
    if total_debit != total_credit:
        raise HTTPException(
            status_code=400,
            detail=f"Debits ({total_debit}) must equal credits ({total_credit})",
        )
    transaction = Transaction(
        date=payload.date,
        reference=payload.reference,
        description=payload.description,
    )
    db.add(transaction)
    db.flush()
    for line in lines_data:
        acc = _get_account_by_code(db, line.account_code)
        db.add(
            TransactionLine(
                transaction_id=transaction.id,
                account_id=acc.id,
                debit=line.debit,
                credit=line.credit,
                line_description=line.line_description,
            )
        )
    for link in getattr(payload, "entity_links", []) or []:
        role = link.role.strip().lower()
        if link.entity_id:
            entity = db.get(Entity, link.entity_id)
            if not entity:
                raise HTTPException(status_code=400, detail=f"Entity not found: {link.entity_id}")
        else:
            entity = _get_or_create_entity(db, role, link.name or "")
        db.add(
            TransactionEntity(
                transaction_id=transaction.id,
                entity_id=entity.id,
                role=role,
            )
        )
    attachments = _load_attachments(db, getattr(payload, "attachment_ids", []) or [])
    for a in attachments:
        if a.transaction_id and a.transaction_id != transaction.id:
            raise HTTPException(status_code=400, detail=f"Attachment already linked: {a.id}")
        a.transaction_id = transaction.id
    return transaction


@router.post("", response_model=TransactionRead, status_code=201)
def create_transaction(
    payload: TransactionCreate,
    db: Session = Depends(get_db),
) -> TransactionRead:
    transaction = _create_transaction_from_payload(db, payload)
    db.commit()
    db.refresh(transaction)
    _load_transaction_with_lines(db, transaction)
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
    return _transaction_to_read(t)


@router.delete("/{transaction_id}", status_code=204)
def delete_transaction(
    transaction_id: UUID,
    db: Session = Depends(get_db),
) -> None:
    t = db.get(Transaction, transaction_id)
    if not t:
        raise HTTPException(status_code=404, detail="Transaction not found")
    db.delete(t)
    db.commit()


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
