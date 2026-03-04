"""
Bank statement parser: handles CSV, Excel, and OCR-extracted statements.
Normalizes raw data into structured BankStatementRow records.
"""
from __future__ import annotations

import csv
import io
import json
import logging
import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

from app.utils.jalali import try_parse_jalali
from app.utils.persian_numbers import parse_persian_number, _persian_to_ascii

logger = logging.getLogger(__name__)


@dataclass
class ParsedRow:
    row_index: int
    tx_date: date
    description: str = ""
    reference: str | None = None
    debit: int = 0
    credit: int = 0
    balance: int | None = None
    counterparty: str | None = None
    raw_text: str = ""
    confidence: float = 1.0


@dataclass
class ParseResult:
    rows: list[ParsedRow] = field(default_factory=list)
    bank_name: str = ""
    account_number: str | None = None
    currency: str = "IRR"
    from_date: date | None = None
    to_date: date | None = None
    errors: list[str] = field(default_factory=list)
    source_type: str = "csv"


_PERSIAN_DIGIT_TABLE = str.maketrans("۰۱۲۳۴۵۶۷۸۹", "0123456789")
_ARABIC_DIGIT_TABLE = str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789")


def _normalize_digits(text: str) -> str:
    return text.translate(_PERSIAN_DIGIT_TABLE).translate(_ARABIC_DIGIT_TABLE)


def _parse_amount(raw: str) -> int:
    """Parse an amount string into integer Rials, handling various formats."""
    if not raw:
        return 0
    text = _normalize_digits(str(raw).strip())
    text = text.replace(",", "").replace("_", "").replace(" ", "")
    neg = text.startswith("-") or text.startswith("(")
    text = text.strip("-()").strip()
    if not text:
        return 0
    try:
        val = abs(int(float(text)))
    except (ValueError, TypeError):
        fa = parse_persian_number(raw)
        val = fa if fa else 0
    return -val if neg else val


def _parse_date(raw: str) -> date | None:
    """Parse a date string — tries Jalali first, then ISO, then common formats."""
    if not raw:
        return None
    text = _normalize_digits(str(raw).strip())

    jalali = try_parse_jalali(text)
    if jalali:
        return jalali

    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%d/%m/%Y", "%m/%d/%Y", "%d-%m-%Y"):
        try:
            from datetime import datetime as _dt
            return _dt.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


_HEADER_PATTERNS = {
    "date": re.compile(r"(date|تاریخ|tarikh)", re.IGNORECASE),
    "description": re.compile(r"(description|شرح|توضیح|بابت|desc)", re.IGNORECASE),
    "debit": re.compile(r"(debit|بدهکار|withdraw|برداشت|مبلغ بدهکار)", re.IGNORECASE),
    "credit": re.compile(r"(credit|بستانکار|deposit|واریز|مبلغ بستانکار)", re.IGNORECASE),
    "balance": re.compile(r"(balance|مانده|موجودی)", re.IGNORECASE),
    "reference": re.compile(r"(ref|reference|شماره پیگیری|tracking|شناسه)", re.IGNORECASE),
    "amount": re.compile(r"(amount|مبلغ|مقدار)(?!.*(?:بدهکار|بستانکار|debit|credit))", re.IGNORECASE),
}


def _detect_columns(headers: list[str]) -> dict[str, int]:
    """Map semantic roles to column indices from header row."""
    mapping: dict[str, int] = {}
    for i, h in enumerate(headers):
        h_clean = h.strip()
        for role, pat in _HEADER_PATTERNS.items():
            if pat.search(h_clean) and role not in mapping:
                mapping[role] = i
                break
    return mapping


def _extract_counterparty(desc: str) -> str | None:
    """Try to extract a counterparty name from the description."""
    if not desc:
        return None
    patterns = [
        r"(?:from|to|بابت|از|به)\s+(.{3,60}?)(?:\s*[-–]|\s*$)",
        r"(?:transfer|واریز|انتقال)\s+(?:to|به|از|from)\s+(.{3,60}?)(?:\s*[-–]|\s*$)",
    ]
    for pat in patterns:
        m = re.search(pat, desc, re.IGNORECASE)
        if m:
            name = m.group(1).strip().rstrip(".-,;:)")
            if len(name) >= 2:
                return name
    return None


