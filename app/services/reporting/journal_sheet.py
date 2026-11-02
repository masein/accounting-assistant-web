"""The journal as one Excel sheet, in the shape the tax office («دارایی») and
other services take it every three months (2026-11-02, scenarios BL1–BL7).

The owner's accountant made it by hand: one row per voucher line —

    ردیف | تاریخ | کد حساب کل | عنوان حساب کل | کد حساب معین | عنوان حساب معین | شرح | مبلغ بدهکار (ریال) | مبلغ بستانکار (ریال)

* ردیف is the voucher's number in its year (from 1 Farvardin, or 1 January
  in a Gregorian company), the same on each of its lines: a quarter's file
  goes on from where the one before ended.
* The کل is the highest account above the line's that is not a group (or
  the account itself); the معین is the line's account when it sits below
  that, else empty. Nothing is dropped: a line on a DETAIL account names
  that account as its معین.
* A voucher's debit lines come before its credit lines, as in the owner's file.
* Dates are text in the company's calendar with Latin digits («1405/01/05»),
  as an upload reads them; codes and amounts are numbers.
* The totals sit beside the last line, as in the owner's file (J/K), with
  the difference under them; numbers — the app never writes a formula.
"""
from __future__ import annotations

import io
import re
from datetime import date
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.core.money import plain, sheet_format
from app.models.account import Account, AccountLevel
from app.models.transaction import Transaction, TransactionLine
from app.services.reporting.repository import is_base_view, line_dr_cr

HEADERS = {
    "fa": ("ردیف", "تاریخ", "کد حساب کل", "عنوان حساب کل", "کد حساب معین", "عنوان حساب معین", "شرح",
           "مبلغ بدهکار ({ccy})", "مبلغ بستانکار ({ccy})"),
    "en": ("Voucher", "Date", "General ledger code", "General ledger account", "Subsidiary code",
           "Subsidiary account", "Description", "Debit ({ccy})", "Credit ({ccy})"),
}
TITLE = {"fa": "دفتر روزنامه", "en": "General journal"}
WIDTHS = (8, 13, 11, 28, 12, 28, 45, 21, 21)
MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
_SHEET_BAD = re.compile(r"[\[\]:*?/\\]")
# an imported voucher's reference keeps its number in the books it came from (journal_import._reference)
_IMPORTED = re.compile(r"^IMPORT-[a-z]+-(.+?)(?:-\d{4}-\d{2}-\d{2})?$")


def _jalali(db: Session) -> bool:
    from app.services.locale_service import get_display_calendar
    return get_display_calendar(db) == "jalali"


def _language(db: Session, lang: str | None) -> str:
    """Asked for, else Persian for an Iranian company (its template or its calendar): the file is
    the tax office's."""
    if lang in HEADERS:
        return lang
    from app.services.locale_service import get_reporting_locale
    return "fa" if (get_reporting_locale(db) or "").strip().lower() == "ir" or _jalali(db) else "en"


def _day(d: date, jalali: bool) -> str:
    if jalali:
        from app.utils.jalali import gregorian_to_jalali
        y, m, dd = gregorian_to_jalali(d)
        return f"{y:04d}/{m:02d}/{dd:02d}"
    return f"{d.year:04d}/{d.month:02d}/{d.day:02d}"


def _year_start(d: date, jalali: bool) -> date:
    if jalali:
        from app.utils.jalali import gregorian_to_jalali, jalali_to_gregorian
        return jalali_to_gregorian(gregorian_to_jalali(d)[0], 1, 1)
    return date(d.year, 1, 1)


def _in_order(v: Transaction) -> tuple:
    """By day, then as entered. A file imported at once was entered in one moment: its own voucher
    numbers keep a day's vouchers in the order they had (random ids put the fourth before the first)."""
    m = _IMPORTED.match(v.reference or "")
    n = m.group(1) if m else ""
    return (v.date, v.created_at is None, v.created_at, not n.isdigit(), int(n) if n.isdigit() else 0, n, str(v.id))


def _where(currency: str | None) -> list:
    out = [Transaction.deleted_at.is_(None)]
    if currency and not is_base_view(currency):
        out.append(Transaction.currency == currency)
    return out


def _currency(db: Session, currency: str | None, lang: str) -> tuple[str, str]:
    """The amounts' currency and its word in the header: «ریال»; all currencies are the base one's values."""
    from app.core import currencies
    code = (currency or "").strip().upper()
    if not code or is_base_view(code):
        from app.services.fx_base import base_currency
        code = base_currency(db)
    if lang != "fa":
        return code, code
    return code, "تومان" if code == "IRT" else currencies.after_amount(code, lang)


def _code(code: str) -> int | str:
    """A code of digits is a number, as in the owner's file; «0105» or «11.10» stays text."""
    return int(code) if code.isdigit() and str(int(code)) == code else code


