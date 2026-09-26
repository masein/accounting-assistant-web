"""Tax years, ITSA quarters and VAT periods with HMRC's deadlines."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

STAGGERS = {
    # quarters ending in these months
    1: (3, 6, 9, 12),
    2: (4, 7, 10, 1),
    3: (5, 8, 11, 2),
}


def _month_end(y: int, m: int) -> date:
    nxt = date(y + (m // 12), m % 12 + 1, 1)
    return nxt - timedelta(days=1)


def _add_months(d: date, months: int) -> date:
    """Same day ``months`` later, clamped to the month's last day."""
    y, m = divmod(d.month - 1 + months, 12)
    y, m = d.year + y, m + 1
    return min(date(y, m, 1) + timedelta(days=d.day - 1), _month_end(y, m))


# --- tax years (6 April – 5 April) -------------------------------------------------------

def tax_year_of(d: date) -> int:
    """The starting year: 6 Apr 2026 – 5 Apr 2027 is 2026 ("2026-27")."""
    return d.year if d >= date(d.year, 4, 6) else d.year - 1


def tax_year_label(start_year: int) -> str:
    return f"{start_year}-{str(start_year + 1)[-2:]}"


def parse_tax_year(label: str | int) -> int:
    """'2026-27' or 2026 → 2026."""
    text = str(label).strip()
    try:
        start = int(text.split("-")[0])
    except ValueError as e:
        raise ValueError("tax year must look like 2026-27") from e
    if "-" in text:
        tail = text.split("-", 1)[1]
        if tail != str(start + 1)[-len(tail):]:
            raise ValueError("tax year must be consecutive years, e.g. 2026-27")
    if not 2016 <= start <= 2100:
        raise ValueError("tax year out of range")
    return start


def tax_year_bounds(start_year: int) -> tuple[date, date]:
    return date(start_year, 4, 6), date(start_year + 1, 4, 5)


# --- ITSA quarterly update periods -----------------------------------------------------------

@dataclass(frozen=True)
class ItsaQuarter:
    tax_year: int       # starting year
    quarter: int        # 1..4
    basis: str          # "standard" (6 Apr …) or "calendar" (1 Apr …)

    @property
    def start(self) -> date:
        y = self.tax_year
        if self.basis == "calendar":
            return [date(y, 4, 1), date(y, 7, 1), date(y, 10, 1), date(y + 1, 1, 1)][self.quarter - 1]
        return [date(y, 4, 6), date(y, 7, 6), date(y, 10, 6), date(y + 1, 1, 6)][self.quarter - 1]

    @property
    def end(self) -> date:
        y = self.tax_year
        if self.basis == "calendar":
            return [date(y, 6, 30), date(y, 9, 30), date(y, 12, 31), date(y + 1, 3, 31)][self.quarter - 1]
        return [date(y, 7, 5), date(y, 10, 5), date(y + 1, 1, 5), date(y + 1, 4, 5)][self.quarter - 1]

    @property
    def cumulative_start(self) -> date:
        """Updates are cumulative from the start of the tax year."""
        return date(self.tax_year, 4, 1) if self.basis == "calendar" else date(self.tax_year, 4, 6)

    @property
    def deadline(self) -> date:
        """The 7th of the second month after the quarter's last month:
        7 Aug, 7 Nov, 7 Feb, 7 May — the same on either basis."""
        y = self.tax_year
        return [date(y, 8, 7), date(y, 11, 7), date(y + 1, 2, 7), date(y + 1, 5, 7)][self.quarter - 1]

    def as_dict(self) -> dict:
        return {"tax_year": tax_year_label(self.tax_year), "quarter": self.quarter, "basis": self.basis,
                "start": self.start.isoformat(), "end": self.end.isoformat(),
                "cumulative_start": self.cumulative_start.isoformat(), "deadline": self.deadline.isoformat()}


def itsa_quarters(start_year: int, basis: str = "standard") -> list[ItsaQuarter]:
    if basis not in ("standard", "calendar"):
        raise ValueError("basis must be standard or calendar")
    return [ItsaQuarter(start_year, q, basis) for q in (1, 2, 3, 4)]


def itsa_quarter_of(d: date, basis: str = "standard") -> ItsaQuarter:
    y = tax_year_of(d) if basis == "standard" else (d.year if d.month >= 4 else d.year - 1)
    for q in itsa_quarters(y, basis):
        if q.start <= d <= q.end:
            return q
    raise ValueError(f"no ITSA quarter for {d}")  # pragma: no cover - the four quarters cover the year


# --- VAT return periods ------------------------------------------------------------------------

@dataclass(frozen=True)
class VatPeriod:
    start: date
    end: date

    @property
    def deadline(self) -> date:
        """One calendar month and seven days after the period ends — HMRC
        counts the month to the end of the next month, so it is always the
        7th of the second month: 31 Mar → 7 May, 28 Feb → 7 Apr."""
        nxt = _add_months(date(self.end.year, self.end.month, 1), 1)
        return _month_end(nxt.year, nxt.month) + timedelta(days=7)

    @property
    def key(self) -> str:
        return self.end.isoformat()

    def as_dict(self) -> dict:
        return {"start": self.start.isoformat(), "end": self.end.isoformat(), "deadline": self.deadline.isoformat(),
                "key": self.key}


def vat_period_ending(end: date, stagger: str | int) -> VatPeriod:
    """The period that ends on ``end`` (a month end in the stagger)."""
    if end != _month_end(end.year, end.month):
        raise ValueError("a VAT period ends on a month end")
    if str(stagger) == "monthly":
        return VatPeriod(date(end.year, end.month, 1), end)
    months = STAGGERS.get(int(stagger))
    if months is None:
        raise ValueError("stagger must be 1, 2, 3 or monthly")
    if end.month not in months:
        raise ValueError(f"stagger {stagger} quarters end in months {months}")
    first = _add_months(date(end.year, end.month, 1), -2)
    return VatPeriod(first, end)


def vat_periods_before(today: date, stagger: str | int, count: int = 6) -> list[VatPeriod]:
    """The ``count`` most recent periods ending on or before the end of the
    current month, newest first (the current one included)."""
    out: list[VatPeriod] = []
    y, m = today.year, today.month
    while len(out) < count:
        end = _month_end(y, m)
        try:
            out.append(vat_period_ending(end, stagger))
        except ValueError:
            pass
        y, m = (y - 1, 12) if m == 1 else (y, m - 1)
    return out
