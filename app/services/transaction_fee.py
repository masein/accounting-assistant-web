from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.models.entity import Entity
from app.models.transaction import Transaction
from app.models.transaction_fee import (
    FeeApplicationStatus,
    FeeType,
    PaymentMethod,
    TransactionFee,
    TransactionFeeApplication,
)

FEE_EXPENSE_ACCOUNT_CODE = "6210"
BANK_ACCOUNT_CODE = "1110"

DEFAULT_PAYMENT_METHODS = [
    ("paya", "Paya"),
    ("card_to_card", "Card-to-Card"),
    ("zaba", "Zaba"),
    ("satna", "Satna"),
    ("internal_transfer", "Internal Transfer"),
]

PAYMENT_METHOD_ALIASES: dict[str, tuple[str, ...]] = {
    "Paya": ("paya", "paya transfer", "پایا"),
    "Card-to-Card": (
        "card to card",
        "card-to-card",
        "cart to cart",
        "card2card",
        "pos",
        "کارت به کارت",
        "کارت‌به‌کارت",
    ),
    "Zaba": ("zaba", "زابا"),
    "Satna": ("satna", "ساتنا"),
    "Internal Transfer": (
        "internal transfer",
        "fee free transfer",
        "same bank transfer",
        "انتقال داخلی",
        "انتقال بین حساب",
        "بدون کارمزد",
    ),
    "Online Banking": (
        "online banking",
        "internet banking",
        "mobile banking",
        "online gateway",
        "payment gateway",
        "gateway",
        "بانکداری اینترنتی",
        "بانکداری آنلاین",
    ),
}

_PAYMENT_KEYWORDS = (
    "paid",
    "payed",
    "pay",
    "payment",
    "transfer",
    "deducted",
    "withdrawal",
    "outflow",
    "پرداخت",
    "واریز",
    "برداشت",
)

_RECEIPT_KEYWORDS = (
    "received",
    "receipt",
    "deposit",
    "inflow",
    "دریافت",
)


@dataclass
class FeeComputation:
    amount_mode: str
    input_amount: int
    base_amount: int
    fee_amount: int
    gross_amount: int
    net_amount: int
    applied_cap: bool


@dataclass
class PaymentContext:
    is_payment: bool
    amount: int
    amount_mode: str
    method_name: str | None
    bank_name: str | None


def _normalize_whitespace(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "")).strip()


def _normalize_text_for_match(text: str) -> str:
    """
    Normalize English/Persian user text for robust intent/keyword matching.
    """
    t = (text or "").strip().lower()
    # Normalize Arabic forms often seen on Persian keyboards.
    t = t.replace("ي", "ی").replace("ك", "ک")
    # Normalize half-space and punctuation variants.
    t = t.replace("\u200c", " ").replace("‌", " ")
    t = t.replace("-", " ")
    return _normalize_whitespace(t)


def _method_key(name: str) -> str:
    raw = (name or "").strip().lower()
    raw = raw.replace("&", " and ")
    raw = re.sub(r"[^a-z0-9]+", "_", raw)
    raw = re.sub(r"_+", "_", raw).strip("_")
    return raw or "method"


def canonical_method_name(name: str) -> str:
    n = _normalize_whitespace(name)
    if not n:
        return "Payment Method"
    lower = _normalize_text_for_match(n)
    for canonical, aliases in PAYMENT_METHOD_ALIASES.items():
        canonical_norm = _normalize_text_for_match(canonical)
        alias_norms = {_normalize_text_for_match(a) for a in aliases}
        if lower == canonical_norm or lower in alias_norms:
            return canonical
    if lower == "card":
        return "Card-to-Card"
    return " ".join(p[:1].upper() + p[1:].lower() for p in n.split(" "))


def ensure_default_payment_methods(db: Session) -> int:
    created = 0
    for key, name in DEFAULT_PAYMENT_METHODS:
        existing = db.execute(select(PaymentMethod).where(PaymentMethod.key == key)).scalars().first()
        if existing:
            continue
        db.add(PaymentMethod(key=key, name=name, is_active=True))
        created += 1
    if created:
        db.commit()
    return created


