"""Payroll: employee pay profiles, pay runs (compute), confirm-gated posting to
the ledger, settlement from bank, payslips, and a per-employee year summary.

Postings are locale-aware via ``account_resolver`` and always balanced:
  Post (accrue):  DR wages_expense (gross)
                  CR paye_payable (income tax) / CR social_security_payable
                  CR payroll_deductions_payable (pre-tax) / CR net_pay_payable (net)
  Pay (settle):   DR net_pay_payable (net) / CR bank
Posting and paying are separate, explicit steps — money never moves on its own.
"""
from __future__ import annotations

from datetime import date
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models.account import Account
from app.models.employee_pay import EmployeePayProfile
from app.models.entity import Entity
from app.models.pay_run import PayRun, PayRunLine
from app.models.time_billing import TimeEntry
from app.models.transaction import Transaction, TransactionLine
from app.services import payroll_service
from app.services.time_billing_service import payroll_hours_summary
from app.services.account_resolver import resolve_account_code
from app.services.audit_service import log_audit_event
from app.services.fx_service import get_reporting_currency
from app.services.payroll_service import PayrollInputError
from app.services.period_service import assert_period_open

router = APIRouter(prefix="/payroll", tags=["payroll"])

_date = date  # alias for schema fields named `date`


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class PayProfileUpsert(BaseModel):
    entity_id: UUID
    pay_type: str = Field("salaried", description="salaried | hourly")
    base_salary: int = Field(0, ge=0, description="Per-period gross for salaried staff")
    hourly_rate: int = Field(0, ge=0)
    standard_hours: float = Field(0, ge=0, description="Hours per period; overtime threshold")
    monthly_standard_hours: float | None = Field(
        None, ge=0, description="Required hours per MONTH for hours-derived pay; null → standard_hours"
    )
    overtime_multiplier: float = Field(1.5, ge=1)
    income_tax_rate: float = Field(0, ge=0, le=1)
    social_security_rate: float = Field(0, ge=0, le=1)
    pension_rate: float = Field(0, ge=0, le=1, description="Pre-tax deduction fraction")
    currency: str | None = None
    active: bool = True


class PayRunEmployeeInput(BaseModel):
    entity_id: UUID
    hours: float | None = Field(None, description="Total hours worked (hourly staff)")
    proration: float = Field(1.0, ge=0, le=1, description="Fraction of period worked (salaried)")
    gross_override: int | None = Field(
        None, ge=0, description="Use a pre-computed gross (e.g. a day-weighted mid-period raise)"
    )


class PayRunCreate(BaseModel):
    period_start: _date
    period_end: _date
    pay_date: _date
    currency: str | None = None
    # If omitted, every active profile is included.
    employees: list[PayRunEmployeeInput] | None = None


class ProrateRaiseRequest(BaseModel):
    period_start: _date
    period_end: _date
    change_date: _date
    old_amount: int = Field(..., ge=0)
    new_amount: int = Field(..., ge=0)


# ---------------------------------------------------------------------------
# Serialization
# ---------------------------------------------------------------------------


def _profile_read(p: EmployeePayProfile, name: str | None = None) -> dict:
    return {
        "id": str(p.id),
        "entity_id": str(p.entity_id),
        "employee_name": name,
        "pay_type": p.pay_type,
        "base_salary": int(p.base_salary or 0),
        "hourly_rate": int(p.hourly_rate or 0),
        "standard_hours": float(p.standard_hours or 0),
        "monthly_standard_hours": (
            float(p.monthly_standard_hours) if p.monthly_standard_hours is not None else None
        ),
        "overtime_multiplier": float(p.overtime_multiplier or 0),
        "income_tax_rate": float(p.income_tax_rate or 0),
        "social_security_rate": float(p.social_security_rate or 0),
        "pension_rate": float(p.pension_rate or 0),
        "currency": p.currency,
        "active": bool(p.active),
    }


def _line_read(ln: PayRunLine) -> dict:
    return {
        "id": str(ln.id),
        "entity_id": str(ln.entity_id),
        "employee_name": ln.employee_name,
        "hours": float(ln.hours or 0),
        "overtime_hours": float(ln.overtime_hours or 0),
        "leave_hours": float(getattr(ln, "leave_hours", 0) or 0),
        "proration": float(ln.proration or 1),
        "gross": int(ln.gross or 0),
        "pre_tax_deductions": int(ln.pre_tax_deductions or 0),
        "taxable_base": int(ln.taxable_base or 0),
        "income_tax": int(ln.income_tax or 0),
        "social_security": int(ln.social_security or 0),
        "net_pay": int(ln.net_pay or 0),
    }


