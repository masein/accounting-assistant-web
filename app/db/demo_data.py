"""Demo data seeders — produce believable financial statements across two
fiscal years for both Iranian and UK locales. Used by the admin
"reset and load demo" endpoint so the user can flip the locale toggle and
show a populated set of statements to others.

Both seeders post journal entries that build a small, internally-consistent
trading business: capital injection, fixed-asset purchase, borrowings,
sales, COGS, payroll, finance costs, tax, and (in year 2) a dividend
declaration. Numbers are calibrated so each statement reconciles and tells
a coherent profit-growth story.
"""
from __future__ import annotations

from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.account import Account, AccountLevel
from app.models.transaction import Transaction, TransactionLine


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _ensure_account(
    session: Session, code: str, name: str, level: AccountLevel = AccountLevel.GENERAL,
) -> Account:
    existing = session.execute(select(Account).where(Account.code == code)).scalar_one_or_none()
    if existing:
        return existing
    parent_id = None
    parent_code = code[:2] if len(code) > 2 else (code[:1] if len(code) > 1 else None)
    if parent_code:
        parent = session.execute(select(Account).where(Account.code == parent_code)).scalar_one_or_none()
        if parent:
            parent_id = parent.id
    acc = Account(code=code, name=name, level=level, parent_id=parent_id)
    session.add(acc)
    session.flush()
    return acc


def _post(
    session: Session, txn_date: date, description: str,
    lines: list[tuple[str, int, int]], *, currency: str = "IRR",
) -> Transaction:
    """Post a balanced journal entry. ``lines`` is a list of
    ``(account_code, debit, credit)``; debits and credits must sum to the
    same total."""
    total_dr = sum(dr for _, dr, _ in lines)
    total_cr = sum(cr for _, _, cr in lines)
    if total_dr != total_cr:
        raise ValueError(f"Unbalanced demo entry on {txn_date}: dr={total_dr}, cr={total_cr}")
    txn = Transaction(date=txn_date, description=description, currency=currency)
    session.add(txn)
    session.flush()
    for code, dr, cr in lines:
        acc = session.execute(select(Account).where(Account.code == code)).scalar_one_or_none()
        if acc is None:
            raise ValueError(f"Account {code!r} missing for demo entry on {txn_date}")
        session.add(TransactionLine(transaction_id=txn.id, account_id=acc.id, debit=dr, credit=cr))
    session.flush()
    return txn


# ---------------------------------------------------------------------------
# Iran demo — IRR amounts
# ---------------------------------------------------------------------------
#
# Story: a small Iranian trading company. Year 1 (2024) is the start-up
# year — capital, PP&E purchase, long-term bank loan, two sales cycles,
# wages, opex, interest, tax accrual. Year 2 (2025) doubles activity —
# more PP&E, partial loan repayment, tax paid, dividend declared.

_IR_EXTRA_ACCOUNTS = [
    # Iranian-spec extras the demo needs but the seed leaves out.
    ("114", "موجودی مواد و کالا", AccountLevel.GROUP),
    ("1140", "موجودی مواد و کالا", AccountLevel.GENERAL),
    ("213", "مالیات پرداختنی", AccountLevel.GROUP),
    ("2130", "مالیات پرداختنی", AccountLevel.GENERAL),
    ("214", "سود سهام پرداختنی", AccountLevel.GROUP),
    ("2140", "سود سهام پرداختنی", AccountLevel.GENERAL),
    ("215", "تسهیلات مالی کوتاه‌مدت", AccountLevel.GROUP),
    ("2150", "تسهیلات مالی کوتاه‌مدت", AccountLevel.GENERAL),
    ("22", "بدهی‌های غیرجاری", AccountLevel.GROUP),
    ("222", "تسهیلات مالی بلندمدت", AccountLevel.GROUP),
    ("2220", "تسهیلات مالی بلندمدت", AccountLevel.GENERAL),
    ("32", "اندوخته‌ها", AccountLevel.GROUP),
    ("321", "اندوخته قانونی", AccountLevel.GROUP),
    ("3210", "اندوخته قانونی", AccountLevel.GENERAL),
    ("33", "سود (زیان) انباشته", AccountLevel.GROUP),
    ("3300", "سود (زیان) انباشته", AccountLevel.GENERAL),
    ("51", "بهای تمام شده درآمدهای عملیاتی", AccountLevel.GROUP),
    ("5110", "بهای تمام شده فروش", AccountLevel.GENERAL),
    ("641", "هزینه مالیات سال جاری", AccountLevel.GROUP),
    ("6410", "هزینه مالیات سال جاری", AccountLevel.GENERAL),
]


