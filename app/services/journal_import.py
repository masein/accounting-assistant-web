"""Historical journals from another system (roadmap 2026-09 §4.11).

One importer for the journal (general journal / دفتر روزنامه) exports of
Hesabfa, Sepidar, Holoo, Xero, QuickBooks and any spreadsheet with one line
per row. Nothing is tied to one vendor's exact layout — they change between
versions and languages — instead:

* the **header row** is found by its column names (English and Persian,
  Arabic ي/ك folded), even under a report title; the user can change any
  detected column in the preview;
* a **preset** only sets what the headers can't say: how dates are written
  (US month-first for QuickBooks, Jalali for the Iranian systems) and how
  rows group into vouchers. It is guessed from the headers;
* rows group into **vouchers** by the voucher/entry number (carried down when
  only the first row of a voucher has it, as QuickBooks and Sepidar do), else
  by date + reference; "Total" / "جمع" rows are skipped;
* each **account** in the file is matched to the chart by code, then by name;
  what doesn't match is mapped by the user once and remembered;
* the preview reports unbalanced vouchers, dates in a closed period, and
  vouchers already imported (reference ``IMPORT-<preset>-<voucher>``), none
  of which is posted; the rest posts through the canonical ledger path.
"""
from __future__ import annotations

import difflib
import io
import json
import re
from collections import Counter, OrderedDict, defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.services.book_text import bt
from app.core.money import json_dumps, grouped

MAX_ROWS = 50_000
MAX_VOUCHERS = 10_000
ROUNDING_LIMIT = 10                              # whole units (and at most 0.001%) a voucher may be out and still balance
MAP_KEY = "journal_import_account_map"          # company app_settings row: source account → chart code
PRESETS = ("auto", "generic", "quickbooks", "xero", "hesabfa", "sepidar", "holoo")
IRANIAN = ("hesabfa", "sepidar", "holoo")
FIELDS = ("date", "voucher", "reference", "account_code", "account_name", "account_code_general",
          "account_name_general", "description", "debit", "credit", "amount", "party", "currency")

