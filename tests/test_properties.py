"""Property tests for the arithmetic everything else stands on (roadmap §6).

Seeded random inputs — thousands per property, the same every run — against
invariants rather than hand-picked examples: account balances, the money
conversions and the report periods. A failure prints the seed and input.
"""
from __future__ import annotations

import random
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal
from fractions import Fraction

import jdatetime
import pytest

from app.services.ai_accountant.base import ToolError
from app.services.ai_accountant.spending_tools import resolve_period
from app.services.fx_base import to_base
from app.services.fx_service import convert_minor
from app.services.reporting.common import (
    ASSET, EQUITY, EXPENSE, LIABILITY, OTHER, REVENUE, balance_from_turnovers, classify_account_code,
)

N = 3000
KINDS = (ASSET, LIABILITY, EQUITY, REVENUE, EXPENSE, OTHER)
DEBIT_NATURE = {ASSET, EXPENSE, OTHER}


def _rng(name: str) -> random.Random:
    return random.Random(f"aa-properties-{name}")


def _amount(r: random.Random) -> int:
    # small, round, and huge (a large Iranian company's rial figures pass 9e15)
    return r.choice([r.randint(0, 999), r.randint(0, 10**7) * 1000, r.randint(0, 10**18)])


# --- balances ----------------------------------------------------------------------------------------------------

def test_a_balance_is_the_turnover_difference_on_the_accounts_side():
    r = _rng("balance")
    for _ in range(N):
        kind, d, c = r.choice(KINDS), _amount(r), _amount(r)
        b = balance_from_turnovers(kind, d, c)
        assert b == (d - c if kind in DEBIT_NATURE else c - d), (kind, d, c)
        assert balance_from_turnovers(kind, c, d) == -b                       # swapping the sides flips it
        d2, c2 = _amount(r), _amount(r)
        assert balance_from_turnovers(kind, d + d2, c + c2) == b + balance_from_turnovers(kind, d2, c2)


def _codes() -> list[str]:
    from app.db.seed import SEED_ACCOUNTS, UK_SEED_ACCOUNTS           # the real charts
    return [row[0] for row in SEED_ACCOUNTS] + [row[0] for row in UK_SEED_ACCOUNTS]


def test_balanced_journals_leave_a_trial_balance_that_nets_to_nothing():
    """Σ debit-nature balances = Σ credit-nature balances, over both seeded
    charts, for any set of balanced journals."""
    r = _rng("trial")
    codes = _codes()
    for trial in range(300):
        turnover: dict[str, list[int]] = {}
        for _ in range(r.randint(1, 30)):
            legs = r.randint(2, 5)
            total = _amount(r)
            parts = [r.randint(0, total) for _ in range(legs - 2)]
            parts = sorted(parts)
            credit_parts = [b - a for a, b in zip([0, *parts], [*parts, total])]
            debit_code, *credit_codes = r.sample(codes, len(credit_parts) + 1)
            turnover.setdefault(debit_code, [0, 0])[0] += total
            for code, amt in zip(credit_codes, credit_parts):
                turnover.setdefault(code, [0, 0])[1] += amt
        debit_side = credit_side = 0
        for code, (d, c) in turnover.items():
            kind = classify_account_code(code)
            b = balance_from_turnovers(kind, d, c)
            if kind in DEBIT_NATURE:
                debit_side += b
            else:
                credit_side += b
        assert debit_side == credit_side, trial


def test_every_code_has_exactly_one_nature():
    r = _rng("classify")
    alphabet = "0123456789 .-/A"
    for _ in range(N):
        code = "".join(r.choice(alphabet) for _ in range(r.randint(0, 8)))
        kind = classify_account_code(code)
        assert kind in KINDS
        assert classify_account_code(f"  {code}  ") == kind                # surrounding spaces don't matter
        c = code.strip()
        if c.startswith("91"):
            assert kind == OTHER
    # every seeded account has a statement nature (memo accounts 91xx aside)
    unplaced = [c for c in _codes() if classify_account_code(c) == OTHER and not c.startswith("91")]
    assert unplaced == []


# --- money conversions -------------------------------------------------------------------------------------------

def _half_up(x: Fraction) -> int:
    """Round to the nearest whole unit, halves away from zero — computed apart from Decimal."""
    sign = -1 if x < 0 else 1
    a = abs(x)
    whole, rest = divmod(a.numerator, a.denominator)
    return sign * (whole + (1 if Fraction(rest, a.denominator) >= Fraction(1, 2) else 0))


def test_a_conversion_is_the_exact_product_rounded_half_up():
    r = _rng("convert")
    for _ in range(N):
        amount = r.choice([1, -1]) * _amount(r)
        rate = r.choice([r.random() * 10, r.uniform(0.00001, 0.01), float(r.randint(1, 1_500_000)),
                         r.randint(1, 999) + 0.5, 0.5, 1.0])
        exact = Fraction(amount) * Fraction(Decimal(repr(rate)))
        assert convert_minor(amount, rate) == _half_up(exact), (amount, rate)
    assert convert_minor(5, 0.5) == 3 and convert_minor(-5, 0.5) == -3        # never half-to-even
    assert convert_minor(10**18 + 1, 1.0) == 10**18 + 1                        # no float product


def _balanced_lines(r: random.Random) -> list[tuple[int, int]]:
    total = r.choice([r.randint(1, 999), r.randint(1, 10**9), r.randint(1, 10**15)])
    n = r.randint(1, 5)
    cuts = sorted(r.randint(0, total) for _ in range(n - 1))
    credits = [b - a for a, b in zip([0, *cuts], [*cuts, total])]
    lines = [(total, 0)] + [(0, c) for c in credits]
    if r.random() < 0.3:                                   # several debits too
        cut = r.randint(0, total)
        lines[0:1] = [(cut, 0), (total - cut, 0)]
    r.shuffle(lines)
    return lines