def seed_iran_demo(session: Session) -> int:
    """Post the Iranian demo journal entries. Returns number of entries posted."""
    for code, name, level in _IR_EXTRA_ACCOUNTS:
        _ensure_account(session, code, name, level)

    M = 1_000_000  # million-rial multiplier

    entries: list[tuple[date, str, list[tuple[str, int, int]]]] = [
        # ----- Year 1 (2024): start-up + initial trading -----
        (date(2024, 1, 15), "Initial capital injection",
         [("1110", 5_000 * M, 0), ("3110", 0, 5_000 * M)]),
        (date(2024, 2, 1), "Purchase plant and machinery",
         [("1210", 800 * M, 0), ("1110", 0, 800 * M)]),
        (date(2024, 3, 1), "Long-term bank loan drawdown",
         [("1110", 2_000 * M, 0), ("2220", 0, 2_000 * M)]),
        (date(2024, 4, 30), "Q2 sales — cash receipt",
         [("1110", 1_500 * M, 0), ("4110", 0, 1_500 * M)]),
        (date(2024, 4, 30), "Q2 cost of sales",
         [("5110", 800 * M, 0), ("1110", 0, 800 * M)]),
        (date(2024, 6, 30), "H1 wages and salaries",
         [("6110", 400 * M, 0), ("1110", 0, 400 * M)]),
        (date(2024, 8, 31), "Other operating expenses",
         [("6112", 200 * M, 0), ("1110", 0, 200 * M)]),
        (date(2024, 10, 15), "Q4 sales — cash receipt",
         [("1110", 2_000 * M, 0), ("4110", 0, 2_000 * M)]),
        (date(2024, 10, 15), "Q4 cost of sales",
         [("5110", 1_000 * M, 0), ("1110", 0, 1_000 * M)]),
        (date(2024, 12, 15), "H2 wages and salaries",
         [("6110", 400 * M, 0), ("1110", 0, 400 * M)]),
        (date(2024, 12, 15), "Finance expense — interest paid",
         [("6210", 150 * M, 0), ("1110", 0, 150 * M)]),
        (date(2024, 12, 25), "FY 2024 corporation-tax accrual",
         [("6410", 200 * M, 0), ("2130", 0, 200 * M)]),
        # ----- Year 2 (2025): expansion + dividend -----
        (date(2025, 2, 1), "Additional plant and machinery",
         [("1210", 500 * M, 0), ("1110", 0, 500 * M)]),
        (date(2025, 4, 15), "Long-term loan principal repayment",
         [("2220", 500 * M, 0), ("1110", 0, 500 * M)]),
        (date(2025, 4, 30), "Q2 sales — cash receipt",
         [("1110", 3_000 * M, 0), ("4110", 0, 3_000 * M)]),
        (date(2025, 4, 30), "Q2 cost of sales",
         [("5110", 1_500 * M, 0), ("1110", 0, 1_500 * M)]),
        (date(2025, 6, 30), "H1 wages and salaries",
         [("6110", 500 * M, 0), ("1110", 0, 500 * M)]),
        (date(2025, 8, 31), "Other operating expenses",
         [("6112", 250 * M, 0), ("1110", 0, 250 * M)]),
        (date(2025, 10, 15), "Q4 sales — cash receipt",
         [("1110", 4_000 * M, 0), ("4110", 0, 4_000 * M)]),
        (date(2025, 10, 15), "Q4 cost of sales",
         [("5110", 2_000 * M, 0), ("1110", 0, 2_000 * M)]),
        (date(2025, 11, 30), "Pay FY 2024 corporation tax",
         [("2130", 200 * M, 0), ("1110", 0, 200 * M)]),
        (date(2025, 12, 15), "H2 wages and salaries",
         [("6110", 500 * M, 0), ("1110", 0, 500 * M)]),
        (date(2025, 12, 15), "Finance expense — interest paid",
         [("6210", 130 * M, 0), ("1110", 0, 130 * M)]),
        (date(2025, 12, 20), "Dividend declared (payable)",
         [("3300", 100 * M, 0), ("2140", 0, 100 * M)]),
        (date(2025, 12, 25), "FY 2025 corporation-tax accrual",
         [("6410", 350 * M, 0), ("2130", 0, 350 * M)]),
    ]

    for txn_date, desc, lines in entries:
        _post(session, txn_date, desc, lines, currency="IRR")

    session.commit()
    return len(entries)


# ---------------------------------------------------------------------------
# UK demo — GBP amounts (whole £)
# ---------------------------------------------------------------------------
#
# Story: a small UK limited company. Year 1 (2024) is the start-up year
# — capital, plant purchase, bank loan, two sales cycles, payroll split
# between distribution and admin, rent, power, finance costs, tax accrual.
# Year 2 (2025) doubles activity — additional equipment, loan repayment,
# tax paid, dividend declared.


