"""Spotting payments that already repeat.

The valuable property is restraint: a suggestion the user must read and
dismiss costs them attention, so a false positive is worse than a miss. Most
of these tests are about what must NOT be proposed.
"""
from __future__ import annotations

from datetime import date, timedelta

import pytest
from sqlalchemy import delete, select

from app.models.account import Account
from app.models.recurring import RecurringRule
from app.models.transaction import Transaction, TransactionLine
from app.services.recurring_detection import MIN_OCCURRENCES, detect_recurring, display_name

TODAY = date(2026, 8, 30)
BANK = "1110"


@pytest.fixture(autouse=True)
def _isolate(db):
    """Start each test with an empty ledger.

    The suite shares one session, so a series posted by an earlier test would
    merge into this one's — same description, duplicate dates — and break the
    cadence check.
    """
    def _clear():
        db.execute(delete(TransactionLine))
        db.execute(delete(Transaction))
        db.execute(delete(RecurringRule))
        db.commit()

    _clear()
    yield
    _clear()


def _post(db, when: date, desc: str, code: str, amount: int, *, incoming: bool = False):
    other = db.execute(select(Account).where(Account.code == code)).scalars().one()
    cash = db.execute(select(Account).where(Account.code == BANK)).scalars().one()
    txn = Transaction(date=when, description=desc)
    db.add(txn)
    db.flush()
    if incoming:
        db.add(TransactionLine(transaction_id=txn.id, account_id=cash.id, debit=amount, credit=0))
        db.add(TransactionLine(transaction_id=txn.id, account_id=other.id, debit=0, credit=amount))
    else:
        db.add(TransactionLine(transaction_id=txn.id, account_id=other.id, debit=amount, credit=0))
        db.add(TransactionLine(transaction_id=txn.id, account_id=cash.id, debit=0, credit=amount))
    db.commit()
    return txn


def _monthly(db, desc: str, code: str, amount: int, months: int, **kw):
    for i in range(months):
        _post(db, TODAY - timedelta(days=30 * (months - 1 - i)), desc, code, amount, **kw)


def _find(candidates, needle: str):
    return next((c for c in candidates if needle in c.description), None)


# ---------------------------------------------------------------------------
# What it should catch
# ---------------------------------------------------------------------------
def test_a_steady_monthly_payment_is_detected(db):
    _monthly(db, "اجاره دفتر", "6112", 50_000_000, 4)
    hit = _find(detect_recurring(db, today=TODAY), "اجاره دفتر")
    assert hit is not None
    assert hit.frequency == "monthly"
    assert hit.typical_amount == 50_000_000
    assert hit.occurrences == 4
    assert hit.direction == "payment"
    assert hit.counter_account_code == "6112"
    assert hit.bank_account_code == BANK


def test_the_next_date_is_projected(db):
    _monthly(db, "اجاره دفتر", "6112", 50_000_000, 4)
    hit = _find(detect_recurring(db, today=TODAY), "اجاره دفتر")
    assert hit.last_date == TODAY
    assert timedelta(days=25) <= (hit.next_expected - TODAY) <= timedelta(days=35)


def test_incoming_money_is_flagged_as_a_receipt(db):
    _monthly(db, "اجاره واحد ۲", "4110", 8_000_000, 3, incoming=True)
    hit = _find(detect_recurring(db, today=TODAY), "اجاره واحد ۲")
    assert hit is not None and hit.direction == "receipt"


def test_reference_numbers_do_not_split_a_series(db):
    """The same merchant with different card digits each month is one series."""
    for i, ref in enumerate(["POS-111", "POS-222", "POS-333", "POS-444"]):
        _post(db, TODAY - timedelta(days=30 * (3 - i)), f"{ref} NETFLIX 99{i}", "6112", 290_000)
    hits = [c for c in detect_recurring(db, today=TODAY) if "NETFLIX" in c.description]
    assert len(hits) == 1
    assert hits[0].occurrences == 4


def test_a_small_drift_in_amount_is_tolerated(db):
    """A utility bill varies a little; that's still a standing commitment."""
    for i, amt in enumerate([1_000_000, 1_050_000, 980_000, 1_020_000]):
        _post(db, TODAY - timedelta(days=30 * (3 - i)), "قبض برق", "6112", amt)
    hit = _find(detect_recurring(db, today=TODAY), "قبض برق")
    assert hit is not None and hit.amount_varies is True


def test_weekly_and_quarterly_cadences_are_recognised(db):
    for i in range(5):
        _post(db, TODAY - timedelta(days=7 * (4 - i)), "نظافت هفتگی", "6112", 500_000)
    for i in range(3):
        _post(db, TODAY - timedelta(days=91 * (2 - i)), "بیمه فصلی", "6130", 9_000_000)
    found = {c.description: c.frequency for c in detect_recurring(db, today=TODAY)}
    assert found.get("نظافت هفتگی") == "weekly"
    assert found.get("بیمه فصلی") == "quarterly"


# ---------------------------------------------------------------------------
# What it must NOT propose
# ---------------------------------------------------------------------------
def test_two_occurrences_are_not_a_pattern(db):
    _monthly(db, "شاید تکراری", "6112", 1_000_000, MIN_OCCURRENCES - 1)
    assert _find(detect_recurring(db, today=TODAY), "شاید تکراری") is None


def test_irregular_dates_are_not_proposed(db):
    """Same shop, no rhythm — a habit, not a standing order."""
    for offset in (0, 3, 40, 44, 100):
        _post(db, TODAY - timedelta(days=offset), "سوپرمارکت", "6112", 400_000)
    assert _find(detect_recurring(db, today=TODAY), "سوپرمارکت") is None