class _Tiers:
    """Each account's (کل, معین): the highest account above it that is not a group, and the account
    itself when it is below that."""

    def __init__(self, db: Session) -> None:
        self._by_id = {a.id: a for a in db.execute(select(Account)).scalars()}

    def of(self, acc: Account) -> tuple[Account, Account | None]:
        chain, seen, a = [], set(), acc
        while a is not None and a.id not in seen:
            seen.add(a.id)
            chain.append(a)
            a = self._by_id.get(a.parent_id) if a.parent_id else None
        # the tree, not the level: own codes move the app's «حقوق پرداختنی» (a GENERAL) under the
        # school's 1600 «بستانکاران», where it is a معین (docs/PLAN_OWN_CODES.md)
        kol = next((x for x in reversed(chain) if x.level != AccountLevel.GROUP), acc)
        return kol, (acc if acc.id != kol.id else None)


def rows(db: Session, from_date: date, to_date: date, currency: str | None = None) -> list[list[Any]]:
    """The journal's lines in the period, one list per line, in the sheet's column order. A voucher's
    number counts the vouchers in the same view: a rial file's numbers have no gaps for dollar ones."""
    currency = (currency or "").strip().upper() or None
    jalali = _jalali(db)
    where = _where(currency)
    vouchers = db.execute(
        select(Transaction)
        .where(*where, Transaction.date >= from_date, Transaction.date <= to_date)
        .options(selectinload(Transaction.lines).selectinload(TransactionLine.account))
    ).scalars().unique().all()
    vouchers = sorted(vouchers, key=_in_order)
    tiers = _Tiers(db)
    numbers: dict[date, int] = {}            # a year's start → the last number given in it
    out: list[list[Any]] = []
    for v in vouchers:
        start = _year_start(v.date, jalali)
        if start not in numbers:
            numbers[start] = int(db.execute(
                select(func.count(Transaction.id)).where(*where, Transaction.date >= start, Transaction.date < from_date)
            ).scalar() or 0) if start < from_date else 0
        numbers[start] += 1
        # debits first, as in the owner's file: a line keeps no order of its own, and Postgres hands
        # back one an import or a merge rewrote after the others
        lines = sorted(((ln, *line_dr_cr(ln, currency)) for ln in v.lines), key=lambda x: not x[1])
        for ln, debit, credit in lines:
            kol, moin = tiers.of(ln.account)
            out.append([
                numbers[start], _day(v.date, jalali),
                _code(kol.code), kol.name,
                _code(moin.code) if moin else None, moin.name if moin else None,
                (ln.line_description or v.description or "").strip() or None,
                plain(debit), plain(credit),
            ])
    return out


def workbook(db: Session, from_date: date, to_date: date, currency: str | None = None, lang: str | None = None) -> bytes:
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font
    from openpyxl.utils import get_column_letter

    from app.core.spreadsheet import no_formulas
    from app.services.documents.branding import build_brand

    lang = _language(db, lang)
    jalali = _jalali(db)
    body = rows(db, from_date, to_date, currency)
    code, ccy = _currency(db, currency, lang)
    unit = sheet_format(code)
    money = f"_(* {unit}_);_(* \\({unit}\\);_(* \"-\"??_);_(@_)"   # the owner's file: a zero shows «-»
    font = "B Nazanin" if lang == "fa" else "Calibri"

    wb = Workbook()
    ws = wb.active
    company = (build_brand(db)["issuer"]["name"] or "").strip()
    year = _day(from_date, jalali)[:4]
    ws.title = _SHEET_BAD.sub("-", " ".join(x for x in (TITLE[lang], year, company) if x))[:31].strip() or TITLE[lang]
    ws.sheet_view.rightToLeft = lang == "fa"
    ws.append([h.format(ccy=ccy) for h in HEADERS[lang]])
    for cell in ws[1]:
        cell.font = Font(name=font, bold=True, size=14)
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
    ws.row_dimensions[1].height = 40
    for i, width in enumerate(WIDTHS, start=1):
        ws.column_dimensions[get_column_letter(i)].width = width
    for r in body:
        ws.append(r)
        row = ws.max_row
        for c in range(1, 10):
            cell = ws.cell(row=row, column=c)
            cell.font = Font(name=font, size=12)
            if c >= 8:
                cell.number_format = money
            elif c == 7:
                cell.alignment = Alignment(wrap_text=True, vertical="top")
    last = ws.max_row
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = f"A1:I{last}"
    if body:
        debit = sum(r[7] for r in body)
        credit = sum(r[8] for r in body)
        for col, value, row in (("J", debit, last), ("K", credit, last), ("J", debit - credit, last + 1)):
            cell = ws[f"{col}{row}"]
            cell.value = plain(value)
            cell.number_format = money
            cell.font = Font(name=font, bold=True, size=12)
        ws.column_dimensions["J"].width = ws.column_dimensions["K"].width = 21
    no_formulas(wb)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def filename(db: Session, from_date: date, to_date: date, lang: str | None = None) -> str:
    lang = _language(db, lang)
    jalali = _jalali(db)
    a, b = (_day(d, jalali).replace("/", "-") for d in (from_date, to_date))
    return f"{TITLE[lang]} {a} {'تا' if lang == 'fa' else 'to'} {b}.xlsx"
