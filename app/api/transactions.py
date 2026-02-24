from __future__ import annotations

import base64
import logging
import re
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
from app.services.reporting.cash_flow_service import CashFlowService
from app.services.reporting.financial_statement_service import FinancialStatementService
from app.services.reporting.inventory_report_service import InventoryReportService
from app.services.reporting.ledger_service import LedgerService
from app.services.reporting.operations_report_service import OperationsReportService
from app.services.reporting.report_intent import ReportIntent, parse_report_intent
from app.services.reporting.sales_report_service import SalesReportService
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
    name = re.sub(r"\s+", " ", (name or "").strip())
    # Guardrail: reject malformed phrase-like names from chat extraction.
    lower_name = name.lower()
    if (
        len(name) < 2
        or len(name) > 80
        or len(name.split()) > 5
        or re.search(r"\b(via|bank|account|about|project|payment|transaction)\b", lower_name)
        or lower_name in {"us", "our", "me", "we", "you", "your"}
    ):
        raise HTTPException(status_code=400, detail=f"Invalid entity name: {name}")
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


def _all_bank_names(db: Session) -> list[str]:
    return [
        n
        for n in db.execute(
            select(Entity.name).where(Entity.type == "bank").order_by(Entity.name)
        ).scalars().all()
        if n
    ]


def _looks_like_edit_request(messages: list[dict[str, str]]) -> bool:
    last_user = next((m.get("content") or "" for m in reversed(messages) if (m.get("role") or "") == "user"), "").strip()
    if not last_user:
        return False
    low = last_user.lower()
    explicit = any(
        k in low
        for k in (
            "edit",
            "update",
            "change",
            "fix",
            "correct",
            "set ",
            "reverse",
            "ویرایش",
            "اصلاح",
            "تغییر",
            "update transaction",
        )
    )
    if explicit:
        return True
    # Continue edit flow if assistant explicitly asked for edit search/change fields.
    recent_assistant = [
        (m.get("content") or "").lower()
        for m in messages[-4:]
        if (m.get("role") or "") == "assistant"
    ]
    assistant_in_edit_flow = any(
        ("transaction to edit" in a)
        or ("what to change" in a)
        or ("matching transaction" in a)
        or ("transaction id" in a)
        for a in recent_assistant
    )
    if not assistant_in_edit_flow:
        return False
    return bool(
        re.search(r"\b20\d{2}-\d{2}-\d{2}\b", low)
        or re.search(r"\b[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}\b", low)
        or any(k in low for k in ("reference", "ref", "client", "bank", "payee", "supplier", "transaction"))
    )


def _normalize_entity_mentions_for_context(
    mentions: list[dict[str, str]],
    *,
    context_text: str,
) -> list[dict[str, str]]:
    """
    Resolve ambiguous role collisions from model output (same name as both payee and supplier).
    """
    if not mentions:
        return mentions
    context = (context_text or "").lower()
    employee_like = any(k in context for k in ("employee", "salary", "wage", "حقوق", "دستمزد"))
    supplier_like = any(
        k in context
        for k in ("vendor", "supplier", "hosting", "domain", "server", "subscription", "renewal", "purchase", "invoice")
    )
    payee_names = {(m.get("name") or "").strip().lower() for m in mentions if (m.get("role") or "").strip().lower() == "payee"}
    supplier_names = {(m.get("name") or "").strip().lower() for m in mentions if (m.get("role") or "").strip().lower() == "supplier"}
    overlap = {n for n in payee_names if n and n in supplier_names}
    if employee_like and not payee_names and supplier_names:
        # If context clearly indicates employee compensation and model only returned supplier,
        # convert supplier mentions into payee.
        converted: list[dict[str, str]] = []
        for m in mentions:
            role = (m.get("role") or "").strip().lower()
            name = (m.get("name") or "").strip()
            if role == "supplier" and name:
                converted.append({"role": "payee", "name": name})
            else:
                converted.append(m)
        mentions = converted
        payee_names = {(m.get("name") or "").strip().lower() for m in mentions if (m.get("role") or "").strip().lower() == "payee"}
        supplier_names = {(m.get("name") or "").strip().lower() for m in mentions if (m.get("role") or "").strip().lower() == "supplier"}
        overlap = {n for n in payee_names if n and n in supplier_names}
    if not overlap:
        return mentions
    out: list[dict[str, str]] = []
    for m in mentions:
        role = (m.get("role") or "").strip().lower()
        name = (m.get("name") or "").strip()
        low_name = name.lower()
        if not low_name or low_name not in overlap:
            out.append(m)
            continue
        if employee_like and role == "supplier":
            continue
        if supplier_like and role == "payee":
            continue
        # Default: keep payee for employee/person-like payments.
        if role == "supplier":
            continue
        out.append(m)
    return out


