"""Anomaly detection as insights (roadmap 2026-09 §5.2).

Six patterns auditors and fraud checks look for, each an ``Insight`` in the
same feed as the others (dashboard card, AI chat briefing):

* ``duplicate_payment`` — the same amount paid to the same supplier twice
  within a week, or the same supplier reference paid twice.
* ``just_under_threshold`` — two or more payments or expense claims for one
  payee/employee just under the expense approval threshold (or, with no
  threshold set, just under the same round number) within 30 days: the
  classic way to split a payment past an approval.
* ``new_vendor_large`` — a first-ever payment to a supplier that is several
  times the company's typical supplier payment.
* ``round_weekend`` — round-amount entries dated on a weekend (Friday in
  Iran, Saturday/Sunday elsewhere).
* ``category_drift`` — one expense account taking a much larger share of
  spending over the last 90 days than over the nine months before.
* ``reversal_pattern`` — several entries for one counterparty undone, or
  several credit notes issued to it, within 30 days.

Reads are flat column queries (no object graphs) and every detector limits
itself to a window; amounts are only compared within one currency.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from statistics import median
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.account import Account
from app.models.entity import Entity, TransactionEntity
from app.models.transaction import Transaction, TransactionLine
from app.services.insight_service import _TEMPLATES, Insight, _fmt
from app.services.reporting.common import EXPENSE, classify_account_code

RECENT_DAYS = 30
DUPLICATE_WINDOW_DAYS = 7
NEW_VENDOR_MULTIPLE = 3
MIN_BASELINE_PAYMENTS = 10
THRESHOLD_BAND = 0.9          # within 10 % under the approval threshold
ROUND_BAND = 0.95             # within 5 % under a round number
DRIFT_POINTS = 10.0           # percentage points
DRIFT_MIN_SHARE = 20.0
REVERSAL_COUNT = 3


@dataclass
class Payment:
    txn_id: Any
    date: date
    currency: str
    amount: int
    entity_id: Any
    entity_name: str
    reference: str
    description: str


def _is_cash(db: Session):
    from app.services.cash_service import company_cash_predicate
    return company_cash_predicate(db)


def supplier_payments(db: Session, since: date, until: date) -> list[Payment]:
    """Money out of a cash/bank account on journals linked to a supplier or
    payee — one Payment per (journal, linked supplier)."""
    is_cash = _is_cash(db)
    live = (Transaction.date >= since, Transaction.date <= until, Transaction.deleted_at.is_(None))
    paid: dict = defaultdict(int)
    meta: dict = {}
    for tid, d, cur, ref, desc, code, credit in db.execute(
        select(Transaction.id, Transaction.date, Transaction.currency, Transaction.reference,
               Transaction.description, Account.code, TransactionLine.credit)
        .join(TransactionLine, TransactionLine.transaction_id == Transaction.id)
        .join(Account, TransactionLine.account_id == Account.id)
        .where(*live)
    ):
        if is_cash(code or "") and credit:
            paid[tid] += int(credit)
            meta[tid] = (d, (cur or "IRR").upper(), (ref or "").strip(), desc or "")
    out: list[Payment] = []
    for tid, eid, name in db.execute(
        select(TransactionEntity.transaction_id, TransactionEntity.entity_id, Entity.name)
        .join(Transaction, TransactionEntity.transaction_id == Transaction.id)
        .outerjoin(Entity, TransactionEntity.entity_id == Entity.id)
        .where(*live, TransactionEntity.role.in_(("supplier", "payee")))
    ):
        if tid in paid:
            d, cur, ref, desc = meta[tid]
            out.append(Payment(tid, d, cur, paid[tid], eid, name or "", ref, desc))
    out.sort(key=lambda p: (p.date, str(p.txn_id)))
    return out


# --- 1. duplicate payments ----------------------------------------------------------------

def detect_duplicate_payments(db: Session, today: date) -> list[Insight]:
    pays = supplier_payments(db, today - timedelta(days=RECENT_DAYS + DUPLICATE_WINDOW_DAYS + 60), today)
    recent_cut = today - timedelta(days=RECENT_DAYS)
    seen: set = set()
    out: list[Insight] = []
    by_entity: dict = defaultdict(list)
    for p in pays:
        by_entity[(p.entity_id, p.currency)].append(p)
    for items in by_entity.values():
        for i, a in enumerate(items):
            for b in items[i + 1:]:
                if b.date < recent_cut:
                    continue
                same_amount = a.amount == b.amount and (b.date - a.date).days <= DUPLICATE_WINDOW_DAYS
                same_ref = bool(a.reference) and a.reference == b.reference
                if not (same_amount or same_ref) or a.txn_id == b.txn_id:
                    continue
                pair = tuple(sorted((str(a.txn_id), str(b.txn_id))))
                if pair in seen:
                    continue
                seen.add(pair)
                out.append(Insight(
                    key=f"duplicate-payment-{pair[0]}-{pair[1]}", kind="duplicate_payment", severity="high",
                    page="transactions",
                    params={"name": a.entity_name, "amount": _fmt(b.amount), "first": a.date.isoformat(),
                            "second": b.date.isoformat()},
                    amount=b.amount,
                    data={"entity_id": str(a.entity_id), "entity_name": a.entity_name,
                          "transaction_ids": list(pair), "amount": b.amount, "currency": b.currency,
                          "reason": "same reference" if same_ref else "same amount within a week",
                          "reference": a.reference or None},
                ))
    out.sort(key=lambda i: -(i.amount or 0))
    return out[:3]


# --- 2. just under the approval threshold ---------------------------------------------------------

def _round_ceiling(amount: int) -> int | None:
    """The round number (1, 2 or 5 × a power of ten) that ``amount`` sits
    just under, if any."""
    if amount <= 0:
        return None
    magnitude = 10 ** (len(str(amount)) - 1)
    for step in (1, 2, 5, 10):
        r = step * magnitude
        if ROUND_BAND * r <= amount < r:
            return r
    return None


def detect_just_under_threshold(db: Session, today: date) -> list[Insight]:
    from app.models.mileage_claim import MileageClaim
    from app.services.expense_settings import get_approval_threshold

    threshold = get_approval_threshold(db)
    since = today - timedelta(days=RECENT_DAYS)
    groups: dict = defaultdict(list)   # (who, limit) -> [(date, amount, kind)]
    names: dict = {}
    for p in supplier_payments(db, since, today):
        limit = threshold if threshold and THRESHOLD_BAND * threshold <= p.amount < threshold else None
        if limit is None and not threshold:
            limit = _round_ceiling(p.amount)
        if limit:
            groups[(f"entity:{p.entity_id}", limit)].append((p.date, p.amount, "payment"))
            names[f"entity:{p.entity_id}"] = p.entity_name
    if threshold:
        for eid, name, d, amount in db.execute(
            select(MileageClaim.entity_id, MileageClaim.employee_name, MileageClaim.claim_date, MileageClaim.amount)
            .where(MileageClaim.claim_date >= since, MileageClaim.claim_date <= today)
        ):
            if THRESHOLD_BAND * threshold <= int(amount or 0) < threshold:
                who = f"employee:{eid or name}"
                groups[(who, threshold)].append((d, int(amount), "claim"))
                names[who] = name or ""
    out: list[Insight] = []
    for (who, limit), items in groups.items():
        if len(items) < 2:
            continue
        total = sum(a for _, a, _ in items)
        out.append(Insight(
            key=f"just-under-{who}-{limit}-{today:%Y-%m}", kind="just_under_threshold", severity="warning",
            page="expenses" if who.startswith("employee:") else "transactions",
            params={"name": names.get(who, ""), "count": len(items), "limit": _fmt(limit), "total": _fmt(total)},
            amount=total,
            data={"who": who, "name": names.get(who, ""), "limit": limit, "configured_threshold": bool(threshold),
                  "items": [{"date": d.isoformat(), "amount": a, "kind": k} for d, a, k in sorted(items)]},
        ))
    out.sort(key=lambda i: -(i.amount or 0))
    return out[:3]


# --- 3. a new vendor's large first payment -------------------------------------------------------------

def detect_new_vendor_large(db: Session, today: date) -> list[Insight]:
    pays = supplier_payments(db, today - timedelta(days=395), today)
    recent_cut = today - timedelta(days=RECENT_DAYS)
    out: list[Insight] = []
    for currency in {p.currency for p in pays}:
        mine = [p for p in pays if p.currency == currency]
        baseline = [p.amount for p in mine if p.date < recent_cut]
        if len(baseline) < MIN_BASELINE_PAYMENTS:
            continue
        typical = median(baseline)
        first_seen: dict = {}
        for p in mine:
            first_seen.setdefault(p.entity_id, p)
        for eid, p in first_seen.items():
            if p.date < recent_cut or p.amount < NEW_VENDOR_MULTIPLE * typical:
                continue
            out.append(Insight(
                key=f"new-vendor-large-{p.txn_id}", kind="new_vendor_large", severity="warning", page="entities",
                params={"name": p.entity_name, "amount": _fmt(p.amount), "date": p.date.isoformat(),
                        "typical": _fmt(typical)},
                amount=p.amount,
                data={"entity_id": str(eid), "entity_name": p.entity_name, "transaction_id": str(p.txn_id),
                      "amount": p.amount, "typical_supplier_payment": int(typical), "currency": currency},
            ))
    out.sort(key=lambda i: -(i.amount or 0))
    return out[:3]


# --- 4. round amounts on a weekend ------------------------------------------------------------------------

def _weekend_days(db: Session) -> set[int]:
    from app.services.locale_service import get_reporting_locale
    return {4} if (get_reporting_locale(db) or "").lower() == "ir" else {5, 6}   # Mon=0 … Sun=6


def _is_round(amount: int) -> bool:
    """One significant digit: 5,000,000 or 20,000 — not 5,250,000."""
    if amount < 10:
        return False
    magnitude = 10 ** (len(str(amount)) - 1)
    return amount % magnitude == 0


def detect_round_weekend(db: Session, today: date) -> list[Insight]:
    weekend = _weekend_days(db)
    since = today - timedelta(days=90)
    totals: dict = defaultdict(int)
    meta: dict = {}
    for tid, d, cur, desc, debit in db.execute(
        select(Transaction.id, Transaction.date, Transaction.currency, Transaction.description, TransactionLine.debit)
        .join(TransactionLine, TransactionLine.transaction_id == Transaction.id)
        .where(Transaction.date >= since, Transaction.date <= today, Transaction.deleted_at.is_(None))
    ):
        totals[tid] += int(debit or 0)
        meta[tid] = (d, (cur or "IRR").upper(), desc or "")
    sizes = [v for v in totals.values() if v > 0]
    if len(sizes) < 10:
        return []
    typical = median(sizes)
    recent_cut = today - timedelta(days=RECENT_DAYS)
    hits = []
    for tid, amount in totals.items():
        d, cur, desc = meta[tid]
        if d >= recent_cut and d.weekday() in weekend and _is_round(amount) and amount >= typical:
            hits.append({"transaction_id": str(tid), "date": d.isoformat(), "amount": amount, "currency": cur,
                         "description": desc})
    if not hits:
        return []
    hits.sort(key=lambda h: -h["amount"])
    return [Insight(
        key=f"round-weekend-{today:%Y-%m}-{len(hits)}", kind="round_weekend", severity="info", page="transactions",
        params={"count": len(hits), "largest": _fmt(hits[0]["amount"]), "date": hits[0]["date"]},
        amount=hits[0]["amount"], data={"entries": hits[:10]},
    )]


# --- 5. expense category drift -----------------------------------------------------------------------------

def detect_category_drift(db: Session, today: date) -> list[Insight]:
    recent_from = today - timedelta(days=90)
    base_from = today - timedelta(days=360)
    shares = {"recent": defaultdict(int), "base": defaultdict(int)}
    names: dict = {}
    for d, code, name, debit, credit in db.execute(
        # base value: shares of spending across every currency (roadmap §4.6)
        select(Transaction.date, Account.code, Account.name, TransactionLine.base_debit, TransactionLine.base_credit)
        .join(TransactionLine, TransactionLine.transaction_id == Transaction.id)
        .join(Account, TransactionLine.account_id == Account.id)
        .where(Transaction.date >= base_from, Transaction.date <= today, Transaction.deleted_at.is_(None))
    ):
        if classify_account_code(code or "") != EXPENSE:
            continue
        bucket = "recent" if d >= recent_from else "base"
        shares[bucket][code] += int(debit or 0) - int(credit or 0)
        names[code] = name
    recent_total = sum(v for v in shares["recent"].values() if v > 0)
    base_total = sum(v for v in shares["base"].values() if v > 0)
    if recent_total <= 0 or base_total <= 0:
        return []
    best = None
    for code, amount in shares["recent"].items():
        if amount <= 0:
            continue
        now = amount / recent_total * 100
        before = max(0, shares["base"].get(code, 0)) / base_total * 100
        if now >= DRIFT_MIN_SHARE and now - before >= DRIFT_POINTS and (best is None or now - before > best[1]):
            best = (code, now - before, now, before)
    if best is None:
        return []
    code, _gain, now, before = best
    return [Insight(
        key=f"category-drift-{code}-{today:%Y-%m}", kind="category_drift", severity="info", page="manager",
        params={"account": names.get(code, code), "now": f"{now:.0f}", "before": f"{before:.0f}"},
        amount=shares["recent"][code],
        data={"account_code": code, "account_name": names.get(code), "share_last_90_days": round(now, 1),
              "share_previous_9_months": round(before, 1)},
    )]


# --- 6. a counterparty with a sudden run of reversals ---------------------------------------------------------

def detect_reversal_pattern(db: Session, today: date) -> list[Insight]:
    from app.models.credit_note import CreditNote

    since_dt = datetime.combine(today - timedelta(days=RECENT_DAYS), datetime.min.time(), tzinfo=timezone.utc)
    undone: dict = defaultdict(int)
    names: dict = {}
    for eid, name in db.execute(
        select(TransactionEntity.entity_id, Entity.name)
        .join(Transaction, TransactionEntity.transaction_id == Transaction.id)
        .outerjoin(Entity, TransactionEntity.entity_id == Entity.id)
        .where(Transaction.deleted_at.is_not(None), Transaction.deleted_at >= since_dt)
        .execution_options(include_deleted=True)            # the undone ones are the point here
    ):
        undone[eid] += 1
        names[eid] = name or ""
    credits: dict = defaultdict(int)
    for eid, in db.execute(select(CreditNote.entity_id).where(
            CreditNote.date >= today - timedelta(days=RECENT_DAYS), CreditNote.entity_id.is_not(None))):
        credits[eid] += 1
    if credits:
        for eid, name in db.execute(select(Entity.id, Entity.name).where(Entity.id.in_(list(credits)))):
            names.setdefault(eid, name or "")
    out: list[Insight] = []
    for eid in set(undone) | set(credits):
        total = undone.get(eid, 0) + credits.get(eid, 0)
        if total < REVERSAL_COUNT:
            continue
        out.append(Insight(
            key=f"reversal-pattern-{eid}-{today:%Y-%m}", kind="reversal_pattern", severity="warning", page="entities",
            params={"name": names.get(eid, ""), "undone": undone.get(eid, 0), "credits": credits.get(eid, 0)},
            amount=total,
            data={"entity_id": str(eid), "entity_name": names.get(eid, ""), "undone_entries_30d": undone.get(eid, 0),
                  "credit_notes_30d": credits.get(eid, 0)},
        ))
    out.sort(key=lambda i: -(i.amount or 0))
    return out[:3]


DETECTORS = (
    ("duplicate_payment", detect_duplicate_payments),
    ("just_under_threshold", detect_just_under_threshold),
    ("new_vendor_large", detect_new_vendor_large),
    ("round_weekend", detect_round_weekend),
    ("category_drift", detect_category_drift),
    ("reversal_pattern", detect_reversal_pattern),
)


_TEMPLATES.update({
    "duplicate_payment": {
        "title": {"en": "Possible duplicate payment to {name}", "fa": "پرداخت احتمالاً تکراری به {name}",
                  "es": "Posible pago duplicado a {name}", "ar": "دفعة مكررة محتملة إلى {name}"},
        "message": {"en": "{amount} was paid on {first} and again on {second}. Check it wasn't paid twice.",
                    "fa": "{amount} در {first} و دوباره در {second} پرداخت شده. بررسی کنید دو بار پرداخت نشده باشد.",
                    "es": "Se pagaron {amount} el {first} y otra vez el {second}. Comprueba que no se pagó dos veces.",
                    "ar": "دُفع {amount} في {first} ومرة أخرى في {second}. تحقّق من أنه لم يُدفع مرتين."},
    },
    "just_under_threshold": {
        "title": {"en": "{count} payments to {name} just under {limit}", "fa": "{count} پرداخت به {name} کمی زیر {limit}",
                  "es": "{count} pagos a {name} justo por debajo de {limit}",
                  "ar": "{count} دفعات إلى {name} أقل بقليل من {limit}"},
        "message": {"en": "Together {total} in 30 days, each just below the approval limit — a split payment skips the approval.",
                    "fa": "در مجموع {total} در ۳۰ روز، هر کدام کمی زیر سقف تأیید — تقسیم پرداخت، تأیید را دور می‌زند.",
                    "es": "En total {total} en 30 días, cada uno justo por debajo del límite de aprobación: dividir un pago evita la aprobación.",
                    "ar": "المجموع {total} خلال 30 يومًا، كلٌّ منها أقل بقليل من حد الموافقة — تقسيم الدفعة يتجاوز الموافقة."},
    },
    "new_vendor_large": {
        "title": {"en": "Large first payment to a new supplier: {name}", "fa": "نخستین پرداخت بزرگ به تأمین‌کنندهٔ جدید: {name}",
                  "es": "Primer pago elevado a un proveedor nuevo: {name}", "ar": "أول دفعة كبيرة لمورّد جديد: {name}"},
        "message": {"en": "{amount} on {date}; supplier payments are usually about {typical}. Worth confirming the bank details.",
                    "fa": "{amount} در {date}؛ پرداخت به تأمین‌کنندگان معمولاً حدود {typical} است. بهتر است مشخصات بانکی تأیید شود.",
                    "es": "{amount} el {date}; los pagos a proveedores suelen ser de unos {typical}. Conviene confirmar los datos bancarios.",
                    "ar": "{amount} بتاريخ {date}؛ عادةً تكون دفعات الموردين نحو {typical}. يجدر تأكيد البيانات البنكية."},
    },
    "round_weekend": {
        "title": {"en": "{count} round-amount entries dated on a weekend", "fa": "{count} سند با مبلغ رُند در روز تعطیل",
                  "es": "{count} asientos de importe redondo con fecha de fin de semana",
                  "ar": "{count} قيود بمبالغ مدوّرة بتاريخ عطلة نهاية الأسبوع"},
        "message": {"en": "The largest is {largest} on {date}. Round, off-day entries are worth a second look.",
                    "fa": "بزرگ‌ترین {largest} در {date} است. سندهای رُند در روز تعطیل ارزش بازبینی دارند.",
                    "es": "El mayor es de {largest} el {date}. Los asientos redondos en días no laborables merecen otra mirada.",
                    "ar": "أكبرها {largest} بتاريخ {date}. القيود المدوّرة في أيام العطلة تستحق نظرة ثانية."},
    },
    "category_drift": {
        "title": {"en": "{account} is now {now}% of spending", "fa": "{account} اکنون {now}٪ هزینه‌هاست",
                  "es": "{account} es ahora el {now}% del gasto", "ar": "{account} صار {now}% من الإنفاق"},
        "message": {"en": "It was {before}% over the nine months before. Check the entries are coded to the right account.",
                    "fa": "در نه ماه قبل {before}٪ بود. بررسی کنید سندها به حساب درست زده شده باشند.",
                    "es": "Era el {before}% en los nueve meses anteriores. Comprueba que los asientos estén en la cuenta correcta.",
                    "ar": "كان {before}% خلال الأشهر التسعة السابقة. تحقّق من ترحيل القيود إلى الحساب الصحيح."},
    },
    "reversal_pattern": {
        "title": {"en": "Many reversals for {name}", "fa": "برگشت‌های پرتعداد برای {name}",
                  "es": "Muchas anulaciones para {name}", "ar": "انعكاسات كثيرة لـ {name}"},
        "message": {"en": "In 30 days: {undone} entries undone and {credits} credit notes. Worth checking what changed.",
                    "fa": "در ۳۰ روز: {undone} سند برگشت خورده و {credits} یادداشت بستانکار. بررسی کنید چه تغییری رخ داده.",
                    "es": "En 30 días: {undone} asientos anulados y {credits} notas de crédito. Conviene revisar qué cambió.",
                    "ar": "خلال 30 يومًا: {undone} قيود أُلغيت و{credits} إشعارات دائنة. يجدر التحقق مما تغيّر."},
    },
})