def seed_uk_demo(session: Session) -> int:
    entries: list[tuple[date, str, list[tuple[str, int, int]]]] = [
        # ----- Year 1 (2024): start-up + initial trading -----
        (date(2024, 1, 15), "Issue of share capital",
         [("1200", 100_000, 0), ("3000", 0, 100_000)]),
        (date(2024, 2, 1), "Purchase plant and machinery",
         [("0010", 30_000, 0), ("1200", 0, 30_000)]),
        (date(2024, 3, 1), "Bank loan drawdown",
         [("1200", 40_000, 0), ("2800", 0, 40_000)]),
        (date(2024, 4, 30), "Q2 sales — cash receipt",
         [("1200", 35_000, 0), ("4000", 0, 35_000)]),
        (date(2024, 4, 30), "Q2 purchases (cost of sales)",
         [("5000", 18_000, 0), ("1200", 0, 18_000)]),
        (date(2024, 6, 30), "H1 admin wages",
         [("7100", 12_000, 0), ("1200", 0, 12_000)]),
        (date(2024, 6, 30), "H1 distribution wages",
         [("7000", 6_000, 0), ("1200", 0, 6_000)]),
        (date(2024, 7, 1), "Annual rent",
         [("7200", 8_000, 0), ("1200", 0, 8_000)]),
        (date(2024, 8, 31), "Light, heat and power",
         [("7300", 3_000, 0), ("1200", 0, 3_000)]),
        (date(2024, 10, 15), "Q4 sales — cash receipt",
         [("1200", 45_000, 0), ("4000", 0, 45_000)]),
        (date(2024, 10, 15), "Q4 purchases (cost of sales)",
         [("5000", 22_000, 0), ("1200", 0, 22_000)]),
        (date(2024, 12, 1), "H2 admin wages",
         [("7100", 12_000, 0), ("1200", 0, 12_000)]),
        (date(2024, 12, 1), "H2 distribution wages",
         [("7000", 6_000, 0), ("1200", 0, 6_000)]),
        (date(2024, 12, 15), "Loan interest paid",
         [("8200", 2_500, 0), ("1200", 0, 2_500)]),
        (date(2024, 12, 15), "Bank charges",
         [("8000", 500, 0), ("1200", 0, 500)]),
        (date(2024, 12, 25), "FY 2024 corporation-tax accrual",
         [("9000", 4_000, 0), ("2300", 0, 4_000)]),
        # ----- Year 2 (2025): expansion + dividend -----
        (date(2025, 2, 1), "Additional plant and machinery",
         [("0010", 15_000, 0), ("1200", 0, 15_000)]),
        (date(2025, 4, 15), "Loan principal repayment",
         [("2800", 8_000, 0), ("1200", 0, 8_000)]),
        (date(2025, 4, 30), "Q2 sales — cash receipt",
         [("1200", 70_000, 0), ("4000", 0, 70_000)]),
        (date(2025, 4, 30), "Q2 purchases (cost of sales)",
         [("5000", 35_000, 0), ("1200", 0, 35_000)]),
        (date(2025, 6, 30), "H1 admin wages",
         [("7100", 14_000, 0), ("1200", 0, 14_000)]),
        (date(2025, 6, 30), "H1 distribution wages",
         [("7000", 8_000, 0), ("1200", 0, 8_000)]),
        (date(2025, 7, 1), "Annual rent",
         [("7200", 8_500, 0), ("1200", 0, 8_500)]),
        (date(2025, 8, 31), "Light, heat and power",
         [("7300", 3_500, 0), ("1200", 0, 3_500)]),
        (date(2025, 10, 15), "Q4 sales — cash receipt",
         [("1200", 80_000, 0), ("4000", 0, 80_000)]),
        (date(2025, 10, 15), "Q4 purchases (cost of sales)",
         [("5000", 40_000, 0), ("1200", 0, 40_000)]),
        (date(2025, 11, 30), "Pay FY 2024 corporation tax",
         [("2300", 4_000, 0), ("1200", 0, 4_000)]),
        (date(2025, 12, 1), "H2 admin wages",
         [("7100", 14_000, 0), ("1200", 0, 14_000)]),
        (date(2025, 12, 1), "H2 distribution wages",
         [("7000", 8_000, 0), ("1200", 0, 8_000)]),
        (date(2025, 12, 15), "Loan interest paid",
         [("8200", 2_200, 0), ("1200", 0, 2_200)]),
        (date(2025, 12, 15), "Bank charges",
         [("8000", 600, 0), ("1200", 0, 600)]),
        (date(2025, 12, 20), "Dividend declared (payable)",
         [("3100", 5_000, 0), ("2700", 0, 5_000)]),
        (date(2025, 12, 25), "FY 2025 corporation-tax accrual",
         [("9000", 8_000, 0), ("2300", 0, 8_000)]),
    ]

    for txn_date, desc, lines in entries:
        _post(session, txn_date, desc, lines, currency="GBP")

    session.commit()
    return len(entries)
