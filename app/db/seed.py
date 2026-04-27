"""
Seed a minimal chart of accounts if the database has no accounts.

Two locale-specific charts are provided:

* ``SEED_ACCOUNTS`` — Iranian / Persian standard (groups + general accounts,
  4-digit Iranian-spec prefixes 11xx assets, 21xx liabilities, 31xx equity,
  41xx revenue, 6xxx expenses).
* ``UK_SEED_ACCOUNTS`` — Sage-style UK chart for FRS 102 Section 1A small
  companies (0xxx fixed assets, 1xxx current assets, 2xxx creditors,
  3xxx capital + reserves, 4xxx turnover, 5xxx cost of sales,
  7xxx overheads, 8xxx finance, 9xxx tax).

The ``seed_chart_if_empty`` helper picks the chart based on its ``locale``
argument (default = ``"ir"`` to preserve existing behavior).
"""
from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from app.models.account import Account, AccountLevel
from app.models.transaction_fee import PaymentMethod
from app.models.user import User
from app.core.auth import hash_password

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


# (code, name, level) — order matters: parents before children
SEED_ACCOUNTS = [
    # Groups (2-digit)
    ("11", "دارایی‌های جاری", AccountLevel.GROUP),
    ("12", "دارایی‌های غیرجاری", AccountLevel.GROUP),
    ("21", "بدهی‌های جاری", AccountLevel.GROUP),
    ("31", "حقوق مالکانه", AccountLevel.GROUP),
    ("41", "فروش و درآمدها", AccountLevel.GROUP),
    ("61", "هزینه‌های عملیاتی", AccountLevel.GROUP),
    ("62", "سایر هزینه‌ها و درآمدهای غیرعملیاتی", AccountLevel.GROUP),
    ("91", "حساب‌های انتظامی", AccountLevel.GROUP),
    # General (4-digit) — will link to parent by code prefix
    ("1110", "موجودی نقد و بانک", AccountLevel.GENERAL),
    ("1112", "حساب‌ها و اسناد دریافتنی تجاری", AccountLevel.GENERAL),
    ("1210", "دارایی‌های ثابت مشهود", AccountLevel.GENERAL),
    ("2110", "حساب‌ها و اسناد پرداختنی تجاری", AccountLevel.GENERAL),
    ("3110", "سرمایه", AccountLevel.GENERAL),
    ("4110", "فروش", AccountLevel.GENERAL),
    ("6110", "هزینه‌های حقوق و دستمزد", AccountLevel.GENERAL),
    ("6112", "سایر هزینه‌های عملیاتی", AccountLevel.GENERAL),
    ("6210", "هزینه‌های مالی", AccountLevel.GENERAL),
]


