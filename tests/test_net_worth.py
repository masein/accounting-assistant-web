"""Personal net worth, with gold/FX revaluation.

The ledger holds what gold and foreign currency COST in rials. Under high
inflation that historical figure understates reality within months, so net
worth is reported at current value: registered holdings are restated at
quantity x today's rate, with the gap shown as an unrealized gain.

Rates ride the ordinary exchange_rates table — GOLD_GRAM is just a
pseudo-currency — so manually entered, effective-dated rates work unchanged.
"""
from __future__ import annotations

from datetime import date, timedelta

import pytest
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.db.seed import PERSONAL_SEED_ACCOUNTS, _parent_code_ir
from app.models.account import Account
from app.models.exchange_rate import ExchangeRate
from app.models.personal_holding import PersonalHolding
from app.models.transaction import Transaction, TransactionLine
from app.services.locale_service import set_reporting_locale
from app.services.net_worth_service import compute_net_worth

TODAY = date.today()

BANK = "1110"
CASH = "1120"
GOLD = "1130"
FX = "1140"
LOAN = "2110"
INSTALMENTS = "2120"
INCOME = "4110"
FOOD = "6110"


@pytest.fixture
def db():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)

    @event.listens_for(engine, "connect")
    def _fk(conn, _rec):  # pragma: no cover
        cur = conn.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()

    Base.metadata.create_all(bind=engine)
    s = sessionmaker(bind=engine, autoflush=False, autocommit=False)()
    by_code = {}
    for code, name_fa, _en, level in PERSONAL_SEED_ACCOUNTS:
        acc = Account(code=code, name=name_fa, level=level)
        s.add(acc)
        by_code[code] = acc
    s.flush()
    for code, *_ in PERSONAL_SEED_ACCOUNTS:
        p = _parent_code_ir(code)
        if p and p in by_code:
            by_code[code].parent_id = by_code[p].id
    set_reporting_locale(s, "ir")
    s.commit()
    try:
        yield s
    finally:
        s.close()


def _post(db: Session, dr: str, cr: str, amount: int, *, when: date | None = None, desc="x"):
    a = db.execute(select(Account).where(Account.code == dr)).scalars().one()
    b = db.execute(select(Account).where(Account.code == cr)).scalars().one()
    txn = Transaction(date=when or TODAY, description=desc)
    db.add(txn)
    db.flush()
    db.add(TransactionLine(transaction_id=txn.id, account_id=a.id, debit=amount, credit=0))
    db.add(TransactionLine(transaction_id=txn.id, account_id=b.id, debit=0, credit=amount))
    db.commit()
    return txn


def _hold(db: Session, code: str, unit: str, qty: float, label: str | None = None):
    db.add(PersonalHolding(account_code=code, unit=unit, quantity=qty, label=label))
    db.commit()


def _rate(db: Session, unit: str, rate: float, *, on: date | None = None, to: str = "IRR"):
    db.add(ExchangeRate(from_currency=unit, to_currency=to, rate=rate,
                        effective_date=on or TODAY - timedelta(days=1)))
    db.commit()


def _line(nw, code):
    for l in list(nw.assets) + list(nw.liabilities):
        if l.account_code == code:
            return l
    return None


# ---------------------------------------------------------------------------
# The basic sum
# ---------------------------------------------------------------------------
def test_net_worth_is_assets_minus_liabilities(db):
    _post(db, BANK, INCOME, 50_000_000)          # salary in
    _post(db, FOOD, BANK, 2_000_000)             # spending
    _post(db, BANK, LOAN, 30_000_000)            # took a loan: cash up, debt up

    nw = compute_net_worth(db, with_trend=False)

    assert nw.total_assets == 78_000_000         # 50m - 2m + 30m
    assert nw.total_liabilities == 30_000_000
    assert nw.net_worth == 48_000_000


def test_liabilities_are_credit_positive(db):
    _post(db, BANK, INSTALMENTS, 12_000_000)
    _post(db, INSTALMENTS, BANK, 2_000_000)      # paid one instalment
    nw = compute_net_worth(db, with_trend=False)
    assert _line(nw, INSTALMENTS).book_value == 10_000_000
    assert nw.net_worth == 0                     # 10m bank vs 10m owed


def test_income_and_expense_accounts_are_not_on_the_statement(db):
    _post(db, FOOD, BANK, 1_000_000)
    nw = compute_net_worth(db, with_trend=False)
    codes = {l.account_code for l in nw.assets + nw.liabilities}
    assert FOOD not in codes and INCOME not in codes


