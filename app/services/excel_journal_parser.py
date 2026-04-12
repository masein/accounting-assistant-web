"""
Excel Journal Import Parser
Parses double-entry accounting journals from Excel files.
Handles hierarchical accounts (Title 1/2/3), Jalali date codes, and project tags.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

import jdatetime
import openpyxl


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class ParsedJournalLine:
    row_index: int
    title1: str  # top-level account (دارایی, بستانکاران, سود و زیان)
    title2: str  # mid-level (هزینه عملیاتی, حساب پرداختنی, ...)
    title3: str  # detail (هزینه وب سایت, person name, ...)
    description: str
    debit: float
    credit: float  # stored as positive
    project_group: str | None = None
    project: str | None = None
    project_name: str | None = None


@dataclass
class ParsedVoucher:
    voucher_number: int | str
    date_code: int | str | None  # raw day column value
    gregorian_date: date | None = None
    lines: list[ParsedJournalLine] = field(default_factory=list)
    total_debit: float = 0
    total_credit: float = 0
    is_balanced: bool = True


@dataclass
class ColumnMapping:
    """Which Excel column index maps to which field."""
    row_num: int | None = None        # A - ردیف
    voucher_num: int | None = None    # B - شماره سند
    day: int | None = None            # C - Day
    title1: int | None = None         # D - Title 1
    title2: int | None = None         # E - Title 2
    title3: int | None = None         # F - Title 3
    notes: int | None = None          # G - Notes
    debit: int | None = None          # H - بدهکار
    credit: int | None = None        # I - بستانکار
    balance: int | None = None        # J - Balance
    project_group: int | None = None  # K - Project Group
    project: int | None = None        # L - Project
    project_name: int | None = None   # M - Project Name


@dataclass
class AccountSuggestion:
    """Suggested mapping from Title hierarchy to our chart of accounts."""
    title1: str
    title2: str
    title3: str
    suggested_code: str | None = None
    suggested_name: str | None = None
    needs_creation: bool = False


@dataclass
class ExcelParseResult:
    vouchers: list[ParsedVoucher]
    column_mapping: ColumnMapping
    headers: list[str]
    raw_preview: list[list[Any]]  # first N rows for UI preview
    unique_accounts: list[AccountSuggestion]
    jalali_year: int | None = None
    errors: list[str] = field(default_factory=list)
    total_rows: int = 0
    total_vouchers: int = 0


# ---------------------------------------------------------------------------
# Column detection
# ---------------------------------------------------------------------------

_HEADER_PATTERNS = {
    'row_num': re.compile(r'ردیف|row|#|شماره ردیف', re.IGNORECASE),
    'voucher_num': re.compile(r'شماره\s*سند|voucher|سند|doc', re.IGNORECASE),
    'day': re.compile(r'^day$|تاریخ|date|روز', re.IGNORECASE),
    'title1': re.compile(r'title\s*1|عنوان.*1|سرفصل.*1|گروه.*حساب', re.IGNORECASE),
    'title2': re.compile(r'title\s*2|عنوان.*2|سرفصل.*2|حساب.*کل', re.IGNORECASE),
    'title3': re.compile(r'title\s*3|عنوان.*3|سرفصل.*3|حساب.*معین|تفصیلی', re.IGNORECASE),
    'notes': re.compile(r'notes|شرح|توضیح|یادداشت|بابت', re.IGNORECASE),
    'debit': re.compile(r'بدهکار|debit|مبلغ بدهکار', re.IGNORECASE),
    'credit': re.compile(r'بستانکار|credit|مبلغ بستانکار', re.IGNORECASE),
    'balance': re.compile(r'balance|مانده|موجودی', re.IGNORECASE),
    'project_group': re.compile(r'project\s*group|گروه.*پروژه', re.IGNORECASE),
    'project': re.compile(r'^project$|پروژه', re.IGNORECASE),
    'project_name': re.compile(r'project\s*name|نام.*پروژه', re.IGNORECASE),
}


def _detect_columns(headers: list[str]) -> ColumnMapping:
    """Auto-detect column mapping from header row."""
    mapping = ColumnMapping()
    used: set[int] = set()

    for field_name, pattern in _HEADER_PATTERNS.items():
        for i, h in enumerate(headers):
            if i in used:
                continue
            if h and pattern.search(str(h).strip()):
                setattr(mapping, field_name, i)
                used.add(i)
                break

    # Fallback: if we didn't detect title columns but have enough columns,
    # assume standard order (A=row, B=voucher, C=day, D=t1, E=t2, F=t3, G=notes, H=debit, I=credit)
    if mapping.title1 is None and len(headers) >= 9:
        if mapping.voucher_num is not None and mapping.debit is not None:
            # We at least got voucher and debit, try to fill gaps
            pass
        else:
            # Assume standard layout
            mapping.row_num = 0
            mapping.voucher_num = 1
            mapping.day = 2
            mapping.title1 = 3
            mapping.title2 = 4
            mapping.title3 = 5
            mapping.notes = 6
            mapping.debit = 7
            mapping.credit = 8
            if len(headers) > 9:
                mapping.balance = 9
            if len(headers) > 10:
                mapping.project_group = 10
            if len(headers) > 11:
                mapping.project = 11
            if len(headers) > 12:
                mapping.project_name = 12

    return mapping


# ---------------------------------------------------------------------------
# Amount parsing
# ---------------------------------------------------------------------------

_PERSIAN_DIGITS = str.maketrans('۰۱۲۳۴۵۶۷۸۹', '0123456789')
_ARABIC_DIGITS = str.maketrans('٠١٢٣٤٥٦٧٨٩', '0123456789')


def _parse_amount(raw: Any) -> float:
    """Parse an amount that could be numeric, string with $ -, Persian digits, etc."""
    if raw is None:
        return 0.0
    if isinstance(raw, (int, float)):
        return abs(float(raw))

    s = str(raw).strip()
    # Handle "$ -" or just "-" meaning zero
    if s in ('$ -', '-', '$-', '', '—', '–'):
        return 0.0

    # Remove currency symbols, commas, spaces
    s = s.replace('$', '').replace(',', '').replace(' ', '').replace('\u200c', '')
    # Translate Persian/Arabic digits
    s = s.translate(_PERSIAN_DIGITS).translate(_ARABIC_DIGITS)

    # Handle parentheses for negative: (100) -> -100
    if s.startswith('(') and s.endswith(')'):
        s = '-' + s[1:-1]

    try:
        return abs(float(s))
    except ValueError:
        return 0.0


# ---------------------------------------------------------------------------
# Date conversion
# ---------------------------------------------------------------------------

def _jalali_day_to_gregorian(day_code: Any, jalali_year: int) -> date | None:
    """Convert a Jalali day code (e.g., 419 = month 4, day 19) to Gregorian date."""
    if day_code is None:
        return None
    try:
        code = int(day_code)
    except (ValueError, TypeError):
        return None

    if code < 100:
        return None

    if code >= 10000:
        # Full date: 14030419 = year 1403, month 04, day 19
        y = code // 10000
        remainder = code % 10000
        m = remainder // 100
        d = remainder % 100
    elif code >= 1000:
        # MMdd format: 1018 = month 10, day 18
        m = code // 100
        d = code % 100
    else:
        # Mdd format: 419 = month 4, day 19
        m = code // 100
        d = code % 100

    if not (1 <= m <= 12 and 1 <= d <= 31):
        return None

    try:
        jd = jdatetime.date(jalali_year, m, d)
        return jd.togregorian()
    except (ValueError, OverflowError):
        return None


# ---------------------------------------------------------------------------
# Account mapping
# ---------------------------------------------------------------------------

# Map common Persian account titles to our chart of accounts codes
_TITLE1_MAP = {
    'دارایی': '11',  # Assets (current)
    'دارایی جاری': '11',
    'دارایی غیرجاری': '12',
    'بستانکاران': '21',  # Payables
    'بدهکاران': '11',  # Receivables → assets
    'سود و زیان': '61',  # P&L → expenses
    'سود و زیان دوره': '61',
    'سود و زیان دوره (صندوق جریان)': '61',
    'حقوق مالکانه': '31',  # Equity
    'درآمد': '41',  # Revenue
    'فروش': '41',
}

_TITLE2_MAP = {
    'هزینه عملیاتی': '6112',  # Operating expenses
    'حساب پرداختنی': '2110',  # Accounts payable
    'جاری شرکاء': '3110',  # Partners' current → equity/capital
    'دارایی جاری': '1110',  # Current assets → cash & bank
    'بستانکاران تجاری': '2110',  # Trade payables
    'بدهکاران تجاری': '1112',  # Trade receivables
    'حساب دریافتنی': '1112',
    'هزینه‌های حقوق': '6110',  # Salary expenses
    'هزینه مالی': '6210',  # Financial expenses
}


def _suggest_account_code(title1: str, title2: str, title3: str) -> str | None:
    """Suggest an account code based on the title hierarchy."""
    t1 = (title1 or '').strip()
    t2 = (title2 or '').strip()

    # Try title2 first (more specific)
    for key, code in _TITLE2_MAP.items():
        if key in t2 or t2 in key:
            return code

    # Fall back to title1
    for key, code in _TITLE1_MAP.items():
        if key in t1 or t1 in key:
            # If it's an expense category, use 6112
            if code.startswith('6'):
                return '6112'
            if code == '21':
                return '2110'
            if code == '11':
                return '1110'
            if code == '31':
                return '3110'
            if code == '41':
                return '4110'
            return code

    return None


# ---------------------------------------------------------------------------
# Main parse function
# ---------------------------------------------------------------------------

def parse_excel_journal(
    file_path: str | Path,
    jalali_year: int | None = None,
    column_mapping: dict[str, int] | None = None,
) -> ExcelParseResult:
    """
    Parse an Excel file as a double-entry accounting journal.

    Args:
        file_path: Path to the .xlsx file
        jalali_year: Jalali year for date conversion (e.g. 1403)
        column_mapping: Optional override for column positions {field_name: col_index}
    """
    wb = openpyxl.load_workbook(str(file_path), data_only=True, read_only=True)
    ws = wb.active
    if ws is None:
        return ExcelParseResult(
            vouchers=[], column_mapping=ColumnMapping(), headers=[],
            raw_preview=[], unique_accounts=[], errors=["No active sheet found"],
        )

    # Read all rows
    all_rows: list[list[Any]] = []
    for row in ws.iter_rows(values_only=True):
        all_rows.append(list(row))
    wb.close()

    if not all_rows:
        return ExcelParseResult(
            vouchers=[], column_mapping=ColumnMapping(), headers=[],
            raw_preview=[], unique_accounts=[], errors=["File is empty"],
        )

    # Headers from first row
    headers = [str(c) if c else '' for c in all_rows[0]]

    # Detect or apply column mapping
    if column_mapping:
        mapping = ColumnMapping(**{k: v for k, v in column_mapping.items() if hasattr(ColumnMapping, k)})
    else:
        mapping = _detect_columns(headers)

    # Auto-detect Jalali year from day codes if not provided
    if jalali_year is None:
        # Check if any day code looks like a full date (>= 10000)
        for row in all_rows[1:6]:
            if mapping.day is not None and mapping.day < len(row):
                try:
                    code = int(row[mapping.day])
                    if code >= 10000:
                        jalali_year = code // 10000
                        break
                except (ValueError, TypeError):
                    pass
        if jalali_year is None:
            # Default to current Jalali year
            jalali_year = jdatetime.date.today().year

    # Parse data rows
    vouchers_map: dict[str, ParsedVoucher] = {}
    errors: list[str] = []
    seen_accounts: dict[str, AccountSuggestion] = {}
    data_rows = all_rows[1:]  # skip header

    for row_idx, row in enumerate(data_rows, start=2):
        # Skip empty rows
        if not any(c for c in row):
            continue

        def _get(col_idx: int | None, default: Any = '') -> Any:
            if col_idx is None or col_idx >= len(row):
                return default
            return row[col_idx] if row[col_idx] is not None else default

        voucher_key = str(_get(mapping.voucher_num, ''))
        if not voucher_key or voucher_key == 'None':
            continue  # skip rows without voucher number

        day_code = _get(mapping.day)
        title1 = str(_get(mapping.title1, '')).strip()
        title2 = str(_get(mapping.title2, '')).strip()
        title3 = str(_get(mapping.title3, '')).strip()
        notes = str(_get(mapping.notes, '')).strip()
        debit = _parse_amount(_get(mapping.debit))
        credit = _parse_amount(_get(mapping.credit))
        proj_group = str(_get(mapping.project_group, '')).strip() or None
        proj = str(_get(mapping.project, '')).strip() or None
        proj_name = str(_get(mapping.project_name, '')).strip() or None

        # Track unique account combinations
        acct_key = f"{title1}||{title2}||{title3}"
        if acct_key not in seen_accounts and (title1 or title2 or title3):
            suggested = _suggest_account_code(title1, title2, title3)
            seen_accounts[acct_key] = AccountSuggestion(
                title1=title1, title2=title2, title3=title3,
                suggested_code=suggested,
                suggested_name=title3 or title2 or title1,
            )

        line = ParsedJournalLine(
            row_index=row_idx,
            title1=title1, title2=title2, title3=title3,
            description=notes,
            debit=debit, credit=credit,
            project_group=proj_group, project=proj, project_name=proj_name,
        )

        if voucher_key not in vouchers_map:
            greg_date = _jalali_day_to_gregorian(day_code, jalali_year)
            vouchers_map[voucher_key] = ParsedVoucher(
                voucher_number=voucher_key,
                date_code=day_code,
                gregorian_date=greg_date,
            )
        vouchers_map[voucher_key].lines.append(line)

    # Calculate totals and check balances
    vouchers: list[ParsedVoucher] = []
    for v in vouchers_map.values():
        v.total_debit = sum(l.debit for l in v.lines)
        v.total_credit = sum(l.credit for l in v.lines)
        v.is_balanced = abs(v.total_debit - v.total_credit) < 0.01
        if not v.is_balanced:
            errors.append(
                f"Voucher {v.voucher_number}: unbalanced "
                f"(debit={v.total_debit}, credit={v.total_credit})"
            )
        vouchers.append(v)

    # Sort vouchers by number
    vouchers.sort(key=lambda v: (
        int(v.voucher_number) if str(v.voucher_number).isdigit() else 0,
        str(v.voucher_number),
    ))

    # Build preview (first 20 data rows + header)
    raw_preview = all_rows[:21]

    return ExcelParseResult(
        vouchers=vouchers,
        column_mapping=mapping,
        headers=headers,
        raw_preview=raw_preview,
        unique_accounts=list(seen_accounts.values()),
        jalali_year=jalali_year,
        errors=errors,
        total_rows=len(data_rows),
        total_vouchers=len(vouchers),
    )
