from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, timedelta

from app.services.reporting.common import ParsedRange, period_for_keyword
from app.utils.jalali import try_parse_jalali


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
        "balance",
        "current",
        "show",
        "the",
        "of",
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

    # --- Jalali numeric dates: 1404/11/27 or ۱۴۰۴/۱۱/۲۷ ---
    from app.utils.jalali import _to_ascii, jalali_to_gregorian
    ascii_t = _to_ascii(t)
    jalali_dates: list[date] = []
    for jm in re.finditer(r"\b(1[34]\d{2})[/\-](0?[1-9]|1[0-2])[/\-](0?[1-9]|[12]\d|3[01])\b", ascii_t):
        try:
            jalali_dates.append(jalali_to_gregorian(int(jm.group(1)), int(jm.group(2)), int(jm.group(3))))
        except ValueError:
            pass
    if not jalali_dates:
        for jm in re.finditer(r"\b(0?[1-9]|[12]\d|3[01])[/\-](0?[1-9]|1[0-2])[/\-](1[34]\d{2})\b", ascii_t):
            try:
                jalali_dates.append(jalali_to_gregorian(int(jm.group(3)), int(jm.group(2)), int(jm.group(1))))
            except ValueError:
                pass
    if len(jalali_dates) >= 2:
        jalali_dates.sort()
        return jalali_dates[0], jalali_dates[-1]
    if len(jalali_dates) == 1:
        return (None, jalali_dates[0])

    # --- Jalali month name: "27 بهمن 1404" or "بهمن 1404" ---
    jd = try_parse_jalali(text)
    if jd:
        return (None, jd)

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


_NOT_A_BANK = {"the", "a", "an", "my", "our", "your", "this", "that", "in", "on", "at", "to", "from", "with", "for", "is", "it"}


def _extract_bank_name(text: str) -> str | None:
    raw = text or ""
    m_en2_all = list(re.finditer(r"\b([A-Za-z][A-Za-z0-9]{1,30})\s+bank\b", raw, re.IGNORECASE))
    if m_en2_all:
        name = _cleanup_bank_name(m_en2_all[-1].group(1)).title()
        if name.lower() not in _NOT_A_BANK:
            return name
    m_en = re.search(r"\b(?:bank)\s+([A-Za-z][A-Za-z0-9\s]{1,30})", raw, re.IGNORECASE)
    if m_en:
        name = _cleanup_bank_name(m_en.group(1)).title()
        if name.lower() not in _NOT_A_BANK:
            return name
    m_fa = re.search(r"بانک\s+([آ-یA-Za-z0-9\s]{1,30})", raw)
    if m_fa:
        name = _cleanup_bank_name(m_fa.group(1))
        if name.lower() not in _NOT_A_BANK:
            return name
    return None


def _extract_limit(text: str) -> int | None:
    raw = text or ""
    m = re.search(r"\b(?:last|latest)\s+(\d{1,3})\b", raw, re.IGNORECASE)
    if not m:
        m = re.search(r"\b(\d{1,3})\s+(?:latest|last)\b", raw, re.IGNORECASE)
    if m:
        try:
            n = int(m.group(1))
            if n > 0:
                return n
        except ValueError:
            return None
    return None


def parse_report_intent(text: str, today: date | None = None) -> ReportIntent | None:
    t = (text or "").strip()
    if not t:
        return None
    low = _normalize_text(t)
    from_date, to_date = _extract_dates(low, today=today)
    account_code = _extract_account_code(t)
    bank_name = _extract_bank_name(t)
    limit = _extract_limit(t)

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

    # "balance of mellat bank", "current balance mellat bank", "show me the balance"
    if "balance" in low and bank_name:
        return ReportIntent(
            key="account_ledger",
            from_date=from_date,
            to_date=to_date,
            account_code=account_code,
            bank_name=bank_name,
        )
    # Bare "current balance" / "what is the balance" / "how much do i have" without bank
    is_balance_query = (
        (re.search(r"\b(?:current\s+)?balance\b", low) and "inventory" not in low and "trial" not in low)
        or re.search(r"\bhow much\b.*\b(?:money|have|bank|cash|in the bank)\b", low)
        or re.search(r"\b(?:whats?|what'?s)\s+my\s+(?:cash|money|balance)\b", low)
        or re.search(r"\btotal\s+(?:money|cash|balance)\b", low)
    )
    if is_balance_query and not bank_name:
        return ReportIntent(
            key="account_ledger",
            from_date=from_date,
            to_date=to_date,
            account_code=account_code or "1110",
        )

    # "who owes me" / "how much i owe" → debtor/creditor
    if re.search(r"\b(?:who\s+owes?|owes?\s+me|owe\s+to|how much\s+(?:do\s+)?i\s+owe)\b", low):
        return ReportIntent(key="debtor_creditor", from_date=from_date, to_date=to_date)

    # "expenses this month" / "what did i spend" → income statement (shows expense breakdown)
    if re.search(r"\b(?:expenses?|spending|spent|spend)\b", low) and re.search(r"\b(?:this|last|show|what|how much|my)\b", low):
        return ReportIntent(key="income_statement", from_date=from_date, to_date=to_date)

    # "revenue this month" / "how much did i earn/make"
    if re.search(r"\b(?:revenue|income|earn(?:ed|ings?)?|mak(?:e|ing)|sold)\b", low) and re.search(r"\b(?:this|last|show|what|how much|my|total)\b", low):
        if "income statement" not in low:
            return ReportIntent(key="income_statement", from_date=from_date, to_date=to_date)

    # Bank transaction listing aliases (e.g. "show me 10 latest transactions of Mellat bank")
    if bank_name and (
        ("transaction" in low)
        or ("transactions" in low)
        or ("txns" in low)
        or ("trxn" in low)
        or ("تراکنش" in t)
        or ("آخرین" in t)
    ):
        effective_from = from_date
        effective_to = to_date
        if limit and (from_date is None and to_date is None):
            # For "latest N" queries with no explicit period, search all-time.
            effective_from = date(1900, 1, 1)
        return ReportIntent(
            key="account_ledger",
            from_date=effective_from,
            to_date=effective_to,
            account_code=account_code,
            bank_name=bank_name,
            limit=limit or 10,
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

    # "transactions on <date>" without bank → general journal for that date
    if (
        ("transaction" in low or "transactions" in low or "تراکنش" in t)
        and not bank_name
        and (from_date is not None or to_date is not None)
    ):
        return ReportIntent(key="general_journal", from_date=from_date, to_date=to_date)

    # Convenient query aliases used in chat
    if (("last transaction" in low) or ("lates transaction" in low) or ("latest transaction" in low)) and not bank_name:
        effective_from = from_date
        effective_to = to_date
        if from_date is None and to_date is None:
            effective_from = date(1900, 1, 1)
        return ReportIntent(key="general_journal", from_date=effective_from, to_date=effective_to, limit=1)
    return None