def parse_csv(content: str | bytes, bank_name: str = "") -> ParseResult:
    """Parse a CSV bank statement."""
    if isinstance(content, bytes):
        for enc in ("utf-8-sig", "utf-8", "cp1256", "latin-1"):
            try:
                content = content.decode(enc)
                break
            except UnicodeDecodeError:
                continue

    result = ParseResult(source_type="csv", bank_name=bank_name)
    reader = csv.reader(io.StringIO(content))
    all_rows = list(reader)

    if len(all_rows) < 2:
        result.errors.append("CSV has fewer than 2 rows (need header + data)")
        return result

    col_map = _detect_columns(all_rows[0])
    if "date" not in col_map:
        result.errors.append("Could not detect a date column in headers")
        return result

    for idx, row in enumerate(all_rows[1:], start=1):
        if not any(cell.strip() for cell in row):
            continue
        try:
            date_val = _parse_date(row[col_map["date"]]) if "date" in col_map else None
            if not date_val:
                result.errors.append(f"Row {idx}: could not parse date '{row[col_map.get('date', 0)]}'")
                continue

            desc = row[col_map["description"]].strip() if "description" in col_map and col_map["description"] < len(row) else ""
            ref = row[col_map["reference"]].strip() if "reference" in col_map and col_map["reference"] < len(row) else None

            debit = 0
            credit = 0
            if "debit" in col_map and "credit" in col_map:
                debit = abs(_parse_amount(row[col_map["debit"]])) if col_map["debit"] < len(row) else 0
                credit = abs(_parse_amount(row[col_map["credit"]])) if col_map["credit"] < len(row) else 0
            elif "amount" in col_map:
                amt = _parse_amount(row[col_map["amount"]]) if col_map["amount"] < len(row) else 0
                if amt < 0:
                    debit = abs(amt)
                else:
                    credit = amt

            balance = None
            if "balance" in col_map and col_map["balance"] < len(row):
                balance = _parse_amount(row[col_map["balance"]])

            parsed = ParsedRow(
                row_index=idx,
                tx_date=date_val,
                description=desc,
                reference=ref if ref else None,
                debit=debit,
                credit=credit,
                balance=balance,
                counterparty=_extract_counterparty(desc),
                raw_text=",".join(row),
                confidence=0.95,
            )
            result.rows.append(parsed)
        except (IndexError, ValueError) as e:
            result.errors.append(f"Row {idx}: {e}")

    if result.rows:
        result.from_date = min(r.tx_date for r in result.rows)
        result.to_date = max(r.tx_date for r in result.rows)
    return result


def parse_excel(file_path: str, bank_name: str = "") -> ParseResult:
    """Parse an Excel bank statement using openpyxl."""
    result = ParseResult(source_type="excel", bank_name=bank_name)
    try:
        import openpyxl
    except ImportError:
        result.errors.append("openpyxl not installed — cannot parse Excel files")
        return result

    wb = openpyxl.load_workbook(file_path, read_only=True, data_only=True)
    ws = wb.active
    all_rows = [[str(cell.value or "") for cell in row] for row in ws.iter_rows()]
    wb.close()

    if len(all_rows) < 2:
        result.errors.append("Excel has fewer than 2 rows")
        return result

    csv_text = "\n".join(",".join(row) for row in all_rows)
    return parse_csv(csv_text, bank_name=bank_name)


def parse_ocr_rows(ocr_text: str, bank_name: str = "") -> ParseResult:
    """
    Parse OCR-extracted text from a bank statement image/PDF.
    Handles tabular text that may be misaligned.
    """
    result = ParseResult(source_type="ocr_image", bank_name=bank_name)
    lines = [l.strip() for l in ocr_text.split("\n") if l.strip()]

    date_amount_pattern = re.compile(
        r"(\d{2,4}[/\-\.]\d{1,2}[/\-\.]\d{1,4})"  # date
        r"\s+"
        r"(.*?)"  # description
        r"\s+"
        r"([\d,۰-۹,.]+)"  # amount1
        r"(?:\s+([\d,۰-۹,.]+))?"  # optional amount2
        r"(?:\s+([\d,۰-۹,.]+))?",  # optional balance
    )

    idx = 0
    for line in lines:
        m = date_amount_pattern.search(line)
        if not m:
            continue
        d = _parse_date(m.group(1))
        if not d:
            continue
        idx += 1
        desc = m.group(2).strip()
        amt1 = _parse_amount(m.group(3))
        amt2 = _parse_amount(m.group(4) or "")
        bal = _parse_amount(m.group(5) or "") or None

        debit, credit = 0, 0
        if amt2:
            debit, credit = amt1, amt2
        elif amt1 < 0:
            debit = abs(amt1)
        else:
            credit = amt1

        result.rows.append(ParsedRow(
            row_index=idx,
            tx_date=d,
            description=desc,
            debit=debit,
            credit=credit,
            balance=bal,
            counterparty=_extract_counterparty(desc),
            raw_text=line,
            confidence=0.7,
        ))

    if result.rows:
        result.from_date = min(r.tx_date for r in result.rows)
        result.to_date = max(r.tx_date for r in result.rows)

    return result


def classify_transaction(description: str) -> tuple[str | None, str | None]:
    """
    Classify a bank statement row description into a category and suggested account code.
    Returns (category, suggested_account_code).
    """
    low = (description or "").lower()

    rules: list[tuple[list[str], str, str]] = [
        (["salary", "payroll", "حقوق", "دستمزد"], "salary", "6110"),
        (["rent", "اجاره"], "rent", "6112"),
        (["electricity", "gas", "water", "برق", "گاز", "آب", "قبض"], "utilities", "6190"),
        (["insurance", "بیمه"], "insurance", "6140"),
        (["tax", "مالیات", "ارزش افزوده", "vat"], "tax", "2120"),
        (["loan", "وام", "تسهیلات"], "loan", "2110"),
        (["purchase", "خرید", "buy"], "purchase", "5110"),
        (["sale", "فروش", "revenue", "درآمد"], "revenue", "4110"),
        (["transfer", "انتقال", "حواله"], "transfer", "1110"),
        (["fee", "commission", "کارمزد", "wage"], "bank_fee", "6180"),
        (["interest", "سود", "بهره"], "interest", "4120"),
    ]

    for keywords, category, code in rules:
        if any(k in low for k in keywords):
            return category, code

    return None, None
