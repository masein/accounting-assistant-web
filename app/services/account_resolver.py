"""Locale-aware resolution of the canonical posting accounts used by the
AR/AP layer (invoice recognition, payments, credit notes).

The chart of accounts uses different codes per reporting locale (UK Sage vs
the Iranian standard). Rather than hardcode 1110/4110/6112 — which only exist
in the Iranian chart — endpoints resolve a *category* ("ar", "bank", …) to
the code for the active locale, mirroring the alias map ``search_accounts``
uses. The resolved code is verified to exist in the seeded chart; if the
locale's preferred code is missing we fall back to the other locales' codes,
then raise a clear error so a posting never silently picks the wrong account.
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.account import Account
from app.services.locale_service import get_reporting_locale

# category → per-locale account code. Codes match the seeded charts
# (app/db/seed.py) and the search_accounts alias map.
POSTING_CODES: dict[str, dict[str, str]] = {
    "uk": {
        "ar": "1100",               # Trade debtors
        "ap": "2100",               # Trade creditors
        "bank": "1200",             # Bank current account
        "revenue": "4000",          # Sales
        "expense": "5000",          # Purchases / cost of sales
        "sales_returns": "4100",    # Sales returns (contra-revenue)
        "customer_credit": "2150",  # Customer credits and deposits (liability)
        "supplier_advance": "1500", # Supplier prepayments and advances (asset)
        "vat_output": "2200",       # VAT payable (output tax on sales) — liability
        "vat_input": "1400",        # VAT receivable (input tax on purchases) — asset
        "accrued_liability": "2400",        # Accruals and deferred income — liability
        "accrued_income": "1410",           # Accrued income — asset
        "prepaid_expense": "1300",          # Prepayments and other debtors — asset
        "depreciation_expense": "8500",     # Depreciation expense
        "accumulated_depreciation": "0090", # Accumulated depreciation — contra-asset
        "bank_fee": "8000",                 # Bank charges
        "interest_income": "8300",          # Interest received
        "wages_expense": "7100",            # Administrative wages and salaries (gross pay)
        "paye_payable": "2211",             # PAYE income tax withheld — liability
        "social_security_payable": "2212",  # National Insurance payable — liability
        "net_pay_payable": "2250",          # Net wages payable — liability
        "payroll_deductions_payable": "2260",  # Pension/other deductions payable — liability
    },
    "ir": {
        "ar": "1112",
        "ap": "2110",
        "bank": "1110",
        "revenue": "4110",
        "expense": "6112",
        "sales_returns": "4110",    # no separate returns account → revenue
        "customer_credit": "2120",  # پیش‌دریافت از مشتریان
        "supplier_advance": "1120", # پیش‌پرداخت به تأمین‌کنندگان
        "vat_output": "2130",       # مالیات بر ارزش افزوده پرداختنی — liability
        "vat_input": "1130",        # مالیات بر ارزش افزوده دریافتنی — asset
        "accrued_liability": "2140",        # هزینه‌های تعهدشده پرداختنی — liability
        "accrued_income": "1140",           # درآمد تعهدشده دریافتنی — asset
        "prepaid_expense": "1150",          # پیش‌پرداخت هزینه‌ها — asset
        "depreciation_expense": "6120",     # هزینه استهلاک
        "accumulated_depreciation": "1219", # استهلاک انباشته — contra-asset
        "bank_fee": "6210",                 # هزینه‌های مالی (bank charges)
        "interest_income": "4120",          # درآمد سود (interest income)
        "wages_expense": "6110",            # حقوق و دستمزد (gross pay)
        "paye_payable": "2160",             # مالیات حقوق پرداختنی — liability
        "social_security_payable": "2170",  # بیمه تأمین اجتماعی پرداختنی — liability
        "net_pay_payable": "2180",          # حقوق پرداختنی — liability
        "payroll_deductions_payable": "2190",  # کسورات حقوق پرداختنی — liability
    },
}
# default chart tries Iranian codes first (the historical default), then UK.
POSTING_CODES["default"] = dict(POSTING_CODES["ir"])

# Names for auto-creating a posting account that's missing from an older chart
# (these accounts were added in a later release, so charts seeded before it
# lack them). Names match app/db/seed.py.
POSTING_NAMES: dict[str, dict[str, str]] = {
    "uk": {
        "ar": "Trade debtors",
        "ap": "Trade creditors",
        "bank": "Bank current account",
        "revenue": "Sales",
        "expense": "Purchases",
        "sales_returns": "Sales returns",
        "customer_credit": "Customer credits and deposits",
        "supplier_advance": "Supplier prepayments and advances",
        "vat_output": "VAT payable",
        "vat_input": "VAT receivable",
        "accrued_liability": "Accruals and deferred income",
        "accrued_income": "Accrued income",
        "prepaid_expense": "Prepayments and other debtors",
        "depreciation_expense": "Depreciation expense",
        "accumulated_depreciation": "Accumulated depreciation",
        "bank_fee": "Bank charges",
        "interest_income": "Interest received",
        "wages_expense": "Administrative wages and salaries",
        "paye_payable": "PAYE income tax payable",
        "social_security_payable": "National Insurance payable",
        "net_pay_payable": "Net wages payable",
        "payroll_deductions_payable": "Payroll deductions payable",
    },
    "ir": {
        "ar": "حساب‌ها و اسناد دریافتنی تجاری",
        "ap": "حساب‌ها و اسناد پرداختنی تجاری",
        "bank": "موجودی نقد و بانک",
        "revenue": "فروش",
        "expense": "سایر هزینه‌های عملیاتی",
        "sales_returns": "فروش",
        "customer_credit": "پیش‌دریافت از مشتریان",
        "supplier_advance": "پیش‌پرداخت به تأمین‌کنندگان",
        "vat_output": "مالیات بر ارزش افزوده پرداختنی",
        "vat_input": "مالیات بر ارزش افزوده دریافتنی",
        "accrued_liability": "هزینه‌های تعهدشده پرداختنی",
        "accrued_income": "درآمد تعهدشده دریافتنی",
        "prepaid_expense": "پیش‌پرداخت هزینه‌ها",
        "depreciation_expense": "هزینه استهلاک",
        "accumulated_depreciation": "استهلاک انباشته",
        "bank_fee": "هزینه‌های مالی",
        "interest_income": "درآمد سود",
        "wages_expense": "حقوق و دستمزد",
        "paye_payable": "مالیات حقوق پرداختنی",
        "social_security_payable": "بیمه تأمین اجتماعی پرداختنی",
        "net_pay_payable": "حقوق پرداختنی",
        "payroll_deductions_payable": "کسورات حقوق پرداختنی",
    },
}
POSTING_NAMES["default"] = dict(POSTING_NAMES["ir"])

# Order in which to fall back when the active locale lacks an account.
_FALLBACK_ORDER = ("ir", "uk")


class AccountResolutionError(Exception):
    """No account matches the requested category and it couldn't be created."""


