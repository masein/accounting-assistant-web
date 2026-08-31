"""Supporting logic for the legacy ``POST /transactions/chat`` endpoint.

Intent detection, entity and bank matching, fee-context parsing and report
building for the pre-AI-accountant chat. Extracted from
``app/api/transactions.py``, where 800 lines of it sat between the HTTP
handlers: none of it touches a request or a response, and almost all of it is
plain text analysis that is far easier to reason about — and to test — on its
own.

Behaviour is unchanged; this is a move, not a rewrite. The router imports these
back under their original private names, so the chat handler itself is
untouched.
"""
from __future__ import annotations

import re
from datetime import date, timedelta

from uuid import UUID
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload
from app.models.account import Account, AccountLevel
from app.models.entity import Entity, TransactionEntity
from app.models.transaction import Transaction, TransactionLine
from app.schemas.transaction import ResolvedEntityLink
from app.services.reporting.cash_flow_service import CashFlowService
from app.services.reporting.financial_statement_service import FinancialStatementService
from app.services.reporting.inventory_report_service import InventoryReportService
from app.services.reporting.ledger_service import LedgerService
from app.services.reporting.operations_report_service import OperationsReportService
from app.services.reporting.report_intent import ReportIntent, parse_report_intent
from app.services.reporting.sales_report_service import SalesReportService
from app.services.transaction_fee import (
    canonical_method_name,
    parse_fee_question_context,
)


def _friendly_ai_error(raw_msg: str) -> str:
    """Map technical AISuggestError messages to user-friendly copy."""
    low = raw_msg.lower()
    if "timeout" in low or "did not respond" in low or "time" in low:
        return "The AI is taking longer than expected — please try again in a moment."
    if "cannot reach" in low or "connect" in low:
        return "Cannot reach the AI server right now. Please check your connection or try again shortly."
    if "interrupted" in low or "transport" in low:
        return "The connection to the AI was interrupted. Please try again."
    if "parse" in low or "json" in low or "unclear" in low:
        return "The AI response was unclear. Trying a simpler approach — please send your message again."
    return "Something went wrong with the AI. Please try again in a moment."


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
    from app.utils.jalali import _to_ascii
    ascii_low = _to_ascii(low)
    return bool(
        re.search(r"\b20\d{2}-\d{2}-\d{2}\b", low)
        or re.search(r"\b1[34]\d{2}[/\-]\d{1,2}[/\-]\d{1,2}\b", ascii_low)
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
    cleaned: list[dict[str, str]] = []
    for m in mentions:
        role = (m.get("role") or "").strip().lower()
        name = re.sub(r"\s+", " ", (m.get("name") or "").strip())
        low_name = name.lower()
        if role in ("client", "payee", "supplier"):
            if (
                not low_name
                or low_name in {"us", "to us", "our", "our account", "to our account"}
                or low_name.startswith("to ")
                or low_name.startswith("for ")
                or " bank" in low_name
                or " حساب" in low_name
            ):
                continue
        cleaned.append({"role": role, "name": name})
    mentions = cleaned
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
            "how much",
            "total money",
            "total cash",
            "who owes",
            "i owe",
            "expenses",
            "spending",
            "revenue",
            "earnings",
            "گردش حساب",
            "گردش بانک",
            "صورت حساب",
            "ترازنامه",
            "سود و زیان",
            "جریان وجوه نقد",
        )
    )
    return (subject and verb) or report_hint


def _parse_entity_transaction_query(text: str) -> str | None:
    """
    Detect queries like "transactions with Nikzade", "have I had any transactions with Ali Roshan",
    "show me dealings with supplier X". Returns the entity name or None.
    """
    low = (text or "").strip().lower()
    if not low:
        return None
    patterns = [
        r"(?:transactions?|dealings?|history|records?)\s+(?:with|for|of|involving)\s+(.+?)(?:\?|$)",
        r"(?:have\s+(?:i|we)\s+(?:had\s+)?(?:any\s+)?)?(?:transactions?|dealings?)\s+with\s+(.+?)(?:\?|$)",
        r"(?:show|find|get|list|search)\s+(?:me\s+)?(?:all\s+)?(?:transactions?|dealings?|records?)\s+(?:with|for|of|involving)\s+(.+?)(?:\?|$)",
        r"(?:did\s+(?:i|we)\s+(?:have|do|make)\s+(?:any\s+)?)?(?:transactions?|business|dealings?)\s+with\s+(.+?)(?:\?|$)",
        r"(?:any\s+)?(?:transactions?|payments?|receipts?)\s+(?:with|from|to)\s+(.+?)(?:\?|$)",
        r"(?:what\s+(?:are|were)\s+(?:the\s+)?)?(?:transactions?|dealings?)\s+with\s+(.+?)(?:\?|$)",
        # Persian patterns
        r"تراکنش[‌ها]*\s*(?:ی|های)?\s*(?:با|برای)\s+(.+?)(?:\?|؟|$)",
        r"معامل[هات]*\s*(?:ی|های)?\s*(?:با|برای)\s+(.+?)(?:\?|؟|$)",
        r"(?:آیا\s+)?(?:با|از)\s+(.+?)\s+تراکنش(?:ی)?\s+(?:داشت[هم]|دارم|داریم)",
        r"حساب\s*(?:ی|های)?\s*(?:با|برای)\s+(.+?)(?:\?|؟|$)",
        r"گردش\s*(?:حساب)?\s*(?:با|برای)\s+(.+?)(?:\?|؟|$)",
        r"(?:نمایش|نشان بده|لیست)\s+(?:تراکنش|معامل)[هات‌ها]*\s*(?:ی|های)?\s*(?:با|برای)\s+(.+?)(?:\?|؟|$)",
    ]
    for pat in patterns:
        m = re.search(pat, low, re.IGNORECASE)
        if m:
            name = m.group(1).strip().rstrip("?.!, ")
            stop_words = {"the", "a", "an", "my", "our", "any", "all", "some"}
            words = name.split()
            words = [w for w in words if w.lower() not in stop_words]
            name = " ".join(words).strip()
            if len(name) >= 2:
                _BANK_NAMES = {
                    "melli", "tejarat", "saderat", "saman", "parsian",
                    "pasargad", "mellat", "melat", "sina", "melli bank",
                    "tejarat bank", "saderat bank", "saman bank",
                    "parsian bank", "pasargad bank", "mellat bank", "sina bank",
                    "ملی", "تجارت", "صادرات", "سامان", "پارسیان", "پاسارگاد", "ملت", "سینا",
                }
                if name.lower() in _BANK_NAMES or name.lower().replace(" bank", "") in _BANK_NAMES:
                    return None
                return name
    return None