def _run_read(run: PayRun) -> dict:
    return {
        "id": str(run.id),
        "period_start": run.period_start.isoformat(),
        "period_end": run.period_end.isoformat(),
        "pay_date": run.pay_date.isoformat(),
        "currency": run.currency,
        "status": run.status,
        "total_gross": int(run.total_gross or 0),
        "total_tax": int(run.total_tax or 0),
        "total_social": int(run.total_social or 0),
        "total_deductions": int(run.total_deductions or 0),
        "total_net": int(run.total_net or 0),
        "post_transaction_id": str(run.post_transaction_id) if run.post_transaction_id else None,
        "pay_transaction_id": str(run.pay_transaction_id) if run.pay_transaction_id else None,
        "lines": [_line_read(ln) for ln in run.lines],
    }


# ---------------------------------------------------------------------------
# Ledger posting helper
# ---------------------------------------------------------------------------


def _post_balanced(db: Session, *, on: date, reference: str, description: str,
                   currency: str, lines: list[tuple[str, int, int, str]]) -> Transaction:
    assert_period_open(db, on)
    total_dr = sum(d for _, d, _c, _l in lines)
    total_cr = sum(c for _, _d, c, _l in lines)
    if total_dr != total_cr or total_dr <= 0:
        raise HTTPException(status_code=400, detail="Payroll entry must be balanced and non-zero.")
    txn = Transaction(date=on, reference=reference[:128], description=description,
                      currency=(currency or "IRR").strip().upper())
    db.add(txn)
    db.flush()
    for code, debit, credit, line_desc in lines:
        acc = db.execute(select(Account).where(Account.code == code)).scalars().one_or_none()
        if not acc:
            raise HTTPException(status_code=422, detail=f"Account not found: {code}")
        db.add(TransactionLine(transaction_id=txn.id, account_id=acc.id,
                               debit=int(debit), credit=int(credit), line_description=line_desc))
    db.flush()
    log_audit_event(db, action="create", entity_type="transaction", entity_id=str(txn.id),
                    detail=description)
    return txn


def _employee_name(db: Session, entity_id: UUID) -> str:
    ent = db.get(Entity, entity_id)
    return ent.name if ent else str(entity_id)


# ---------------------------------------------------------------------------
# Pay profiles
# ---------------------------------------------------------------------------


@router.get("/profiles")
def list_profiles(db: Session = Depends(get_db)) -> list[dict]:
    rows = db.execute(select(EmployeePayProfile)).scalars().all()
    names = {e.id: e.name for e in db.execute(select(Entity)).scalars().all()}
    return [_profile_read(p, names.get(p.entity_id)) for p in rows]


@router.post("/profiles", status_code=201)
def upsert_profile(payload: PayProfileUpsert, db: Session = Depends(get_db)) -> dict:
    """Create or update an employee's pay profile (one per employee entity)."""
    ent = db.get(Entity, payload.entity_id)
    if not ent:
        raise HTTPException(status_code=404, detail="Employee entity not found.")
    if ent.type != "employee":
        raise HTTPException(status_code=422, detail="Pay profiles are only for employee entities.")
    if payload.pay_type not in ("salaried", "hourly"):
        raise HTTPException(status_code=422, detail="pay_type must be 'salaried' or 'hourly'.")

    prof = db.execute(
        select(EmployeePayProfile).where(EmployeePayProfile.entity_id == payload.entity_id)
    ).scalars().one_or_none()
    cur = (payload.currency or get_reporting_currency(db) or "IRR").upper()
    if prof is None:
        prof = EmployeePayProfile(entity_id=payload.entity_id)
        db.add(prof)
    prof.pay_type = payload.pay_type
    prof.base_salary = int(payload.base_salary)
    prof.hourly_rate = int(payload.hourly_rate)
    prof.standard_hours = float(payload.standard_hours)
    prof.monthly_standard_hours = (
        float(payload.monthly_standard_hours) if payload.monthly_standard_hours is not None else None
    )
    prof.overtime_multiplier = float(payload.overtime_multiplier)
    prof.income_tax_rate = float(payload.income_tax_rate)
    prof.social_security_rate = float(payload.social_security_rate)
    prof.pension_rate = float(payload.pension_rate)
    prof.currency = cur
    prof.active = bool(payload.active)
    db.commit()
    db.refresh(prof)
    return _profile_read(prof, ent.name)


