"""Bank-statement row categorization.

The old classifier mapped narrations to hardcoded Iranian codes, so UK and
personal charts got suggestions for accounts they don't have. The replacement
resolves against the tenant's own chart, and learns from what the user has
actually posted before.
"""
from __future__ import annotations

from datetime import date, timedelta

import pytest
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.db.seed import (
    PERSONAL_SEED_ACCOUNTS,
    SEED_ACCOUNTS,
    UK_SEED_ACCOUNTS,
    _parent_code_ir,
    _parent_code_uk,
)
from app.models.account import Account
from app.models.transaction import Transaction, TransactionLine
from app.services.locale_service import set_reporting_locale
from app.services.statement_categorizer import (
    normalize_narration,
    suggest_for_row,
)

TODAY = date.today()


def _make_session(chart, parent_fn, locale: str) -> Session:
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)

    @event.listens_for(engine, "connect")
    def _fk(conn, _rec):  # pragma: no cover
        cur = conn.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()

    Base.metadata.create_all(bind=engine)
    db = sessionmaker(bind=engine, autoflush=False, autocommit=False)()
    by_code: dict[str, Account] = {}
    for code, name, level in chart:
        acc = Account(code=code, name=name, level=level)
        db.add(acc)
        by_code[code] = acc
    db.flush()
    for code, _n, _l in chart:
        p = parent_fn(code)
        if p and p in by_code:
            by_code[code].parent_id = by_code[p].id
    set_reporting_locale(db, locale)
    db.commit()
    return db


@pytest.fixture
def personal():
    chart = [(c, fa, lvl) for c, fa, _en, lvl in PERSONAL_SEED_ACCOUNTS]
    db = _make_session(chart, _parent_code_ir, "ir")
    try:
        yield db
    finally:
        db.close()


@pytest.fixture
def personal_en():
    chart = [(c, en, lvl) for c, _fa, en, lvl in PERSONAL_SEED_ACCOUNTS]
    db = _make_session(chart, _parent_code_ir, "uk")
    try:
        yield db
    finally:
        db.close()


@pytest.fixture
def uk():
    db = _make_session(UK_SEED_ACCOUNTS, _parent_code_uk, "uk")
    try:
        yield db
    finally:
        db.close()


@pytest.fixture
def ir():
    db = _make_session(SEED_ACCOUNTS, _parent_code_ir, "ir")
    try:
        yield db
    finally:
        db.close()


def _post(db: Session, description: str, code: str, amount: int, *, when: date | None = None):
    """Post a two-line entry: Dr <code> / Cr bank-ish, as the user would have."""
    other = db.execute(select(Account).where(Account.code == code)).scalars().one()
    bank = db.execute(
        select(Account).where(Account.code.in_(["1120", "1110", "1200"]))
    ).scalars().first()
    txn = Transaction(date=when or TODAY, description=description)
    db.add(txn)
    db.flush()
    db.add(TransactionLine(transaction_id=txn.id, account_id=other.id, debit=amount, credit=0))
    db.add(TransactionLine(transaction_id=txn.id, account_id=bank.id, debit=0, credit=amount))
    db.commit()
    return txn


# ---------------------------------------------------------------------------
# Narration normalization
# ---------------------------------------------------------------------------
def test_normalization_strips_reference_noise():
    """Card/terminal/reference numbers change every row; keeping them would
    make every narration unique and defeat history matching."""
    a = normalize_narration("POS-4821 خرید SNAPP 123456")
    b = normalize_narration("POS-9910 خرید SNAPP 998877")
    assert a == b == "snapp"


def test_normalization_folds_persian_digits_and_case():
    assert normalize_narration("TESCO ۱۲۳") == "tesco"


def test_blank_narration_yields_no_suggestion(personal):
    assert suggest_for_row(personal, None, is_debit=True) is None
    assert suggest_for_row(personal, "   ", is_debit=True) is None


# ---------------------------------------------------------------------------
# Keyword signal, resolved against each chart
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("narration,expected_code", [
    ("خرید اسنپ", "6130"),               # transport
    ("SNAPP TRIP", "6130"),
    ("سوپرمارکت رفاه", "6110"),          # groceries
    ("TESCO STORES", "6110"),
    ("رستوران شبانه", "6180"),           # dining
    ("قبض برق", "6140"),                 # utilities
    ("اجاره خانه", "6120"),              # rent
    ("داروخانه دکتر", "6150"),           # health
    ("NETFLIX SUBSCRIPTION", "6190"),    # subscriptions
    ("کارمزد بانکی", "6210"),            # bank fee
])
def test_personal_chart_keyword_resolution(personal, narration, expected_code):
    hit = suggest_for_row(personal, narration, is_debit=True)
    assert hit is not None, f"no suggestion for {narration!r}"
    assert hit.account_code == expected_code
    assert hit.source == "keyword"


def test_uk_chart_resolves_the_same_narration_to_a_uk_account(uk):
    """No Iranian codes leak: a UK chart gets a UK account or nothing."""
    hit = suggest_for_row(uk, "UBER TRIP LONDON", is_debit=True)
    assert hit is not None
    assert hit.account_code in {a.code for a in uk.execute(select(Account)).scalars().all()}
    assert hit.account_code.startswith("7")  # UK overheads, e.g. 7400 motor