def get_or_create_payment_method(db: Session, method_name: str) -> PaymentMethod:
    name = canonical_method_name(method_name)
    key = _method_key(name)
    existing = db.execute(
        select(PaymentMethod).where((PaymentMethod.key == key) | (PaymentMethod.name.ilike(name)))
    ).scalars().first()
    if existing:
        if not existing.is_active:
            existing.is_active = True
        if not existing.name:
            existing.name = name
        if not existing.key:
            existing.key = key
        db.flush()
        return existing
    row = PaymentMethod(key=key, name=name, is_active=True)
    db.add(row)
    db.flush()
    return row


def find_payment_method(db: Session, method_name: str) -> PaymentMethod | None:
    name = canonical_method_name(method_name)
    key = _method_key(name)
    return db.execute(
        select(PaymentMethod).where((PaymentMethod.key == key) | (PaymentMethod.name.ilike(name)))
    ).scalars().first()


def get_or_create_bank_entity(db: Session, bank_name: str) -> Entity:
    name = _normalize_whitespace(bank_name)
    existing = db.execute(
        select(Entity).where(Entity.type == "bank", Entity.name.ilike(name))
    ).scalars().first()
    if existing:
        return existing
    row = Entity(type="bank", name=name)
    db.add(row)
    db.flush()
    return row


def find_bank_entity_by_name(db: Session, bank_name: str) -> Entity | None:
    name = _normalize_whitespace(bank_name)
    if not name:
        return None
    return db.execute(
        select(Entity).where(Entity.type == "bank", Entity.name.ilike(name))
    ).scalars().first()


def _effective_fee_values(rule: TransactionFee) -> tuple[int, int]:
    flat_fee = max(0, int(rule.flat_fee or 0))
    percent_bps = max(0, int(rule.percent_bps or 0))
    if rule.fee_type == FeeType.FLAT:
        if flat_fee == 0:
            flat_fee = max(0, int(rule.fee_value or 0))
        percent_bps = 0
    elif rule.fee_type == FeeType.PERCENT:
        if percent_bps == 0:
            percent_bps = max(0, int(rule.fee_value or 0))
        flat_fee = 0
    elif rule.fee_type == FeeType.HYBRID:
        if flat_fee == 0 and percent_bps == 0 and (rule.fee_value or 0) > 0:
            flat_fee = max(0, int(rule.fee_value))
    else:
        flat_fee = 0
        percent_bps = 0
    return flat_fee, percent_bps


def fee_amount_for_base(base_amount: int, rule: TransactionFee) -> tuple[int, bool]:
    base = max(0, int(base_amount or 0))
    if rule.fee_type == FeeType.FREE:
        return 0, False
    flat_fee, percent_bps = _effective_fee_values(rule)
    percent_fee = int(round(base * (percent_bps / 10_000)))
    if rule.fee_type == FeeType.FLAT:
        fee = flat_fee
    elif rule.fee_type == FeeType.PERCENT:
        fee = percent_fee
    else:
        fee = flat_fee + percent_fee
    cap_applied = False
    if rule.max_fee is not None:
        cap = max(0, int(rule.max_fee))
        if fee > cap:
            fee = cap
            cap_applied = True
    return max(0, int(fee)), cap_applied


def _gross_for_base(base: int, rule: TransactionFee) -> int:
    fee, _ = fee_amount_for_base(base, rule)
    return base + fee


def _solve_base_from_gross(gross_amount: int, rule: TransactionFee) -> int:
    gross = max(0, int(gross_amount or 0))
    if gross == 0 or rule.fee_type == FeeType.FREE:
        return gross
    lo, hi = 0, gross
    best = 0
    while lo <= hi:
        mid = (lo + hi) // 2
        total = _gross_for_base(mid, rule)
        if total == gross:
            return mid
        if total < gross:
            best = mid
            lo = mid + 1
        else:
            hi = mid - 1
    start = max(0, best - 10_000)
    end = min(gross, best + 10_000)
    nearest = best
    nearest_delta = abs(_gross_for_base(best, rule) - gross)
    for base in range(start, end + 1):
        delta = abs(_gross_for_base(base, rule) - gross)
        if delta < nearest_delta:
            nearest = base
            nearest_delta = delta
            if delta == 0:
                return base
    return nearest