def _looks_like_fee_correction(text: str) -> bool:
    low = (text or "").strip().lower()
    if not low:
        return False
    return (
        any(k in low for k in ("fee", "transaction fee", "کارمزد"))
        and any(k in low for k in ("wrong", "should be", "%", "rial", "toman", "0"))
    )


def _find_last_voucher_assistant_idx(messages: list[dict[str, str]]) -> int:
    marker = "here's the voucher based on what you said"
    for idx in range(len(messages) - 1, -1, -1):
        m = messages[idx]
        if (m.get("role") or "") != "assistant":
            continue
        content = (m.get("content") or "").strip().lower()
        if marker in content:
            return idx
    return -1


def _parse_included_fee_context(last_assistant_message: str) -> tuple[str, str] | None:
    text = re.sub(r"\s+", " ", (last_assistant_message or "").strip())
    # Example: "Included transaction fee 380,000 IRR (Paya via Mellat)."
    m = re.search(r"included\s+transaction\s+fee[\s\S]*?\((.+?)\s+via\s+(.+?)\)", text, re.IGNORECASE)
    if not m:
        return None
    method = canonical_method_name(m.group(1))
    bank = re.sub(r"\s+", " ", (m.group(2) or "").strip())
    if not method or not bank:
        return None
    return method, bank


def _looks_like_transaction_user_text(text: str) -> bool:
    t = (text or "").strip().lower()
    if not t:
        return False
    has_action = any(
        k in t
        for k in (
            "paid",
            "payed",
            "received",
            "payment",
            "receipt",
            "transfer",
            "پرداخت",
            "دریافت",
            "واریز",
            "برداشت",
        )
    )
    has_counterparty = any(k in t for k in (" to ", " from ", " for ", "bank", "supplier", "client", "employee", "via", "with"))
    has_amount = bool(
        re.search(
            r"(?<!\d)\d[\d,]{2,}(?:\s*(?:irr|rial|rials|ریال|تومان))?(?!\d)|(?<!\d)\d+(?:\.\d+)?\s*[kmb](?!\w)",
            t,
            re.IGNORECASE,
        )
    )
    return has_action and (has_amount or has_counterparty)


def _select_transaction_context_text(messages: list[dict[str, str]]) -> str:
    """
    Pick the most relevant user text for *current* voucher generation.
    This avoids pulling stale counterparties from older chat turns.
    """
    if not messages:
        return ""
    # If we're in a fee follow-up turn, use the latest transaction-like user message
    # before the last fee-question assistant message.
    fee_q_idx = None
    for idx in range(len(messages) - 1, -1, -1):
        m = messages[idx]
        if (m.get("role") or "") != "assistant":
            continue
        if parse_fee_question_context(m.get("content") or ""):
            fee_q_idx = idx
            break
    if fee_q_idx is not None:
        candidate_users = [
            (idx, (m.get("content") or "").strip())
            for idx, m in enumerate(messages[:fee_q_idx])
            if (m.get("role") or "") == "user" and (m.get("content") or "").strip()
        ]
        anchor_pair = next((p for p in reversed(candidate_users) if _looks_like_transaction_user_text(p[1])), None)
        if anchor_pair:
            anchor_idx = anchor_pair[0]
            merged = [
                (m.get("content") or "").strip()
                for m in messages[anchor_idx:fee_q_idx]
                if (m.get("role") or "") == "user" and (m.get("content") or "").strip()
            ]
            if merged:
                return " . ".join(merged)
            return anchor_pair[1]
        if candidate_users:
            return candidate_users[-1][1]
    # Otherwise, prefer latest transaction-like user message.
    user_pairs = [
        (idx, (m.get("content") or "").strip())
        for idx, m in enumerate(messages)
        if (m.get("role") or "") == "user" and (m.get("content") or "").strip()
    ]
    anchor_pair = next((p for p in reversed(user_pairs) if _looks_like_transaction_user_text(p[1])), None)
    if anchor_pair:
        anchor_idx = anchor_pair[0]
        merged = [
            (m.get("content") or "").strip()
            for m in messages[anchor_idx:]
            if (m.get("role") or "") == "user" and (m.get("content") or "").strip()
        ]
        if merged:
            return " . ".join(merged)
        return anchor_pair[1]
    return user_pairs[-1][1] if user_pairs else ""