def test_ir_sme_chart_resolves_within_its_own_chart(ir):
    hit = suggest_for_row(ir, "هزینه ایاب و ذهاب پرسنل", is_debit=True)
    assert hit is not None
    assert hit.account_code in {a.code for a in ir.execute(select(Account)).scalars().all()}


def test_english_named_personal_chart_also_resolves(personal_en):
    hit = suggest_for_row(personal_en, "TESCO STORES 1234", is_debit=True)
    assert hit is not None and hit.account_code == "6110"


def test_unknown_merchant_returns_nothing(personal):
    """Better to leave it blank for the user than to guess wrongly."""
    assert suggest_for_row(personal, "ZZQX HOLDINGS LLC", is_debit=True) is None


def test_longest_keyword_wins(personal):
    """'اسنپ فود' is dining, not transport, even though 'اسنپ' also matches."""
    hit = suggest_for_row(personal, "اسنپ فود سفارش", is_debit=True)
    assert hit is not None and hit.account_code == "6180"


# ---------------------------------------------------------------------------
# Direction awareness
# ---------------------------------------------------------------------------
def test_credit_row_resolves_to_income_not_expense(personal):
    hit = suggest_for_row(personal, "واریز حقوق مرداد", is_debit=False)
    assert hit is not None
    assert hit.account_code == "4110"  # salary income


def test_expense_keyword_on_a_credit_row_is_not_forced(personal):
    """Money arriving from a narration that looks like an expense must not be
    posted to an expense account."""
    hit = suggest_for_row(personal, "سوپرمارکت رفاه", is_debit=False)
    assert hit is None or not hit.account_code.startswith("6")


def test_debit_row_never_suggests_a_revenue_account(personal):
    hit = suggest_for_row(personal, "حقوق", is_debit=True)
    assert hit is None or not hit.account_code.startswith("4")


def test_loan_repayment_resolves_to_the_liability_not_an_expense(personal):
    hit = suggest_for_row(personal, "قسط وام مسکن", is_debit=True)
    assert hit is not None
    assert hit.account_code.startswith("2")  # installments payable


# ---------------------------------------------------------------------------
# History signal
# ---------------------------------------------------------------------------
def test_history_overrides_keywords(personal):
    """The user filed this merchant under Education twice; respect that rather
    than the keyword table's guess."""
    _post(personal, "خرید کتاب فروشگاه مرکزی", "6160", 100_000)
    _post(personal, "خرید کتاب فروشگاه مرکزی", "6160", 120_000)

    hit = suggest_for_row(personal, "خرید کتاب فروشگاه مرکزی", is_debit=True)
    assert hit is not None
    assert hit.source == "history"
    assert hit.account_code == "6160"
    assert hit.confidence > 0.9


def test_history_matches_through_changing_reference_numbers(personal):
    _post(personal, "POS-1111 SNAPP 4821", "6220", 50_000)
    hit = suggest_for_row(personal, "POS-2222 SNAPP 9910", is_debit=True)
    assert hit is not None
    assert hit.source == "history"
    assert hit.account_code == "6220"  # what the user chose, not the keyword guess


def test_history_ignores_the_bank_leg(personal):
    """The learned account must be what the money was *for*, never the bank."""
    _post(personal, "پرداخت آزمایشی", "6110", 10_000)
    hit = suggest_for_row(personal, "پرداخت آزمایشی", is_debit=True)
    assert hit is not None and hit.account_code == "6110"


def test_history_picks_the_most_frequent_choice(personal):
    _post(personal, "فروشگاه زنجیره ای", "6110", 10_000)
    _post(personal, "فروشگاه زنجیره ای", "6110", 11_000)
    _post(personal, "فروشگاه زنجیره ای", "6170", 12_000)
    hit = suggest_for_row(personal, "فروشگاه زنجیره ای", is_debit=True)
    assert hit is not None and hit.account_code == "6110"


def test_history_respects_direction(personal):
    """Past expense postings must not be reused for an incoming payment."""
    _post(personal, "شرکت الف", "6110", 10_000)
    hit = suggest_for_row(personal, "شرکت الف", is_debit=False)
    assert hit is None or not hit.account_code.startswith("6")


def test_soft_deleted_history_is_ignored(personal):
    from datetime import datetime, timezone

    txn = _post(personal, "ورودی حذف شده", "6160", 10_000)
    txn.deleted_at = datetime.now(timezone.utc)
    personal.commit()
    hit = suggest_for_row(personal, "ورودی حذف شده", is_debit=True)
    assert hit is None or hit.source != "history"


def test_unrelated_history_does_not_bleed_into_a_new_merchant(personal):
    _post(personal, "داروخانه هلال", "6150", 10_000)
    hit = suggest_for_row(personal, "اسنپ سفر", is_debit=True)
    assert hit is not None
    assert hit.source == "keyword" and hit.account_code == "6130"