def calculate_total_with_fee(amount: int, rule: TransactionFee, amount_mode: str = "net") -> FeeComputation:
    mode = (amount_mode or "net").strip().lower()
    if mode not in ("net", "gross"):
        mode = "net"
    input_amount = max(0, int(amount or 0))
    if mode == "gross":
        gross = input_amount
        base = _solve_base_from_gross(gross, rule)
        fee, cap_applied = fee_amount_for_base(base, rule)
        exact = base + fee
        if exact != gross:
            diff = gross - exact
            # Keep traceability with statement amount even if rounding produced a tiny mismatch.
            fee = max(0, fee + diff)
        net = base
        return FeeComputation(
            amount_mode="gross",
            input_amount=input_amount,
            base_amount=base,
            fee_amount=fee,
            gross_amount=gross,
            net_amount=net,
            applied_cap=cap_applied,
        )
    base = input_amount
    fee, cap_applied = fee_amount_for_base(base, rule)
    gross = base + fee
    return FeeComputation(
        amount_mode="net",
        input_amount=input_amount,
        base_amount=base,
        fee_amount=fee,
        gross_amount=gross,
        net_amount=base,
        applied_cap=cap_applied,
    )


def calculate_total_with_fee_for_mapping(
    db: Session,
    *,
    amount: int,
    method_name: str,
    bank_name: str,
    amount_mode: str = "net",
    as_of: date | None = None,
) -> tuple[FeeComputation, TransactionFee]:
    _, _, rule = resolve_fee_rule(db, method_name=method_name, bank_name=bank_name, as_of=as_of)
    if rule is None:
        raise ValueError(f"No fee rule mapped for {canonical_method_name(method_name)} via {bank_name}")
    return calculate_total_with_fee(amount=amount, rule=rule, amount_mode=amount_mode), rule


# Product-spec helper (camelCase) for integrations.
def calculateTotalWithFee(
    amount: int,
    method: str,
    bank: str,
    db: Session,
    amount_mode: str = "net",
    as_of: date | None = None,
) -> FeeComputation:
    calc, _ = calculate_total_with_fee_for_mapping(
        db,
        amount=amount,
        method_name=method,
        bank_name=bank,
        amount_mode=amount_mode,
        as_of=as_of,
    )
    return calc


def get_active_fee_rule(
    db: Session,
    method_id: uuid.UUID,
    bank_id: uuid.UUID,
    as_of: date | None = None,
) -> TransactionFee | None:
    target_date = as_of or date.today()
    q = (
        select(TransactionFee)
        .where(
            TransactionFee.method_id == method_id,
            TransactionFee.bank_id == bank_id,
            TransactionFee.is_active.is_(True),
            TransactionFee.effective_from <= target_date,
        )
        .order_by(TransactionFee.effective_from.desc(), TransactionFee.created_at.desc())
    )
    return db.execute(q).scalars().first()


def resolve_fee_rule(
    db: Session,
    method_name: str,
    bank_name: str,
    as_of: date | None = None,
) -> tuple[PaymentMethod | None, Entity | None, TransactionFee | None]:
    method = find_payment_method(db, method_name)
    bank = find_bank_entity_by_name(db, bank_name)
    if not method or not bank:
        return method, bank, None
    return method, bank, get_active_fee_rule(db, method.id, bank.id, as_of=as_of)


def build_fee_line_items(fee_amount: int, method_name: str, bank_name: str) -> list[dict[str, Any]]:
    fee = max(0, int(fee_amount or 0))
    if fee <= 0:
        return []
    note = f"Transaction fee - {canonical_method_name(method_name)} via {_normalize_whitespace(bank_name)}"
    return [
        {
            "account_code": FEE_EXPENSE_ACCOUNT_CODE,
            "debit": fee,
            "credit": 0,
            "line_description": note,
        },
        {
            "account_code": BANK_ACCOUNT_CODE,
            "debit": 0,
            "credit": fee,
            "line_description": f"Bank fee deduction - {_normalize_whitespace(bank_name)}",
        },
    ]


def infer_amount_mode(messages: list[dict[str, str]]) -> str:
    text = " ".join([(m.get("content") or "") for m in messages if m.get("role") == "user"]).lower()
    if any(k in text for k in ("gross", "including fee", "total deducted", "statement amount", "amount deducted")):
        return "gross"
    if any(k in text for k in ("net", "excluding fee", "without fee", "after fee")):
        return "net"
    return "net"


def _parse_number_token(token: str) -> float:
    t = token.strip().replace(",", "").replace("_", "")
    return float(t)


def _money_from_token(token: str, suffix: str | None = None, currency: str | None = None) -> int:
    try:
        num = _parse_number_token(token)
    except ValueError:
        return 0
    mul = 1
    sf = (suffix or "").strip().lower()
    if sf == "k":
        mul = 1_000
    elif sf == "m":
        mul = 1_000_000
    elif sf == "b":
        mul = 1_000_000_000
    val = int(round(num * mul))
    cur = (currency or "").strip().lower()
    if "toman" in cur:
        val *= 10
    return max(0, val)