def test_zero_balance_accounts_are_omitted(db):
    _post(db, BANK, INCOME, 1_000_000)
    _post(db, INCOME, BANK, 1_000_000)           # spent it all back
    nw = compute_net_worth(db, with_trend=False)
    assert _line(nw, BANK) is None


def test_soft_deleted_transactions_are_excluded(db):
    from datetime import datetime, timezone

    txn = _post(db, BANK, INCOME, 5_000_000)
    txn.deleted_at = datetime.now(timezone.utc)
    db.commit()
    assert compute_net_worth(db, with_trend=False).net_worth == 0


# ---------------------------------------------------------------------------
# Revaluation — the point of the feature
# ---------------------------------------------------------------------------
def test_gold_is_restated_at_the_current_rate(db):
    """Bought 10g at 30m rials; gold is now 5m/g, so it's worth 50m."""
    _post(db, GOLD, BANK, 30_000_000)
    _hold(db, GOLD, "GOLD_GRAM", 10)
    _rate(db, "GOLD_GRAM", 5_000_000)

    nw = compute_net_worth(db, with_trend=False)
    gold = _line(nw, GOLD)

    assert gold.book_value == 30_000_000
    assert gold.market_value == 50_000_000
    assert gold.unrealized_gain == 20_000_000
    assert gold.revalued is True
    assert gold.quantity == 10
    assert gold.unit == "GOLD_GRAM"


def test_net_worth_includes_the_revaluation(db):
    _post(db, GOLD, BANK, 30_000_000)   # moved 30m of savings into gold
    _post(db, BANK, INCOME, 30_000_000)  # ...funded by income, so bank nets 0
    _hold(db, GOLD, "GOLD_GRAM", 10)
    _rate(db, "GOLD_GRAM", 5_000_000)

    nw = compute_net_worth(db, with_trend=False)
    assert nw.net_worth == 50_000_000        # not the 30m book value
    assert nw.unrealized_gain == 20_000_000


def test_foreign_currency_uses_the_same_mechanism(db):
    _post(db, FX, BANK, 100_000_000)
    _hold(db, FX, "USD", 1_000)
    _rate(db, "USD", 150_000)

    fx = _line(compute_net_worth(db, with_trend=False), FX)
    assert fx.market_value == 150_000_000
    assert fx.unrealized_gain == 50_000_000


def test_a_falling_price_produces_a_loss(db):
    _post(db, GOLD, BANK, 60_000_000)
    _hold(db, GOLD, "GOLD_GRAM", 10)
    _rate(db, "GOLD_GRAM", 5_000_000)
    assert _line(compute_net_worth(db, with_trend=False), GOLD).unrealized_gain == -10_000_000


def test_the_latest_rate_on_or_before_the_date_wins(db):
    # Bought 90 days ago, so the holding exists at both valuation dates.
    _post(db, GOLD, BANK, 10_000_000, when=TODAY - timedelta(days=90))
    _hold(db, GOLD, "GOLD_GRAM", 1)
    _rate(db, "GOLD_GRAM", 4_000_000, on=TODAY - timedelta(days=60))
    _rate(db, "GOLD_GRAM", 6_000_000, on=TODAY - timedelta(days=1))

    assert _line(compute_net_worth(db, with_trend=False), GOLD).market_value == 6_000_000
    old = compute_net_worth(db, as_of=TODAY - timedelta(days=30), with_trend=False)
    assert _line(old, GOLD).market_value == 4_000_000


def test_two_units_on_one_account_are_summed(db):
    """Coins and loose grams both sit in the gold account."""
    _post(db, GOLD, BANK, 50_000_000)
    _hold(db, GOLD, "GOLD_GRAM", 10, label="شمش")
    _hold(db, GOLD, "GOLD_COIN", 2, label="سکه")
    _rate(db, "GOLD_GRAM", 5_000_000)
    _rate(db, "GOLD_COIN", 40_000_000)

    gold = _line(compute_net_worth(db, with_trend=False), GOLD)
    assert gold.market_value == 130_000_000     # 10*5m + 2*40m
    assert gold.unit is None                    # mixed units, no single unit shown


def test_a_missing_rate_is_reported_not_silently_zero(db):
    """Valuing someone's gold at nothing because a rate is unset would be worse
    than saying so."""
    _post(db, GOLD, BANK, 30_000_000)
    _hold(db, GOLD, "GOLD_GRAM", 10)   # no rate on file

    nw = compute_net_worth(db, with_trend=False)
    gold = _line(nw, GOLD)
    assert gold.market_value == 30_000_000     # falls back to book
    assert gold.revalued is False
    assert "GOLD_GRAM" in nw.missing_rates


