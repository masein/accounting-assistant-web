"""Payroll calculation — pure gross→net maths, kept free of DB/HTTP so it's
trivially testable and reusable by the API layer.

Model (all amounts whole currency units):
  gross            = salary (optionally prorated) OR regular·rate + overtime·rate·multiplier
  pre_tax          = round(gross · pension_rate)          # employee pre-tax deduction
  taxable_base     = gross − pre_tax                       # income tax is on the reduced base
  income_tax       = round(taxable_base · income_tax_rate)
  social_security  = round(gross · social_security_rate)   # on gross (NI / بیمه)
  net_pay          = gross − pre_tax − income_tax − social_security

The post splits balance: DR wages(gross) = CR tax + CR social + CR deductions + CR net.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date


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
) -> PayComponents:
    """Compute one employee's pay breakdown.

    - salaried: gross = round(base_salary · proration) unless ``gross_override``
      is given (e.g. a day-weighted mid-period raise).
    - hourly: ``hours`` is total WORKED; hours over ``standard_hours`` are paid
      at ``overtime_multiplier``. ``leave_hours`` (paid time off) are paid at
      the plain rate and never count toward overtime. Zero total payable hours
      are rejected; zero worked with positive leave is fine (a full-leave month).
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
    )