def parse_amount_int(text: str) -> int:
    if not text:
        return 0
    t = text.lower()
    t = re.sub(
        r"(?:account|acc(?:ount)?|iban|card|شماره\s*حساب|شماره\s*کارت)\s*[:#-]?\s*\d[\d,]{5,}",
        " ",
        t,
    )
    m = re.search(r"(\d[\d,]*(?:\.\d+)?)\s*([kmb])\b", t)
    if m:
        return _money_from_token(m.group(1), m.group(2), None)
    m = re.search(r"(\d[\d,]*(?:\.\d+)?)\s*(million|billion)\b", t)
    if m:
        sf = "m" if m.group(2).lower().startswith("m") else "b"
        return _money_from_token(m.group(1), sf, None)
    m = re.search(r"(\d[\d,]*(?:\.\d+)?)\s*(tomans?|rials?)\b", t)
    if m:
        return _money_from_token(m.group(1), None, m.group(2))
    # Currency code/word may come immediately after amount (e.g. "79471400IRR")
    m = re.search(r"(?<!\d)(\d[\d,]*(?:\.\d+)?)(?:\s*(?:irr|rial|rials|ریال|ريال|تومان|tomans?))(?!\d)", t, re.IGNORECASE)
    if m:
        return _money_from_token(m.group(1), None, None)
    m = re.search(r"(?<!\d)(\d[\d,]{2,})(?!\d)", t)
    if m:
        return _money_from_token(m.group(1), None, None)
    return 0


def is_payment_intent(messages: list[dict[str, str]]) -> bool:
    text = _normalize_text_for_match(" ".join([(m.get("content") or "") for m in messages if m.get("role") == "user"]))
    # Treat explicit "paid us / to us" style phrases as receipt (money in), not payment-out.
    receipt_phrase_hints = (
        "paid us",
        "pay us",
        "to us",
        "received from",
        "got paid",
        "be ma",
        "به ما",
        "به حساب ما",
        "واریز کرد",
        "دریافت کردیم",
    )
    if any(h in text for h in receipt_phrase_hints):
        return False
    pay_hits = sum(1 for k in _PAYMENT_KEYWORDS if k in text)
    recv_hits = sum(1 for k in _RECEIPT_KEYWORDS if k in text)
    return pay_hits > 0 and pay_hits >= recv_hits


def extract_payment_method(text: str) -> str | None:
    raw = (text or "").strip()
    if not raw:
        return None
    lower = _normalize_text_for_match(raw)
    for canonical, aliases in PAYMENT_METHOD_ALIASES.items():
        canonical_norm = _normalize_text_for_match(canonical)
        alias_norms = [_normalize_text_for_match(a) for a in aliases]
        if canonical_norm in lower:
            return canonical
        if any(a in lower for a in alias_norms):
            return canonical
    m = re.search(r"\b(?:via|with|through|using|با)\s+([a-z\u0600-\u06ff][a-z0-9\u0600-\u06ff\-\s]{2,60})\b", lower)
    if m:
        frag = _normalize_whitespace(m.group(1))
        if re.search(r"\d{4,}", frag):
            return None
        if any(k in frag for k in ("account", "iban", "شماره", "حساب", "card", "کارت")):
            return None
        if re.search(r"\b(?:mellat|melli|tejarat|saderat|saman|parsian|pasargad)\b", frag):
            return None
        if "bank" in frag and "online banking" not in frag and "internet banking" not in frag:
            return None
        return canonical_method_name(frag)
    return None


def extract_bank_name(text: str, known_banks: list[str]) -> str | None:
    if not text:
        return None
    normalized_known = [b for b in known_banks if b]
    lower = text.lower()
    for name in sorted(normalized_known, key=len, reverse=True):
        if re.search(r"\b" + re.escape(name.lower()) + r"\b", lower):
            return name
    m = re.search(r"\b(?:from|via|through|to)\s+([a-z][a-z0-9\s]{1,40})\s+bank\b", lower)
    if m:
        return canonical_method_name(m.group(1))
    return None