# ---------------------------------------------------------------------------
# Pay runs
# ---------------------------------------------------------------------------


@router.post("/runs", status_code=201)
def create_run(payload: PayRunCreate, db: Session = Depends(get_db)) -> dict:
    """Create and calculate a pay run for a period. Computes each employee's
    gross→net but posts nothing — that's a separate confirm-gated step.

    Hourly staff: when no explicit ``hours``/``gross_override`` is supplied,
    gross is DERIVED from the employee's payable tracked time in the period —
    regular = min(worked, required) · rate, monthly overtime = hours over
    ``monthly_standard_hours`` (fallback ``standard_hours``) at the multiplier,
    plus paid leave at the plain rate. The contributing entries are linked to
    the run so each hour is paid exactly once; POSTING the run locks them.
    """
    if payload.period_end < payload.period_start:
        raise HTTPException(status_code=422, detail="period_end is before period_start.")
    cur = (payload.currency or get_reporting_currency(db) or "IRR").upper()

    # Resolve the set of employees: explicit list, or every active profile.
    if payload.employees:
        inputs = {i.entity_id: i for i in payload.employees}
        profiles = db.execute(
            select(EmployeePayProfile).where(EmployeePayProfile.entity_id.in_(list(inputs.keys())))
        ).scalars().all()
        missing = set(inputs) - {p.entity_id for p in profiles}
        if missing:
            raise HTTPException(status_code=422, detail=f"No pay profile for: {sorted(map(str, missing))}")
    else:
        profiles = db.execute(
            select(EmployeePayProfile).where(EmployeePayProfile.active.is_(True))
        ).scalars().all()
        inputs = {}
        if not profiles:
            raise HTTPException(status_code=422, detail="No active pay profiles to run.")

    run = PayRun(period_start=payload.period_start, period_end=payload.period_end,
                 pay_date=payload.pay_date, currency=cur, status="draft")
    db.add(run)
    db.flush()

    totals = {"gross": 0, "tax": 0, "social": 0, "ded": 0, "net": 0}
    included_any = False
    for prof in profiles:
        inp = inputs.get(prof.entity_id)
        is_hourly = (prof.pay_type or "").lower() == "hourly"
        explicit = inp is not None and (inp.hours is not None or inp.gross_override is not None)
        derived = None
        if is_hourly and not explicit:
            derived = payroll_hours_summary(
                db, prof.entity_id, payload.period_start, payload.period_end
            )
            if derived["worked_hours"] <= 0 and derived["leave_hours"] <= 0:
                if inp is not None:
                    raise HTTPException(
                        status_code=422,
                        detail=f"{_employee_name(db, prof.entity_id)}: no payable tracked hours in the period.",
                    )
                continue  # auto-included hourly employee with nothing to pay — skip
        # Monthly required hours are the OT threshold for hours-derived pay.
        required = float(
            prof.monthly_standard_hours
            if prof.monthly_standard_hours is not None
            else (prof.standard_hours or 0)
        )
        try:
            comp = payroll_service.calculate(
                pay_type=prof.pay_type,
                base_salary=int(prof.base_salary or 0),
                hourly_rate=int(prof.hourly_rate or 0),
                standard_hours=(required if derived is not None else float(prof.standard_hours or 0)),
                overtime_multiplier=float(prof.overtime_multiplier or 1),
                income_tax_rate=float(prof.income_tax_rate or 0),
                social_security_rate=float(prof.social_security_rate or 0),
                pension_rate=float(prof.pension_rate or 0),
                hours=(derived["worked_hours"] if derived is not None else (inp.hours if inp else None)),
                leave_hours=(derived["leave_hours"] if derived is not None else 0.0),
                proration=(inp.proration if inp else 1.0),
                gross_override=(inp.gross_override if inp else None),
            )
        except PayrollInputError as e:
            raise HTTPException(status_code=422, detail=f"{_employee_name(db, prof.entity_id)}: {e}") from e

        db.add(PayRunLine(
            run_id=run.id, entity_id=prof.entity_id,
            employee_name=_employee_name(db, prof.entity_id),
            hours=comp.hours, overtime_hours=comp.overtime_hours,
            leave_hours=comp.leave_hours, proration=comp.proration,
            gross=comp.gross, pre_tax_deductions=comp.pre_tax_deductions,
            taxable_base=comp.taxable_base, income_tax=comp.income_tax,
            social_security=comp.social_security, net_pay=comp.net_pay,
        ))
        included_any = True
        # Link the contributing entries so they're excluded from other runs and
        # can be locked (payroll_status='paid') when this run posts.
        if derived is not None and derived["entry_ids"]:
            for entry in db.execute(
                select(TimeEntry).where(TimeEntry.id.in_(derived["entry_ids"]))
            ).scalars().all():
                entry.payroll_run_id = run.id
        totals["gross"] += comp.gross
        totals["tax"] += comp.income_tax
        totals["social"] += comp.social_security
        totals["ded"] += comp.pre_tax_deductions
        totals["net"] += comp.net_pay

    if not included_any:
        raise HTTPException(
            status_code=422,
            detail="No employees with anything to pay in this period.",
        )

    run.total_gross = totals["gross"]
    run.total_tax = totals["tax"]
    run.total_social = totals["social"]
    run.total_deductions = totals["ded"]
    run.total_net = totals["net"]
    db.commit()
    db.refresh(run)
    return _run_read(run)


