"""Personal net worth: what you own minus what you owe, at today's value.

Two things make this more than a balance-sheet read.

**Revaluation.** The ledger records gold and foreign currency at rial cost.
Under high inflation that historical figure understates reality within months,
so any holding registered in ``personal_holdings`` is restated at
quantity x current rate, and the difference is reported as an unrealized gain.
Rates come from the ordinary ``exchange_rates`` table — a unit like GOLD_GRAM
is just a pseudo-currency — so manually entered, effective-dated rates work
without any new machinery.

**Trend.** A single number says little; the useful question is which way it is
moving. Month-end net worth is computed from cumulative ledger balances, with
holdings revalued at the rate effective at each month end, so the curve
reflects both saving and price movement.

Reporting-only: nothing here posts a journal entry. The books stay at cost.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.account import Account
from app.models.personal_holding import PersonalHolding
from app.models.transaction import Transaction, TransactionLine
from app.services.fx_service import get_rate, get_reporting_currency
from app.services.reporting.common import ASSET, LIABILITY, classify_account_code

# How many trailing months the trend covers, current month included.
TREND_MONTHS = 12


@dataclass
class Line:
    account_code: str
    account_name: str
    book_value: int
    market_value: int
    unit: str | None = None
    quantity: float | None = None
    rate: float | None = None
    revalued: bool = False

    @property
    def unrealized_gain(self) -> int:
        return self.market_value - self.book_value


@dataclass
class NetWorth:
    as_of: date
    currency: str
    assets: list[Line] = field(default_factory=list)
    liabilities: list[Line] = field(default_factory=list)
    trend: list[tuple[str, int]] = field(default_factory=list)
    missing_rates: list[str] = field(default_factory=list)

    @property
    def total_assets(self) -> int:
        return sum(l.market_value for l in self.assets)

    @property
    def total_liabilities(self) -> int:
        return sum(l.market_value for l in self.liabilities)

    @property
    def net_worth(self) -> int:
        return self.total_assets - self.total_liabilities

    @property
    def unrealized_gain(self) -> int:
        return sum(l.unrealized_gain for l in self.assets)


def _month_key(d: date) -> str:
    return f"{d.year:04d}-{d.month:02d}"


def _month_ends(as_of: date, months: int) -> list[date]:
    """The last `months` month-end dates, oldest first, ending at ``as_of``."""
    ends: list[date] = []
    cursor = as_of
    for _ in range(months):
        ends.append(cursor)
        first = cursor.replace(day=1)
        cursor = first - timedelta(days=1)
    return list(reversed(ends))


def _balances_by_account(db: Session, on: date) -> dict[str, int]:
    """Signed balance per account code up to ``on``, in its natural direction:
    assets debit-positive, liabilities credit-positive."""
    rows = db.execute(
        select(TransactionLine, Transaction.date, Account.code)
        .join(Transaction, TransactionLine.transaction_id == Transaction.id)
        .join(Account, TransactionLine.account_id == Account.id)
        .where(Transaction.deleted_at.is_(None), Transaction.date <= on)
    ).all()
    out: dict[str, int] = {}
    for line, _d, code in rows:
        nature = classify_account_code(code)
        if nature == ASSET:
            out[code] = out.get(code, 0) + line.debit - line.credit
        elif nature == LIABILITY:
            out[code] = out.get(code, 0) + line.credit - line.debit
    return out


def _holdings_by_account(db: Session) -> dict[str, list[PersonalHolding]]:
    grouped: dict[str, list[PersonalHolding]] = {}
    for h in db.execute(select(PersonalHolding)).scalars().all():
        grouped.setdefault(h.account_code, []).append(h)
    return grouped


def _market_value(
    db: Session, holdings: list[PersonalHolding], currency: str, on: date
) -> tuple[int | None, list[str], float | None]:
    """Current worth of the holdings backing one account.

    Returns (value, units missing a rate, single rate when unambiguous). A
    missing rate is reported rather than silently treated as zero — quietly
    valuing someone's gold at nothing is worse than saying the rate is unset.
    """
    total = 0.0
    missing: list[str] = []
    rates: list[float] = []
    for h in holdings:
        rate = get_rate(db, h.unit, currency, on)
        if rate is None:
            missing.append(h.unit)
            continue
        total += float(h.quantity) * rate
        rates.append(rate)
    if missing and not rates:
        return None, missing, None
    return int(round(total)), missing, (rates[0] if len(rates) == 1 else None)


def compute_net_worth(
    db: Session, *, as_of: date | None = None, with_trend: bool = True
) -> NetWorth:
    as_of = as_of or date.today()
    currency = get_reporting_currency(db)
    result = NetWorth(as_of=as_of, currency=currency)

    accounts = {a.code: a for a in db.execute(select(Account)).scalars().all()}
    balances = _balances_by_account(db, as_of)
    holdings = _holdings_by_account(db)

    for code, book in sorted(balances.items()):
        acc = accounts.get(code)
        if acc is None:
            continue
        nature = classify_account_code(code)
        line = Line(
            account_code=code,
            account_name=acc.name,
            book_value=book,
            market_value=book,
        )
        if nature == ASSET and code in holdings:
            market, missing, rate = _market_value(db, holdings[code], currency, as_of)
            result.missing_rates.extend(missing)
            if market is not None:
                hs = holdings[code]
                line.market_value = market
                line.revalued = True
                line.quantity = sum(float(h.quantity) for h in hs)
                line.unit = hs[0].unit if len(hs) == 1 else None
                line.rate = rate
        # A zero balance with no holding is noise on a personal statement.
        if line.book_value == 0 and line.market_value == 0:
            continue
        (result.assets if nature == ASSET else result.liabilities).append(line)

    if with_trend:
        result.trend = _compute_trend(db, as_of, currency, holdings, accounts)
    return result


def _compute_trend(
    db: Session,
    as_of: date,
    currency: str,
    holdings: dict[str, list[PersonalHolding]],
    accounts: dict[str, Account],
) -> list[tuple[str, int]]:
    """Month-end net worth, oldest first.

    Holdings are revalued at each month end's rate, so the curve moves with
    prices as well as with saving. Quantities are current — the app doesn't
    track when a holding was acquired — so early months assume today's
    quantity; that is documented rather than hidden.
    """
    series: list[tuple[str, int]] = []
    for end in _month_ends(as_of, TREND_MONTHS):
        balances = _balances_by_account(db, end)
        total = 0
        for code, book in balances.items():
            if code not in accounts:
                continue
            nature = classify_account_code(code)
            value = book
            if nature == ASSET and code in holdings:
                market, _missing, _rate = _market_value(db, holdings[code], currency, end)
                if market is not None:
                    value = market
            total += value if nature == ASSET else -value
        series.append((_month_key(end), total))
    return series