def extract_payment_context(
    messages: list[dict[str, str]],
    known_banks: list[str],
) -> PaymentContext:
    user_texts = [(m.get("content") or "") for m in messages if m.get("role") == "user"]
    is_payment = is_payment_intent(messages)
    amount = 0
    method_name = None
    bank_name = None
    for txt in reversed(user_texts):
        if amount <= 0:
            amount = parse_amount_int(txt)
        if method_name is None:
            method_name = extract_payment_method(txt)
        if bank_name is None:
            bank_name = extract_bank_name(txt, known_banks)
        if amount > 0 and method_name and bank_name:
            break
    return PaymentContext(
        is_payment=is_payment,
        amount=amount,
        amount_mode=infer_amount_mode(messages),
        method_name=method_name,
        bank_name=bank_name,
    )


def _find_cap(text: str) -> int | None:
    t = text.lower()
    m = re.search(
        r"(?:max(?:imum)?|up\s*to|cap(?:ped)?(?:\s*at)?)\s*(?:of|at)?\s*(\d[\d,]*(?:\.\d+)?)\s*([kmb])?\s*(tomans?|rials?)?",
        t,
    )
    if not m:
        return None
    val = _money_from_token(m.group(1), m.group(2), m.group(3))
    return val if val > 0 else None


def parse_fee_config_text(text: str) -> dict[str, Any] | None:
    if not text:
        return None
    t = text.strip().lower()
    if not t:
        return None
    has_fee_keyword = any(k in t for k in ("fee", "transaction fee", "کارمزد", "%", "toman", "rial", "max", "cap", "free"))
    from_bare_number = False
    if not has_fee_keyword and not re.fullmatch(r"\s*0+(?:\.0+)?\s*(?:tomans?|rials?)?\s*", t):
        # Allow bare numeric replies like "5000" as fee answers, but reject long free-form sentences
        # that likely describe a new transaction.
        if re.fullmatch(r"\s*\d[\d,]*(?:\.\d+)?\s*(?:tomans?|rials?)?\s*", t):
            from_bare_number = True
        else:
            return None
    if any(k in t for k in ("fee-free", "fee free", "no fee", "zero fee", "free")):
        return {
            "fee_type": "free",
            "fee_value": 0,
            "flat_fee": 0,
            "percent_bps": 0,
            "max_fee": None,
            "from_bare_number": False,
        }
    # Accept explicit zero-fee statements such as:
    # "0", "fee is 0", "transaction fee was 0 for this payment", "0 toman".
    if re.search(r"\b(?:fee|transaction fee|کارمزد)\b", t) and re.search(r"\b0+(?:\.0+)?\b", t):
        return {
            "fee_type": "free",
            "fee_value": 0,
            "flat_fee": 0,
            "percent_bps": 0,
            "max_fee": None,
            "from_bare_number": False,
        }
    if re.fullmatch(r"\s*0+(?:\.0+)?\s*(?:tomans?|rials?)?\s*", t):
        return {
            "fee_type": "free",
            "fee_value": 0,
            "flat_fee": 0,
            "percent_bps": 0,
            "max_fee": None,
            "from_bare_number": False,
        }

    percent_bps = 0
    m_percent = re.search(r"(\d+(?:\.\d+)?)\s*%", t)
    if m_percent:
        percent_bps = max(0, int(round(float(m_percent.group(1)) * 100)))
    max_fee = _find_cap(t)

    sanitized = re.sub(r"\d+(?:\.\d+)?\s*%", " ", t)
    sanitized = re.sub(
        r"(?:max(?:imum)?|up\s*to|cap(?:ped)?(?:\s*at)?)\s*(?:of|at)?\s*\d[\d,]*(?:\.\d+)?\s*[kmb]?\s*(?:tomans?|rials?)?",
        " ",
        sanitized,
    )
    money_matches = re.findall(r"(\d[\d,]*(?:\.\d+)?)\s*([kmb])?\s*(tomans?|rials?)?", sanitized)
    flat_fee = 0
    for tok, suffix, currency in money_matches:
        value = _money_from_token(tok, suffix, currency)
        if value > 0:
            flat_fee = value
            break
    if percent_bps > 0 and flat_fee > 0:
        fee_type = "hybrid"
        fee_value = 0
    elif percent_bps > 0:
        fee_type = "percent"
        fee_value = percent_bps
    elif flat_fee > 0:
        fee_type = "flat"
        fee_value = flat_fee
    else:
        return None
    return {
        "fee_type": fee_type,
        "fee_value": fee_value,
        "flat_fee": flat_fee,
        "percent_bps": percent_bps,
        "max_fee": max_fee,
        "from_bare_number": from_bare_number,
    }


