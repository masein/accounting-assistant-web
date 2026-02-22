from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, timedelta

from app.services.reporting.common import ParsedRange, period_for_keyword


@dataclass
class ReportIntent:
    key: str
    from_date: date | None = None
    to_date: date | None = None
    account_code: str | None = None
    bank_name: str | None = None
    limit: int | None = None


def _normalize_text(text: str) -> str:
    t = (text or "").strip().lower()
    t = t.replace("ي", "ی").replace("ك", "ک")
    t = t.replace("\u200c", " ").replace("‌", " ")
    t = re.sub(r"\s+", " ", t)
    return t


def _cleanup_bank_name(name: str) -> str:
    raw = _normalize_text(name)
    raw = re.sub(r"[^\w\u0600-\u06FF\s\-]", " ", raw)
    raw = re.sub(r"\s+", " ", raw).strip()
    stop_words = {
        "هم",
        "میخوام",
        "می خواهم",
        "میخواهم",
        "می‌خوام",
        "مخوام",
        "رو",
        "را",
        "از",
        "برای",
        "در",
        "این",
        "ماه",
        "سال",
        "حساب",
        "گردش",
        "statement",
        "report",
        "for",
        "bank",
        "account",
        "ledger",
    }
    parts = [p for p in raw.split(" ") if p]
    while parts and parts[0] in stop_words:
        parts.pop(0)
    cleaned: list[str] = []
    for p in parts:
        if p in stop_words:
            break
        cleaned.append(p)
    out = " ".join(cleaned).strip()
    return out or (" ".join(parts).strip() if parts else raw)


def _extract_dates(text: str, today: date | None = None) -> tuple[date | None, date | None]:
    now = today or date.today()
    t = (text or "").strip().lower()

    # ISO explicit dates (one date = to_date)
    iso = re.findall(r"\b(\d{4}-\d{2}-\d{2})\b", t)
    if len(iso) >= 2:
        try:
            d1 = date.fromisoformat(iso[0])
            d2 = date.fromisoformat(iso[1])
            return (d1, d2) if d1 <= d2 else (d2, d1)
        except ValueError:
            pass
    if len(iso) == 1:
        try:
            d = date.fromisoformat(iso[0])
            return (None, d)
        except ValueError:
            pass

    for k in (
        "today",
        "امروز",
        "yesterday",
        "دیروز",
        "this month",
        "این ماه",
        "last month",
        "ماه قبل",
        "ماه گذشته",
        "this year",
        "امسال",
        "از اول سال",
        "last week",
        "هفته قبل",
        "هفته گذشته",
        "three months",
        "last 3 months",
        "سه ماه اخیر",
    ):
        p = period_for_keyword(k, today=now)
        if p and k in t:
            return p.from_date, p.to_date

    m_days = re.search(r"\blast\s+(\d{1,3})\s+days?\b", t)
    if m_days:
        n = max(1, int(m_days.group(1)))
        return now - timedelta(days=n), now
    m_months = re.search(r"\blast\s+(\d{1,2})\s+months?\b", t)
    if m_months:
        n = max(1, int(m_months.group(1)))
        return now - timedelta(days=30 * n), now
    return None, None


def _extract_account_code(text: str) -> str | None:
    m = re.search(r"\b(1[0-9]{3}|2[0-9]{3}|3[0-9]{3}|4[0-9]{3}|5[0-9]{3}|6[0-9]{3}|7[0-9]{3}|8[0-9]{3}|9[0-9]{3})\b", text or "")
    if m:
        return m.group(1)
    return None


def _extract_bank_name(text: str) -> str | None:
    raw = text or ""
    m_en2_all = list(re.finditer(r"\b([A-Za-z][A-Za-z0-9]{1,30})\s+bank\b", raw, re.IGNORECASE))
    if m_en2_all:
        return _cleanup_bank_name(m_en2_all[-1].group(1)).title()
    m_en = re.search(r"\b(?:bank)\s+([A-Za-z][A-Za-z0-9\s]{1,30})", raw, re.IGNORECASE)
    if m_en:
        return _cleanup_bank_name(m_en.group(1)).title()
    m_fa = re.search(r"بانک\s+([آ-یA-Za-z0-9\s]{1,30})", raw)
    if m_fa:
        return _cleanup_bank_name(m_fa.group(1))
    return None