def test_a_balanced_entry_stays_balanced_in_the_base_currency():
    r = _rng("to_base")
    worst = 0
    for _ in range(N):
        lines = _balanced_lines(r)
        rate = r.choice([r.random() * 3, r.uniform(0.0001, 0.001), r.uniform(1, 100_000), 1 / 3, 2 / 3, 0.5])
        out = to_base(lines, rate)
        assert len(out) == len(lines)
        assert sum(d for d, _ in out) == sum(c for _, c in out), (lines, rate, out)
        exact_rate = Fraction(Decimal(repr(float(rate))))
        for (d, c), (bd, bc) in zip(lines, out):
            assert bd >= 0 and bc >= 0
            assert (d == 0) == (bd == 0) or bd <= 1, (lines, rate, out)      # an empty side stays (near) empty
            for orig, conv in ((d, bd), (c, bc)):
                dev = abs(Fraction(conv) - Fraction(orig) * exact_rate)
                worst = max(worst, dev)
                assert dev < len(lines), (lines, rate, out)                   # nudged by whole units only
    assert worst < 2                                                          # in practice: under two units
    # an entry that didn't balance is only rounded, never "fixed"
    assert to_base([(3, 0), (0, 2)], 0.5) == [(2, 0), (0, 1)]


# --- report periods ----------------------------------------------------------------------------------------------

def _jfirst(d: date) -> bool:
    return jdatetime.date.fromgregorian(date=d).day == 1


def _same_month(a: date, b: date, cal: str) -> bool:
    if cal == "jalali":
        ja, jb = jdatetime.date.fromgregorian(date=a), jdatetime.date.fromgregorian(date=b)
        return (ja.year, ja.month) == (jb.year, jb.month)
    return (a.year, a.month) == (b.year, b.month)


def _first_of_month(d: date, cal: str) -> bool:
    return _jfirst(d) if cal == "jalali" else d.day == 1


def _first_of_year(d: date, cal: str) -> bool:
    if cal == "jalali":
        j = jdatetime.date.fromgregorian(date=d)
        return (j.month, j.day) == (1, 1)
    return (d.month, d.day) == (1, 1)


@pytest.mark.parametrize("cal", ["jalali", "gregorian"])
def test_report_periods_tile_the_calendar(cal):
    r = _rng(f"period-{cal}")
    week_start = 5 if cal == "jalali" else 0                # Saturday / Monday
    days = [date(1990, 1, 1) + timedelta(days=r.randint(0, 365 * 50)) for _ in range(N)]
    # and the edges: every first and last day of a month for a few years
    for y in range(2023, 2027):
        for m in range(1, 13):
            first = date(y, m, 1)
            days += [first, first - timedelta(days=1)]
    for today in days:
        p = {k: resolve_period(k, today, cal) for k in
             ("today", "yesterday", "this_week", "last_week", "this_month", "last_month", "this_year", "last_year")}
        for k, v in p.items():
            assert v.from_date <= v.to_date <= today, (k, today)
            assert v.calendar == cal
        assert (p["today"].from_date, p["today"].to_date) == (today, today)
        assert p["yesterday"].to_date == today - timedelta(days=1) == p["yesterday"].from_date
        # weeks: this one runs from its first day to today, the last one is 7 days just before it
        tw, lw = p["this_week"], p["last_week"]
        assert tw.from_date.weekday() == week_start and tw.to_date == today and (today - tw.from_date).days < 7
        assert (lw.to_date - lw.from_date).days == 6 and lw.to_date + timedelta(days=1) == tw.from_date
        # months and years in the company's own calendar
        tm, lm, ty, ly = p["this_month"], p["last_month"], p["this_year"], p["last_year"]
        assert _first_of_month(tm.from_date, cal) and _same_month(tm.from_date, today, cal) and tm.to_date == today
        assert _first_of_month(lm.from_date, cal) and _same_month(lm.from_date, lm.to_date, cal)
        assert lm.to_date + timedelta(days=1) == tm.from_date                  # no gap, no overlap
        assert _first_of_year(ty.from_date, cal) and ty.to_date == today
        assert _first_of_year(ly.from_date, cal) and ly.to_date + timedelta(days=1) == ty.from_date
        assert (ly.to_date - ly.from_date).days + 1 in (365, 366)
        assert tm.from_date >= ty.from_date and tw.from_date <= today
        # labels name the period (Persian always carries the range)
        assert tm.label_en and tm.label_fa and ly.label_en.startswith(("Jalali year", "Year"))


def test_a_custom_period_is_the_range_asked_for_and_never_backwards():
    r = _rng("custom")
    for _ in range(500):
        a = date(2000, 1, 1) + timedelta(days=r.randint(0, 9000))
        b = a + timedelta(days=r.randint(0, 800))
        for cal in ("jalali", "gregorian"):
            p = resolve_period("custom", b, cal, from_date=a, to_date=b)
            assert (p.from_date, p.to_date) == (a, b)
            if a != b:
                with pytest.raises(ToolError):
                    resolve_period("custom", b, cal, from_date=b, to_date=a)
    with pytest.raises(ToolError):
        resolve_period("custom", date(2026, 1, 1), "jalali", from_date=date(2026, 1, 1))
    with pytest.raises(ToolError):
        resolve_period("fortnight", date(2026, 1, 1), "gregorian")