def test_accounts_without_holdings_are_never_revalued(db):
    _post(db, BANK, INCOME, 10_000_000)
    bank = _line(compute_net_worth(db, with_trend=False), BANK)
    assert bank.revalued is False
    assert bank.market_value == bank.book_value


def test_revaluation_posts_no_journal_entry(db):
    """Reporting-only: the books stay at cost."""
    _post(db, GOLD, BANK, 30_000_000)
    _hold(db, GOLD, "GOLD_GRAM", 10)
    _rate(db, "GOLD_GRAM", 5_000_000)
    before = len(db.execute(select(Transaction)).scalars().all())
    compute_net_worth(db, with_trend=False)
    assert len(db.execute(select(Transaction)).scalars().all()) == before


# ---------------------------------------------------------------------------
# Trend
# ---------------------------------------------------------------------------
def test_trend_has_one_point_per_month_ending_today(db):
    from app.services.net_worth_service import TREND_MONTHS

    _post(db, BANK, INCOME, 1_000_000)
    nw = compute_net_worth(db)
    assert len(nw.trend) == TREND_MONTHS
    assert nw.trend[-1][0] == f"{TODAY.year:04d}-{TODAY.month:02d}"
    periods = [p for p, _v in nw.trend]
    assert periods == sorted(periods)   # oldest first


def test_trend_is_cumulative_not_per_month(db):
    """Net worth is a running balance: last month's savings still count."""
    two_months_ago = (TODAY.replace(day=1) - timedelta(days=1)).replace(day=1) - timedelta(days=1)
    _post(db, BANK, INCOME, 5_000_000, when=two_months_ago)
    _post(db, BANK, INCOME, 3_000_000, when=TODAY)

    values = dict(compute_net_worth(db).trend)
    assert values[f"{TODAY.year:04d}-{TODAY.month:02d}"] == 8_000_000


def test_trend_before_any_activity_is_zero(db):
    _post(db, BANK, INCOME, 5_000_000)
    trend = compute_net_worth(db).trend
    assert trend[0][1] == 0        # a year ago, nothing had happened yet
    assert trend[-1][1] == 5_000_000


# ---------------------------------------------------------------------------
# Holdings API
# ---------------------------------------------------------------------------
class TestHoldingsApi:
    """Endpoint behaviour through the real app (shared session fixtures)."""

    def test_upsert_replaces_rather_than_duplicating(self, auth_client):
        first = auth_client.post("/personal/holdings", json={
            "account_code": "1110", "unit": "USD", "quantity": 100})
        assert first.status_code == 201, first.text
        second = auth_client.post("/personal/holdings", json={
            "account_code": "1110", "unit": "USD", "quantity": 250})
        assert second.status_code == 201

        rows = auth_client.get("/personal/holdings").json()
        usd = [r for r in rows if r["unit"] == "USD" and r["account_code"] == "1110"]
        assert len(usd) == 1
        assert usd[0]["quantity"] == 250

    def test_unit_is_normalised_to_upper_case(self, auth_client):
        r = auth_client.post("/personal/holdings", json={
            "account_code": "1110", "unit": "usd", "quantity": 5})
        assert r.json()["unit"] == "USD"

    def test_unknown_account_is_rejected(self, auth_client):
        r = auth_client.post("/personal/holdings", json={
            "account_code": "999999", "unit": "USD", "quantity": 1})
        assert r.status_code == 400

    def test_negative_quantity_is_rejected(self, auth_client):
        r = auth_client.post("/personal/holdings", json={
            "account_code": "1110", "unit": "USD", "quantity": -5})
        assert r.status_code == 422

    def test_delete_removes_it(self, auth_client):
        created = auth_client.post("/personal/holdings", json={
            "account_code": "1110", "unit": "EUR", "quantity": 10}).json()
        assert auth_client.delete(f"/personal/holdings/{created['id']}").status_code == 204
        rows = auth_client.get("/personal/holdings").json()
        assert all(r["id"] != created["id"] for r in rows)

    def test_delete_unknown_is_404(self, auth_client):
        r = auth_client.delete("/personal/holdings/00000000-0000-0000-0000-000000000000")
        assert r.status_code == 404

    def test_net_worth_endpoint_shape(self, auth_client):
        body = auth_client.get("/personal/net-worth").json()
        for key in ("as_of", "currency", "assets", "liabilities", "total_assets",
                    "total_liabilities", "net_worth", "unrealized_gain", "trend",
                    "missing_rates"):
            assert key in body, key
        assert body["net_worth"] == body["total_assets"] - body["total_liabilities"]

    def test_trend_can_be_skipped(self, auth_client):
        assert auth_client.get("/personal/net-worth?trend=false").json()["trend"] == []