def _transaction_window_messages(messages: list[dict[str, str]]) -> list[dict[str, str]]:
    """
    Use only messages after the last confirmed voucher response to avoid carrying
    stale counterparties/amounts into the next transaction.
    """
    marker = "here's the voucher based on what you said"
    cut_idx = -1
    for idx in range(len(messages) - 1, -1, -1):
        m = messages[idx]
        if (m.get("role") or "") != "assistant":
            continue
        content = (m.get("content") or "").strip().lower()
        if marker in content:
            cut_idx = idx
            break
    if cut_idx >= 0:
        window = messages[cut_idx + 1 :]
        if window:
            return window
    return messages


def _fee_question_message(method_name: str, bank_name: str, prefix: str | None = None) -> str:
    base = (
        f"What is the transaction fee for {canonical_method_name(method_name)} via {bank_name}? "
        "You can answer like: '5000 toman', '1%', or '1% + 5000 with max 30000'."
    )
    if prefix:
        return f"{prefix} {base}".strip()
    return base


def _align_payment_amount_with_context(transaction: dict, amount: int) -> dict:
    """
    If AI produced a simple payment entry but ignored the clarified amount from chat,
    align the single non-fee debit and bank credit to the requested amount.
    """
    if amount <= 0:
        return transaction
    lines = transaction.get("lines")
    if not isinstance(lines, list) or not lines:
        return transaction
    bank_indices = [i for i, ln in enumerate(lines) if str(ln.get("account_code") or "").strip() == "1110"]
    base_candidate_indices = [i for i, ln in enumerate(lines) if str(ln.get("account_code") or "").strip() not in ("1110", "6210")]
    if len(bank_indices) != 1 or not base_candidate_indices:
        return transaction
    bank_idx = bank_indices[0]
    base_idx = max(base_candidate_indices, key=lambda ix: int(lines[ix].get("debit") or 0))
    current_base = int(lines[base_idx].get("debit") or 0)
    lines[base_idx]["debit"] = amount
    lines[base_idx]["credit"] = 0
    fee_debit = sum(
        max(0, int(ln.get("debit") or 0))
        for ln in lines
        if str(ln.get("account_code") or "").strip() == "6210"
    )
    lines[bank_idx]["debit"] = 0
    lines[bank_idx]["credit"] = max(0, amount + fee_debit)
    total_debit = sum(max(0, int(ln.get("debit") or 0)) for ln in lines)
    total_credit = sum(max(0, int(ln.get("credit") or 0)) for ln in lines)
    if total_debit != total_credit:
        diff = total_debit - total_credit
        lines[bank_idx]["credit"] = max(0, int(lines[bank_idx].get("credit") or 0) + diff)
    transaction["lines"] = lines
    return transaction


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