_SYNONYMS: dict[str, tuple[str, ...]] = {
    "date": ("date", "transaction date", "journal date", "entry date", "posting date", "تاریخ", "تاریخ سند",
             "تاریخ ثبت"),
    "voucher": ("journal number", "journal no", "journal no.", "journal #", "journal", "entry no", "entry number",
                "num", "no", "no.", "voucher", "voucher no", "voucher number", "شماره سند", "ش سند", "ش.سند",
                "شماره", "سند", "شماره سند حسابداری", "ردیف"),
    "reference": ("reference", "ref", "ref no", "reference number", "مرجع", "شماره عطف", "عطف", "شماره مرجع"),
    "account_code": ("account code", "acct code", "account no", "account number", "code", "gl code", "کد حساب",
                     "کد", "کد معین", "کد کل", "شماره حساب", "کد حساب معین", "کد حساب کل"),
    "account_name": ("account", "account name", "gl account", "نام حساب", "شرح حساب", "حساب", "عنوان حساب",
                     "نام معین", "معین", "عنوان حساب معین", "عنوان معین", "نام حساب معین", "عنوان حساب کل",
                     "عنوان کل", "نام حساب کل", "نام کل"),
    "description": ("description", "memo/description", "memo", "narration", "details", "line description", "شرح",
                    "شرح سند", "توضیحات", "شرح آرتیکل", "شرح ردیف", "توضیح"),
    "debit": ("debit", "debits", "debit amount", "dr", "debit (gbp)", "debit (usd)", "بدهکار", "بدهكار", "بدهی",
              "مبلغ بدهکار", "گردش بدهکار"),
    "credit": ("credit", "credits", "credit amount", "cr", "credit (gbp)", "credit (usd)", "بستانکار", "بستانكار",
               "مبلغ بستانکار", "گردش بستانکار"),
    "amount": ("amount", "net", "net amount", "gross", "value", "مبلغ", "net (gbp)"),
    "party": ("name", "contact", "customer", "supplier", "vendor", "payee", "طرف حساب", "طرف مقابل", "نام تفصیلی",
              "تفصیلی", "شرح تفصیلی", "کد تفصیلی", "نام طرف حساب"),
    "currency": ("currency", "ccy", "ارز", "واحد پول"),
}
_TOTAL_WORDS = ("total", "totals", "جمع", "جمع کل", "مجموع", "grand total")
_MONTHS = {m: i for i, m in enumerate(
    ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"), start=1)}


def _norm(text: Any) -> str:
    from app.services.migration_import import normalize_fa
    s = normalize_fa(str(text)) if text is not None else ""
    return re.sub(r"\s+", " ", s.replace("‌", " ")).strip().lower()


# --- reading ----------------------------------------------------------------------------------------

def read_rows(filename: str, data: bytes) -> list[list[str | None]]:
    from app.services import migration_import as mig
    try:
        if data[:2] == b"PK":
            rows = mig._rows_from_xlsx(data)
        elif data.lstrip(b"\xef\xbb\xbf").lstrip()[:5] == b"<?xml" or b"<Workbook" in data[:4096]:
            rows = mig._rows_from_spreadsheetml(data)
        else:
            text = data.decode("utf-8-sig", errors="replace")
            import csv
            import io
            dialect = csv.excel_tab if filename.lower().endswith(".tsv") or text.count("\t") > text.count(",") else csv.excel
            rows = [list(r) for r in csv.reader(io.StringIO(text), dialect)]
    except mig.MigrationParseError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001 — a corrupt workbook
        raise HTTPException(status_code=422, detail=f"Could not read the file: {exc}") from exc
    if len(rows) > MAX_ROWS:
        raise HTTPException(status_code=422, detail=f"More than {grouped(MAX_ROWS)} rows — split the export by year.")
    return rows


def read_book(filename: str, data: bytes) -> list[tuple[str, list[list[str | None]]]]:
    """Every sheet of a workbook as (name, rows); another file is one unnamed sheet."""
    if data[:2] != b"PK":
        return [("", read_rows(filename, data))]
    from openpyxl import load_workbook
    try:
        wb = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    except Exception as exc:  # noqa: BLE001 — a corrupt workbook
        raise HTTPException(status_code=422, detail=f"Could not read the file: {exc}") from exc
    try:
        book = []
        for ws in wb.worksheets:
            rows = [[(str(c) if c is not None else None) for c in row] for row in ws.iter_rows(values_only=True)]
            if len(rows) > MAX_ROWS:
                raise HTTPException(status_code=422, detail=f"More than {grouped(MAX_ROWS)} rows — split the export by year.")
            book.append((ws.title, rows))
        return book
    finally:
        wb.close()


def _journal_score(rows: list[list]) -> int:
    try:
        _i, found, _h = detect_columns(rows)
    except HTTPException:
        return 0
    if not (("debit" in found and "credit" in found) or "amount" in found):
        return 0
    return len(found) + (3 if "account_code" in found or "account_name" in found else 0)


def _first_date(rows: list[list]) -> date | None:
    try:
        i, found, _h = detect_columns(rows)
    except HTTPException:
        return None
    col = found.get("date")
    if col is None:
        return None
    seen = [parse_date(r[col]) for r in rows[i + 1:i + 200] if r and col < len(r) and r[col] not in (None, "")]
    seen = sorted(d for d in seen if d is not None)
    return seen[len(seen) // 2] if seen else None           # the median: a typed year doesn't decide


def pick_sheet(book: list[tuple[str, list[list]]], wanted: str | None = None) -> tuple[str, list[list], list[dict]]:
    """The sheet to read — the one asked for, else the one that looks most like
    a journal (a hand-kept workbook keeps its bank statements, chart and trial
    balance beside the journal, and the first sheet was read: a bank
    statement, 2026-10-12) — with every sheet for the page to offer."""
    sheets = [{"name": n, "rows": sum(1 for r in rows if any(c not in (None, "") for c in r)),
               "score": _journal_score(rows)} for n, rows in book]
    if wanted is not None:
        for n, rows in book:
            if n == wanted:
                return n, rows, sheets
        raise HTTPException(status_code=422, detail=f"No sheet named «{wanted}» in the file.")
    # two journals alike (a year's halves, two years): the earlier first, as they go in oldest first
    firsts = [_first_date(rows) if sheets[i]["score"] else None for i, (_n, rows) in enumerate(book)]
    best = max(range(len(book)), key=lambda i: (sheets[i]["score"], -(firsts[i].toordinal() if firsts[i] else 10 ** 7), -i))
    return book[best][0], book[best][1], sheets


# A group-level code names the account only roughly# A group-level code names the account only roughly: an Iranian export lists
# «کد کل» (11) before «کد معین» (1112), and the first column used to win, so
# every line went to the group account. A more specific column takes over.
_GENERAL_HEADERS = {"کد کل", "کد گروه", "نام کل", "نام گروه", "کد حساب کل", "عنوان حساب کل", "عنوان کل", "نام حساب کل"}


def _match_field(header: str) -> str | None:
    h = re.sub(r"\([^)]*\)", "", _norm(header)).rstrip(":").strip()
    if not h:
        return None
    for fld, words in _SYNONYMS.items():
        if h in words:
            return fld
    return None


def detect_columns(rows: list[list]) -> tuple[int, dict[str, int], list[str]]:
    """(header row index, field → column, header texts) — the row among the first
    30 whose cells name the most fields, needing at least debit+credit or amount."""
    best = (-1, {}, [], 0)
    for i, row in enumerate(rows[:30]):
        found: dict[str, int] = {}
        weak: set[str] = set()
        general_col: dict[str, int] = {}
        for j, cell in enumerate(row or []):
            fld = _match_field(cell) if cell is not None else None
            if not fld:
                continue
            general = _norm(cell).rstrip(":").strip() in _GENERAL_HEADERS
            if general:
                general_col.setdefault(fld, j)
            if fld not in found or (fld in weak and not general):
                found[fld] = j
                weak.discard(fld)
                if general:
                    weak.add(fld)
        # «کد حساب کل» beside «کد حساب معین»: a line with no sub-ledger takes its ledger
        for fld in ("account_code", "account_name"):
            if fld in general_col and found.get(fld) != general_col[fld]:
                found[f"{fld}_general"] = general_col[fld]
        if found:
            _sniff(rows[i + 1:i + 61], row, found)
        score = len(found) + (2 if ("debit" in found and "credit" in found) or "amount" in found else 0)
        if found and score > best[3]:
            best = (i, found, [str(c) if c is not None else "" for c in row], score)
    if best[0] < 0:                              # nothing recognisable: the first row, for the user to map
        first = next((i for i, r in enumerate(rows[:30]) if any(c not in (None, "") for c in (r or []))), None)
        if first is None:
            raise HTTPException(status_code=422, detail="The file has no rows.")
        return first, {}, [str(c) if c is not None else "" for c in rows[first]]
    return best[0], best[1], best[2]


_DATE_LIKE = re.compile(r"^\s*(1[3-4]\d\d|20\d\d|19\d\d)[/\-.]\d{1,2}[/\-.]\d{1,2}")


def _sniff(sample: list[list], header: list, found: dict[str, int]) -> None:
    """A column whose header cell is blank, found by what it holds: dates, or
    voucher numbers (small whole numbers repeating over a voucher's lines). A
    hand-kept journal's second-half sheet labelled neither (2026-10-12)."""
    blank = [j for j in range(max((len(r) for r in sample), default=0))
             if j >= len(header) or header[j] in (None, "") or not str(header[j]).strip()]
    values = {j: [r[j] for r in sample if j < len(r) and r[j] not in (None, "") and str(r[j]).strip()] for j in blank}
    if "date" not in found:
        for j in blank:
            vs = values[j]
            if len(vs) >= 3 and sum(1 for v in vs if _DATE_LIKE.match(_ascii(v))) >= 0.8 * len(vs):
                found["date"] = j
                break
    if "voucher" not in found:
        filled = sum(1 for r in sample if any(c not in (None, "") for c in r))
        for j in blank:
            if j == found.get("date"):
                continue
            vs = [_ascii(v) for v in values[j]]
            nums = [v[:-2] if v.endswith(".0") else v for v in vs]
            if len(nums) < 3 or not all(n.isdigit() and len(n) <= 7 for n in nums):
                continue
            ints = [int(n) for n in nums]
            rising = all(a <= b for a, b in zip(ints, ints[1:]))
            # repeating over a voucher's lines, or written on its first line only
            if rising and (len(set(nums)) < len(nums) or len(nums) < 0.9 * filled):
                found["voucher"] = j
                break


def guess_preset(headers: list[str]) -> str:
    joined = " | ".join(_norm(h) for h in headers)
    if re.search(r"[؀-ۿ]", joined):
        if "کد حساب کل" in joined and "کد حساب معین" in joined:
            return "generic"                 # a hand-kept journal: ledger and sub-ledger side by side
        return "sepidar" if "کد تفصیلی" in joined or "کد معین" in joined else "hesabfa"
    if "transaction type" in joined or "memo/description" in joined:
        return "quickbooks"
    if "narration" in joined or "journal number" in joined or "journal #" in joined:
        return "xero"
    return "generic"


# --- values -----------------------------------------------------------------------------------------

def parse_amount(raw: Any) -> Decimal:
    if raw is None:
        return Decimal(0)
    s = _norm(raw).replace("٬", ",").replace("٫", ".").replace(" ", "")
    s = re.sub(r"[^\d.,()\-+cr]", "", s)
    if not s or s in ("-", "--"):
        return Decimal(0)
    neg = s.startswith("(") and s.endswith(")") or s.startswith("-") or s.endswith("-") or s.endswith("cr")
    s = s.strip("()+-").removesuffix("cr").removesuffix("dr")
    if s.count(",") and s.count("."):
        s = s.replace(",", "") if s.rfind(".") > s.rfind(",") else s.replace(".", "").replace(",", ".")
    elif s.count(",") and not re.fullmatch(r"\d+,\d{1,2}", s):
        s = s.replace(",", "")
    else:
        s = s.replace(",", ".")
    try:
        v = Decimal(s)
    except InvalidOperation:
        return Decimal(0)
    return -v if neg else v


def parse_date(raw: Any, *, order: str = "dmy") -> date | None:
    """ISO, Excel serials and datetimes, d/m/y or m/d/y (``order``), '27 Sep 2026',
    and Jalali (a year below 1700), Persian digits included."""
    if raw is None:
        return None
    if isinstance(raw, datetime):
        return raw.date()
    if isinstance(raw, date):
        return raw
    s = _norm(raw)
    if not s:
        return None
    m = re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})(?:[ t]\d{2}:\d{2}(?::\d{2})?)?", s)
    if m:
        y, mo, d = map(int, m.groups())
        return _gregorian_or_jalali(y, mo, d)
    if re.fullmatch(r"\d{5}(\.\d+)?", s):                       # an Excel serial
        return date(1899, 12, 30) + timedelta(days=int(float(s)))
    m = re.fullmatch(r"(1[34]\d{2}|20\d{2})(\d{2})(\d{2})(?:\.0)?", s)   # year, month, day run together: 14040101
    if m:
        return _gregorian_or_jalali(*map(int, m.groups()))
    m = re.fullmatch(r"(\d{1,2})[ -]([a-z]{3})[a-z]*[ ,-]+(\d{4})", s)
    if m and m.group(2) in _MONTHS:
        return _safe(int(m.group(3)), _MONTHS[m.group(2)], int(m.group(1)))
    m = re.fullmatch(r"([a-z]{3})[a-z]* (\d{1,2}),? (\d{4})", s)
    if m and m.group(1) in _MONTHS:
        return _safe(int(m.group(3)), _MONTHS[m.group(1)], int(m.group(2)))
    m = re.fullmatch(r"(\d{1,4})[/.\-](\d{1,2})[/.\-](\d{1,4})", s)
    if not m:
        return None
    a, b, c = map(int, m.groups())
    if a > 31:                                                  # year first: 1405/07/05 or 2026/09/27
        return _gregorian_or_jalali(a, b, c)
    year = c + (2000 if c < 100 else 0)
    day, month = (b, a) if order == "mdy" else (a, b)
    if month > 12 and day <= 12:
        day, month = month, day
    return _gregorian_or_jalali(year, month, day)


def _safe(y, m, d):
    try:
        return date(y, m, d)
    except ValueError:
        return None


def _gregorian_or_jalali(y, m, d):
    if y < 1700:
        import jdatetime
        try:
            return jdatetime.date(y, m, d).togregorian()
        except ValueError:
            return None
    return _safe(y, m, d)


# --- parsing ----------------------------------------------------------------------------------------------

@dataclass
class Line:
    row: int
    account_code: str
    account_name: str
    description: str
    debit: int
    credit: int
    party: str | None = None
    currency: str | None = None
    exact: Decimal = Decimal(0)
    ledger: str = ""                     # the line's ledger (کل) title, when a sub-ledger named it
    ledger_code: str = ""                # and the ledger's code in the file (1300 over 1301–1306)

    @property
    def account_key(self) -> str:
        return f"{self.account_code}|{_norm(self.account_name)}" if self.account_code else f"|{_norm(self.account_name)}"


@dataclass
class Voucher:
    key: str
    number: str
    on: date | None
    reference: str | None
    lines: list[Line] = field(default_factory=list)

    @property
    def debit(self) -> int:
        return sum(ln.debit for ln in self.lines)

    @property
    def credit(self) -> int:
        return sum(ln.credit for ln in self.lines)


def _cell(row, col):
    if col is None or col >= len(row):
        return None
    v = row[col]
    return None if v is None or str(v).strip() == "" else v


def _ascii(value) -> str:
    from app.services.migration_import import normalize_fa
    return normalize_fa(str(value)).strip()


def _round_voucher(v: "Voucher") -> int:
    """Whole units, half up. A voucher that balances to the cent must still
    balance after rounding: the difference goes on its largest line (the
    usual rounding plug). Returns how many lines had fractions."""
    fractional = 0
    for ln in v.lines:
        n = int(ln.exact.quantize(Decimal(1), rounding=ROUND_HALF_UP)) if ln.exact >= 0 else \
            -int((-ln.exact).quantize(Decimal(1), rounding=ROUND_HALF_UP))
        fractional += ln.exact != n
        ln.debit, ln.credit = max(n, 0), max(-n, 0)
    off = sum(ln.exact for ln in v.lines)
    total = sum(ln.exact for ln in v.lines if ln.exact > 0)
    if off != 0 and abs(off) <= ROUNDING_LIMIT and abs(off) * 100_000 <= total:
        # a hand-kept journal a rial or two out (an opening voucher among them):
        # the difference goes on the largest line, as the decimals' rounding does
        biggest = max(v.lines, key=lambda ln: abs(ln.exact))
        biggest.exact -= off
        fractional += 1
    if sum(ln.exact for ln in v.lines) == 0:
        # Take each surplus unit back from the line whose rounding added the
        # most on the heavy side, so the totals stay the true (rounded) totals.
        gap = sum(ln.debit - ln.credit for ln in v.lines)
        while gap:
            step = 1 if gap > 0 else -1
            side = [ln for ln in v.lines if (ln.debit - ln.credit) * step > 0] or v.lines
            pick = max(side, key=lambda ln: ((ln.debit - ln.credit) - ln.exact) * step)
            n = pick.debit - pick.credit - step
            pick.debit, pick.credit = max(n, 0), max(-n, 0)
            gap -= step
    return fractional


def parse_file(rows: list[list], *, preset: str = "auto", columns: dict[str, int] | None = None) -> dict[str, Any]:
    header_idx, detected, headers = detect_columns(rows)
    preset = guess_preset(headers) if preset in (None, "", "auto") else preset
    if preset not in PRESETS:
        raise HTTPException(status_code=422, detail=f"Unknown preset: {preset}")
    cols = dict(detected)
    for fld, col in (columns or {}).items():                       # the user's corrections
        if fld not in FIELDS:
            raise HTTPException(status_code=422, detail=f"Unknown field: {fld}")
        if col is None or (isinstance(col, int) and col < 0):
            cols.pop(fld, None)
        elif isinstance(col, int) and col < len(headers):
            cols[fld] = col
    # «ردیف» numbers a voucher when it repeats over the voucher's lines; one per
    # line, it only counts rows (a bank statement's): then the date groups them
    if "voucher" in cols and "voucher" not in (columns or {}) \
            and _norm(headers[cols["voucher"]] if cols["voucher"] < len(headers) else "") == "ردیف":
        filled = [r for r in rows[header_idx + 1:] if r and any(c not in (None, "") for c in r)]
        seen_numbers = [_ascii(r[cols["voucher"]]) for r in filled
                        if cols["voucher"] < len(r) and r[cols["voucher"]] not in (None, "")]
        # blank on a voucher's later lines (merged cells) is a voucher number carried down
        if seen_numbers and len(set(seen_numbers)) == len(seen_numbers) and len(seen_numbers) >= 0.9 * len(filled):
            cols.pop("voucher")
    # a number column with nothing in it numbers nothing (a 1405 sheet left «ردیف» empty
    # and its 121 lines read as one voucher, 2026-10-12)
    if "voucher" in cols and "voucher" not in (columns or {}) and not any(
            r and cols["voucher"] < len(r) and r[cols["voucher"]] not in (None, "") and str(r[cols["voucher"]]).strip()
            for r in rows[header_idx + 1:]):
        cols.pop("voucher")
    needs = []
    if not (("debit" in cols and "credit" in cols) or "amount" in cols):
        needs.append("amounts")
    if "account_code" not in cols and "account_name" not in cols:
        needs.append("account")
    if "date" not in cols:
        needs.append("date")
    if needs:
        return {"preset": preset, "grouping": None, "header_row": header_idx + 1, "headers": headers,
                "columns": cols, "vouchers": [], "skipped": [], "rows_without_account": 0, "needs": needs,
                "rounded_lines": 0}
    order = "mdy" if preset == "quickbooks" else "dmy"
    # QuickBooks fills the date (and number) only on a transaction's first row:
    # a filled date starts the next voucher. Otherwise the voucher number
    # decides, carried down while blank; without one, date + reference.
    grouping = "block" if preset == "quickbooks" else ("number" if "voucher" in cols else "date")
    vouchers: "OrderedDict[str, Voucher]" = OrderedDict()
    skipped: list[dict[str, Any]] = []
    no_account = 0
    block, cur_number, cur_date, cur_ref = 0, None, None, None
    day_block, running = 0, Decimal(0)              # by date: a voucher ends where its lines balance
    jalali_dates = 0                                # dates written in the Jalali calendar (1404/03/31)
    for i, row in enumerate(rows[header_idx + 1:], start=header_idx + 2):
        row = list(row or [])
        texts = [_norm(c) for c in row if c is not None and str(c).strip()]
        if not texts:
            continue
        if any(t in _TOTAL_WORDS or t.startswith(("total ", "total:", "جمع ")) for t in texts[:3]):
            continue
        number = _cell(row, cols.get("voucher"))
        raw_date = _cell(row, cols.get("date"))
        on = parse_date(raw_date, order=order) if raw_date is not None else None
        ref = _cell(row, cols.get("reference"))
        if raw_date is not None and on is None:
            skipped.append({"row": i, "reason": "unreadable date", "value": str(raw_date)[:40]})
            continue
        if on is not None and re.match(r"^\s*1[34]\d\d\b", _ascii(raw_date)):
            jalali_dates += 1
        if grouping == "block":
            if on is not None:
                block, cur_date, cur_number, cur_ref = block + 1, on, (_ascii(number) if number is not None else None), ref
            key = f"b{block}"
        elif grouping == "number":
            if number is not None and _ascii(number) != str(cur_number):
                cur_number, cur_ref = _ascii(number), None
            if on is not None:
                cur_date = on
            key = f"n{cur_number}"
        else:
            if on is not None and on != cur_date:
                cur_date, day_block, running = on, day_block + 1, Decimal(0)
            key = f"d{cur_date}|{ref if ref is not None else cur_ref}|{day_block}"
        if ref is not None:
            cur_ref = str(ref).strip()
        code = re.sub(r"\.0$", "", _ascii(_cell(row, cols.get("account_code")) or ""))
        name = str(_cell(row, cols.get("account_name")) or "").strip()
        ledger = str(_cell(row, cols.get("account_name_general")) or "").strip()
        ledger_code = re.sub(r"\.0$", "", _ascii(_cell(row, cols.get("account_code_general")) or ""))
        if code and not name and ledger:
            name = ledger                    # a sub-ledger code with no title: its ledger's («—» was created)
        if not code and not name:
            # no sub-ledger on this line: its ledger (a hand-kept journal fills
            # «کد حساب معین» only where the ledger has sub-ledgers)
            code, ledger_code = ledger_code, ""
            name, ledger = ledger, ""
        if "amount" in cols and not ("debit" in cols and "credit" in cols):
            amt = parse_amount(_cell(row, cols["amount"]))
            debit, credit = (amt, Decimal(0)) if amt >= 0 else (Decimal(0), -amt)
        else:
            debit, credit = parse_amount(_cell(row, cols["debit"])), parse_amount(_cell(row, cols["credit"]))
        exact = debit - credit
        if not (code or name):
            no_account += 1 if exact else 0                     # subtotal rows and the like
            continue
        if exact == 0:
            continue
        if cur_date is None:
            skipped.append({"row": i, "reason": "no date"})
            continue
        v = vouchers.get(key)
        if v is None:
            if len(vouchers) >= MAX_VOUCHERS:
                raise HTTPException(status_code=422, detail=f"More than {grouped(MAX_VOUCHERS)} vouchers — split the export.")
            label = str(cur_number).strip() if cur_number not in (None, "") else f"{cur_date.isoformat()}-{len(vouchers) + 1}"
            v = vouchers[key] = Voucher(key=key, number=label, on=cur_date, reference=cur_ref)
        party = _cell(row, cols.get("party"))
        v.lines.append(Line(row=i, account_code=code, account_name=name, ledger=ledger,
                            ledger_code=ledger_code if ledger and ledger_code != code else "",
                            description=str(_cell(row, cols.get("description")) or "").strip(),
                            debit=0, credit=0, exact=exact,
                            party=str(party).strip() if party is not None else None,
                            currency=str(_cell(row, cols.get("currency")) or "").strip().upper() or None))
        if grouping == "date":
            running += exact
            if running == 0:
                day_block, running = day_block + 1, Decimal(0)
    rounded = sum(_round_voucher(v) for v in vouchers.values())
    return {"preset": preset, "grouping": grouping, "header_row": header_idx + 1, "headers": headers,
            "columns": cols, "vouchers": list(vouchers.values()), "skipped": skipped,
            "rows_without_account": no_account, "needs": [], "rounded_lines": rounded,
            "jalali_dates": jalali_dates > 0}


# --- accounts ------------------------------------------------------------------------------------------

def saved_map(db: Session) -> dict[str, str]:
    from app.models.app_setting import AppSetting
    row = db.execute(select(AppSetting).where(AppSetting.key == MAP_KEY)).scalar_one_or_none()
    try:
        return json.loads(row.value) if row and row.value else {}
    except ValueError:
        return {}


def save_map(db: Session, mapping: dict[str, str]) -> None:
    from app.models.app_setting import AppSetting
    merged = saved_map(db) | {k: v for k, v in mapping.items() if v}
    row = db.execute(select(AppSetting).where(AppSetting.key == MAP_KEY)).scalar_one_or_none()
    if row is None:
        db.add(AppSetting(key=MAP_KEY, value=json_dumps(merged, ensure_ascii=False)))
    else:
        row.value = json_dumps(merged, ensure_ascii=False)
    db.flush()


# An account a hand-kept journal names that the chart doesn't have: where to
# create it, from its title (any of the words, none of the others). «map»
# takes the chart's own account for the category instead of a new one.
_CREATE_RULES: list[tuple[tuple[str, ...], tuple[str, ...], tuple[str, str]]] = [
    (("سود و زیان انباشته", "سود وزیان انباشته", "زیان انباشته", "سود انباشته", "سود و زیان", "سود وزیان"),
     ("هزینه", "درآمد", "فروش"), ("map", "retained_earnings")),
    (("سرمایه",), ("گذاری", "افزایش", "هزینه"), ("map", "share_capital")),
    (("تنخواه",), (), ("role", "petty_cash")),
    (("وام", "تسهیلات"), ("هزینه", "سود", "کارمزد", "بهره"), ("group", "21")),
    (("بانک",), ("کارمزد", "هزینه", "وام", "تسهیلات", "سپرده", "سود", "بهره"), ("bank", "")),
    (("کارمزد", "بهره", "جریمه دیرکرد"), (), ("group", "62")),
    (("درآمد", "شهریه", "فروش"), ("هزینه", "پیش"), ("group", "41")),
    (("بستانکاران", "پرداختنی", "ذخیره", "ذخایر", "حساب جاری", "جاری شرکا"), (), ("group", "21")),
    (("بدهکاران", "دریافتنی", "ودیعه", "پیش پرداخت", "علی الحساب", "مساعده"), (), ("group", "11")),
    (("استهلاک انباشته",), (), ("group", "12")),
    (("دارایی ثابت", "دارایی های ثابت", "داراییهای ثابت", "اثاثیه", "تجهیزات", "ساختمان", "وسایط", "نرم افزار"),
     ("هزینه",), ("group", "12")),
    (("هزینه", "مخارج", "اجاره", "قبوض", "ملزومات", "پذیرایی", "تعمیرات", "حقوق", "دستمزد", "پرسنل", "بیمه",
      "اردو", "آموزش", "استهلاک", "عیدی", "پاداش"), (), ("group", "61")),
]
CREATE_GROUPS = ("11", "12", "21", "41", "61", "62")


def _create_plan(db: Session, name: str, chart_codes: set[str]) -> str | None:
    """The target for an account the chart lacks: an existing code («map»),
    "new:bank", or "new:<parent code>"; None when the title says nothing."""
    from app.services.account_resolver import find_account_code
    title = _norm(name)
    for words, never, (kind, arg) in _CREATE_RULES:
        if any(_norm(w) in title for w in words) and not any(_norm(w) in title for w in never):
            if kind == "map":
                return find_account_code(db, arg)
            if kind == "bank":
                return "new:bank"
            if kind == "role":
                code = find_account_code(db, arg)
                return f"new:{code}" if code else ("new:11" if "11" in chart_codes else None)
            return f"new:{arg}" if arg in chart_codes else None
    return None


def _names_agree(a: str, b: str) -> bool:
    """Whether a file's account and the chart's under the same code are one. In
    two languages (an English export, a Persian chart) only the code can say."""
    a, b = _norm(a), _norm(b)
    persian = re.compile(r"[؀-ۿ]")
    if not a or not b or bool(persian.search(a)) != bool(persian.search(b)):
        return True
    return a in b or b in a or difflib.SequenceMatcher(None, a, b).ratio() >= 0.5


def create_account_for(db: Session, name: str, target: str, ledger: str = "", code: str = "") -> str:
    """Create (or find) the account a "new:…" choice names; returns its code.
    A bank is a bank as the contacts page makes one. Under a group, the line's
    own ledger (کل, «بستانکاران») is created first and the sub-ledger (معین,
    «تامین اجتماعی») under it, as the source keeps them — every account
    straight under the group used up its four-digit codes (2196–2199 for one
    school's liabilities, 2026-10-12). Each takes its balance-sheet line from
    its title (a petty cash at 1151 would otherwise read by its code). Books that use their own codes
    (docs/PLAN_OWN_CODES.md, scenario AN8) make it under the file's own ``code``, its class from its ledger."""
    from app.models.account import Account
    from app.models.entity import Entity
    from app.services.chart_service import create_account
    from app.services.chart_codes import own_codes_in_use
    from app.services.reporting.statement_lines import infer
    title = (name or "").strip()[:512] or "—"
    own = own_codes_in_use()

    def free(c: str) -> str | None:
        """The file's own code for a new account, when the books use their own codes and it's free."""
        c = (c or "").strip()
        if not (own and c.isdigit()):
            return None
        return None if db.execute(select(Account.id).where(Account.code == c)).first() else c
    if target == "new:bank":
        found = db.execute(select(Entity).where(Entity.type == "bank", Entity.code.isnot(None))).scalars().all()
        for e in found:
            if _norm(e.name) == _norm(title):
                return e.code
        from app.services import migration_import as mig
        from app.services.ai_accountant.entity_create import _resolve_bank_account
        # «بانک دی حساب 0105222028000»: the bank «بانک دی», its account number kept
        number = mig.bank_account_number(title)
        bank_name = re.sub(r"\s*(حساب|شماره حساب|ش\.?ح)\s*$", "", mig.strip_account_number(title)).strip() or title
        for e in found:
            if number and (e.account_number or "").strip() == number:
                return e.code
        mine = free(code)
        if mine:                                   # a bank of books in their own codes: its file code
            from app.services.book_text import bt
            group = db.execute(select(Account).where(Account.class_code == "11")).scalars().first()
            acc = create_account(db, name=bt(db, "bank_account_name", name=bank_name),
                                 parent_code=group.code if group else None, code=mine)
            bank_code = acc.code
        else:
            bank_code, _created = _resolve_bank_account(db, bank_name, None, None)
        db.add(Entity(type="bank", name=bank_name, code=bank_code, account_number=number))
        db.flush()
        return bank_code
    from app.models.account import AccountLevel

    def child_named(parent: Account | None, wanted: str) -> Account | None:
        if parent is None:
            return None
        for child in db.execute(select(Account).where(Account.parent_id == parent.id)).scalars():
            if _norm(child.name) == _norm(wanted):
                return child
        return None

    def make(parent_code: str, wanted: str, own_code: str | None = None) -> Account:
        acc = create_account(db, name=wanted, parent_code=parent_code, code=own_code)
        line = infer(acc.class_code or acc.code, wanted)            # by what it is, not its own digits
        if line:
            acc.statement_line = line
            db.flush()
        return acc

    def heading_named(wanted: str) -> Account | None:
        """The heading an earlier line made for this ledger, under whichever group."""
        for acc in db.execute(select(Account).where(Account.level != AccountLevel.GROUP,
                                                    Account.is_active.is_(True))).scalars():
            if _norm(acc.name) == _norm(wanted) and db.execute(
                    select(Account.id).where(Account.parent_id == acc.id).limit(1)).first() is not None:
                return acc
        return None

    def ledger_group(wanted: str, fallback: Account) -> str:
        """Where a ledger's heading goes, by its own title: «مخارج مدرسه» under
        61 even when the line that makes it is its bank fees (62)."""
        plan = _create_plan(db, wanted, {c for (c,) in db.execute(select(Account.code)).all()}) or ""
        group = plan[4:] if plan.startswith("new:") else ""
        return group if group in CREATE_GROUPS else fallback.code

    parent = db.execute(select(Account).where(Account.code == target.split(":", 1)[1])).scalars().first()
    if parent is None:
        raise HTTPException(status_code=422, detail=f"Account not found: {target.split(':', 1)[1]}")
    ledger = (ledger or "").strip()[:512]
    if ledger and parent.level == AccountLevel.GROUP and _norm(ledger) != _norm(title):
        # one heading per ledger: its bank fees going to 62 and the rest to 61 made
        # «مخارج مدرسه» twice, 6211 and 6132 (2026-10-19, scenario AH6)
        parent = child_named(parent, ledger) or heading_named(ledger) \
            or make(ledger_group(ledger, parent), ledger)
    found = child_named(parent, title)
    return found.code if found is not None else make(parent.code, title, free(code)).code


def match_accounts(db: Session, vouchers: list[Voucher], overrides: dict[str, str] | None = None) -> list[dict[str, Any]]:
    """One entry per distinct account in the file: how it maps to the chart."""
    from app.models.account import Account, AccountLevel
    chart = db.execute(select(Account).where(Account.is_active.is_(True))).scalars().all()
    postable = [a for a in chart if a.level != AccountLevel.GROUP]
    by_code = {a.code: a for a in postable}
    by_name: dict[str, Account] = {}
    for a in postable:
        by_name.setdefault(_norm(a.name), a)
    remembered = saved_map(db) | (overrides or {})
    seen: "OrderedDict[str, dict]" = OrderedDict()
    for v in vouchers:
        for ln in v.lines:
            k = ln.account_key
            entry = seen.get(k)
            if entry is None:
                entry = seen[k] = {"key": k, "code": ln.account_code, "name": ln.account_name, "ledger": ln.ledger,
                                   "lines": 0, "debit": 0, "credit": 0, "mapped_to": None, "how": None, "suggestions": []}
            entry["lines"] += 1
            entry["debit"] += ln.debit
            entry["credit"] += ln.credit
    names = list(by_name)
    chart_codes = {a.code for a in chart}
    for k, e in seen.items():
        target = remembered.get(k)
        # a sub-ledger named after a person or a school («دبستان فرهنگ نو») says
        # where it belongs through its ledger («بستانکاران»)
        plan = (_create_plan(db, e["name"], chart_codes) if e["name"] else None) \
            or (_create_plan(db, e["ledger"], chart_codes) if e.get("ledger") else None)
        if target and (target in by_code or target.startswith("new:")):
            e["mapped_to"], e["how"] = target, ("create" if target.startswith("new:") else "chosen")
        elif e["code"] and e["code"] in by_code and _names_agree(e["name"], by_code[e["code"]].name):
            # the same code under another name is another account: a hand-kept
            # chart's 1110 «تنخواه گردان» is not the seeded 1110 «موجودی نقد و بانک»
            e["mapped_to"], e["how"] = e["code"], "code"
        elif e["name"] and _norm(e["name"]) in by_name:
            e["mapped_to"], e["how"] = by_name[_norm(e["name"])].code, "name"
        elif plan:
            e["mapped_to"], e["how"] = plan, ("create" if plan.startswith("new:") else "role")
        if plan:
            e["create"] = plan
        if e["mapped_to"] is None or e["how"] == "create":
            close = difflib.get_close_matches(_norm(e["name"] or e["code"]), names, n=3, cutoff=0.5)
            e["suggestions"] = [{"code": by_name[n].code, "name": by_name[n].name} for n in close]
            if e["code"] and e["code"] in by_code and not _names_agree(e["name"], by_code[e["code"]].name):
                e["suggestions"].insert(0, {"code": e["code"], "name": by_code[e["code"]].name})
    return list(seen.values())


# --- preview and apply ----------------------------------------------------------------------------------------

def _party_role(code: str) -> str | None:
    """A party on a receivable or revenue line is a client; on a payable or
    expense line a supplier; on a bank or other line, unknown."""
    from app.services.account_resolver import POSTING_CODES
    from app.services.reporting.common import EXPENSE, REVENUE, classify_account_code
    if code in {t["ar"] for t in POSTING_CODES.values()}:
        return "client"
    if code in {t["ap"] for t in POSTING_CODES.values()}:
        return "supplier"
    kind = classify_account_code(code)
    return "client" if kind == REVENUE else "supplier" if kind == EXPENSE else None


def _reference(preset: str, v: Voucher) -> str:
    """The posted journal's reference, which also marks it imported. With its
    date: an Iranian system numbers vouchers from 1 again every fiscal year, so
    voucher 2 of the second year was taken for the first year's voucher 2 and
    never posted. ISO, so the company's display calendar can't change it."""
    return f"IMPORT-{preset}-{v.number}-{v.on.isoformat() if v.on else ''}"[:128]


def _legacy_reference(preset: str, v: Voucher) -> str:
    """Before the date was added (2026-10-07): still "already imported", but
    only on the same day."""
    return f"IMPORT-{preset}-{v.number}"[:128]


# An Iranian system closes each fiscal year in its own books: the temporary
# accounts into retained earnings, then every account to zero (سند اختتامیه),
# and opens the next year with the same balances (سند افتتاحیه). Posted here
# they would wipe out the year — its income statement read zero revenue and its
# balance sheet zeros — and an opening voucher after a year already in the
# books doubled every balance. The app never closes a year (its statements
# carry the unclosed result in equity), so a closing is left out, and an
# opening is kept only for the first year brought in.
# Year-end closing, not «بستن حساب بانک سپه» (closing a bank account, a real
# entry a hand-kept journal recorded and the bare «بستن حساب» dropped).
_CLOSING_WORDS = ("اختتامیه", "بستن حسابهای موقت", "بستن حساب های موقت", "بستن حسابهای دائم", "بستن حساب های دائم",
                  "بستن حساب سود و زیان", "بستن حسابهای درآمد", "بستن حساب های درآمد", "بستن حسابهای سود و زیان",
                  "سند بستن", "closing entry", "closing entries", "closing voucher", "year-end closing",
                  "year end closing", "close the year", "closing of temporary accounts")
_OPENING_WORDS = ("افتتاحیه", "نقل از سال قبل", "مانده اول دوره", "مانده ابتدای دوره", "opening balance",
                  "opening entry", "opening voucher", "brought forward")


def _jalali_books(db: Session) -> bool:
    from app.services.locale_service import get_display_calendar
    try:
        return get_display_calendar(db) == "jalali"
    except Exception:  # noqa: BLE001 — a calendar never fails an import
        return False


def _year_start(on: date, jalali: bool) -> date:
    """The first day of ``on``'s year in the books' calendar (1 Farvardin)."""
    if not jalali:
        return date(on.year, 1, 1)
    from app.utils.jalali import gregorian_to_jalali, jalali_to_gregorian
    jy, _m, _d = gregorian_to_jalali(on)
    return jalali_to_gregorian(jy, 1, 1)


def _year_of(on: date, jalali: bool) -> int:
    if not jalali:
        return on.year
    from app.utils.jalali import gregorian_to_jalali
    return gregorian_to_jalali(on)[0]


def _in_year(on: date, year: int, jalali: bool) -> date:
    """The same day and month in ``year`` (Esfand 30 / Feb 29 the last day there is)."""
    if not jalali:
        try:
            return on.replace(year=year)
        except ValueError:
            return on.replace(year=year, day=28)
    from app.utils.jalali import gregorian_to_jalali, jalali_to_gregorian
    _y, m, d = gregorian_to_jalali(on)
    for day in range(d, 0, -1):            # Esfand 30 in a year that has 29
        try:
            return jalali_to_gregorian(year, m, day)
        except ValueError:
            continue
    return jalali_to_gregorian(year, m, 1)


def stray_years(db: Session, parsed: dict[str, Any]) -> tuple[int | None, set[str]]:
    """(the file's year, the vouchers typed in another): a year's file where a
    few vouchers — under a tenth — say 1400, 1401 or 1403 among 1404's, as a
    hand-kept journal did (2026-10-12). A file that really spans years has more
    than a few in each and is left alone."""
    jalali = parsed.get("jalali_dates") or _jalali_books(db)        # a Jalali file's years, whatever the books show
    vouchers: list[Voucher] = parsed["vouchers"]
    years = Counter(_year_of(v.on, jalali) for v in vouchers if v.on)
    if len(years) < 2:
        return (next(iter(years)) if years else None), set()
    main, count = years.most_common(1)[0]
    stray = {v.key for v in vouchers if v.on and _year_of(v.on, jalali) != main}
    if len(stray) * 10 > len(vouchers):
        return main, set()
    return main, stray


def settle_years(db: Session, parsed: dict[str, Any], mode: str | None) -> dict[str, Any]:
    """«fix»: move the stray vouchers into the file's year (same day and month);
    «keep»: post them as written; unsaid: they are held back for the owner."""
    main, stray = stray_years(db, parsed)
    parsed["file_year"], parsed["stray_years"], parsed["years_mode"] = main, stray, mode
    # each one's date as the file writes it, still shown once it's moved (scenario P10)
    parsed["stray_written"] = {v.key: v.on.isoformat() for v in parsed["vouchers"] if v.key in stray and v.on}
    if mode == "fix" and stray:
        jalali = parsed.get("jalali_dates") or _jalali_books(db)
        for v in parsed["vouchers"]:
            if v.key in stray:
                v.on = _in_year(v.on, main, jalali)
    return parsed


def _year_end_kind(v: "Voucher") -> str | None:
    """"closing" or "opening" when the voucher says it is one, else None."""
    text = " ".join(_norm(t) for t in (v.reference, *(ln.description for ln in v.lines)) if t)
    if any(_norm(w) in text for w in _CLOSING_WORDS):
        return "closing"
    if any(_norm(w) in text for w in _OPENING_WORDS):
        return "opening"
    return None


def _assess(db: Session, parsed: dict[str, Any], overrides: dict[str, str] | None):
    """(accounts, voucher key → problem or None) for every voucher."""
    from sqlalchemy import func

    from app.models.transaction import Transaction
    from app.services.period_service import get_closed_period

    vouchers: list[Voucher] = parsed["vouchers"]
    accounts = match_accounts(db, vouchers, overrides)
    mapped = {a["key"]: a["mapped_to"] for a in accounts}
    closed = get_closed_period(db)
    refs = [_reference(parsed["preset"], v) for v in vouchers]
    existing: set[str] = set()
    for i in range(0, len(refs), 500):
        existing |= set(db.execute(select(Transaction.reference)
                                   .where(Transaction.reference.in_(refs[i:i + 500]))).scalars())
    legacy = sorted({_legacy_reference(parsed["preset"], v) for v in vouchers})
    legacy_on: set = set()
    for i in range(0, len(legacy), 500):
        legacy_on |= {(r, d) for r, d in db.execute(select(Transaction.reference, Transaction.date)
                                                   .where(Transaction.reference.in_(legacy[i:i + 500])))}
    today = date.today()
    kinds = {v.key: _year_end_kind(v) for v in vouchers}
    # What an opening voucher would repeat: an earlier year's books (whose closing
    # carried these balances), an earlier opening, or the opening balances the
    # chart migration posted that day or before. Not the year's own first
    # months: a hand-kept journal entered its opening on 1404/03/31, after a
    # quarter of vouchers, and lost it as a repeat (2026-10-12).
    from app.services.migration_import import OPENING_REFERENCE
    in_books = db.execute(select(func.min(Transaction.date))).scalar()
    in_file = min((v.on for v in vouchers if v.on and kinds[v.key] is None), default=None)
    earliest = min((d for d in (in_books, in_file) if d), default=None)
    openings = sorted(v.on for v in vouchers if kinds[v.key] == "opening")
    migrated_opening = db.execute(select(func.min(Transaction.date))
                                  .where(Transaction.reference == OPENING_REFERENCE)).scalar()
    jalali = parsed.get("jalali_dates") or _jalali_books(db)
    problems: dict[str, str | None] = {}
    held_years = parsed.get("stray_years") if parsed.get("years_mode") is None else set()
    for v in vouchers:
        if v.debit != v.credit:
            problems[v.key] = "unbalanced"
        elif held_years and v.key in held_years:
            problems[v.key] = "out_of_year"
        elif v.on > today:
            problems[v.key] = "future"
        elif kinds[v.key] == "closing":
            problems[v.key] = "year_end_closing"
        elif kinds[v.key] == "opening" and ((earliest is not None and earliest < _year_start(v.on, jalali))
                                             or openings[0] < v.on
                                             or (migrated_opening is not None and migrated_opening <= v.on)):
            problems[v.key] = "opening_repeat"
        elif closed is not None and v.on <= closed:
            problems[v.key] = "closed_period"
        elif (_reference(parsed["preset"], v) in existing
              or (_legacy_reference(parsed["preset"], v), v.on) in legacy_on):
            problems[v.key] = "already_imported"
        elif any(mapped.get(ln.account_key) is None for ln in v.lines):
            problems[v.key] = "unmapped_account"
        else:
            problems[v.key] = None
    return accounts, problems


def remember_codes(db: Session, parsed: dict[str, Any], accounts: list[dict[str, Any]], *,
                   only_existing: bool = False) -> int:
    """Keep the file's codes on the accounts they became («1302 ملزومات اداری»
    → 613202; its ledger's 1300 → the heading over it), so a search by them
    finds the account and cards show both (scenario AH7). ``only_existing``:
    only accounts the chart had already, by the user's choice, code or name.
    Returns how many accounts got a code."""
    from app.models.account import Account
    targets: dict[str, list[str]] = {}

    def add(app_code: str | None, file_code: str | None) -> None:
        file_code = (file_code or "").strip()
        if app_code and file_code and not app_code.startswith("new:") and file_code != app_code:
            got = targets.setdefault(app_code, [])
            if file_code not in got:
                got.append(file_code)

    mapped = {}
    for a in accounts:
        if only_existing and a.get("how") not in ("chosen", "code", "name"):
            continue
        mapped[a["key"]] = a.get("mapped_to")
        add(a.get("mapped_to"), a.get("code"))
    by_code = {acc.code: acc for acc in db.execute(select(Account).where(Account.code.in_(
        [c for c in mapped.values() if c and not c.startswith("new:")] or ["-"]))).scalars()}
    # a heading takes the code most of its ledger's lines give it: a few rows of
    # منادی حق's file call «مخارج مدرسه» 1110, the rest 1300
    seen: dict[str, Counter] = defaultdict(Counter)
    for v in parsed["vouchers"]:
        for ln in v.lines:
            acc = by_code.get(mapped.get(ln.account_key) or "")
            if acc is None or not ln.ledger_code or acc.parent_id is None:
                continue
            parent = db.get(Account, acc.parent_id)
            if parent is not None and _norm(parent.name) == _norm(ln.ledger):
                seen[parent.code][ln.ledger_code] += 1
    for heading, counts in seen.items():
        add(heading, min(counts, key=lambda c: (-counts[c], c)))
    changed = 0
    for acc in db.execute(select(Account).where(Account.code.in_(list(targets) or ["-"]))).scalars():
        have = [c for c in (acc.source_codes or "").split(",") if c]
        merged = have + [c for c in targets[acc.code] if c not in have]
        if merged != have:
            acc.source_codes = ",".join(merged)[:255]
            changed += 1
    db.flush()
    return changed


def codes_reused(vouchers: list[Voucher]) -> list[dict[str, Any]]:
    """A code the file gives to more than one account: منادی حق's 1305 is
    «سهم هزینه مشترک», «قبوض» and «کارمزد بانکی» (scenario AH10)."""
    names: dict[str, dict[str, str]] = {}
    for v in vouchers:
        for ln in v.lines:
            if ln.account_code and ln.account_name:
                names.setdefault(ln.account_code, {}).setdefault(_norm(ln.account_name), ln.account_name)
    return [{"code": code, "names": list(found.values())} for code, found in sorted(names.items()) if len(found) > 1]


# Why a voucher is held back, in the reader's language (scenario AH2).
HELD_FOR = {
    "already_imported": {"en": "already in the books", "fa": "از قبل در دفاتر", "es": "ya en los libros",
                         "ar": "موجود مسبقًا في الدفاتر"},
    "opening_repeat": {"en": "a repeated opening", "fa": "افتتاحیهٔ تکراری", "es": "apertura repetida",
                       "ar": "قيد افتتاحي مكرر"},
    "year_end_closing": {"en": "a year-end closing", "fa": "اختتامیهٔ پایان سال", "es": "cierre de ejercicio",
                         "ar": "قيد إقفال نهاية السنة"},
    "unbalanced": {"en": "not balanced", "fa": "تراز نیست", "es": "descuadrado", "ar": "غير متوازن"},
    "future": {"en": "dated in the future", "fa": "با تاریخ آینده", "es": "con fecha futura", "ar": "بتاريخ مستقبلي"},
    "closed_period": {"en": "in a closed period", "fa": "در دورهٔ بسته", "es": "en un periodo cerrado",
                      "ar": "في فترة مقفلة"},
    "out_of_year": {"en": "in another year", "fa": "در سال دیگر", "es": "en otro año", "ar": "في سنة أخرى"},
    "unmapped_account": {"en": "an account to match", "fa": "حساب نگاشت‌نشده", "es": "una cuenta por asignar",
                         "ar": "حساب غير مربوط"},
}


def _group_labels(db: Session) -> list[dict[str, str]]:
    from app.models.account import Account
    rows = db.execute(select(Account.code, Account.name).where(Account.code.in_(CREATE_GROUPS))).all()
    return [{"target": f"new:{c}", "code": c, "name": n} for c, n in sorted(rows)]


def review(db: Session, parsed: dict[str, Any], overrides: dict[str, str] | None = None) -> dict[str, Any]:
    vouchers: list[Voucher] = parsed["vouchers"]
    accounts, problems = _assess(db, parsed, overrides)
    counts: dict[str, int] = defaultdict(int)
    for p in problems.values():
        counts[p or "ready"] += 1
    rows = [{"key": v.key, "number": v.number, "date": v.on.isoformat(), "reference": v.reference,
             "lines": len(v.lines), "debit": v.debit, "credit": v.credit, "problem": problems[v.key]}
            for v in vouchers]
    rows.sort(key=lambda r: (r["problem"] is None, r["date"]))          # problems first
    dates = [v.on for v in vouchers]
    return {
        "preset": parsed["preset"], "grouping": parsed["grouping"], "header_row": parsed["header_row"],
        "headers": parsed["headers"], "columns": parsed["columns"], "accounts": accounts,
        "vouchers": rows[:500], "voucher_count": len(vouchers),
        "line_count": sum(len(v.lines) for v in vouchers),
        "counts": dict(counts), "skipped_rows": parsed["skipped"][:100],
        "rows_without_account": parsed["rows_without_account"], "needs": parsed["needs"],
        "rounded_lines": parsed["rounded_lines"],
        "from": min(dates).isoformat() if dates else None, "to": max(dates).isoformat() if dates else None,
        "file_year": parsed.get("file_year"), "stray_years": len(parsed.get("stray_years") or ()),
        "years_mode": parsed.get("years_mode"), "create_groups": _group_labels(db),
        "debit_total": sum(v.debit for v in vouchers), "credit_total": sum(v.credit for v in vouchers),
        "codes_reused": codes_reused(vouchers),
        "stray_vouchers": _stray_vouchers(parsed),
    }


def _as_written(on: "date | str | None", parsed: dict[str, Any]) -> str | None:
    """A date in the file's own calendar and shape: «1404/01/05» for a Jalali journal, else ISO."""
    if not on:
        return None
    d = date.fromisoformat(on) if isinstance(on, str) else on
    if parsed.get("jalali_dates"):
        from app.utils.jalali import gregorian_to_jalali
        y, m, dd = gregorian_to_jalali(d)
        return f"{y:04d}/{m:02d}/{dd:02d}"
    return d.isoformat()


def _stray_vouchers(parsed: dict[str, Any]) -> list[dict[str, Any]]:
    """The vouchers the out-of-year question means: number, the date written and the one it has now, amount,
    words and accounts — «تاریخ ۱ سند بیرون از سال فایل» left the owner asking which (2026-10-21, P10)."""
    written = parsed.get("stray_written") or {}
    out = []
    for v in parsed["vouchers"]:
        if v.key not in written:
            continue
        def names(side: str) -> list[str]:
            got: list[str] = []
            for ln in v.lines:
                name = (ln.account_name or ln.account_code or "").strip()
                if getattr(ln, side) and name and name not in got:
                    got.append(name)
            return got[:2]
        words = next((ln.description.strip() for ln in v.lines if (ln.description or "").strip()), "") or (v.reference or "")
        out.append({"number": v.number, "written": written[v.key], "date": v.on.isoformat() if v.on else None,
                    # as the file writes it: a Jalali journal's «1404/01/05», whatever calendar the books show
                    "written_as": _as_written(written[v.key], parsed), "date_as": _as_written(v.on, parsed),
                    "amount": v.debit, "description": words[:120], "debit_accounts": names("debit"),
                    "credit_accounts": names("credit")})
    return out[:20]


def apply(db: Session, parsed: dict[str, Any], *, account_map: dict[str, str], currency: str | None = None,
          link_parties: bool = True) -> dict[str, Any]:
    """Post every ready voucher; everything else is reported, never posted."""
    from app.schemas.entity import EntityLink
    from app.schemas.transaction import TransactionCreate, TransactionLineCreate
    from app.services.fx_service import get_reporting_currency
    from app.services.ledger_posting import create_transaction_from_payload

    if parsed["needs"]:
        raise HTTPException(status_code=422, detail="Choose the columns first: " + ", ".join(parsed["needs"]))
    accounts, problems = _assess(db, parsed, account_map)
    created = 0
    for a in accounts:
        if a["mapped_to"] and a["mapped_to"].startswith("new:") and any(
                problems[v.key] is None and any(ln.account_key == a["key"] for ln in v.lines) for v in parsed["vouchers"]):
            a["mapped_to"], a["how"] = create_account_for(db, a["name"], a["mapped_to"], a.get("ledger") or "",
                                                          code=a.get("code") or ""), "created"
            created += 1
    mapped = {a["key"]: a["mapped_to"] for a in accounts}
    default_ccy = (currency or get_reporting_currency(db) or "IRR").strip().upper()
    parties = _Parties(db)
    posted, skipped, to_banks = 0, defaultdict(int), 0
    for v in parsed["vouchers"]:
        if problems[v.key]:
            skipped[problems[v.key]] += 1
            continue
        codes = []
        for ln in v.lines:
            code = mapped[ln.account_key]
            own = parties.bank_account(ln.party, code)
            to_banks += own is not None
            codes.append(own or code)
        # each party once, with its own share of the voucher (debit: owes us),
        # as Sepidar keeps a detail account's balance across every sub-ledger
        shares: dict[str, int] = {}
        if link_parties:
            for ln in v.lines:
                if ln.party and len(ln.party) <= 200:
                    shares[ln.party] = shares.get(ln.party, 0) + ln.debit - ln.credit
        links, amounts = [], []
        for name, share in shares.items():
            line = next(ln for ln in v.lines if ln.party == name)
            entity = parties.entity(name, _party_role(mapped[line.account_key]) or ("client" if share >= 0 else "supplier"))
            links.append(EntityLink(role=entity.type, entity_id=entity.id))
            amounts.append((entity.id, share))
        ccy = next((ln.currency for ln in v.lines if ln.currency), None) or default_ccy
        txn = create_transaction_from_payload(db, TransactionCreate(
            date=v.on, reference=_reference(parsed["preset"], v),
            description=(v.lines[0].description or bt(db, "imported_voucher", number=v.number))[:2000], currency=ccy,
            lines=[TransactionLineCreate(account_code=code, debit=ln.debit, credit=ln.credit,
                                         line_description=(ln.description or ln.account_name or None) and
                                         (ln.description or ln.account_name)[:512]) for ln, code in zip(v.lines, codes)],
            entity_links=links))
        if amounts:
            db.flush()
            share_of = dict(amounts)
            for link in txn.entity_links or []:
                if link.entity_id in share_of:
                    link.amount = share_of[link.entity_id]
        posted += 1
    save_map(db, {a["key"]: a["mapped_to"] for a in accounts
                  if a["mapped_to"] and a["how"] != "code" and not a["mapped_to"].startswith("new:")})
    remember_codes(db, parsed, accounts)
    db.flush()
    # the books now say which twin sub-ledger is in use (rial or foreign-currency
    # petty cash, ...): suggest again where new entries post
    from app.services import posting_roles
    roles = posting_roles.apply_suggestions(db)
    from app.api.reports import invalidate_dashboard_cache
    invalidate_dashboard_cache()
    return {"posted": posted, "skipped": dict(skipped), "voucher_count": len(parsed["vouchers"]),
            "bank_lines": to_banks, "posting_roles": roles, "accounts_created": created}


class _Parties:
    """An imported line's party as the company knows it. A party is matched by
    name whatever its type (the chart migration made Sepidar's تفصیلی rows
    clients, employees and banks; a voucher line saying only «a supplier» used
    to create a second one), a bank also by the account number in its title
    («آینده جردن 0101790457004»); a stranger is created."""

    def __init__(self, db: Session):
        from app.models.account import Account
        from app.models.entity import Entity
        from app.services import migration_import as mig
        self.db, self.mig = db, mig
        self.by_name: dict[str, Any] = {}
        self.by_number: dict[str, Any] = {}
        for e in db.execute(select(Entity)).scalars():
            self.by_name.setdefault(_norm(e.name), e)
            if e.type == "bank" and e.account_number:
                self.by_number.setdefault(e.account_number.strip(), e)
        rows = db.execute(select(Account.id, Account.code, Account.parent_id)).all()
        self.parent = {r.code: r.parent_id for r in rows}
        self.code_of = {r.id: r.code for r in rows}

    def _find(self, name: str):
        number = self.mig.bank_account_number(name)
        if number and number in self.by_number:
            return self.by_number[number]
        return self.by_name.get(_norm(name)) or self.by_name.get(_norm(self.mig.strip_account_number(name)))

    def entity(self, name: str, role: str):
        found = self._find(name)
        if found is not None:
            return found
        from app.services.ledger_posting import get_or_create_entity
        made = get_or_create_entity(self.db, role, name)
        self.by_name[_norm(name)] = made
        return made

    def bank_account(self, party: str | None, code: str) -> str | None:
        """A line on a bank sub-ledger (111005) naming one of the company's
        banks goes to that bank's own account (111005001): Sepidar keeps a
        bank's balance as a detail account, and the banks' own accounts stayed
        at 0 with everything on the sub-ledger (2026-10-12)."""
        if not party:
            return None
        bank = self._find(party)
        if bank is None or bank.type != "bank" or not bank.code or bank.code == code:
            return None
        cur, hops = self.parent.get(bank.code), 0
        while cur is not None and hops < 12:
            if self.code_of.get(cur) == code:
                return bank.code
            cur, hops = self.parent.get(self.code_of.get(cur)), hops + 1
        return None
