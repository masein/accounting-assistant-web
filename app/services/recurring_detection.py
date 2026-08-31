"""Spot payments that repeat, and offer to automate them.

People notice the third identical rent payment long after the app could have.
This scans posted history for the same counterparty and account recurring on a
steady cadence, so the user can turn it into a RecurringRule with one click
instead of re-keying it every month.

Deliberately NOT an LLM job. It's arithmetic over structured ledger data:
deterministic, free, instant, and it works offline — all of which matter more
here than anything a model could add. The hard part isn't recognising a
pattern, it's *not* crying wolf, so the thresholds below are conservative.

Grouping reuses ``normalize_narration`` from the statement categorizer, which
strips the card and reference digits that differ between months — the reason
"POS-4821 SNAPP" and "POS-9910 SNAPP" collapse to one merchant.
"""
from __future__ import annotations

import statistics
from dataclasses import dataclass
from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.models.account import Account
from app.models.recurring import RecurringRule
from app.models.transaction import Transaction, TransactionLine
from app.services.reporting.common import ASSET, classify_account_code
from app.services.statement_categorizer import normalize_narration

def _looks_like_a_reference(token: str) -> bool:
    r"""A transient identifier rather than part of the name.

    Deliberately narrow. Stripping "POS-1004" is a clear win, but a short
    standalone number is usually meaningful — "اجاره واحد ۲" is rent for unit
    2, and dropping the ۲ would be worse than leaving a stray digit in. So only
    long digit runs, or tokens mixing letters and digits, are treated as noise.
    Note \d matches Persian digits too, which is exactly why the length rule
    matters here.
    """
    core = token.strip("-_#:.,()[]")
    if not core:
        return False
    if core.isdigit():
        return len(core) >= 3
    has_digit = any(ch.isdigit() for ch in core)
    has_alpha = any(ch.isalpha() for ch in core)
    return has_digit and has_alpha


def display_name(description: str | None) -> str:
    """A human name for the series: the user's own wording, minus the noise.

    Case and word order are preserved — this is what a rule will be called
    forever, so it should read like something a person wrote.
    """
    raw = (description or "").strip()
    kept = [tok for tok in raw.split() if not _looks_like_a_reference(tok)]
    return " ".join(kept) or raw

# How far back to look.
LOOKBACK_DAYS = 400
# Three points is the minimum that distinguishes a rhythm from a coincidence:
# two payments a month apart are far more often unrelated than recurring.
MIN_OCCURRENCES = 3
# Cadences we recognise, as (label, expected days, tolerance).
CADENCES = [
    ("weekly", 7, 2),
    ("monthly", 30, 5),
    ("quarterly", 91, 10),
    ("yearly", 365, 20),
]
# Amounts may drift (a utility bill), but not arbitrarily: past this the series
# is a habit rather than a standing commitment, and a prefilled amount would be
# a guess.
MAX_AMOUNT_VARIATION = 0.25


@dataclass
class RecurringCandidate:
    key: str
    description: str
    counter_account_code: str
    counter_account_name: str
    bank_account_code: str | None
    direction: str            # payment | receipt
    frequency: str
    typical_amount: int
    occurrences: int
    last_date: date
    next_expected: date
    amount_varies: bool

    def as_dict(self) -> dict:
        return {
            "key": self.key,
            "description": self.description,
            "counter_account_code": self.counter_account_code,
            "counter_account_name": self.counter_account_name,
            "bank_account_code": self.bank_account_code,
            "direction": self.direction,
            "frequency": self.frequency,
            "typical_amount": self.typical_amount,
            "occurrences": self.occurrences,
            "last_date": self.last_date.isoformat(),
            "next_expected": self.next_expected.isoformat(),
            "amount_varies": self.amount_varies,
        }