# UK chart (FRS 102 Section 1A). Sage-style 4-digit codes, group level uses
# the 1-digit prefix so the hierarchy lines up with the FRS 102 statement
# format (Companies Act 2006 Schedule 1).
UK_SEED_ACCOUNTS = [
    # Groups (1-digit)
    ("0", "Fixed assets", AccountLevel.GROUP),
    ("1", "Current assets", AccountLevel.GROUP),
    ("2", "Creditors and provisions", AccountLevel.GROUP),
    ("3", "Capital and reserves", AccountLevel.GROUP),
    ("4", "Turnover", AccountLevel.GROUP),
    ("5", "Cost of sales", AccountLevel.GROUP),
    ("7", "Overheads", AccountLevel.GROUP),
    ("8", "Finance and other charges", AccountLevel.GROUP),
    ("9", "Taxation", AccountLevel.GROUP),
    # Tangible fixed assets — code 00xx
    ("0010", "Plant and machinery — cost", AccountLevel.GENERAL),
    ("0011", "Plant and machinery — accumulated depreciation", AccountLevel.GENERAL),
    ("0020", "Office equipment — cost", AccountLevel.GENERAL),
    ("0021", "Office equipment — accumulated depreciation", AccountLevel.GENERAL),
    ("0030", "Motor vehicles — cost", AccountLevel.GENERAL),
    ("0031", "Motor vehicles — accumulated depreciation", AccountLevel.GENERAL),
    ("0040", "Land and buildings — cost", AccountLevel.GENERAL),
    ("0041", "Land and buildings — accumulated depreciation", AccountLevel.GENERAL),
    # Intangibles — code 01xx
    ("0100", "Goodwill — cost", AccountLevel.GENERAL),
    ("0101", "Goodwill — accumulated amortisation", AccountLevel.GENERAL),
    ("0110", "Other intangible assets — cost", AccountLevel.GENERAL),
    ("0111", "Other intangible assets — accumulated amortisation", AccountLevel.GENERAL),
    # Fixed-asset investments — code 02xx
    ("0200", "Fixed-asset investments", AccountLevel.GENERAL),
    # Current assets — code 1xxx
    ("1000", "Stocks", AccountLevel.GENERAL),
    ("1100", "Trade debtors", AccountLevel.GENERAL),
    ("1200", "Bank current account", AccountLevel.GENERAL),
    ("1210", "Bank deposit account", AccountLevel.GENERAL),
    ("1220", "Petty cash", AccountLevel.GENERAL),
    ("1300", "Prepayments and other debtors", AccountLevel.GENERAL),
    ("1400", "VAT receivable", AccountLevel.GENERAL),
    # Current liabilities — code 21xx-27xx (creditors due within one year)
    ("2100", "Trade creditors", AccountLevel.GENERAL),
    ("2200", "VAT payable", AccountLevel.GENERAL),
    ("2210", "PAYE / NIC payable", AccountLevel.GENERAL),
    ("2300", "Corporation tax payable", AccountLevel.GENERAL),
    ("2400", "Accruals and deferred income", AccountLevel.GENERAL),
    ("2500", "Bank overdraft", AccountLevel.GENERAL),
    ("2600", "Bank loan — current portion", AccountLevel.GENERAL),
    ("2700", "Other creditors", AccountLevel.GENERAL),
    # Non-current liabilities — code 28xx (creditors due after more than one year)
    ("2800", "Bank loan — long term", AccountLevel.GENERAL),
    ("2810", "Finance leases / hire purchase — long term", AccountLevel.GENERAL),
    ("2900", "Other long-term creditors", AccountLevel.GENERAL),
    # Provisions — code 295x
    ("2950", "Provisions for liabilities", AccountLevel.GENERAL),
    # Capital and reserves — code 3xxx
    ("3000", "Called up share capital", AccountLevel.GENERAL),
    ("3010", "Share premium account", AccountLevel.GENERAL),
    ("3020", "Revaluation reserve", AccountLevel.GENERAL),
    ("3030", "Other reserves", AccountLevel.GENERAL),
    ("3100", "Profit and loss account (retained earnings)", AccountLevel.GENERAL),
    # Turnover — code 4xxx
    ("4000", "Sales", AccountLevel.GENERAL),
    ("4100", "Sales returns", AccountLevel.GENERAL),
    ("4200", "Other operating income", AccountLevel.GENERAL),
    # Cost of sales — code 5xxx
    ("5000", "Purchases", AccountLevel.GENERAL),
    ("5100", "Direct labour", AccountLevel.GENERAL),
    ("5200", "Direct expenses", AccountLevel.GENERAL),
    ("5900", "Stock movement adjustment", AccountLevel.GENERAL),
    # Overheads — code 7xxx (split into distribution costs vs administrative)
    ("7000", "Distribution costs — wages", AccountLevel.GENERAL),
    ("7050", "Distribution costs — other", AccountLevel.GENERAL),
    ("7100", "Administrative wages and salaries", AccountLevel.GENERAL),
    ("7200", "Rent", AccountLevel.GENERAL),
    ("7300", "Light, heat and power", AccountLevel.GENERAL),
    ("7400", "Motor expenses", AccountLevel.GENERAL),
    ("7500", "Travel and entertainment", AccountLevel.GENERAL),
    ("7600", "Office expenses (printing, stationery, telephone)", AccountLevel.GENERAL),
    ("7700", "Repairs and maintenance", AccountLevel.GENERAL),
    ("7800", "Professional fees", AccountLevel.GENERAL),
    ("7850", "Sundry administrative expenses", AccountLevel.GENERAL),
    ("7900", "Bad debts written off", AccountLevel.GENERAL),
    # Finance — code 8xxx
    ("8000", "Bank charges", AccountLevel.GENERAL),
    ("8100", "Bank interest paid", AccountLevel.GENERAL),
    ("8200", "Loan interest paid", AccountLevel.GENERAL),
    ("8300", "Interest received", AccountLevel.GENERAL),
    ("8400", "Investment income / dividends received", AccountLevel.GENERAL),
    ("8500", "Depreciation expense", AccountLevel.GENERAL),
    ("8600", "Amortisation expense", AccountLevel.GENERAL),
    # Taxation — code 9xxx
    ("9000", "Corporation tax expense", AccountLevel.GENERAL),
    ("9100", "Deferred tax", AccountLevel.GENERAL),
]