def parse_report_intent(text: str, today: date | None = None) -> ReportIntent | None:
    t = (text or "").strip()
    if not t:
        return None
    low = _normalize_text(t)
    from_date, to_date = _extract_dates(low, today=today)
    account_code = _extract_account_code(t)
    bank_name = _extract_bank_name(t)

    # Financial statements
    if ("balance sheet" in low) or ("ترازنامه" in t):
        return ReportIntent(key="balance_sheet", from_date=from_date, to_date=to_date)
    if ("income statement" in low) or ("profit and loss" in low) or ("سود و زیان" in t):
        return ReportIntent(key="income_statement", from_date=from_date, to_date=to_date)
    if ("cash flow" in low) or ("جریان وجوه نقد" in t):
        return ReportIntent(key="cash_flow", from_date=from_date, to_date=to_date)

    # Books / ledgers
    if ("general journal" in low) or ("دفتر روزنامه" in t):
        return ReportIntent(key="general_journal", from_date=from_date, to_date=to_date)
    if ("general ledger" in low) or ("دفتر کل" in t):
        return ReportIntent(key="general_ledger", from_date=from_date, to_date=to_date)
    if ("trial balance" in low) or ("مرور حساب" in t):
        return ReportIntent(key="trial_balance", from_date=from_date, to_date=to_date)
    if (
        ("account ledger" in low)
        or ("گردش حساب" in t)
        or ("گردش بانک" in t)
        or ("صورت حساب بانک" in t)
        or ("statement between dates" in low)
        or (("bank statement" in low) or ("bank balance" in low))
        or (("گردش" in t) and ("بانک" in t))
    ):
        return ReportIntent(
            key="account_ledger",
            from_date=from_date,
            to_date=to_date,
            account_code=account_code,
            bank_name=bank_name,
        )

    # Inventory
    if (("inventory" in low) and ("movement" in low)) or ("گردش انبار" in t):
        return ReportIntent(key="inventory_movement", from_date=from_date, to_date=to_date)
    if (("inventory" in low) and ("balance" in low)) or ("موجودی انبار" in t):
        return ReportIntent(key="inventory_balance", from_date=from_date, to_date=to_date)

    # Sales / purchases
    if ("sales" in low or "فروش" in t) and (("product" in low) or ("کالا" in t) or ("تفکیک" in t)):
        return ReportIntent(key="sales_by_product", from_date=from_date, to_date=to_date)
    if ("sales" in low or "فروش" in t) and ("invoice" in low or "فاکتور" in t):
        return ReportIntent(key="sales_by_invoice", from_date=from_date, to_date=to_date)
    if ("purchase" in low or "خرید" in t) and (("product" in low) or ("کالا" in t) or ("تفکیک" in t)):
        return ReportIntent(key="purchase_by_product", from_date=from_date, to_date=to_date)
    if ("purchase" in low or "خرید" in t) and ("invoice" in low or "فاکتور" in t):
        return ReportIntent(key="purchase_by_invoice", from_date=from_date, to_date=to_date)

    # AR/AP
    if (
        "accounts receivable" in low
        or "accounts payable" in low
        or "debtor" in low
        or "creditor" in low
        or "بدهکار" in t
        or "بستانکار" in t
    ):
        return ReportIntent(key="debtor_creditor", from_date=from_date, to_date=to_date)

    # Convenient query aliases used in chat
    if ("last transaction" in low) or ("lates transaction" in low) or ("latest transaction" in low):
        return ReportIntent(key="general_journal", from_date=from_date, to_date=to_date, limit=1)
    return None