def _looks_like_non_payment_query(text: str) -> bool:
    lower = (text or "").strip().lower()
    if not lower:
        return False
    subject = any(
        k in lower
        for k in (
            "transaction",
            "transactions",
            "voucher",
            "entry",
            "entries",
            "ledger",
            "report",
            "balance sheet",
            "income statement",
            "cash flow",
            "trial balance",
            "دفتر",
            "گزارش",
            "تراز",
            "گردش",
            "سود",
            "زیان",
            "انبار",
            "فروش",
            "خرید",
        )
    )
    verb = any(
        k in lower
        for k in (
            "show",
            "list",
            "find",
            "get",
            "latest",
            "lates",
            "recent",
            "what was",
            "what is",
            "نشان",
            "بده",
            "میخوام",
            "می خواهم",
            "میخواهم",
            "ببینم",
        )
    )
    report_hint = any(
        k in lower
        for k in (
            "dashboard",
            "history",
            "chart",
            "balance",
            "missing references",
            "گردش حساب",
            "گردش بانک",
            "صورت حساب",
            "ترازنامه",
            "سود و زیان",
            "جریان وجوه نقد",
        )
    )
    return (subject and verb) or report_hint


def _user_says_unknown_method(text: str) -> bool:
    low = (text or "").strip().lower()
    if not low:
        return False
    hints = (
        "don't know the method",
        "dont know the method",
        "do not know the method",
        "i don't know method",
        "i dont know method",
        "unknown method",
        "not sure method",
        "نمیدونم روش",
        "نمی دونم روش",
        "روش رو نمی‌دونم",
        "روش را نمی دانم",
    )
    return any(h in low for h in hints)


def _normalize_for_match(text: str) -> str:
    t = (text or "").strip().lower()
    t = t.replace("ي", "ی").replace("ك", "ک")
    t = t.replace("\u200c", " ").replace("‌", " ")
    t = re.sub(r"\s+", " ", t)
    return t


def _canonical_bank_key(name: str) -> str:
    n = _normalize_for_match(name)
    if not n:
        return ""
    if any(k in n for k in ("melli", "meli", "ملی", "ملي")):
        return "melli"
    if any(k in n for k in ("mellat", "ملت")):
        return "mellat"
    return re.sub(r"[^a-z0-9\u0600-\u06ff]+", "", n)


def _find_bank_entity_by_text(db: Session, text: str) -> Entity | None:
    raw = (text or "").strip()
    if not raw:
        return None
    exact = (
        db.execute(
            select(Entity).where(
                Entity.type == "bank",
                Entity.name.ilike(raw),
            )
        )
        .scalars()
        .first()
    )
    if exact:
        return exact
    banks = db.execute(select(Entity).where(Entity.type == "bank").order_by(Entity.name)).scalars().all()
    norm_raw = _normalize_for_match(raw)
    key_raw = _canonical_bank_key(raw)
    for b in banks:
        name = (b.name or "").strip()
        if not name:
            continue
        norm_name = _normalize_for_match(name)
        if norm_name and (norm_name in norm_raw or norm_raw in norm_name):
            return b
        if key_raw and _canonical_bank_key(name) == key_raw:
            return b
    return None


def _infer_followup_report_intent(messages: list[dict], db: Session) -> ReportIntent | None:
    last_user = next((m.get("content") or "" for m in reversed(messages) if m.get("role") == "user"), "").strip()
    if not last_user:
        return None
    bank = _find_bank_entity_by_text(db, last_user)
    if not bank:
        return None
    user_messages = [(m.get("content") or "").strip() for m in messages if m.get("role") == "user" and (m.get("content") or "").strip()]
    if len(user_messages) < 2:
        return None
    for prev in reversed(user_messages[:-1]):
        prev_intent = parse_report_intent(prev)
        prev_low = _normalize_for_match(prev)
        if prev_intent and prev_intent.key == "account_ledger":
            return ReportIntent(
                key="account_ledger",
                from_date=prev_intent.from_date,
                to_date=prev_intent.to_date,
                bank_name=bank.name,
            )
        if any(k in prev_low for k in ("گردش", "ledger", "statement", "bank balance", "دفتر")):
            return ReportIntent(key="account_ledger", bank_name=bank.name)
    return None