def test_a_wildly_varying_amount_is_not_proposed(db):
    """Prefilling an amount here would be a guess, not a suggestion."""
    for i, amt in enumerate([100_000, 900_000, 250_000, 2_000_000]):
        _post(db, TODAY - timedelta(days=30 * (3 - i)), "خرید متغیر", "6112", amt)
    assert _find(detect_recurring(db, today=TODAY), "خرید متغیر") is None


def test_an_alternating_gap_is_not_monthly(db):
    """Gaps of 3 and 57 days have a ~30 day median but are not a monthly bill."""
    days = [0, 3, 60, 63, 120]
    for d in days:
        _post(db, TODAY - timedelta(days=d), "نامنظم", "6112", 500_000)
    assert _find(detect_recurring(db, today=TODAY), "نامنظم") is None


def test_a_series_that_already_has_a_rule_is_not_proposed_again(db):
    _monthly(db, "اجاره دفتر", "6112", 50_000_000, 4)
    db.add(RecurringRule(
        name="اجاره دفتر", direction="payment", frequency="monthly", amount=50_000_000,
        start_date=TODAY, next_run_date=TODAY, status="active",
    ))
    db.commit()
    assert _find(detect_recurring(db, today=TODAY), "اجاره دفتر") is None


def test_compound_entries_are_ignored(db):
    """A payroll run touching many accounts isn't a standing order."""
    other = db.execute(select(Account).where(Account.code == "6112")).scalars().one()
    second = db.execute(select(Account).where(Account.code == "6130")).scalars().one()
    cash = db.execute(select(Account).where(Account.code == BANK)).scalars().one()
    for i in range(4):
        txn = Transaction(date=TODAY - timedelta(days=30 * i), description="سند مرکب")
        db.add(txn)
        db.flush()
        db.add(TransactionLine(transaction_id=txn.id, account_id=other.id, debit=100, credit=0))
        db.add(TransactionLine(transaction_id=txn.id, account_id=second.id, debit=100, credit=0))
        db.add(TransactionLine(transaction_id=txn.id, account_id=cash.id, debit=0, credit=200))
    db.commit()
    assert _find(detect_recurring(db, today=TODAY), "سند مرکب") is None


def test_soft_deleted_entries_do_not_count(db):
    from datetime import datetime, timezone

    _monthly(db, "حذف شده", "6112", 1_000_000, 4)
    for txn in db.execute(
        select(Transaction).where(Transaction.description == "حذف شده")
    ).scalars().all():
        txn.deleted_at = datetime.now(timezone.utc)
    db.commit()
    assert _find(detect_recurring(db, today=TODAY), "حذف شده") is None


def test_blank_descriptions_are_skipped(db):
    for i in range(4):
        _post(db, TODAY - timedelta(days=30 * i), "", "6112", 1_000_000)
    assert all(c.description for c in detect_recurring(db, today=TODAY))


def test_suggestions_are_ordered_by_how_established_they_are(db):
    _monthly(db, "سه‌باره", "6112", 1_000_000, 3)
    _monthly(db, "شش‌باره", "6130", 2_000_000, 6)
    found = [c.description for c in detect_recurring(db, today=TODAY)]
    assert found.index("شش‌باره") < found.index("سه‌باره")


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------
def test_endpoint_returns_serialisable_candidates(auth_client, db):
    _monthly(db, "اجاره API", "6112", 4_000_000, 4)
    rows = auth_client.get("/recurring/detected").json()
    hit = next((r for r in rows if "اجاره API" in r["description"]), None)
    assert hit is not None
    assert hit["frequency"] == "monthly"
    assert hit["typical_amount"] == 4_000_000
    assert isinstance(hit["next_expected"], str)
    assert hit["counter_account_code"] == "6112"


def test_endpoint_is_read_only(auth_client, db):
    _monthly(db, "بدون تغییر", "6112", 1_000_000, 4)
    before = len(db.execute(select(RecurringRule)).scalars().all())
    auth_client.get("/recurring/detected")
    assert len(db.execute(select(RecurringRule)).scalars().all()) == before


# ---------------------------------------------------------------------------
# The suggested name becomes a permanent rule name
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("raw,expected", [
    ("POS-1004 اجاره ماهانه خانه", "اجاره ماهانه خانه"),
    ("POS-4821 TESCO STORES 123", "TESCO STORES"),
    ("TRX99 قبض برق 7", "قبض برق 7"),   # a lone short digit is kept: it may mean something
    ("اجاره واحد ۲", "اجاره واحد ۲"),     # Persian digits are digits too — unit 2 must survive
    ("NETFLIX", "NETFLIX"),
])
def test_display_name_drops_reference_digits_but_keeps_the_wording(raw, expected):
    """The reference changes every month; baking it into a rule name would
    leave the user with 'POS-1004 rent' forever."""
    assert display_name(raw) == expected


def test_display_name_never_returns_empty():
    """A narration that is only a reference still needs some name."""
    assert display_name("123456") == "123456"
    assert display_name("") == ""


def test_the_suggested_name_is_clean(db):
    for i in range(4):
        _post(db, TODAY - timedelta(days=30 * (3 - i)), f"POS-{2000+i} اجاره خانه", "6112", 5_000_000)
    hit = _find(detect_recurring(db, today=TODAY), "اجاره خانه")
    assert hit is not None
    assert hit.description == "اجاره خانه"
    assert "POS" not in hit.description