def _search_transactions_by_entity(db: Session, entity_name: str) -> list[Transaction]:
    """Search transactions linked to an entity by name (fuzzy substring match)."""
    q = (
        select(Transaction)
        .join(TransactionEntity, TransactionEntity.transaction_id == Transaction.id)
        .join(Entity, Entity.id == TransactionEntity.entity_id)
        .where(Entity.name.ilike(f"%{entity_name.strip()}%"))
        .options(
            selectinload(Transaction.lines).selectinload(TransactionLine.account),
            selectinload(Transaction.entity_links).selectinload(TransactionEntity.entity),
        )
        .order_by(Transaction.date.desc(), Transaction.created_at.desc())
        .limit(50)
    )
    return list(db.execute(q).scalars().unique().all())


def _format_entity_transaction_results(entity_name: str, txns: list[Transaction]) -> tuple[str, dict | None]:
    """Format entity transaction search results as a chat message + optional report."""
    from app.utils.jalali import format_jalali

    if not txns:
        return f"No transactions found involving '{entity_name}'.", None

    rows = []
    total_paid = 0
    total_received = 0
    for t in txns:
        total_d = sum(int(ln.debit or 0) for ln in (t.lines or []))
        total_c = sum(int(ln.credit or 0) for ln in (t.lines or []))
        jalali = format_jalali(t.date) if t.date else ""
        roles = ", ".join(
            f"{lnk.role}: {lnk.entity.name}" for lnk in (t.entity_links or []) if lnk.entity
        )
        rows.append({
            "date": f"{t.date} ({jalali})" if jalali else str(t.date),
            "reference": t.reference or "—",
            "description": (t.description or "—")[:80],
            "debit": total_d,
            "credit": total_c,
            "entities": roles,
        })
        total_paid += total_c
        total_received += total_d

    msg = (
        f"Found **{len(txns)} transaction(s)** involving '{entity_name}'.\n"
        f"Total debit: {total_received:,} IRR · Total credit: {total_paid:,} IRR"
    )
    report = {
        "reportType": "entity_transactions",
        "periodLabel": f"All time — {entity_name}",
        "rows": rows,
    }
    return msg, report


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
        if any(k in prev_low for k in ("گردش", "ledger", "statement", "bank balance", "balance", "دفتر")):
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
        effective_from = intent.from_date
        effective_to = intent.to_date
        # Running-balance reports need all-time data when no explicit dates given,
        # otherwise the balance is wrong (missing older transactions).
        if effective_from is None and effective_to is None and (intent.limit or intent.bank_name):
            effective_from = date(1900, 1, 1)
        if intent.bank_name:
            bank = _find_bank_entity_by_text(db, intent.bank_name)
            if bank:
                rep = osvc.person_running_balance(
                    entity_id=bank.id,
                    role="bank",
                    from_date=effective_from,
                    to_date=effective_to,
                )
                if limit and rep.rows:
                    rep.rows = rep.rows[-limit:]
                if rep.rows:
                    from app.utils.jalali import format_jalali
                    current_bal = rep.rows[-1].running_balance
                    bal_formatted = f"{current_bal:,}"
                    last_date = rep.rows[-1].date
                    jalali_str = format_jalali(last_date) if last_date else ""
                    date_label = f"{last_date} ({jalali_str})" if jalali_str else str(last_date)
                    msg = (
                        f"**{bank.name} Bank — Current balance: {bal_formatted} IRR**\n"
                        f"As of {date_label} · {len(rep.rows)} transaction(s)"
                    )
                    return msg, rep.model_dump(by_alias=True)
                account_code = _resolve_bank_account_code(db, bank.name)
                ledger_rep = lsvc.account_ledger(
                    account_code=account_code,
                    from_date=effective_from,
                    to_date=effective_to,
                    page=1,
                    page_size=limit,
                )
                return (
                    f"No entity-linked rows found for {bank.name}; showing account ledger {account_code}.",
                    ledger_rep.model_dump(by_alias=True),
                )
        account_code = intent.account_code or _resolve_bank_account_code(db, intent.bank_name)
        rep = lsvc.account_ledger(account_code=account_code, from_date=effective_from, to_date=effective_to, page=1, page_size=limit)
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
        # Imported lazily: the router imports this module at module level, so
        # a top-level import back would be circular.
        from app.api.transactions import _load_transaction_with_lines

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
        parsed_date: date | None = None
        try:
            parsed_date = date.fromisoformat(date_val)
        except ValueError:
            pass
        if parsed_date is None:
            from app.utils.jalali import try_parse_jalali
            parsed_date = try_parse_jalali(date_val)
        if parsed_date is not None:
            q = q.where(Transaction.date == parsed_date)
            has_filter = True
        else:
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
