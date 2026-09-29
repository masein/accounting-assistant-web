"""Year-end pay for an Iranian company: عیدی و پاداش and حق سنوات (roadmap 2026-09 §3.3).

The labour-law amounts, from the payroll rule set in force at the end of the year:

* **عیدی و پاداش** (قانون افزایش عیدی و پاداش، ۱۳۷۰) — for a full year, two
  months of the last wage (``eid_min_multiple`` × the monthly wage) but no more
  than three months of the minimum wage (``eid_max_multiple`` × 30 days ×
  ``min_wage_daily``).
* **حق سنوات** (ماده ۲۴ قانون کار) — a month's last wage per year of service
  (``sanavat_days_per_year`` days, 30 by default), paid at year end.

Both are pro-rated by the days worked in the Jalali year: from the first day of
the year, or the day the employee was hired (``hired_on``), to the last day —
or the days typed for that person. The monthly wage is the base salary (an
hourly rate × the monthly hours) plus the seniority base for profiles that get
it.

Neither is subject to social insurance (ماده ۳۶ قانون تأمین اجتماعی). سنوات is
exempt from salary tax (ماده ۹۱ ق.م.م). عیدی is exempt up to one month's
exemption — a twelfth of the annual one, the first 0 % bracket — and the rest
is taxed as an addition to a month's salary: the tax on the employee's regular
taxable pay plus the excess, less the tax on the regular pay alone. The
regular pay is the latest regular run's taxable base in the year; without one,
the excess is taxed from the first taxed bracket.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.employee_pay import EmployeePayProfile
from app.models.pay_run import PayRun, PayRunLine
from app.services.payroll_rules import RuleParams
from app.services.payroll_service import _round, progressive_tax

LIVE = ("draft", "posted", "paid")


class YearEndError(ValueError):
    def __init__(self, message: str, status: int = 422):
        super().__init__(message)
        self.status = status


@dataclass
class YearEndLine:
    days_worked: int
    monthly_wage: int
    eidi: int
    sanavat: int
    taxable_eidi: int
    income_tax: int

    @property
    def gross(self) -> int:
        return self.eidi + self.sanavat

    @property
    def net(self) -> int:
        return self.gross - self.income_tax


def year_window(key: str) -> tuple[date, date]:
    """'1405' → (21 March 2026, 20 March 2027)."""
    from app.services.calendar_periods import JALALI, year_bounds
    try:
        year = int(key)
    except (TypeError, ValueError) as e:
        raise YearEndError(f"'{key}' isn't a Jalali year.") from e
    if not 1300 <= year <= 1600:
        raise YearEndError(f"'{key}' isn't a Jalali year.")
    return year_bounds(year, JALALI)


def current_year_key(on: date | None = None) -> str:
    from app.services.calendar_periods import JALALI, year_of
    return str(year_of(on or date.today(), JALALI))


def sanavat_days(rules: RuleParams) -> int:
    if rules.sanavat_days_per_year is not None:
        return int(rules.sanavat_days_per_year)
    return 30 if rules.eid_min_multiple > 0 else 0


def eid_exemption(rules: RuleParams) -> int:
    if rules.eid_exempt_monthly is not None:
        return int(rules.eid_exempt_monthly)
    first = rules.tax_brackets[0] if rules.tax_brackets else None
    return int(first.upto or 0) if first is not None and float(first.rate) == 0 and first.upto else 0


def monthly_wage(profile: EmployeePayProfile, rules: RuleParams) -> int:
    if (profile.pay_type or "").lower() == "hourly":
        hours = float(profile.monthly_standard_hours or profile.standard_hours or rules.monthly_hours or 0)
        base = _round(int(profile.hourly_rate or 0) * hours)
    else:
        base = int(profile.base_salary or 0)
    seniority = rules.seniority_daily * rules.working_days_month if profile.seniority_eligible else 0
    return base + int(seniority)


def days_worked(profile: EmployeePayProfile, start: date, end: date) -> int:
    first = max(start, profile.hired_on) if profile.hired_on else start
    return max(0, (end - first).days + 1)


def calculate(profile: EmployeePayProfile, rules: RuleParams, *, start: date, end: date,
              days: int | None = None, regular_taxable: int | None = None) -> YearEndLine:
    year_days = (end - start).days + 1
    worked = days_worked(profile, start, end) if days is None else max(0, min(int(days), year_days))
    share = worked / year_days
    wage = monthly_wage(profile, rules)
    full_eid = wage * rules.eid_min_multiple
    if rules.eid_max_multiple and rules.min_wage_daily:
        full_eid = min(full_eid, rules.eid_max_multiple * 30 * rules.min_wage_daily)
    eidi = _round(full_eid * share)
    sanavat = _round(wage * sanavat_days(rules) / 30 * share)
    exempt = eid_exemption(rules)
    taxable = max(0, eidi - exempt)
    base = exempt if regular_taxable is None else max(0, int(regular_taxable))
    tax = progressive_tax(base + taxable, rules.tax_brackets) - progressive_tax(base, rules.tax_brackets) if taxable else 0
    return YearEndLine(days_worked=worked, monthly_wage=wage, eidi=eidi, sanavat=sanavat, taxable_eidi=taxable,
                       income_tax=max(0, tax))


def _regular_taxable(db: Session, entity_id, start: date, end: date) -> int | None:
    row = db.execute(
        select(PayRunLine.taxable_base).join(PayRun, PayRun.id == PayRunLine.run_id)
        .where(PayRunLine.entity_id == entity_id, PayRun.kind == "regular", PayRun.status.in_(("posted", "paid")),
               PayRun.period_end >= start, PayRun.period_end <= end)
        .order_by(PayRun.period_end.desc())).first()
    return int(row[0]) if row else None


def create_year_end_run(db: Session, *, year: str | None, pay_date: date,
                        employees: list[dict] | None = None) -> PayRun:
    """A DRAFT year-end run: one line per employee with their عیدی, سنوات and the
    tax on the taxable part of the عیدی. Posting and paying are the ordinary
    run steps."""
    from app.models.entity import Entity
    from app.services.locale_service import get_reporting_locale
    from app.services.payroll_rules import rules_in_force

    key = year or current_year_key(pay_date)
    start, end = year_window(key)
    if (get_reporting_locale(db) or "").lower() == "uk":
        raise YearEndError("عیدی and سنوات are Iranian labour-law payments; a UK company has none.")
    found = rules_in_force(db, "ir", end) or rules_in_force(db, "ir", start)
    if found is None:
        raise YearEndError(f"No payroll rule set covers {key} — add it on the Payroll page (statutory rules).")
    rules = found.params
    if not rules.eid_min_multiple and not sanavat_days(rules):
        raise YearEndError("The payroll rules for this year have no عیدی or سنوات.")

    wanted = {str(e["entity_id"]): e for e in employees} if employees else None
    q = select(EmployeePayProfile)
    q = q.where(EmployeePayProfile.entity_id.in_([_uuid(k) for k in wanted])) if wanted else \
        q.where(EmployeePayProfile.active.is_(True))
    profiles = db.execute(q).scalars().all()
    if wanted and len(profiles) != len(wanted):
        missing = sorted(set(wanted) - {str(p.entity_id) for p in profiles})
        raise YearEndError(f"No pay profile for: {', '.join(missing)}")
    if not profiles:
        raise YearEndError("Nobody is on payroll.")
    clash = db.execute(
        select(PayRunLine.employee_name).join(PayRun, PayRun.id == PayRunLine.run_id)
        .where(PayRun.kind == "year_end", PayRun.year_key == key, PayRun.status.in_(LIVE),
               PayRunLine.entity_id.in_([p.entity_id for p in profiles]))).scalars().all()
    if clash:
        raise YearEndError(f"Already in a year-end run for {key}: {', '.join(sorted(set(clash)))}. "
                           "Void that run first.", 409)

    currency = (rules.currency or "IRR").upper()
    run = PayRun(period_start=start, period_end=end, pay_date=pay_date, currency=currency, status="draft",
                 kind="year_end", year_key=key)
    db.add(run)
    db.flush()
    totals = {"gross": 0, "tax": 0, "net": 0}
    named = [(db.get(Entity, p.entity_id), p) for p in profiles]
    for ent, prof in sorted(named, key=lambda ep: (ep[0].name if ep[0] else "")):
        override = (wanted or {}).get(str(prof.entity_id)) or {}
        line = calculate(prof, rules, start=start, end=end, days=override.get("days_worked"),
                         regular_taxable=_regular_taxable(db, prof.entity_id, start, end))
        db.add(PayRunLine(
            run_id=run.id, entity_id=prof.entity_id, employee_name=ent.name if ent else "?",
            proration=round(line.days_worked / ((end - start).days + 1), 4), gross=line.gross,
            taxable_base=line.taxable_eidi, income_tax=line.income_tax, eidi=line.eidi, sanavat=line.sanavat,
            days_worked=line.days_worked, net_pay=line.net))
        totals["gross"] += line.gross
        totals["tax"] += line.income_tax
        totals["net"] += line.net
    run.total_gross, run.total_tax, run.total_net = totals["gross"], totals["tax"], totals["net"]
    db.flush()
    return run


def _uuid(v):
    import uuid
    try:
        return uuid.UUID(str(v))
    except ValueError as e:
        raise YearEndError(f"'{v}' isn't an employee id.") from e
