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


def _demo_years() -> tuple[int, int]:
    """Return the (prior, current) calendar years the demo should span.

    The demo always anchors to *today* so the most recent fiscal year is the
    current one. This keeps "current year" dashboards, manager reports and the
    AI accountant's default (year-to-date) ledger queries populated — otherwise
    the demo silently ages out and every current-year view reads zero.
    """
    current = date.today().year
    return current - 1, current


def _add_months(d: date, months: int) -> date:
    """Shift a date by ``months`` (positive or negative), clamping the day to
    the target month's length so we never raise on e.g. 31 → February."""
    total = (d.year * 12 + (d.month - 1)) + months
    year, month = divmod(total, 12)
    month += 1
    # Last day of the target month.
    if month == 12:
        last = 31
    else:
        last = (date(year, month + 1, 1) - date(year, month, 1)).days
    return date(year, month, min(d.day, last))


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
# Story: a small Iranian trading company. Year 1 (prior year) is the start-up
# year — capital, PP&E purchase, long-term bank loan, two sales cycles,
# wages, opex, interest, tax accrual. Year 2 (current year) doubles activity —
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
    # Tax expense. `_ensure_account` links a 4-digit code to its 2-digit
    # prefix, so 6410 needs a "64" group to avoid being orphaned in the
    # chart-of-accounts tree. The "641" sub-group is kept for the Iranian
    # standard breakdown (current-year vs prior-year tax).
    ("64", "هزینه مالیات بر درآمد", AccountLevel.GROUP),
    ("641", "هزینه مالیات سال جاری", AccountLevel.GROUP),
    ("6410", "هزینه مالیات سال جاری", AccountLevel.GENERAL),
]


