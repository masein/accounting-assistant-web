"""Statutory payroll rule sets: the parameters, their validation, the seeded
defaults for each locale, and "which rule set is in force on this date".

A rule set is a JSON document (``RuleParams``) with:

* ``tax_brackets`` — MONTHLY thresholds; each step taxes the slice up to
  ``upto`` at ``rate``; the last step has ``upto = null`` (no cap). Iran's
  brackets are set per month by the budget law; the UK's annual bands are
  divided by 12 so both fit the same shape.
* ``insurance_employee_rate`` / ``insurance_employer_rate`` and an optional
  ``insurance_ceiling`` (monthly insurable-wage cap) and ``insurance_floor``
  (below it no insurance is due — the UK primary threshold; 0 for Iran).
* ``insurance_reduces_taxable`` — Iran deducts the worker's 7 % share before
  salary tax; UK National Insurance is not deductible.
* Iran's fixed monthly allowances (``housing_allowance``, ``grocery_allowance``,
  ``child_allowance_per_child``, ``seniority_daily``) which are added to the
  base pay when the profile is in statutory mode. ``child_allowance_insurable``
  is False: حق اولاد is exempt from insurance premiums.
* Reference figures the calculation does not use directly but the UI shows
  and other features will (``min_wage_daily``, ``overtime_multiplier``,
  ``eid_min_multiple`` / ``eid_max_multiple`` for عیدی).

Amounts are whole units of the locale's currency (rial for Iran, pounds for
the UK).
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date

from pydantic import BaseModel, Field, field_validator, model_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.payroll_rules import PayrollRuleSet


class TaxBracket(BaseModel):
    upto: int | None = Field(None, ge=0, description="Monthly taxable income up to which this rate applies; null = no cap")
    rate: float = Field(..., ge=0, le=1)


class RuleParams(BaseModel):
    currency: str = "IRR"
    working_days_month: int = Field(30, ge=1, le=31)
    monthly_hours: float = Field(220, gt=0, description="Divisor for an hourly rate from a monthly wage")
    overtime_multiplier: float = Field(1.4, ge=1)
    min_wage_daily: int = Field(0, ge=0)

    tax_brackets: list[TaxBracket] = Field(default_factory=list)
    insurance_employee_rate: float = Field(0, ge=0, le=1)
    insurance_employer_rate: float = Field(0, ge=0, le=1)
    insurance_floor: int = Field(0, ge=0, description="Monthly wage below which no insurance is due")
    insurance_ceiling: int | None = Field(None, ge=0, description="Monthly insurable-wage cap; null = none")
    insurance_reduces_taxable: bool = False

    housing_allowance: int = Field(0, ge=0)
    grocery_allowance: int = Field(0, ge=0)
    child_allowance_per_child: int = Field(0, ge=0)
    child_allowance_insurable: bool = False
    seniority_daily: int = Field(0, ge=0)

    eid_min_multiple: float = Field(0, ge=0)
    eid_max_multiple: float = Field(0, ge=0)

    @field_validator("currency")
    @classmethod
    def _cur(cls, v: str) -> str:
        v = (v or "").strip().upper()
        if len(v) != 3:
            raise ValueError("currency must be a 3-letter code")
        return v

    @model_validator(mode="after")
    def _brackets_ascending(self) -> "RuleParams":
        last = -1
        for i, b in enumerate(self.tax_brackets):
            if b.upto is None:
                if i != len(self.tax_brackets) - 1:
                    raise ValueError("only the last tax bracket may be uncapped (upto = null)")
            elif b.upto <= last:
                raise ValueError("tax_brackets must have strictly increasing 'upto' values")
            else:
                last = b.upto
        if self.eid_max_multiple and self.eid_max_multiple < self.eid_min_multiple:
            raise ValueError("eid_max_multiple must be >= eid_min_multiple")
        return self


def parse_params(raw: str | dict | None) -> RuleParams:
    if raw is None or raw == "":
        return RuleParams()
    data = json.loads(raw) if isinstance(raw, str) else raw
    return RuleParams.model_validate(data)


# ---------------------------------------------------------------------------
# Seeded defaults
# ---------------------------------------------------------------------------

_IR_1405_MIN_WAGE_DAILY = 5_541_850

# Iran 1405 (21 Mar 2026 – 20 Mar 2027). Sources: Supreme Labour Council
# decree (min wage, allowances) and the 1405 budget law (salary-tax brackets),
# verified 2026-09-25 against sepidarsystem.com, finto.ir and armanmeli.ir.
IR_1405: dict = {
    "currency": "IRR",
    "working_days_month": 30,
    "monthly_hours": 220,
    "overtime_multiplier": 1.4,
    "min_wage_daily": _IR_1405_MIN_WAGE_DAILY,
    # Monthly: exempt to 400 M rial; 10 % to 800 M; 15 % to 1,000 M;
    # 20 % to 1,200 M; 25 % to 1,400 M; 30 % above.
    "tax_brackets": [
        {"upto": 400_000_000, "rate": 0.0},
        {"upto": 800_000_000, "rate": 0.10},
        {"upto": 1_000_000_000, "rate": 0.15},
        {"upto": 1_200_000_000, "rate": 0.20},
        {"upto": 1_400_000_000, "rate": 0.25},
        {"upto": None, "rate": 0.30},
    ],
    "insurance_employee_rate": 0.07,
    "insurance_employer_rate": 0.23,          # 20 % employer + 3 % unemployment
    "insurance_floor": 0,
    "insurance_ceiling": 7 * _IR_1405_MIN_WAGE_DAILY * 30,   # 7× minimum wage
    "insurance_reduces_taxable": True,
    "housing_allowance": 30_000_000,          # حق مسکن
    "grocery_allowance": 22_000_000,          # بن کارگری
    "child_allowance_per_child": 3 * _IR_1405_MIN_WAGE_DAILY,  # حق اولاد = 3 days' wage
    "child_allowance_insurable": False,
    "seniority_daily": 166_667,               # پایه سنوات (5,000,000 / month)
    "eid_min_multiple": 2,
    "eid_max_multiple": 3,
}

# UK 2026/27 (6 Apr 2026 – 5 Apr 2027): personal allowance and bands frozen;
# employee NI 8 % between the primary threshold and the upper earnings limit
# (2 % above, approximated by the ceiling); employer NI 15 % above £5,000/yr.
UK_2026: dict = {
    "currency": "GBP",
    "working_days_month": 21,
    "monthly_hours": 162.5,
    "overtime_multiplier": 1.0,
    "min_wage_daily": 0,
    "tax_brackets": [
        {"upto": 1_048, "rate": 0.0},         # 12,570 / 12 (rounded down like HMRC)
        {"upto": 4_189, "rate": 0.20},        # 50,270 / 12
        {"upto": 10_428, "rate": 0.40},       # 125,140 / 12
        {"upto": None, "rate": 0.45},
    ],
    "insurance_employee_rate": 0.08,
    "insurance_employer_rate": 0.15,
    "insurance_floor": 1_048,                 # primary threshold 12,570 / 12
    "insurance_ceiling": 4_189,               # upper earnings limit 50,270 / 12
    "insurance_reduces_taxable": False,
    "housing_allowance": 0,
    "grocery_allowance": 0,
    "child_allowance_per_child": 0,
    "child_allowance_insurable": False,
    "seniority_daily": 0,
    "eid_min_multiple": 0,
    "eid_max_multiple": 0,
}

DEFAULT_RULE_SETS: tuple[dict, ...] = (
    {"locale": "ir", "year": "1405", "name": "قانون کار و بودجه ۱۴۰۵",
     "effective_from": date(2026, 3, 21), "effective_to": date(2027, 3, 20), "params": IR_1405},
    {"locale": "uk", "year": "2026/27", "name": "PAYE and National Insurance 2026/27",
     "effective_from": date(2026, 4, 6), "effective_to": date(2027, 4, 5), "params": UK_2026},
)


def seed_payroll_rules(db: Session) -> int:
    """Insert the default rule sets that don't exist yet (idempotent by
    locale + year). An edited row is never overwritten. Returns the number
    inserted."""
    inserted = 0
    for spec in DEFAULT_RULE_SETS:
        exists = db.execute(
            select(PayrollRuleSet.id).where(
                PayrollRuleSet.locale == spec["locale"], PayrollRuleSet.year == spec["year"]
            )
        ).first()
        if exists:
            continue
        db.add(PayrollRuleSet(
            locale=spec["locale"], year=spec["year"], name=spec["name"],
            effective_from=spec["effective_from"], effective_to=spec["effective_to"],
            params=json.dumps(RuleParams.model_validate(spec["params"]).model_dump()),
        ))
        inserted += 1
    if inserted:
        db.commit()
    return inserted


@dataclass
class RulesInForce:
    rule_set: PayrollRuleSet
    params: RuleParams


def rules_in_force(db: Session, locale: str, on: date) -> RulesInForce | None:
    """The rule set for ``locale`` whose effective window contains ``on``
    (latest-starting wins). ``default`` locale uses the Iran rules."""
    loc = (locale or "ir").strip().lower()
    if loc not in ("ir", "uk"):
        loc = "ir"
    rows = db.execute(
        select(PayrollRuleSet).where(
            PayrollRuleSet.locale == loc, PayrollRuleSet.effective_from <= on
        ).order_by(PayrollRuleSet.effective_from.desc())
    ).scalars().all()
    for r in rows:
        if r.effective_to is None or r.effective_to >= on:
            return RulesInForce(rule_set=r, params=parse_params(r.params))
    return None


def rule_set_read(r: PayrollRuleSet) -> dict:
    return {
        "id": str(r.id),
        "locale": r.locale,
        "year": r.year,
        "name": r.name,
        "effective_from": r.effective_from.isoformat(),
        "effective_to": r.effective_to.isoformat() if r.effective_to else None,
        "params": parse_params(r.params).model_dump(),
        "updated_at": r.updated_at.isoformat() if r.updated_at else None,
    }