def parse_fee_question_context(last_assistant_message: str) -> tuple[str, str] | None:
    text = _normalize_whitespace(last_assistant_message)
    m = re.search(r"transaction fee for (.+?) via (.+?)\?", text, re.IGNORECASE)
    if not m:
        return None
    method = canonical_method_name(m.group(1))
    bank = _normalize_whitespace(m.group(2))
    return method, bank


def upsert_fee_rule(
    db: Session,
    *,
    method_name: str,
    bank_name: str,
    fee_type: str,
    fee_value: int = 0,
    flat_fee: int = 0,
    percent_bps: int = 0,
    max_fee: int | None = None,
    effective_from: date | None = None,
) -> TransactionFee:
    method = get_or_create_payment_method(db, method_name)
    bank = get_or_create_bank_entity(db, bank_name)
    target_date = effective_from or date.today()
    enum_type = FeeType((fee_type or "flat").strip().lower())
    row = db.execute(
        select(TransactionFee).where(
            TransactionFee.method_id == method.id,
            TransactionFee.bank_id == bank.id,
            TransactionFee.effective_from == target_date,
        )
    ).scalars().first()
    if not row:
        row = TransactionFee(
            method_id=method.id,
            bank_id=bank.id,
            effective_from=target_date,
            is_active=True,
        )
        db.add(row)
        db.flush()
    row.fee_type = enum_type
    row.fee_value = max(0, int(fee_value or 0))
    row.flat_fee = max(0, int(flat_fee or 0))
    row.percent_bps = max(0, int(percent_bps or 0))
    row.max_fee = None if max_fee is None else max(0, int(max_fee))
    row.is_active = True
    db.flush()
    return row


def recalculate_current_month_pending_entries(
    db: Session,
    *,
    method_id: uuid.UUID,
    bank_id: uuid.UUID,
    as_of: date | None = None,
) -> int:
    target_rule = get_active_fee_rule(db, method_id, bank_id, as_of=as_of)
    if not target_rule:
        return 0
    today = date.today()
    month_start = today.replace(day=1)
    q = (
        select(TransactionFeeApplication)
        .options(selectinload(TransactionFeeApplication.transaction))
        .where(
            TransactionFeeApplication.method_id == method_id,
            TransactionFeeApplication.bank_id == bank_id,
            TransactionFeeApplication.status == FeeApplicationStatus.PENDING,
        )
    )
    apps = db.execute(q).scalars().all()
    updated = 0
    for app in apps:
        tx: Transaction | None = getattr(app, "transaction", None)
        anchor_date = tx.date if tx is not None else (app.created_at.date() if app.created_at else today)
        if anchor_date < month_start:
            continue
        mode = (app.amount_mode or "net").lower()
        source_amount = app.gross_amount if mode == "gross" else (app.net_amount or app.base_amount or app.gross_amount)
        if not source_amount:
            continue
        calc = calculate_total_with_fee(int(source_amount), target_rule, amount_mode=mode)
        app.fee_rule_id = target_rule.id
        app.base_amount = calc.base_amount
        app.fee_amount = calc.fee_amount
        app.gross_amount = calc.gross_amount
        app.net_amount = calc.net_amount
        app.note = (
            f"Recalculated on {datetime.utcnow().date().isoformat()} from updated fee rule "
            f"({target_rule.fee_type.value})."
        )
        updated += 1
    return updated