def _parent_code_ir(code: str) -> str | None:
    """Iranian chart hierarchy: 1110 -> 11, 6112 -> 61."""
    if len(code) <= 2:
        return None
    return code[:2]


def _parent_code_uk(code: str) -> str | None:
    """UK chart hierarchy: 0010 -> 0, 7100 -> 7. The Sage chart uses a single
    leading digit as the major group; everything beneath rolls up to that."""
    if len(code) <= 1:
        return None
    return code[:1]


def seed_chart_if_empty(session: "Session", locale: str = "ir") -> int:
    """
    Insert seed accounts for the requested locale if the chart is empty.

    ``locale`` is a soft tag — the only real effect is which list is used.
    Returns the number of accounts inserted (0 when the chart was non-empty).
    """
    from sqlalchemy import func, select

    count = session.execute(select(func.count(Account.id))).scalar()
    if count > 0:
        return 0

    locale_norm = (locale or "ir").strip().lower()
    if locale_norm == "uk":
        seed_list = UK_SEED_ACCOUNTS
        parent_fn = _parent_code_uk
    else:
        seed_list = SEED_ACCOUNTS
        parent_fn = _parent_code_ir

    code_to_id: dict[str, uuid.UUID] = {}
    for code, name, level in seed_list:
        parent_id = None
        parent_code = parent_fn(code)
        if parent_code and parent_code in code_to_id:
            parent_id = code_to_id[parent_code]
        acc = Account(code=code, name=name, level=level, parent_id=parent_id)
        session.add(acc)
        session.flush()
        code_to_id[code] = acc.id
    session.commit()
    return len(seed_list)


def seed_payment_methods_if_empty(session: "Session") -> int:
    """
    Insert default payment methods if none exist.
    """
    from sqlalchemy import func, select

    count = session.execute(select(func.count(PaymentMethod.id))).scalar()
    if count > 0:
        return 0
    defaults = [
        ("paya", "Paya"),
        ("card_to_card", "Card-to-Card"),
        ("zaba", "Zaba"),
        ("satna", "Satna"),
        ("internal_transfer", "Internal Transfer"),
    ]
    for key, name in defaults:
        session.add(PaymentMethod(key=key, name=name, is_active=True))
    session.commit()
    return len(defaults)


def seed_admin_user_if_missing(session: "Session") -> int:
    """
    Ensure the default admin user exists for first login.
    """
    from sqlalchemy import func, select

    existing = (
        session.execute(select(User).where(func.lower(User.username) == "admin"))
        .scalars()
        .first()
    )
    if existing:
        return 0
    password_hash, password_salt = hash_password("admin")
    session.add(
        User(
            username="admin",
            password_hash=password_hash,
            password_salt=password_salt,
            preferred_language="en",
            is_admin=True,
            is_active=True,
        )
    )
    session.commit()
    return 1
