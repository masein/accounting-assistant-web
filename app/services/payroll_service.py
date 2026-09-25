"""Payroll calculation — pure gross→net maths, kept free of DB/HTTP so it's
trivially testable and reusable by the API layer.

Model (all amounts whole currency units):
  gross            = salary (optionally prorated) OR regular·rate + overtime·rate·multiplier
  pre_tax          = round(gross · pension_rate)          # employee pre-tax deduction
  taxable_base     = gross − pre_tax                       # income tax is on the reduced base
  income_tax       = round(taxable_base · income_tax_rate)
  social_security  = round(gross · social_security_rate)   # on gross (NI / بیمه)
  net_pay          = gross − pre_tax − income_tax − social_security

Statutory mode (``rules`` given — see app/services/payroll_rules.py):
  gross            = base pay + fixed allowances (Iran: مسکن، بن، اولاد، سنوات)
  insurable_wage   = clamp(gross − non-insurable allowances, floor, ceiling)
  social_security  = round(insurable · employee_rate)        # worker's share
  employer_social  = round(insurable · employer_rate)        # employer's cost
  taxable_base     = gross − pre_tax [− social_security if the locale deducts it]
  income_tax       = progressive over the monthly brackets
  net_pay          = gross − pre_tax − income_tax − social_security

The post splits balance: DR wages(gross) = CR tax + CR social + CR deductions + CR net,
plus DR employer insurance expense = CR social payable for the employer share.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover
    from app.services.payroll_rules import RuleParams


class PayrollInputError(ValueError):
    """Invalid pay input (e.g. zero/negative hours for an hourly employee)."""


def _round(x: float) -> int:
    """Half-up rounding to whole currency units (amounts are non-negative)."""
    return int(x + 0.5)


@dataclass
class PayComponents:
    gross: int
    pre_tax_deductions: int
    taxable_base: int
    income_tax: int
    social_security: int
    net_pay: int
    hours: float = 0.0            # regular worked hours
    overtime_hours: float = 0.0
    leave_hours: float = 0.0      # paid leave (payable, not worked, no OT)
    proration: float = 1.0
    # Statutory mode only (all zero in flat mode).
    allowances: int = 0           # fixed allowances included in gross
    insurable_wage: int = 0       # wage the insurance was computed on
    employer_social: int = 0      # employer's insurance share (a cost, not a deduction)


def progressive_tax(taxable: int, brackets) -> int:
    """Tax on a MONTHLY taxable amount over ascending brackets: each slice up
    to ``upto`` is taxed at that bracket's ``rate``; an uncapped last bracket
    takes the remainder. Rounded once, half-up, at the end."""
    if taxable <= 0 or not brackets:
        return 0
    tax = 0.0
    lower = 0
    for b in brackets:
        upto = b.upto if hasattr(b, "upto") else b.get("upto")
        rate = float(b.rate if hasattr(b, "rate") else b.get("rate", 0))
        if upto is None:
            tax += (taxable - lower) * rate
            lower = taxable
            break
        slice_top = min(taxable, int(upto))
        if slice_top > lower:
            tax += (slice_top - lower) * rate
            lower = slice_top
        if taxable <= int(upto):
            break
    return _round(tax)


def statutory_allowances(rules: "RuleParams", *, children: int = 0,
                         seniority_eligible: bool = False, proration: float = 1.0) -> tuple[int, int]:
    """(total allowances, non-insurable part) for one month, prorated like the
    base pay. Child allowance is exempt from insurance unless the rule set
    says otherwise."""
    pr = max(0.0, float(proration or 0))
    kids = max(0, int(children or 0))
    child = rules.child_allowance_per_child * kids
    seniority = rules.seniority_daily * rules.working_days_month if seniority_eligible else 0
    total = _round((rules.housing_allowance + rules.grocery_allowance + child + seniority) * pr)
    non_insurable = 0 if rules.child_allowance_insurable else _round(child * pr)
    return total, non_insurable


def split_hours(total_hours: float, standard_hours: float) -> tuple[float, float]:
    """Split worked hours into (regular, overtime). Hours over the standard
    threshold are overtime; if no standard is set, nothing is overtime."""
    if total_hours < 0:
        raise PayrollInputError("Hours cannot be negative.")
    std = float(standard_hours or 0)
    if std <= 0:
        return total_hours, 0.0
    regular = min(total_hours, std)
    overtime = max(0.0, total_hours - std)
    return regular, overtime


def prorate_raise(
    period_start: date, period_end: date, change_date: date,
    old_amount: int, new_amount: int,
) -> int:
    """Day-weighted gross across a mid-period salary change: ``old_amount`` for
    days before ``change_date`` and ``new_amount`` from ``change_date`` onward
    (period end inclusive). If the change is outside the period, returns the
    rate in force for the whole period."""
    if period_end < period_start:
        raise PayrollInputError("period_end is before period_start.")
    total_days = (period_end - period_start).days + 1
    if change_date <= period_start:
        return int(new_amount)
    if change_date > period_end:
        return int(old_amount)
    days_old = (change_date - period_start).days
    days_new = total_days - days_old
    return _round(old_amount * days_old / total_days + new_amount * days_new / total_days)


def calculate(
    *,
    pay_type: str,
    base_salary: int = 0,
    hourly_rate: int = 0,
    standard_hours: float = 0,
    overtime_multiplier: float = 1.5,
    income_tax_rate: float = 0,
    social_security_rate: float = 0,
    pension_rate: float = 0,
    hours: float | None = None,
    leave_hours: float = 0.0,
    proration: float = 1.0,
    gross_override: int | None = None,
    rules: "RuleParams | None" = None,
    children: int = 0,
    seniority_eligible: bool = False,
) -> PayComponents:
    """Compute one employee's pay breakdown.

    - salaried: gross = round(base_salary · proration) unless ``gross_override``
      is given (e.g. a day-weighted mid-period raise).
    - hourly: ``hours`` is total WORKED; hours over ``standard_hours`` are paid
      at ``overtime_multiplier``. ``leave_hours`` (paid time off) are paid at
      the plain rate and never count toward overtime. Zero total payable hours
      are rejected; zero worked with positive leave is fine (a full-leave month).
    - statutory (``rules`` given): the locale's fixed allowances are added to
      the base pay, insurance is computed on the capped insurable wage at the
      rule set's employee/employer rates, and income tax is progressive over
      the monthly brackets. ``income_tax_rate`` / ``social_security_rate`` are
      ignored; ``pension_rate`` still applies as an extra pre-tax deduction.
    """
    pension_rate = float(pension_rate or 0)
    income_tax_rate = float(income_tax_rate or 0)
    social_security_rate = float(social_security_rate or 0)
    reg = ot = 0.0
    leave = max(0.0, float(leave_hours or 0))

    if gross_override is not None:
        if gross_override < 0:
            raise PayrollInputError("Gross cannot be negative.")
        gross = int(gross_override)
        proration = float(proration)
        leave = 0.0
    elif (pay_type or "").lower() == "hourly":
        worked = float(hours) if hours is not None else 0.0
        if hours is None and leave <= 0:
            raise PayrollInputError("Hourly employees need an hours value.")
        if worked < 0:
            raise PayrollInputError("Hours cannot be negative.")
        if worked <= 0 and leave <= 0:
            raise PayrollInputError("Hours must be greater than zero.")
        reg, ot = split_hours(worked, standard_hours)
        gross = _round(
            reg * hourly_rate
            + ot * hourly_rate * float(overtime_multiplier or 1)
            + leave * hourly_rate
        )
    else:  # salaried
        if proration < 0:
            raise PayrollInputError("Proration cannot be negative.")
        leave = 0.0
        gross = _round(float(base_salary) * float(proration))

    allowances = insurable = employer_social = 0
    if rules is not None:
        allowances, non_insurable = statutory_allowances(
            rules, children=children, seniority_eligible=seniority_eligible,
            proration=(float(proration) if gross_override is None and pay_type != "hourly" else 1.0),
        )
        gross += allowances
        pre_tax = _round(gross * pension_rate)
        insurable = max(0, gross - non_insurable)
        if rules.insurance_ceiling is not None:
            insurable = min(insurable, int(rules.insurance_ceiling))
        if insurable < int(rules.insurance_floor or 0):
            insurable = 0
        social_security = _round(insurable * rules.insurance_employee_rate)
        employer_social = _round(insurable * rules.insurance_employer_rate)
        taxable_base = gross - pre_tax - (social_security if rules.insurance_reduces_taxable else 0)
        income_tax = progressive_tax(max(0, taxable_base), rules.tax_brackets)
    else:
        pre_tax = _round(gross * pension_rate)
        taxable_base = gross - pre_tax
        income_tax = _round(taxable_base * income_tax_rate)
        social_security = _round(gross * social_security_rate)
    net_pay = gross - pre_tax - income_tax - social_security
    if net_pay < 0:
        raise PayrollInputError(
            "Withholdings exceed gross pay — check the tax/deduction rates."
        )
    return PayComponents(
        gross=gross, pre_tax_deductions=pre_tax, taxable_base=taxable_base,
        income_tax=income_tax, social_security=social_security, net_pay=net_pay,
        hours=reg, overtime_hours=ot, leave_hours=leave, proration=float(proration),
        allowances=allowances, insurable_wage=insurable, employer_social=employer_social,
    )