@router.get("/runs")
def list_runs(db: Session = Depends(get_db)) -> list[dict]:
    runs = db.execute(select(PayRun).order_by(PayRun.created_at.desc())).scalars().all()
    return [_run_read(r) for r in runs]


@router.get("/runs/{run_id}")
def get_run(run_id: UUID, db: Session = Depends(get_db)) -> dict:
    run = db.get(PayRun, run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Pay run not found.")
    return _run_read(run)


@router.post("/runs/{run_id}/post")
def post_run(run_id: UUID, db: Session = Depends(get_db)) -> dict:
    """Post the run's gross→net accrual to the ledger (confirm-gated). Balanced:
    DR wages_expense / CR tax / CR social / CR deductions / CR net-pay payable."""
    run = db.get(PayRun, run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Pay run not found.")
    if run.status != "draft":
        raise HTTPException(status_code=409, detail=f"Run already {run.status}; cannot post again.")
    if not run.lines or run.total_gross <= 0:
        raise HTTPException(status_code=422, detail="Nothing to post for this run.")

    wages = resolve_account_code(db, "wages_expense")
    paye = resolve_account_code(db, "paye_payable")
    social = resolve_account_code(db, "social_security_payable")
    deductions = resolve_account_code(db, "payroll_deductions_payable")
    net_pay = resolve_account_code(db, "net_pay_payable")

    lines: list[tuple[str, int, int, str]] = [
        (wages, run.total_gross, 0, "Gross wages")
    ]
    if run.total_tax > 0:
        lines.append((paye, 0, run.total_tax, "Income tax withheld"))
    if run.total_social > 0:
        lines.append((social, 0, run.total_social, "Social insurance withheld"))
    if run.total_deductions > 0:
        lines.append((deductions, 0, run.total_deductions, "Pre-tax deductions withheld"))
    lines.append((net_pay, 0, run.total_net, "Net pay payable"))

    txn = _post_balanced(
        db, on=run.pay_date, reference=f"PAYROLL-{run.pay_date.isoformat()}",
        description=f"Payroll {run.period_start.isoformat()}–{run.period_end.isoformat()}",
        currency=run.currency, lines=lines,
    )
    run.post_transaction_id = txn.id
    run.status = "posted"
    # Lock the contributing time entries: settled by this run, no longer
    # editable (same discipline as the invoiced lock on the billing side).
    for entry in db.execute(
        select(TimeEntry).where(TimeEntry.payroll_run_id == run.id)
    ).scalars().all():
        entry.payroll_status = "paid"
    db.commit()
    db.refresh(run)
    return _run_read(run)


@router.post("/runs/{run_id}/void")
def void_run(run_id: UUID, db: Session = Depends(get_db)) -> dict:
    """Void a draft or posted run. A posted run's GL accrual is reversed with a
    balanced counter-entry; either way the run's time entries are unlocked
    (payroll_status back to 'unpaid', unlinked) so they can be re-run. A PAID
    run cannot be voided — the bank settlement must be handled first."""
    run = db.get(PayRun, run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Pay run not found.")
    if run.status == "paid":
        raise HTTPException(status_code=409, detail="Run is paid; reverse the bank settlement first.")
    if run.status == "voided":
        raise HTTPException(status_code=409, detail="Run is already voided.")

    if run.status == "posted" and run.post_transaction_id:
        # Reverse the accrual: swap every debit/credit of the posting entry.
        orig_lines = db.execute(
            select(TransactionLine).where(TransactionLine.transaction_id == run.post_transaction_id)
        ).scalars().all()
        acc_codes = {a.id: a.code for a in db.execute(select(Account)).scalars().all()}
        rev = [
            (acc_codes[l.account_id], int(l.credit or 0), int(l.debit or 0),
             f"Reversal — {l.line_description or ''}".strip())
            for l in orig_lines
        ]
        _post_balanced(
            db, on=run.pay_date, reference=f"PAYROLL-VOID-{run.pay_date.isoformat()}",
            description=f"Void payroll {run.period_start.isoformat()}–{run.period_end.isoformat()}",
            currency=run.currency, lines=rev,
        )

    unlocked = 0
    for entry in db.execute(
        select(TimeEntry).where(TimeEntry.payroll_run_id == run.id)
    ).scalars().all():
        entry.payroll_status = "unpaid"
        entry.payroll_run_id = None
        unlocked += 1
    run.status = "voided"
    log_audit_event(db, action="void", entity_type="pay_run", entity_id=str(run.id),
                    detail=f"Voided pay run ({unlocked} time entries unlocked)")
    db.commit()
    db.refresh(run)
    return _run_read(run)


@router.get("/hours-summary")
def hours_summary(
    period_start: _date, period_end: _date,
    entity_id: UUID | None = None,
    db: Session = Depends(get_db),
) -> list[dict]:
    """Per-employee period timesheet summary for HOURLY staff: required vs
    worked, payable, monthly overtime/undertime, and the projected gross
    breakdown — the employer's review screen before running payroll."""
    if period_end < period_start:
        raise HTTPException(status_code=422, detail="period_end is before period_start.")
    q = select(EmployeePayProfile).where(EmployeePayProfile.pay_type == "hourly")
    if entity_id is not None:
        q = q.where(EmployeePayProfile.entity_id == entity_id)
    out = []
    for prof in db.execute(q).scalars().all():
        s = payroll_hours_summary(db, prof.entity_id, period_start, period_end)
        required = float(
            prof.monthly_standard_hours
            if prof.monthly_standard_hours is not None
            else (prof.standard_hours or 0)
        )
        worked, leave = s["worked_hours"], s["leave_hours"]
        regular = min(worked, required) if required > 0 else worked
        overtime = max(0.0, worked - required) if required > 0 else 0.0
        undertime = max(0.0, required - worked) if required > 0 else 0.0
        rate = int(prof.hourly_rate or 0)
        mult = float(prof.overtime_multiplier or 1)
        out.append({
            "entity_id": str(prof.entity_id),
            "employee_name": _employee_name(db, prof.entity_id),
            "required_hours": required,
            "worked_hours": worked,
            "leave_hours": leave,
            "payable_hours": round(worked + leave, 2),
            "overtime_hours": round(overtime, 2),
            "undertime_hours": round(undertime, 2),
            "entry_count": s["entry_count"],
            "hourly_rate": rate,
            "projected_regular_pay": int(regular * rate + 0.5),
            "projected_overtime_pay": int(overtime * rate * mult + 0.5),
            "projected_leave_pay": int(leave * rate + 0.5),
            "projected_gross": int(regular * rate + overtime * rate * mult + leave * rate + 0.5),
            "currency": prof.currency,
        })
    return out


@router.post("/runs/{run_id}/pay")
def pay_run(run_id: UUID, bank_account_code: str | None = None, db: Session = Depends(get_db)) -> dict:
    """Settle net pay from the bank (separate confirm). DR net-pay payable / CR bank."""
    run = db.get(PayRun, run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Pay run not found.")
    if run.status != "posted":
        raise HTTPException(status_code=409, detail=f"Run is {run.status}; post it before paying.")
    if run.total_net <= 0:
        raise HTTPException(status_code=422, detail="Nothing to pay for this run.")

    net_pay = resolve_account_code(db, "net_pay_payable")
    bank = bank_account_code
    if not (bank and db.execute(select(Account.id).where(Account.code == bank)).first()):
        bank = resolve_account_code(db, "bank")

    txn = _post_balanced(
        db, on=run.pay_date, reference=f"PAYRUN-PAY-{run.pay_date.isoformat()}",
        description=f"Net pay settled for payroll {run.period_start.isoformat()}–{run.period_end.isoformat()}",
        currency=run.currency,
        lines=[
            (net_pay, run.total_net, 0, "Clear net pay payable"),
            (bank, 0, run.total_net, "Net pay paid from bank"),
        ],
    )
    run.pay_transaction_id = txn.id
    run.status = "paid"
    db.commit()
    db.refresh(run)
    return _run_read(run)


# ---------------------------------------------------------------------------
# Payslips & year summary
# ---------------------------------------------------------------------------


def _enforce_own_payslip(entity_id: UUID) -> None:
    """A self-service caller (Employee, no PAYROLL_READ) may only fetch their
    OWN payslip; anyone else's looks like it doesn't exist (404)."""
    from app.core.permissions import Perm, own_scope
    from app.core.request_context import get_current_actor
    restricted, own = own_scope(get_current_actor(), Perm.PAYROLL_READ)
    if restricted and str(entity_id) != str(own or ""):
        raise HTTPException(status_code=404, detail="No payslip for this employee in this run.")


@router.get("/runs/{run_id}/payslip/{entity_id}")
def get_payslip(run_id: UUID, entity_id: UUID, db: Session = Depends(get_db)) -> dict:
    _enforce_own_payslip(entity_id)
    run = db.get(PayRun, run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Pay run not found.")
    line = db.execute(
        select(PayRunLine).where(PayRunLine.run_id == run_id, PayRunLine.entity_id == entity_id)
    ).scalars().one_or_none()
    if not line:
        raise HTTPException(status_code=404, detail="No payslip for this employee in this run.")
    return {
        "run_id": str(run.id),
        "employee_name": line.employee_name,
        "entity_id": str(line.entity_id),
        "period_start": run.period_start.isoformat(),
        "period_end": run.period_end.isoformat(),
        "pay_date": run.pay_date.isoformat(),
        "currency": run.currency,
        "status": run.status,
        **_line_read(line),
    }


@router.get("/runs/{run_id}/payslip/{entity_id}/pdf")
def payslip_pdf(run_id: UUID, entity_id: UUID, db: Session = Depends(get_db)):
    """Branded payslip PDF for one employee in a pay run. Tenant-scoped → 404."""
    from fastapi.responses import Response
    from app.models.entity import Entity
    from app.services.documents import render_payslip_pdf
    _enforce_own_payslip(entity_id)
    run = db.get(PayRun, run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Pay run not found.")
    line = db.execute(
        select(PayRunLine).where(PayRunLine.run_id == run_id, PayRunLine.entity_id == entity_id)
    ).scalars().one_or_none()
    if not line:
        raise HTTPException(status_code=404, detail="No payslip for this employee in this run.")
    employee = db.get(Entity, entity_id)
    pdf = render_payslip_pdf(db, run, line, employee)
    return Response(
        content=pdf, media_type="application/pdf",
        headers={"Content-Disposition": f'inline; filename="payslip-{(line.employee_name or "employee").replace(" ", "_")}.pdf"'},
    )


@router.get("/year-summary")
def year_summary(year: int, entity_id: UUID | None = None, db: Session = Depends(get_db)) -> dict:
    """Per-employee year-to-date totals across all runs whose pay_date falls in
    ``year``. Totals tie to the sum of the underlying pay-run lines."""
    start, end = date(year, 1, 1), date(year, 12, 31)
    q = (
        select(PayRunLine, PayRun)
        .join(PayRun, PayRunLine.run_id == PayRun.id)
        .where(PayRun.pay_date >= start, PayRun.pay_date <= end)
    )
    if entity_id is not None:
        q = q.where(PayRunLine.entity_id == entity_id)

    by_emp: dict[str, dict] = {}
    for line, _run in db.execute(q).all():
        key = str(line.entity_id)
        agg = by_emp.setdefault(key, {
            "entity_id": key, "employee_name": line.employee_name,
            "gross": 0, "pre_tax_deductions": 0, "income_tax": 0,
            "social_security": 0, "net_pay": 0, "runs": 0,
        })
        agg["gross"] += int(line.gross or 0)
        agg["pre_tax_deductions"] += int(line.pre_tax_deductions or 0)
        agg["income_tax"] += int(line.income_tax or 0)
        agg["social_security"] += int(line.social_security or 0)
        agg["net_pay"] += int(line.net_pay or 0)
        agg["runs"] += 1

    return {"year": year, "employees": list(by_emp.values())}


@router.post("/prorate-raise")
def compute_prorate_raise(payload: ProrateRaiseRequest, db: Session = Depends(get_db)) -> dict:
    """Helper: day-weighted gross across a mid-period salary change, so the UI
    can pre-fill ``gross_override`` for a prorated run line."""
    try:
        gross = payroll_service.prorate_raise(
            payload.period_start, payload.period_end, payload.change_date,
            payload.old_amount, payload.new_amount,
        )
    except PayrollInputError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    return {"gross": gross}