def _resolve_bank_account_code(db: Session, bank_name: str | None) -> str:
    if not bank_name:
        return "1110"
    bank = _find_bank_entity_by_text(db, bank_name)
    code = (bank.code if bank else None) or ""
    code = code.strip()
    if code and db.execute(select(Account).where(Account.code == code)).scalars().one_or_none():
        return code
    return "1110"


def _build_report_from_intent(db: Session, intent: ReportIntent) -> tuple[str, dict]:
    fsvc = FinancialStatementService(db)
    lsvc = LedgerService(db)
    isvc = InventoryReportService(db)
    osvc = OperationsReportService(db)
    ssvc = SalesReportService(db)

    if intent.key == "balance_sheet":
        rep = fsvc.balance_sheet(to_date=intent.to_date, comparative_to_date=intent.from_date)
        return "Balance sheet generated.", rep.model_dump(by_alias=True)
    if intent.key == "income_statement":
        rep = fsvc.income_statement(from_date=intent.from_date, to_date=intent.to_date)
        return "Income statement generated.", rep.model_dump(by_alias=True)
    if intent.key == "cash_flow":
        rep = CashFlowService(db).statement(from_date=intent.from_date, to_date=intent.to_date)
        return "Cash flow statement generated.", rep.model_dump(by_alias=True)
    if intent.key == "general_journal":
        page_size = max(1, min(50, int(intent.limit or 20)))
        rep = lsvc.general_journal(from_date=intent.from_date, to_date=intent.to_date, page=1, page_size=page_size)
        return "General journal generated.", rep.model_dump(by_alias=True)
    if intent.key == "general_ledger":
        rep = lsvc.general_ledger(from_date=intent.from_date, to_date=intent.to_date, page=1, page_size=200)
        return "General ledger generated.", rep.model_dump(by_alias=True)
    if intent.key == "trial_balance":
        rep = lsvc.trial_balance(from_date=intent.from_date, to_date=intent.to_date, page=1, page_size=200)
        return "Trial balance generated.", rep.model_dump(by_alias=True)
    if intent.key == "account_ledger":
        limit = max(1, min(200, int(intent.limit or 120)))
        if intent.bank_name:
            bank = _find_bank_entity_by_text(db, intent.bank_name)
            if bank:
                # If a specific bank entity is requested, prefer entity-linked running balance.
                # This works even when multiple banks share the same cash account code (e.g. 1110).
                rep = osvc.person_running_balance(
                    entity_id=bank.id,
                    role="bank",
                    from_date=intent.from_date,
                    to_date=intent.to_date,
                )
                if limit and rep.rows:
                    rep.rows = rep.rows[-limit:]
                if rep.rows:
                    return f"Bank statement generated for {bank.name}.", rep.model_dump(by_alias=True)
                account_code = _resolve_bank_account_code(db, bank.name)
                ledger_rep = lsvc.account_ledger(
                    account_code=account_code,
                    from_date=intent.from_date,
                    to_date=intent.to_date,
                    page=1,
                    page_size=limit,
                )
                return (
                    f"No entity-linked rows found for {bank.name}; showing account ledger {account_code}.",
                    ledger_rep.model_dump(by_alias=True),
                )
        account_code = intent.account_code or _resolve_bank_account_code(db, intent.bank_name)
        rep = lsvc.account_ledger(account_code=account_code, from_date=intent.from_date, to_date=intent.to_date, page=1, page_size=limit)
        return f"Account ledger generated for {account_code}.", rep.model_dump(by_alias=True)
    if intent.key == "debtor_creditor":
        rep = osvc.debtor_creditor(from_date=intent.from_date, to_date=intent.to_date)
        return "Debtor/Creditor report generated.", rep.model_dump(by_alias=True)
    if intent.key == "inventory_balance":
        rep = isvc.balance_report(to_date=intent.to_date)
        return "Inventory balance generated.", rep.model_dump(by_alias=True)
    if intent.key == "inventory_movement":
        rep = isvc.movement_report(from_date=intent.from_date, to_date=intent.to_date, page=1, page_size=150)
        return "Inventory movement report generated.", rep.model_dump(by_alias=True)
    if intent.key == "sales_by_product":
        rep = ssvc.sales_by_product(from_date=intent.from_date, to_date=intent.to_date)
        return "Sales by product report generated.", rep.model_dump(by_alias=True)
    if intent.key == "sales_by_invoice":
        rep = ssvc.sales_by_invoice(from_date=intent.from_date, to_date=intent.to_date)
        return "Sales by invoice report generated.", rep.model_dump(by_alias=True)
    if intent.key == "purchase_by_product":
        rep = ssvc.purchase_by_product(from_date=intent.from_date, to_date=intent.to_date)
        return "Purchase by product report generated.", rep.model_dump(by_alias=True)
    if intent.key == "purchase_by_invoice":
        rep = ssvc.purchase_by_invoice(from_date=intent.from_date, to_date=intent.to_date)
        return "Purchase by invoice report generated.", rep.model_dump(by_alias=True)
    raise HTTPException(status_code=400, detail="Unsupported report intent")


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