def seed_iran_demo(session: Session) -> int:
    """Post the Iranian demo journal entries. Returns number of entries posted."""
    for code, name, level in _IR_EXTRA_ACCOUNTS:
        _ensure_account(session, code, name, level)

    M = 1_000_000  # million-rial multiplier
    y1, y2 = _demo_years()  # (prior year, current year) — anchored to today

    entries: list[tuple[date, str, list[tuple[str, int, int]]]] = [
        # ----- سال اول: راه‌اندازی و فعالیت اولیه -----
        (date(y1, 1, 15), "آورده اولیه سرمایه",
         [("1110", 5_000 * M, 0), ("3110", 0, 5_000 * M)]),
        (date(y1, 2, 1), "خرید ماشین‌آلات و تجهیزات",
         [("1210", 800 * M, 0), ("1110", 0, 800 * M)]),
        (date(y1, 3, 1), "دریافت تسهیلات مالی بلندمدت بانکی",
         [("1110", 2_000 * M, 0), ("2220", 0, 2_000 * M)]),
        (date(y1, 4, 30), "فروش سه‌ماهه دوم — دریافت نقدی",
         [("1110", 1_500 * M, 0), ("4110", 0, 1_500 * M)]),
        (date(y1, 4, 30), "بهای تمام‌شدهٔ کالای فروش‌رفته — سه‌ماهه دوم",
         [("5110", 800 * M, 0), ("1110", 0, 800 * M)]),
        (date(y1, 6, 30), "حقوق و دستمزد نیمهٔ اول سال",
         [("6110", 400 * M, 0), ("1110", 0, 400 * M)]),
        (date(y1, 8, 31), "سایر هزینه‌های عملیاتی",
         [("6112", 200 * M, 0), ("1110", 0, 200 * M)]),
        (date(y1, 10, 15), "فروش سه‌ماهه چهارم — دریافت نقدی",
         [("1110", 2_000 * M, 0), ("4110", 0, 2_000 * M)]),
        (date(y1, 10, 15), "بهای تمام‌شدهٔ کالای فروش‌رفته — سه‌ماهه چهارم",
         [("5110", 1_000 * M, 0), ("1110", 0, 1_000 * M)]),
        (date(y1, 12, 15), "حقوق و دستمزد نیمهٔ دوم سال",
         [("6110", 400 * M, 0), ("1110", 0, 400 * M)]),
        (date(y1, 12, 15), "هزینهٔ مالی — پرداخت سود تسهیلات",
         [("6210", 150 * M, 0), ("1110", 0, 150 * M)]),
        (date(y1, 12, 25), "ذخیرهٔ مالیات بر درآمد سال جاری",
         [("6410", 200 * M, 0), ("2130", 0, 200 * M)]),
        # ----- سال دوم: توسعه و تقسیم سود -----
        (date(y2, 2, 1), "خرید مازاد ماشین‌آلات و تجهیزات",
         [("1210", 500 * M, 0), ("1110", 0, 500 * M)]),
        (date(y2, 4, 15), "بازپرداخت اصل تسهیلات بلندمدت",
         [("2220", 500 * M, 0), ("1110", 0, 500 * M)]),
        (date(y2, 4, 30), "فروش سه‌ماهه دوم — دریافت نقدی",
         [("1110", 3_000 * M, 0), ("4110", 0, 3_000 * M)]),
        (date(y2, 4, 30), "بهای تمام‌شدهٔ کالای فروش‌رفته — سه‌ماهه دوم",
         [("5110", 1_500 * M, 0), ("1110", 0, 1_500 * M)]),
        (date(y2, 6, 30), "حقوق و دستمزد نیمهٔ اول سال",
         [("6110", 500 * M, 0), ("1110", 0, 500 * M)]),
        (date(y2, 8, 31), "سایر هزینه‌های عملیاتی",
         [("6112", 250 * M, 0), ("1110", 0, 250 * M)]),
        (date(y2, 10, 15), "فروش سه‌ماهه چهارم — دریافت نقدی",
         [("1110", 4_000 * M, 0), ("4110", 0, 4_000 * M)]),
        (date(y2, 10, 15), "بهای تمام‌شدهٔ کالای فروش‌رفته — سه‌ماهه چهارم",
         [("5110", 2_000 * M, 0), ("1110", 0, 2_000 * M)]),
        (date(y2, 11, 30), "پرداخت مالیات سال قبل",
         [("2130", 200 * M, 0), ("1110", 0, 200 * M)]),
        (date(y2, 12, 15), "حقوق و دستمزد نیمهٔ دوم سال",
         [("6110", 500 * M, 0), ("1110", 0, 500 * M)]),
        (date(y2, 12, 15), "هزینهٔ مالی — پرداخت سود تسهیلات",
         [("6210", 130 * M, 0), ("1110", 0, 130 * M)]),
        (date(y2, 12, 20), "مصوبهٔ تقسیم سود سهام",
         [("3300", 100 * M, 0), ("2140", 0, 100 * M)]),
        (date(y2, 12, 25), "ذخیرهٔ مالیات بر درآمد سال جاری",
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
# Story: a small UK limited company (Acme Consulting Ltd) — boutique
# software consultancy in its second year. Year 1 (prior year) is the
# startup: founder capital, plant purchase, bank loan, monthly
# operations, two main clients, ramp-up. Year 2 (current year) is expansion:
# more equipment, second engineer hired, third client onboarded,
# steady monthly cadence, year-end dividend.
#
# Density target: video-quality demo. ~120 transactions, 10 entities,
# 8 invoices (mix of paid / open / overdue for AR-aging chart),
# 3 inventory items with movements. Every income/expense category
# has at least one entry per month so charts have texture.


_UK_ENTITIES: list[tuple[str, str]] = [
    # (type, name) — code auto-generated as ENT-{n}
    ("client", "Acme Group plc"),
    ("client", "Beta Ventures Ltd"),
    ("client", "Charlie Industries"),
    ("client", "Delta Holdings"),
    ("supplier", "OfficeMax UK"),
    ("supplier", "BT Telecom"),
    ("supplier", "TechHub Hosting Ltd"),
    ("supplier", "Sherlock Insurance"),
    ("employee", "Alice Patel"),
    ("employee", "Bob Chen"),
    ("bank", "HSBC UK"),
]


_UK_INVENTORY: list[tuple[str, str, str, int]] = [
    # (name, sku, unit, list_price_gbp)
    ("Premium consulting hour", "CONS-PREM-HR", "hour", 180),
    ("Standard consulting hour", "CONS-STD-HR", "hour", 120),
    ("Quarterly software licence — Enterprise", "SW-LIC-Q-ENT", "licence", 2_500),
]


def _seed_uk_entities(session: Session) -> dict[str, "object"]:
    from app.models.entity import Entity

    by_name: dict[str, Entity] = {}
    for n, (typ, name) in enumerate(_UK_ENTITIES, start=1):
        e = Entity(type=typ, name=name, code=f"ENT-{n:03d}")
        session.add(e)
        session.flush()
        by_name[name] = e
    return by_name


def _seed_uk_inventory(session: Session) -> None:
    from app.models.inventory import InventoryItem

    for name, sku, unit, price in _UK_INVENTORY:
        item = InventoryItem(name=name, sku=sku, unit=unit, list_price=price)
        session.add(item)
        session.flush()


def _post_with_entities(
    session: Session, txn_date: date, description: str,
    lines: list[tuple[str, int, int]], *, currency: str = "GBP",
    entity_links: list[tuple[str, str]] | None = None,
    entities_by_name: dict[str, "object"] | None = None,
) -> "object":
    """Like _post but optionally links the transaction to one or more
    entities (e.g. tag a sale with its client). ``entity_links`` is a
    list of ``(entity_name, role)`` tuples."""
    from app.models.entity import TransactionEntity

    txn = _post(session, txn_date, description, lines, currency=currency)
    if entity_links and entities_by_name:
        for ent_name, role in entity_links:
            ent = entities_by_name.get(ent_name)
            if ent is not None:
                session.add(TransactionEntity(
                    transaction_id=txn.id, entity_id=ent.id, role=role,
                ))
        session.flush()
    return txn


def _seed_uk_invoices(
    session: Session, entities_by_name: dict[str, "object"],
) -> int:
    """Post a mix of paid / open / overdue sales+purchase invoices so
    the AR / AP aging panels have realistic content for the demo.
    Each invoice gets 1-3 line items so the Products catalogue and the
    Clients/Suppliers matrices have something to render."""
    from sqlalchemy import select
    from app.models.invoice import Invoice
    from app.models.invoice_item import InvoiceItem
    from app.models.inventory import InventoryItem

    # Look up inventory items so the line items link back via inventory_item_id
    # (lets the Products catalogue match invoice lines to inventory entries).
    inv_items = {
        i.name: i for i in session.execute(select(InventoryItem)).scalars().all()
    }
    cons_prem = inv_items.get("Premium consulting hour")
    cons_std = inv_items.get("Standard consulting hour")
    sw_lic = inv_items.get("Quarterly software licence — Enterprise")

    today = date.today()
    # Relative anchors so the AR/AP aging always has a recent-paid, a current
    # open, and a genuinely-overdue invoice regardless of when the demo runs.
    # ``line_items`` is per-invoice: (product_name, quantity, unit_price,
    # inventory_item_or_none). Header amounts are derived from the line totals
    # below, so they always reconcile.
    invoices_with_lines = [
        # ── Sales ──
        # Recent, paid (issued ~2 months ago, settled).
        (
            ("INV-S-001", "sales", "paid", _add_months(today, -2), _add_months(today, -1),
             None, "Acme Group plc", "Software consulting + enterprise licence"),
            [
                ("Premium consulting hour", 60, 180, cons_prem),       # 10,800
                ("Quarterly software licence — Enterprise", 1, 2_500, sw_lic),  # 2,500
            ],
        ),
        # Earlier, paid.
        (
            ("INV-S-002", "sales", "paid", _add_months(today, -3), _add_months(today, -2),
             None, "Beta Ventures Ltd", "Consulting retainer"),
            [
                ("Premium consulting hour", 40, 180, cons_prem),       # 7,200
                ("Standard consulting hour", 40, 120, cons_std),       # 4,800
            ],
        ),
        # Open — issued today, not yet due.
        (
            ("INV-S-003", "sales", "issued", today, _add_months(today, 1),
             None, "Charlie Industries", "Current-month consulting"),
            [
                ("Standard consulting hour", 80, 120, cons_std),       # 9,600
            ],
        ),
        # Overdue — issued ~2 months ago, due ~1 month ago, still unpaid.
        (
            ("INV-S-004", "sales", "issued", _add_months(today, -2), _add_months(today, -1),
             None, "Delta Holdings", "Consulting services (overdue)"),
            [
                ("Standard consulting hour", 55, 120, cons_std),       # 6,600
            ],
        ),
        # ── Purchases ──
        (
            ("INV-P-001", "purchase", "paid", _add_months(today, -2), _add_months(today, -1),
             None, "OfficeMax UK", "Office supplies"),
            [("Office supplies — quarterly bundle", 1, 1_650, None)],
        ),
        (
            ("INV-P-002", "purchase", "paid", _add_months(today, -2), _add_months(today, -1),
             None, "TechHub Hosting Ltd", "Cloud hosting + dev tools"),
            [("Cloud hosting + tooling", 1, 2_400, None)],
        ),
        # Open — issued today.
        (
            ("INV-P-003", "purchase", "issued", today, _add_months(today, 1),
             None, "Sherlock Insurance", "Annual cyber-liability renewal"),
            [("Cyber-liability premium (annual)", 1, 2_100, None)],
        ),
        # Overdue.
        (
            ("INV-P-004", "purchase", "issued", _add_months(today, -2), _add_months(today, -1),
             None, "BT Telecom", "Telecom — quarterly bill (overdue)"),
            [("Telecom — quarterly bill", 1, 900, None)],
        ),
    ]

    for header, lines in invoices_with_lines:
        number, kind, status, issued, due, _amount, ent_name, description = header
        # Derive the header amount from the line items so they always reconcile.
        amount = int(round(sum(qty * unit_price for _n, qty, unit_price, _i in lines)))
        ent = entities_by_name.get(ent_name)
        inv = Invoice(
            number=number, kind=kind, status=status,
            issue_date=issued, due_date=due,
            amount=amount, currency="GBP",
            description=description,
            entity_id=ent.id if ent else None,
        )
        session.add(inv)
        session.flush()
        for product_name, qty, unit_price, inv_item in lines:
            session.add(InvoiceItem(
                invoice_id=inv.id,
                product_name=product_name,
                quantity=qty,
                unit_price=unit_price,
                line_total=int(round(qty * unit_price)),
                inventory_item_id=inv_item.id if inv_item else None,
            ))
    session.flush()
    return len(invoices_with_lines)


def _monthly_entries(year: int, scale: float = 1.0, *, first_year: int | None = None) -> list[
    tuple[date, str, list[tuple[str, int, int]], list[tuple[str, str]] | None]
]:
    """Generate monthly recurring entries for a year. ``scale`` lets
    year 2 (1.25) be ~25% richer than year 1 (1.0). ``first_year`` is the
    earliest demo year; the second engineer (Bob) is only hired from July of
    that first year onward."""
    out: list[tuple[date, str, list[tuple[str, int, int]], list[tuple[str, str]] | None]] = []

    # Per-month standing items
    for m in range(1, 13):
        # Office rent — 1st of month
        rent_amt = int(700 * scale)
        out.append((date(year, m, 1), f"Office rent — month {m:02d}",
                    [("7200", rent_amt, 0), ("1200", 0, rent_amt)], None))
        # Telecom (BT)
        tel_amt = int(140 * scale)
        out.append((date(year, m, 5), f"BT telecom — month {m:02d}",
                    [("7600", tel_amt, 0), ("1200", 0, tel_amt)],
                    [("BT Telecom", "supplier")]))
        # Hosting + dev tools
        host_amt = int(220 * scale)
        out.append((date(year, m, 7), f"TechHub hosting — month {m:02d}",
                    [("7600", host_amt, 0), ("1200", 0, host_amt)],
                    [("TechHub Hosting Ltd", "supplier")]))
        # Utilities (electricity / heating)
        util_amt = int(260 * scale + (80 if m in (1, 2, 11, 12) else 0))  # winter spike
        out.append((date(year, m, 12), f"Light, heat and power — month {m:02d}",
                    [("7300", util_amt, 0), ("1200", 0, util_amt)], None))
        # Monthly salary — Alice (admin)
        alice_amt = int(2_400 * scale)
        out.append((date(year, m, 25), f"Salary — Alice Patel — month {m:02d}",
                    [("7100", alice_amt, 0), ("1200", 0, alice_amt)],
                    [("Alice Patel", "employee")]))
        # Bob is hired starting July of the first demo year.
        if first_year is None or year > first_year or m >= 7:
            bob_amt = int(2_100 * scale)
            out.append((date(year, m, 25), f"Salary — Bob Chen — month {m:02d}",
                        [("7000", bob_amt, 0), ("1200", 0, bob_amt)],
                        [("Bob Chen", "employee")]))

    # Quarterly client invoicing — one large client per quarter. Bigger
    # year-end deals so the trend chart shows a Q4 spike. Services-firm
    # margins (~65%), so COGS is a modest 35% of revenue (contractors +
    # materials passed through).
    quarterly_clients = ["Acme Group plc", "Beta Ventures Ltd", "Charlie Industries", "Delta Holdings"]
    for q in range(4):
        sale_amt = int((30_000 + q * 5_000) * scale)  # Q1=30k, Q2=35k, Q3=40k, Q4=45k (year 1)
        client = quarterly_clients[q]
        sale_date = date(year, (q + 1) * 3, 25)
        out.append((sale_date, f"Q{q+1} sales — {client}",
                    [("1200", sale_amt, 0), ("4000", 0, sale_amt)],
                    [(client, "client")]))
        cogs_amt = int(sale_amt * 0.35)
        cogs_date = date(year, (q + 1) * 3, 10)
        out.append((cogs_date, f"Q{q+1} subcontractor + materials",
                    [("5000", cogs_amt, 0), ("1200", 0, cogs_amt)],
                    [("OfficeMax UK", "supplier")]))

    # Quarterly insurance
    for m in (3, 6, 9, 12):
        ins_amt = int(420 * scale)
        out.append((date(year, m, 18), f"Sherlock Insurance — Q{m // 3} premium",
                    [("7800", ins_amt, 0), ("1200", 0, ins_amt)],
                    [("Sherlock Insurance", "supplier")]))

    return out


def seed_uk_demo(session: Session) -> int:
    # ── Step 1: seed reference data (entities + inventory) ─────────────
    entities = _seed_uk_entities(session)
    _seed_uk_inventory(session)
    n_invoices = _seed_uk_invoices(session, entities)

    # ── Step 2: post the journal entries ───────────────────────────────
    y1, y2 = _demo_years()  # (prior year, current year) — anchored to today

    # Foundational year-1 setup entries (capital, PP&E, loan).
    setup: list[tuple[date, str, list[tuple[str, int, int]], list[tuple[str, str]] | None]] = [
        (date(y1, 1, 5), "Issue of share capital",
         [("1200", 100_000, 0), ("3000", 0, 100_000)], None),
        (date(y1, 1, 18), "Purchase plant and machinery",
         [("0010", 30_000, 0), ("1200", 0, 30_000)], None),
        (date(y1, 2, 10), "Bank loan drawdown",
         [("1200", 40_000, 0), ("2800", 0, 40_000)],
         [("HSBC UK", "bank")]),
    ]

    # Year-1 monthly cadence (1.0× scale) + year-2 (1.6× — strong growth
    # year: more clients, bigger deals, higher rent & salaries).
    cadence = (
        setup
        + _monthly_entries(y1, scale=1.0, first_year=y1)
        + _monthly_entries(y2, scale=1.6, first_year=y1)
    )

    # Year-end finance + tax for both years.
    year_end: list[tuple[date, str, list[tuple[str, int, int]], list[tuple[str, str]] | None]] = [
        (date(y1, 12, 27), f"Loan interest paid (FY {y1})",
         [("8200", 2_500, 0), ("1200", 0, 2_500)], None),
        (date(y1, 12, 28), f"Bank charges (FY {y1})",
         [("8000", 480, 0), ("1200", 0, 480)], None),
        (date(y1, 12, 30), f"FY {y1} corporation-tax accrual (19%)",
         [("9000", 6_700, 0), ("2300", 0, 6_700)], None),
        (date(y2, 2, 5), "Additional plant and machinery",
         [("0010", 15_000, 0), ("1200", 0, 15_000)], None),
        (date(y2, 4, 18), "Loan principal repayment",
         [("2800", 8_000, 0), ("1200", 0, 8_000)],
         [("HSBC UK", "bank")]),
        (date(y2, 11, 18), f"Pay FY {y1} corporation tax",
         [("2300", 6_700, 0), ("1200", 0, 6_700)], None),
        (date(y2, 12, 22), f"Loan interest paid (FY {y2})",
         [("8200", 2_300, 0), ("1200", 0, 2_300)], None),
        (date(y2, 12, 22), f"Bank charges (FY {y2})",
         [("8000", 600, 0), ("1200", 0, 600)], None),
        (date(y2, 12, 23), "Dividend declared — interim",
         [("3100", 15_000, 0), ("2700", 0, 15_000)], None),
        (date(y2, 12, 28), f"FY {y2} corporation-tax accrual (19%)",
         [("9000", 7_300, 0), ("2300", 0, 7_300)], None),
    ]

    all_entries = cadence + year_end
    for txn_date, desc, lines, ent_links in all_entries:
        _post_with_entities(
            session, txn_date, desc, lines,
            currency="GBP",
            entity_links=ent_links,
            entities_by_name=entities,
        )

    session.commit()
    # Return only the journal count for the existing API contract;
    # entities/invoices/inventory show up in their respective panels.
    return len(all_entries)