def _classify_cadence(intervals: list[int]) -> tuple[str, int] | None:
    """Name the rhythm from the gaps between occurrences, if it is steady.

    Uses the median so one skipped or early payment doesn't disqualify an
    otherwise obvious series.
    """
    if not intervals:
        return None
    median = int(statistics.median(intervals))
    for label, expected, tolerance in CADENCES:
        if abs(median - expected) <= tolerance:
            # Every gap must also be near the cadence; alternating 3 and 57 days
            # has a plausible median and is not a monthly bill.
            if all(abs(i - expected) <= tolerance * 2 for i in intervals):
                return label, median
    return None


def _existing_rule_keys(db: Session) -> set[str]:
    return {
        normalize_narration(r.name)
        for r in db.execute(select(RecurringRule)).scalars().all()
        if r.name
    }


def detect_recurring(db: Session, *, today: date | None = None) -> list[RecurringCandidate]:
    today = today or date.today()
    since = today - timedelta(days=LOOKBACK_DAYS)

    txns = db.execute(
        select(Transaction)
        .where(Transaction.deleted_at.is_(None), Transaction.date >= since)
        .options(selectinload(Transaction.lines).selectinload(TransactionLine.account))
        .order_by(Transaction.date)
    ).scalars().all()

    # (normalized narration, counter account) -> occurrences
    groups: dict[tuple[str, str], list[tuple[date, int, str, str | None, str]]] = {}
    for txn in txns:
        narration = normalize_narration(txn.description)
        if not narration:
            continue
        # A recurring payment has exactly one cash leg and one counter leg;
        # anything more complex is a compound entry, not a standing order.
        if len(txn.lines) != 2:
            continue
        cash = [l for l in txn.lines if classify_account_code(l.account.code) == ASSET]
        counter = [l for l in txn.lines if classify_account_code(l.account.code) != ASSET]
        if len(cash) != 1 or len(counter) != 1:
            continue
        cash_line, counter_line = cash[0], counter[0]
        amount = max(counter_line.debit, counter_line.credit)
        if amount <= 0:
            continue
        direction = "payment" if cash_line.credit > 0 else "receipt"
        groups.setdefault((narration, counter_line.account.code), []).append(
            (txn.date, amount, direction, cash_line.account.code, txn.description or narration)
        )

    taken = _existing_rule_keys(db)
    out: list[RecurringCandidate] = []
    for (narration, counter_code), rows in groups.items():
        if len(rows) < MIN_OCCURRENCES:
            continue
        if narration in taken:
            continue  # already automated — proposing it again is noise

        rows.sort(key=lambda r: r[0])
        dates = [r[0] for r in rows]
        intervals = [(b - a).days for a, b in zip(dates, dates[1:])]
        cadence = _classify_cadence(intervals)
        if cadence is None:
            continue
        frequency, median_gap = cadence

        amounts = [r[1] for r in rows]
        typical = int(statistics.median(amounts))
        spread = (max(amounts) - min(amounts)) / typical if typical else 1.0
        if spread > MAX_AMOUNT_VARIATION:
            continue

        directions = {r[2] for r in rows}
        if len(directions) != 1:
            continue  # money flowed both ways: a transfer pair, not a bill

        account = db.execute(
            select(Account).where(Account.code == counter_code)
        ).scalars().first()
        out.append(RecurringCandidate(
            key=f"{narration}|{counter_code}",
            # The user's own wording, minus the per-month reference digits.
            description=display_name(rows[-1][4]),
            counter_account_code=counter_code,
            counter_account_name=account.name if account else counter_code,
            bank_account_code=rows[-1][3],
            direction=directions.pop(),
            frequency=frequency,
            typical_amount=typical,
            occurrences=len(rows),
            last_date=dates[-1],
            next_expected=dates[-1] + timedelta(days=median_gap),
            amount_varies=spread > 0.01,
        ))

    # Most-established first: a longer series is a safer suggestion.
    out.sort(key=lambda c: (-c.occurrences, c.next_expected))
    return out
