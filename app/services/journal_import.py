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
import json
import re
from collections import OrderedDict, defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.services.book_text import bt

MAX_ROWS = 50_000
MAX_VOUCHERS = 10_000
MAP_KEY = "journal_import_account_map"          # company app_settings row: source account → chart code
PRESETS = ("auto", "generic", "quickbooks", "xero", "hesabfa", "sepidar", "holoo")
IRANIAN = ("hesabfa", "sepidar", "holoo")
FIELDS = ("date", "voucher", "reference", "account_code", "account_name", "description", "debit", "credit",
          "amount", "party", "currency")

_SYNONYMS: dict[str, tuple[str, ...]] = {
    "date": ("date", "transaction date", "journal date", "entry date", "posting date", "تاریخ", "تاریخ سند",
             "تاریخ ثبت"),
    "voucher": ("journal number", "journal no", "journal no.", "journal #", "journal", "entry no", "entry number",
                "num", "no", "no.", "voucher", "voucher no", "voucher number", "شماره سند", "ش سند", "ش.سند",
                "شماره", "سند", "شماره سند حسابداری"),
    "reference": ("reference", "ref", "ref no", "reference number", "مرجع", "شماره عطف", "عطف", "شماره مرجع"),
    "account_code": ("account code", "acct code", "account no", "account number", "code", "gl code", "کد حساب",
                     "کد", "کد معین", "کد کل", "شماره حساب"),
    "account_name": ("account", "account name", "gl account", "نام حساب", "شرح حساب", "حساب", "عنوان حساب",
                     "نام معین", "معین"),
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
        raise HTTPException(status_code=422, detail=f"More than {MAX_ROWS:,} rows — split the export by year.")
    return rows


def _match_field(header: str) -> str | None:
    h = _norm(header).rstrip(":").strip()
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
        for j, cell in enumerate(row or []):
            fld = _match_field(cell) if cell is not None else None
            if fld and fld not in found:
                found[fld] = j
        score = len(found) + (2 if ("debit" in found and "credit" in found) or "amount" in found else 0)
        if found and score > best[3]:
            best = (i, found, [str(c) if c is not None else "" for c in row], score)
    if best[0] < 0:                              # nothing recognisable: the first row, for the user to map
        first = next((i for i, r in enumerate(rows[:30]) if any(c not in (None, "") for c in (r or []))), None)
        if first is None:
            raise HTTPException(status_code=422, detail="The file has no rows.")
        return first, {}, [str(c) if c is not None else "" for c in rows[first]]
    return best[0], best[1], best[2]


def guess_preset(headers: list[str]) -> str:
    joined = " | ".join(_norm(h) for h in headers)
    if re.search(r"[؀-ۿ]", joined):
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
            if on is not None:
                cur_date = on
            key = f"d{cur_date}|{ref if ref is not None else cur_ref}"
        if ref is not None:
            cur_ref = str(ref).strip()
        code = re.sub(r"\.0$", "", _ascii(_cell(row, cols.get("account_code")) or ""))
        name = str(_cell(row, cols.get("account_name")) or "").strip()
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
                raise HTTPException(status_code=422, detail=f"More than {MAX_VOUCHERS:,} vouchers — split the export.")
            label = str(cur_number).strip() if cur_number not in (None, "") else f"{cur_date.isoformat()}-{len(vouchers) + 1}"
            v = vouchers[key] = Voucher(key=key, number=label, on=cur_date, reference=cur_ref)
        party = _cell(row, cols.get("party"))
        v.lines.append(Line(row=i, account_code=code, account_name=name,
                            description=str(_cell(row, cols.get("description")) or "").strip(),
                            debit=0, credit=0, exact=exact,
                            party=str(party).strip() if party is not None else None,
                            currency=str(_cell(row, cols.get("currency")) or "").strip().upper() or None))
    rounded = sum(_round_voucher(v) for v in vouchers.values())
    return {"preset": preset, "grouping": grouping, "header_row": header_idx + 1, "headers": headers,
            "columns": cols, "vouchers": list(vouchers.values()), "skipped": skipped,
            "rows_without_account": no_account, "needs": [], "rounded_lines": rounded}


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
        db.add(AppSetting(key=MAP_KEY, value=json.dumps(merged, ensure_ascii=False)))
    else:
        row.value = json.dumps(merged, ensure_ascii=False)
    db.flush()


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
                entry = seen[k] = {"key": k, "code": ln.account_code, "name": ln.account_name, "lines": 0,
                                   "debit": 0, "credit": 0, "mapped_to": None, "how": None, "suggestions": []}
            entry["lines"] += 1
            entry["debit"] += ln.debit
            entry["credit"] += ln.credit
    names = list(by_name)
    for k, e in seen.items():
        target = remembered.get(k)
        if target and target in by_code:
            e["mapped_to"], e["how"] = target, "chosen"
        elif e["code"] and e["code"] in by_code:
            e["mapped_to"], e["how"] = e["code"], "code"
        elif e["name"] and _norm(e["name"]) in by_name:
            e["mapped_to"], e["how"] = by_name[_norm(e["name"])].code, "name"
        if e["mapped_to"] is None:
            close = difflib.get_close_matches(_norm(e["name"] or e["code"]), names, n=3, cutoff=0.5)
            e["suggestions"] = [{"code": by_name[n].code, "name": by_name[n].name} for n in close]
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
_CLOSING_WORDS = ("اختتامیه", "بستن حساب", "سند بستن", "closing entry", "closing entries", "closing voucher",
                  "year-end closing", "year end closing", "close the year", "closing of temporary accounts")
_OPENING_WORDS = ("افتتاحیه", "نقل از سال قبل", "مانده اول دوره", "مانده ابتدای دوره", "opening balance",
                  "opening entry", "opening voucher", "brought forward")


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
    # what an opening voucher would repeat: anything earlier in the books or in
    # the file, or opening balances the chart migration posted that day or before
    from app.services.migration_import import OPENING_REFERENCE
    in_books = db.execute(select(func.min(Transaction.date))).scalar()
    in_file = min((v.on for v in vouchers if v.on and kinds[v.key] is None), default=None)
    earliest = min((d for d in (in_books, in_file) if d), default=None)
    migrated_opening = db.execute(select(func.min(Transaction.date))
                                  .where(Transaction.reference == OPENING_REFERENCE)).scalar()
    problems: dict[str, str | None] = {}
    for v in vouchers:
        if v.debit != v.credit:
            problems[v.key] = "unbalanced"
        elif v.on > today:
            problems[v.key] = "future"
        elif kinds[v.key] == "closing":
            problems[v.key] = "year_end_closing"
        elif kinds[v.key] == "opening" and ((earliest is not None and earliest < v.on)
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
        "debit_total": sum(v.debit for v in vouchers), "credit_total": sum(v.credit for v in vouchers),
    }


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
    mapped = {a["key"]: a["mapped_to"] for a in accounts}
    default_ccy = (currency or get_reporting_currency(db) or "IRR").strip().upper()
    posted, skipped = 0, defaultdict(int)
    for v in parsed["vouchers"]:
        if problems[v.key]:
            skipped[problems[v.key]] += 1
            continue
        links, seen = [], set()
        if link_parties:
            for ln in v.lines:
                if ln.party and ln.party not in seen and len(ln.party) <= 200:
                    role = _party_role(mapped[ln.account_key])
                    if role:
                        links.append(EntityLink(role=role, name=ln.party))
                        seen.add(ln.party)
        ccy = next((ln.currency for ln in v.lines if ln.currency), None) or default_ccy
        create_transaction_from_payload(db, TransactionCreate(
            date=v.on, reference=_reference(parsed["preset"], v),
            description=(v.lines[0].description or bt(db, "imported_voucher", number=v.number))[:2000], currency=ccy,
            lines=[TransactionLineCreate(account_code=mapped[ln.account_key], debit=ln.debit, credit=ln.credit,
                                         line_description=(ln.description or ln.account_name or None) and
                                         (ln.description or ln.account_name)[:512]) for ln in v.lines],
            entity_links=links))
        posted += 1
    save_map(db, {a["key"]: a["mapped_to"] for a in accounts if a["mapped_to"] and a["how"] != "code"})
    db.flush()
    from app.api.reports import invalidate_dashboard_cache
    invalidate_dashboard_cache()
    return {"posted": posted, "skipped": dict(skipped), "voucher_count": len(parsed["vouchers"])}
