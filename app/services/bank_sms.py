"""Bank SMS / notification capture (roadmap 2026-09 §4.1).

Iranian banks offer no statement API, but every account sends an SMS for
each movement. The user pastes them (one or many at once) or a phone
automation forwards them to ``POST /api/v1/bank-sms`` with an API key. Each
message is parsed here and becomes a row of an "SMS feed" bank statement — one
per bank, account and Jalali month — so the existing statement pipeline
(duplicate check, categorisation, review, approval into journals) handles the
rest.

The parser is deliberately format-agnostic rather than a template per bank:
it normalises the text (Arabic ي/ك, Persian and Arabic-Indic digits,
direction marks), finds the bank by name, the direction by keyword or sign,
the amount on the movement line, the balance after «مانده»/«موجودی», the
masked account, and the date in the forms banks use (1405/07/01, 05/07/01,
0701-13:45, 07/01 13:45). A date is always taken as the most recent
occurrence up to today — an SMS describes the past. Amounts in تومان are
converted to rials.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

import jdatetime

_DIGITS = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")
_BIDI = dict.fromkeys(map(ord, "‎‏‪‫‬‭‮⁦⁧⁨⁩"), None)

# Canonical name → the spellings seen in SMS (after normalisation).
BANKS: dict[str, tuple[str, ...]] = {
    "Mellat": ("ملت",), "Melli": ("ملی ایران", "بانک ملی", "ملی"), "Saderat": ("صادرات",),
    "Tejarat": ("تجارت",), "Saman": ("سامان",), "Blu": ("بلو", "blu"), "Pasargad": ("پاسارگاد",),
    "Ayandeh": ("آینده", "اینده"), "Parsian": ("پارسیان",), "Keshavarzi": ("کشاورزی",), "Sepah": ("سپه",),
    "Refah": ("رفاه",), "Resalat": ("رسالت",), "Maskan": ("مسکن",), "Shahr": ("بانک شهر", "شهر"),
    "EghtesadNovin": ("اقتصاد نوین", "اقتصادنوین"), "Sina": ("سینا",), "Dey": ("بانک دی",),
    "ToseeTaavon": ("توسعه تعاون",), "PostBank": ("پست بانک", "پست‌بانک"), "Karafarin": ("کارآفرین", "کارافرین"),
    "Sarmayeh": ("سرمایه",), "Iranzamin": ("ایران زمین", "ایرانزمین"), "Gardeshgari": ("گردشگری",),
    "MehrIran": ("مهر ایران", "قرض الحسنه مهر"), "Tosee": ("توسعه صادرات",), "Middle East": ("خاورمیانه",),
}

# Direction keywords, strongest first.
CREDIT_WORDS = ("واریز", "انتقال به حساب شما", "دریافت", "سود", "بستانکار", "افزایش", "برگشت")
DEBIT_WORDS = ("برداشت", "خرید", "پرداخت", "کارمزد", "قسط", "بدهکار", "کسر", "انتقال از", "چک")
NEUTRAL_MOVE = ("انتقال", "مبلغ", "تراکنش")
BALANCE_WORDS = ("مانده", "موجودی")
ACCOUNT_WORDS = ("حساب", "کارت", "از", "سپرده")

_AMOUNT = re.compile(r"(?<![\d/])([+\-]?)\s*(\d{1,3}(?:,\d{3})+|\d+)\s*([+\-]?)(?![\d/:])")


@dataclass
class ParsedSms:
    text: str
    bank: str | None = None
    account: str | None = None
    amount: int = 0
    direction: str | None = None      # "credit" (money in) | "debit" (money out)
    balance: int | None = None
    tx_date: date | None = None
    time: str | None = None
    kind: str | None = None           # the keyword that named the movement
    toman: bool = False
    problems: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.problems

    @property
    def fingerprint(self) -> str:
        return hashlib.sha256(normalize(self.text).encode("utf-8")).hexdigest()[:32]

    def as_dict(self) -> dict[str, Any]:
        return {"bank": self.bank, "account": self.account, "amount": self.amount, "direction": self.direction,
                "balance": self.balance, "date": self.tx_date.isoformat() if self.tx_date else None,
                "time": self.time, "kind": self.kind, "problems": list(self.problems)}


def normalize(text: str) -> str:
    t = (text or "").translate(_BIDI).translate(_DIGITS)
    t = t.replace("ي", "ی").replace("ك", "ک").replace("ة", "ه").replace("‌", " ")
    t = t.replace("：", ":").replace("٬", ",").replace("،", ",").replace("٫", ".")
    return "\n".join(line.strip() for line in t.replace("\r", "\n").split("\n")).strip()


def _num(s: str) -> int:
    return int(s.replace(",", ""))


def _bank(t: str) -> str | None:
    low = t.lower()
    best = None
    for name, spellings in BANKS.items():
        for sp in spellings:
            i = low.find(sp.lower())
            if i >= 0 and (best is None or len(sp) > best[1]):
                best = (name, len(sp))
    return best[0] if best else None


def _past_jalali(month: int, day: int, today: date, year: int | None = None) -> date | None:
    """The Gregorian date of Jalali month/day: the given year, else the most
    recent occurrence on or before tomorrow (a day of clock skew allowed)."""
    tj = jdatetime.date.fromgregorian(date=today)
    years = [year] if year else [tj.year, tj.year - 1]
    for y in years:
        try:
            g = jdatetime.date(y, month, day).togregorian()
        except ValueError:
            continue
        g = date(g.year, g.month, g.day)
        if year or g <= today + timedelta(days=1):
            return g
    return None


def _date_time(t: str, today: date) -> tuple[date | None, str | None]:
    time_m = re.search(r"(?<!\d)([01]?\d|2[0-3]):([0-5]\d)(?::[0-5]\d)?(?!\d)", t)
    hhmm = f"{int(time_m.group(1)):02d}:{time_m.group(2)}" if time_m else None
    # 1405/07/01 or 1405-07-01
    m = re.search(r"(?<!\d)(1[34]\d{2})[/\-.](\d{1,2})[/\-.](\d{1,2})(?!\d)", t)
    if m:
        return _past_jalali(int(m.group(2)), int(m.group(3)), today, int(m.group(1))), hhmm
    # 05/07/01 (two-digit Jalali year)
    m = re.search(r"(?<![\d,])(\d{2})/(\d{1,2})/(\d{1,2})(?![\d,])", t)
    if m and 1 <= int(m.group(2)) <= 12:
        return _past_jalali(int(m.group(2)), int(m.group(3)), today, 1400 + int(m.group(1))), hhmm
    # 0701-13:45 / 0701 13:45 (MMDD before the time)
    m = re.search(r"(?<!\d)(\d{2})(\d{2})\s*[-_ ]\s*([01]?\d|2[0-3]):[0-5]\d", t)
    if m and 1 <= int(m.group(1)) <= 12 and 1 <= int(m.group(2)) <= 31:
        return _past_jalali(int(m.group(1)), int(m.group(2)), today), hhmm
    # 07/01 13:45
    m = re.search(r"(?<![\d/])(\d{1,2})/(\d{1,2})(?![\d/])", t)
    if m and 1 <= int(m.group(1)) <= 12 and 1 <= int(m.group(2)) <= 31:
        return _past_jalali(int(m.group(1)), int(m.group(2)), today), hhmm
    return None, hhmm


_ACCOUNT_TOKEN = re.compile(r"\d+(?:[-.]\d+){1,}")


def _line_amount(line: str) -> tuple[int, str] | None:
    """The money figure on a line and the sign written next to it. Account
    numbers (849-800-1234-1, 201.8000.1234.1, 6037****1234, or ten-plus bare
    digits) and dates are never money."""
    line = re.sub(r"(?<!\d)1[34]\d{2}/\d{1,2}/\d{1,2}", " ", line)
    line = re.sub(r"\d*[*xX]{2,}\d*", " ", line)  # masked card / account: 6037****1234
    line = _ACCOUNT_TOKEN.sub(lambda m: m.group(0) if re.fullmatch(r"\d{1,3}(,\d{3})+", m.group(0)) else " ", line)
    best = None
    for m in _AMOUNT.finditer(line):
        raw = m.group(2)
        digits = raw.replace(",", "")
        if "," not in raw and len(digits) >= 10:
            continue
        value = _num(raw)
        sign = m.group(1) or m.group(3) or ""
        if best is None or value > best[0]:
            best = (value, sign)
    return best


def _segments(t: str) -> list[str]:
    """Lines, with a balance figure split off the line it shares (one-line
    SMS put the movement and «مانده» side by side)."""
    out: list[str] = []
    for line in (x for x in t.split("\n") if x.strip()):
        parts = re.split(r"(?=(?:%s))" % "|".join(BALANCE_WORDS), line)
        out.extend(p.strip() for p in parts if p.strip())
    return out


def parse_sms(text: str, *, today: date | None = None) -> ParsedSms:
    today = today or date.today()
    t = normalize(text)
    out = ParsedSms(text=text)
    if not t:
        out.problems.append("empty message")
        return out
    out.bank = _bank(t)
    out.toman = "تومان" in t
    lines = _segments(t)
    moves = [line for line in lines if not any(b in line for b in BALANCE_WORDS)]

    # Direction: the first movement keyword in the message.
    for line in moves:
        hit = next(((w, "credit") for w in CREDIT_WORDS if w in line), None) or \
            next(((w, "debit") for w in DEBIT_WORDS if w in line), None)
        if hit:
            out.kind, out.direction = hit
            break
    # Amount: on the keyword's line if it has one, else a «مبلغ»/«انتقال» line,
    # else the first money figure outside the balance.
    # A bare figure with no movement word only counts in a message that is
    # plainly from a bank (named, or in ریال/تومان) — an OTP code is not money.
    looks_bank = bool(out.bank) or "ریال" in t or out.toman
    candidates = ([line for line in moves if out.kind and out.kind in line]
                  + [line for line in moves if any(w in line for w in NEUTRAL_MOVE)]
                  + (moves if looks_bank else []))
    for line in candidates:
        amt = _line_amount(line)
        if amt:
            out.amount = amt[0]
            if amt[1] == "+":
                out.direction = "credit"
            elif amt[1] == "-":
                out.direction = "debit"
            if out.kind is None:
                out.kind = next((w for w in NEUTRAL_MOVE if w in line), None)
            break
    for line in lines:
        if any(b in line for b in BALANCE_WORDS):
            amt = _line_amount(line)
            if amt:
                out.balance = -amt[0] if amt[1] == "-" else amt[0]
            break
    for line in lines:
        # "حساب:123456789", "از 0123…", "کارت 6037…1234", "برداشت از حساب 849-800-1234-1"
        m = re.search(r"(?:حساب|کارت|سپرده|از)\s*:?\s*([\d*xX\-.]{4,})", line)
        if m:
            out.account = re.sub(r"[.\-]", "", m.group(1))[-24:]
            break
    out.tx_date, out.time = _date_time(t, today)

    if out.toman:
        out.amount *= 10
        if out.balance is not None:
            out.balance *= 10
    if not out.amount:
        out.problems.append("no amount found")
    if out.direction is None and out.amount:
        out.problems.append("could not tell money in from money out")
    if out.tx_date is None:
        out.problems.append("no date found")
    return out


def split_messages(blob: str) -> list[str]:
    """Several pasted SMS: blank lines separate them; so does a new bank
    header line after a date/time line."""
    text = (blob or "").replace("\r\n", "\n").replace("\r", "\n")
    parts = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    out: list[str] = []
    for part in parts:
        chunk: list[str] = []
        for line in part.split("\n"):
            starts_bank = bool(re.match(r"^\s*(بانک|بانك|bank)\b", normalize(line), re.I))
            if starts_bank and chunk:
                out.append("\n".join(chunk).strip())
                chunk = []
            chunk.append(line)
        if chunk:
            out.append("\n".join(chunk).strip())
    return out


def describe(p: ParsedSms) -> str:
    """The narration the statement row carries."""
    word = p.kind or ("واریز" if p.direction == "credit" else "برداشت")
    bits = [f"SMS {p.bank or ''}".strip(), word]
    if p.account:
        bits.append(f"حساب {p.account[-4:]}")
    if p.time:
        bits.append(p.time)
    return " — ".join(bits)


# --- into the statement pipeline ------------------------------------------------------

MAX_MESSAGES = 200
SOURCE_TYPE = "sms"


def _feed_key(p: ParsedSms) -> str:
    jd = jdatetime.date.fromgregorian(date=p.tx_date)
    return f"sms:{p.bank or 'unknown'}:{p.account or '-'}:{jd.year}-{jd.month:02d}"


def _feed(db, p: ParsedSms):
    """The open SMS-feed statement for this bank, account and Jalali month."""
    from sqlalchemy import select

    from app.models.bank_statement import BankStatement
    key = _feed_key(p)
    stmt = db.execute(select(BankStatement).where(BankStatement.source_type == SOURCE_TYPE,
                                                  BankStatement.source_filename == key)).scalars().first()
    if stmt is None:
        jd = jdatetime.date.fromgregorian(date=p.tx_date)
        stmt = BankStatement(bank_name=f"{p.bank or 'Bank'} (SMS {jd.year}/{jd.month:02d})",
                             account_number=p.account, source_type=SOURCE_TYPE, source_filename=key,
                             currency="IRR", from_date=p.tx_date, to_date=p.tx_date, status="parsed", total_rows=0)
        db.add(stmt)
        db.flush()
    return stmt


def ingest(db, messages: list[str] | str, *, today: date | None = None) -> dict[str, Any]:
    """Parse and file bank SMS. Returns what was added, what was already on
    file, what could not be read (with the reason) and any balance gap — a
    balance that does not follow from the previous message means one was
    missed."""
    from sqlalchemy import func, select

    from app.models.bank_statement import BankStatementRow
    from app.services.statement_categorizer import suggest_for_row

    texts = split_messages(messages) if isinstance(messages, str) else [m for m in messages if (m or "").strip()]
    if len(texts) > MAX_MESSAGES:
        raise ValueError(f"At most {MAX_MESSAGES} messages at a time")
    added, duplicates, unparsed, gaps = [], 0, [], []
    touched: dict[str, Any] = {}
    parsed = [(t, parse_sms(t, today=today)) for t in texts]
    # oldest first, so balances chain and row order follows the bank's
    parsed.sort(key=lambda x: ((x[1].tx_date or date.max), x[1].time or ""))
    for text, p in parsed:
        if not p.ok:
            unparsed.append({"text": text[:300], "problems": p.problems})
            continue
        norm = normalize(text)
        exists = db.execute(select(BankStatementRow.id).where(BankStatementRow.raw_text == norm)).first()
        if exists:
            duplicates += 1
            continue
        stmt = _feed(db, p)
        last = db.execute(select(BankStatementRow).where(BankStatementRow.statement_id == stmt.id)
                          .order_by(BankStatementRow.row_index.desc()).limit(1)).scalars().first()
        debit = p.amount if p.direction == "debit" else 0
        credit = p.amount if p.direction == "credit" else 0
        confidence = 0.95
        if last is not None and last.balance is not None and p.balance is not None:
            if last.balance + credit - debit != p.balance:
                confidence = 0.7
                gaps.append({"statement_id": str(stmt.id), "after": last.tx_date.isoformat(),
                             "expected": last.balance + credit - debit, "reported": p.balance})
        description = describe(p)
        hit = suggest_for_row(db, description, is_debit=debit > 0)
        row = BankStatementRow(
            statement_id=stmt.id,
            row_index=(int(db.execute(select(func.coalesce(func.max(BankStatementRow.row_index), -1))
                                      .where(BankStatementRow.statement_id == stmt.id)).scalar()) + 1),
            tx_date=p.tx_date, description=description, reference=None, debit=debit, credit=credit,
            balance=p.balance, counterparty=None, raw_text=norm, confidence=confidence,
            category=hit.category if hit else None, suggested_account_code=hit.account_code if hit else None,
            recon_status="unmatched",
        )
        db.add(row)
        stmt.total_rows = int(stmt.total_rows or 0) + 1
        stmt.from_date = min(stmt.from_date or p.tx_date, p.tx_date)
        stmt.to_date = max(stmt.to_date or p.tx_date, p.tx_date)
        if stmt.account_number is None and p.account:
            stmt.account_number = p.account
        db.flush()
        added.append({**p.as_dict(), "statement_id": str(stmt.id)})
        touched[str(stmt.id)] = {"id": str(stmt.id), "bank_name": stmt.bank_name, "account": stmt.account_number,
                                 "rows": stmt.total_rows}
    return {"received": len(texts), "added": len(added), "duplicates": duplicates, "rows": added,
            "unparsed": unparsed, "gaps": gaps, "statements": list(touched.values())}