def apply_fee_to_transaction_lines(
    transaction: dict[str, Any],
    *,
    method_name: str,
    bank_name: str,
    rule: TransactionFee,
    amount_mode: str = "net",
) -> tuple[dict[str, Any], FeeComputation | None]:
    lines = transaction.get("lines")
    if not isinstance(lines, list) or not lines:
        return transaction, None
    bank_idx = -1
    bank_credit = 0
    for i, line in enumerate(lines):
        if str(line.get("account_code") or "").strip() == BANK_ACCOUNT_CODE and int(line.get("credit") or 0) > bank_credit:
            bank_idx = i
            bank_credit = int(line.get("credit") or 0)
    if bank_idx < 0 or bank_credit <= 0:
        return transaction, None

    debit_candidate_indices = [
        i for i, line in enumerate(lines)
        if str(line.get("account_code") or "").strip() != BANK_ACCOUNT_CODE and int(line.get("debit") or 0) > 0
    ]
    if not debit_candidate_indices:
        return transaction, None

    base_from_lines = sum(
        int(lines[i].get("debit") or 0)
        for i in debit_candidate_indices
        if str(lines[i].get("account_code") or "").strip() != FEE_EXPENSE_ACCOUNT_CODE
    )
    if base_from_lines <= 0:
        base_from_lines = bank_credit

    mode = (amount_mode or "net").lower()
    if mode not in ("net", "gross"):
        mode = "net"
    amount_for_calc = bank_credit if mode == "gross" else base_from_lines
    calc = calculate_total_with_fee(amount_for_calc, rule, amount_mode=mode)
    fee = calc.fee_amount
    if fee <= 0:
        return transaction, calc

    fee_note = f"Transaction fee - {canonical_method_name(method_name)} via {_normalize_whitespace(bank_name)}"
    fee_keywords = ("fee", "transaction fee", "bank fee", "کارمزد")
    fee_candidate_indices: list[int] = []
    for i, l in enumerate(lines):
        code = str(l.get("account_code") or "").strip()
        if code == BANK_ACCOUNT_CODE:
            continue
        desc = str(l.get("line_description") or "").strip().lower()
        if code == FEE_EXPENSE_ACCOUNT_CODE or any(k in desc for k in fee_keywords):
            fee_candidate_indices.append(i)
    # If AI already inserted a fee line (possibly on wrong account), normalize to 6210
    # and avoid doubling fee.
    for idx in fee_candidate_indices:
        lines[idx]["account_code"] = FEE_EXPENSE_ACCOUNT_CODE
        lines[idx]["credit"] = 0
        lines[idx]["line_description"] = fee_note
    if fee_candidate_indices:
        primary_idx = fee_candidate_indices[0]
        existing_fee_total = sum(max(0, int(lines[idx].get("debit") or 0)) for idx in fee_candidate_indices)
        delta = fee - existing_fee_total
        lines[primary_idx]["debit"] = max(0, int(lines[primary_idx].get("debit") or 0)) + delta
        for idx in fee_candidate_indices[1:]:
            lines[idx]["debit"] = 0
            lines[idx]["line_description"] = "Merged duplicate fee line"
    else:
        lines.append(
            {
                "account_code": FEE_EXPENSE_ACCOUNT_CODE,
                "debit": fee,
                "credit": 0,
                "line_description": fee_note,
            }
        )

    bank_line = lines[bank_idx]
    if mode == "gross":
        non_fee_debit_indices = [
            i for i in debit_candidate_indices
            if str(lines[i].get("account_code") or "").strip() != FEE_EXPENSE_ACCOUNT_CODE
        ]
        if non_fee_debit_indices:
            current_base = sum(int(lines[i].get("debit") or 0) for i in non_fee_debit_indices)
            delta = current_base - calc.base_amount
            if delta != 0:
                target_idx = max(non_fee_debit_indices, key=lambda ix: int(lines[ix].get("debit") or 0))
                lines[target_idx]["debit"] = max(0, int(lines[target_idx].get("debit") or 0) - delta)
    else:
        non_fee_debit = sum(
            max(0, int(l.get("debit") or 0))
            for l in lines
            if str(l.get("account_code") or "").strip() not in (BANK_ACCOUNT_CODE, FEE_EXPENSE_ACCOUNT_CODE)
        )
        bank_line["credit"] = non_fee_debit + fee

    bank_desc = (bank_line.get("line_description") or "Payment from bank").strip()
    if "incl. fee" not in bank_desc.lower():
        bank_line["line_description"] = f"{bank_desc} (incl. fee)"
    else:
        bank_line["line_description"] = bank_desc

    total_debit = sum(max(0, int(l.get("debit") or 0)) for l in lines)
    total_credit = sum(max(0, int(l.get("credit") or 0)) for l in lines)
    if total_debit != total_credit:
        diff = total_debit - total_credit
        if diff > 0:
            bank_line["credit"] = max(0, int(bank_line.get("credit") or 0)) + diff
        elif diff < 0:
            bank_line["debit"] = max(0, int(bank_line.get("debit") or 0)) + (-diff)

    transaction["lines"] = lines
    if transaction.get("description"):
        desc = str(transaction["description"]).strip()
        if "fee" not in desc.lower():
            transaction["description"] = f"{desc} (fee via {canonical_method_name(method_name)})"
    return transaction, calc