def _normalize_employee_payment_account(
    db: Session,
    transaction: dict,
    *,
    resolved_entities: list[ResolvedEntityLink],
    entity_mentions: list[dict],
    user_text: str = "",
) -> dict:
    """For employee payees, force primary expense line to wages account 6110."""
    has_employee_payee = False
    for r in resolved_entities or []:
        if (r.role or "").strip().lower() != "payee":
            continue
        e = db.get(Entity, r.entity_id)
        if e and (e.type or "").strip().lower() == "employee":
            has_employee_payee = True
            break
    if not has_employee_payee:
        for m in entity_mentions or []:
            role = (m.get("role") or "").strip().lower() if isinstance(m, dict) else ""
            name = (m.get("name") or "").strip() if isinstance(m, dict) else ""
            if role != "payee" or not name:
                continue
            e = db.execute(select(Entity).where(Entity.type == "employee", Entity.name.ilike(name))).scalars().first()
            if e:
                has_employee_payee = True
                break
    if not has_employee_payee:
        text_norm = (user_text or "").strip().lower()
        if text_norm:
            employee_names = db.execute(select(Entity.name).where(Entity.type == "employee")).scalars().all()
            for nm in employee_names:
                n = (nm or "").strip().lower()
                if n and n in text_norm:
                    has_employee_payee = True
                    break
    if not has_employee_payee:
        return transaction
    lines = transaction.get("lines")
    if not isinstance(lines, list) or not lines:
        return transaction
    fee_keywords = ("fee", "transaction fee", "bank fee", "کارمزد")
    candidate_indices = []
    for i, ln in enumerate(lines):
        code = str(ln.get("account_code") or "").strip()
        debit = int(ln.get("debit") or 0)
        desc = str(ln.get("line_description") or "").strip().lower()
        if debit <= 0 or code in ("1110", "6210"):
            continue
        if any(k in desc for k in fee_keywords):
            continue
        candidate_indices.append(i)
    if not candidate_indices:
        return transaction
    target_idx = max(candidate_indices, key=lambda ix: int(lines[ix].get("debit") or 0))
    if str(lines[target_idx].get("account_code") or "").strip() != "6110":
        lines[target_idx]["account_code"] = "6110"
        if not (lines[target_idx].get("line_description") or "").strip():
            lines[target_idx]["line_description"] = "Employee compensation expense"
    transaction["lines"] = lines
    return transaction


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
    last_user_message = next((m.get("content") or "" for m in reversed(messages) if m.get("role") == "user"), "")
    last_assistant_message = next((m.get("content") or "" for m in reversed(messages) if m.get("role") == "assistant"), "")
    non_payment_query = _looks_like_non_payment_query(last_user_message)
    report_intent = parse_report_intent(last_user_message)
    # Only infer report follow-up context when current user text looks like a report query.
    # This avoids hijacking normal voucher sentences that merely mention a bank name.
    if report_intent is None and non_payment_query:
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
        return ChatResponse(
            message=result["message"],
            transaction=txn_response,
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