def _code_exists(db: Session, code: str) -> bool:
    return db.execute(select(Account.id).where(Account.code == code)).first() is not None


def _ensure_account(db: Session, code: str, name: str, locale: str) -> str:
    """Create the posting account if it's missing (self-heals a chart seeded
    before this account existed), linking it to its group parent by code
    prefix. Returns the code."""
    if _code_exists(db, code):
        return code
    from app.db.seed import _parent_code_ir, _parent_code_uk
    from app.models.account import AccountLevel

    parent_fn = _parent_code_uk if locale == "uk" else _parent_code_ir
    parent_code = parent_fn(code)
    parent = (
        db.execute(select(Account).where(Account.code == parent_code)).scalars().first()
        if parent_code
        else None
    )
    db.add(Account(code=code, name=name, level=AccountLevel.GENERAL,
                   parent_id=(parent.id if parent else None)))
    db.flush()
    return code


def resolve_account_code(db: Session, category: str, *, locale: str | None = None) -> str:
    """Return the chart code for a posting ``category`` in the active locale.

    Verifies the code exists; falls back to the other locales' code for the
    same category. If none exist (an older chart predating the account), the
    locale's preferred account is auto-created rather than failing a posting.
    Raises ``AccountResolutionError`` only for an unknown category.
    """
    cat = category.strip().lower()
    loc = (locale or get_reporting_locale(db) or "default").strip().lower()
    table = POSTING_CODES.get(loc, POSTING_CODES["default"])
    if cat not in table:
        raise AccountResolutionError(f"Unknown posting category: {category!r}")

    # Preferred code for this locale, then cross-locale fallbacks.
    candidates: list[str] = [table[cat]]
    for fb in _FALLBACK_ORDER:
        c = POSTING_CODES[fb].get(cat)
        if c and c not in candidates:
            candidates.append(c)

    for code in candidates:
        if _code_exists(db, code):
            return code

    # Self-heal: create the locale's preferred account for this category.
    name = POSTING_NAMES.get(loc, POSTING_NAMES["default"]).get(cat)
    if name:
        return _ensure_account(db, table[cat], name, loc)
    raise AccountResolutionError(
        f"No account found for category {category!r} (tried {candidates})."
    )


def resolve_posting_accounts(db: Session, *, locale: str | None = None) -> dict[str, str]:
    """All posting categories resolved to codes for the active locale. Skips
    categories whose account isn't in the chart rather than raising, so a
    caller can use what's available."""
    loc = (locale or get_reporting_locale(db) or "default").strip().lower()
    out: dict[str, str] = {}
    for cat in POSTING_CODES.get(loc, POSTING_CODES["default"]):
        try:
            out[cat] = resolve_account_code(db, cat, locale=loc)
        except AccountResolutionError:
            continue
    return out
