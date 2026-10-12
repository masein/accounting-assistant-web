"""Which balance-sheet line an account belongs to, when its code can't say.

The Iranian statements place accounts by code prefix, written for the seeded
chart (4-digit codes) and the standard's 3-digit groups. A chart migrated from
Sepidar numbers otherwise: 2-digit groups, 4-digit ledgers (کل), 6-digit
sub-ledgers (معین). A real company's prepayments (1118xx) read as cash, a bank
and its deposits (1113, 1114xx) as receivables, its software (1212xx) as
fixed assets, advances received (2112xx) as payables and its accumulated
profit and loss (3113xx, 3114xx) as capital (2026-10-12).

So a migration names each ledger's line from its title (``infer``), stored on
the account (``Account.statement_line``), and a report takes an account's own
line or its nearest ancestor's (``line_map``) before falling back to the
prefixes. A bank's own account is cash whatever its code. The seeded chart
carries no lines, so its statements don't move.
"""
from __future__ import annotations

import re

from sqlalchemy import select
from sqlalchemy.orm import Session

# A line's key says its section.
_SECTIONS = (("ncl_", "non_current_liabilities"), ("nca_", "non_current_assets"),
             ("ca_", "current_assets"), ("cl_", "current_liabilities"), ("eq_", "equity"))

LINES = (
    "ca_cash", "ca_st_investments", "ca_trade_receivables", "ca_inventory", "ca_prepayments",
    "ca_held_for_sale", "ca_other",
    "nca_ppe", "nca_investment_property", "nca_intangibles", "nca_lt_investments", "nca_lt_receivables",
    "nca_deferred_tax", "nca_other",
    "cl_trade_payables", "cl_tax_payable", "cl_dividends_payable", "cl_st_loans", "cl_provisions",
    "cl_advances", "cl_held_for_sale_liab", "cl_other",
    "ncl_lt_payables", "ncl_lt_loans", "ncl_deferred_tax", "ncl_employee_benefits", "ncl_other",
    "eq_capital", "eq_capital_increase", "eq_share_premium", "eq_treasury_premium", "eq_legal_reserve",
    "eq_other_reserves", "eq_revaluation_surplus", "eq_fx_translation", "eq_retained_earnings",
    "eq_treasury_stock", "eq_other",
)


def section_of(line: str) -> str | None:
    for prefix, section in _SECTIONS:
        if line.startswith(prefix):
            return section
    return None


def _squash(title: str) -> str:
    """Letters only, one spelling: ي/ی, ك/ک, ة/ه, ۀ/ه, no spaces, ZWNJ or
    punctuation — «پيش‌ پرداخت‌ها» and «پیش پرداختها» are one word."""
    s = (title or "").replace("ي", "ی").replace("ك", "ک").replace("ى", "ی").replace("ۀ", "ه").replace("ة", "ه")
    s = s.replace("أ", "ا").replace("إ", "ا").replace("آ", "ا")
    return re.sub(r"[\s‌‏‎ـ()\-–_/.,،]+", "", s)


def _has(s: str, *words: str) -> bool:
    return all(w in s for w in words)


def _any(s: str, *words: str) -> bool:
    return any(w in s for w in words)


def infer(code: str, title: str) -> str | None:
    """The balance-sheet line a chart row's title names, within the section
    its code's first digit gives (1 assets, 2 liabilities, 3 equity); None
    when the title says nothing a rule knows, so the account takes its
    parent's line or the code prefixes. Long-term wins over current."""
    head = (code or "").strip()[:1]
    s = _squash(title)
    if not s:
        return None
    long_term = _any(s, "بلندمدت", "غیرجاری")
    if head == "1":
        if _any(s, "نامشهود", "نرمافزار", "سرقفلی", "حقامتیاز"):
            return "nca_intangibles"               # software and its amortization too
        if _any(s, "استهلاک", "داراییثابت", "داراییهایثابت", "ثابتمشهود", "اموال", "ماشینالات", "اثاثیه", "ساختمان", "زمین", "وسایطنقلیه"):
            return "nca_ppe"
        if _any(s, "سرمایهگذاری"):
            return "nca_lt_investments" if long_term else "ca_st_investments"
        if _any(s, "پیشپرداخت", "سفارشات", "سفارش"):
            return "ca_prepayments"
        if _any(s, "سپرده"):
            return None if long_term else "ca_st_investments"   # a guarantee deposit takes its parent's line
        if _any(s, "دریافتنی", "بدهکاران", "مطالبات"):
            return "nca_lt_receivables" if long_term else "ca_trade_receivables"
        if _has(s, "موجودی") and _any(s, "کالا", "مواد", "انبار"):
            return "ca_inventory"
        if _any(s, "نقد", "صندوق", "تنخواه", "بانک"):
            return "ca_cash"
        return None
    if head == "2":
        if _any(s, "پیشدریافت"):
            return "cl_advances"
        if _any(s, "سودسهام"):
            return "cl_dividends_payable"
        if _any(s, "پایانخدمت", "بازخرید", "مزایایپایان"):
            return "ncl_employee_benefits"
        if _any(s, "تسهیلات", "وام"):
            return "ncl_lt_loans" if long_term else "cl_st_loans"
        if _any(s, "ذخیرهمالیات", "مالیاتعملکرد", "مالیاتبردرآمد"):
            return "cl_tax_payable"
        if _any(s, "ذخیره"):
            return "cl_provisions"
        if _any(s, "پرداختنی", "بستانکاران"):
            return "ncl_lt_payables" if long_term else "cl_trade_payables"
        return None
    if head == "3":
        if _any(s, "افزایشسرمایه"):
            return "eq_capital_increase"
        if _any(s, "صرفسهام"):
            return "eq_share_premium"
        if _any(s, "سهامخزانه"):
            return "eq_treasury_stock"
        if _any(s, "اندوختهقانونی"):
            return "eq_legal_reserve"
        if _any(s, "تجدیدارزیابی"):
            return "eq_revaluation_surplus"
        if _any(s, "تسعیر"):
            return "eq_fx_translation"
        if _any(s, "اندوخته"):
            return "eq_other_reserves"
        if _any(s, "سودوزیان", "سودزیان", "زیانانباشته", "سودانباشته"):
            return "eq_retained_earnings"           # accumulated and the current year's, not yet closed
        if _any(s, "سرمایه"):
            return "eq_capital"
        return None
    return None


def line_map(db: Session) -> dict[str, tuple[str, str]]:
    """code → (section, line) for every account with a line of its own or an
    ancestor's, and every bank's own account (cash). Accounts without one are
    left to the code prefixes."""
    from app.models.account import Account
    from app.models.entity import Entity

    rows = db.execute(select(Account.id, Account.code, Account.parent_id, Account.statement_line)).all()
    by_id = {r.id: r for r in rows}
    banks = {(c or "").strip() for (c,) in db.execute(select(Entity.code).where(Entity.type == "bank")).all()}
    out: dict[str, tuple[str, str]] = {}
    for r in rows:
        line, cur, hops = None, r, 0
        while cur is not None and hops < 12:
            if cur.statement_line:
                line = cur.statement_line
                break
            cur = by_id.get(cur.parent_id) if cur.parent_id else None
            hops += 1
        if r.code in banks:
            line = "ca_cash"
        section = section_of(line) if line else None
        if section:
            out[r.code] = (section, line)
    return out
