"""Payroll tools for the AI accountant (roadmap 2026-09 §5.1 — "what did we
pay Sara?" and "run September's payroll" were out of the assistant's reach).

Read: get_payroll — recent pay runs, or one employee's pay history.
Proposals (confirm-gated, executed in payroll_execute.py through the same
route code the Payroll page uses):

* propose_run_payroll — calculate a DRAFT run for a period. Nothing reaches
  the books; the draft can be voided.
* propose_post_pay_run — post a draft run's gross→net accrual.
* propose_pay_pay_run — pay a posted run's net pay from the bank.

Posting and paying carry the run's pay date (``on``) and amount, so the
closed-period refusal and the two-person approval threshold (guardrails)
apply to them like any other entry.
"""
from __future__ import annotations

import uuid
from datetime import date as _date
from typing import Any

from pydantic import BaseModel, Field
from sqlalchemy import select

from app.models.employee_pay import EmployeePayProfile
from app.models.pay_run import PayRun, PayRunLine

from .base import BaseTool, ToolContext, ToolError
from .time_tools import _find_entity, _register

LIVE = ("draft", "posted", "paid")


def _run_row(run: PayRun) -> dict[str, Any]:
    return {
        "id": str(run.id), "period_start": run.period_start.isoformat(), "period_end": run.period_end.isoformat(),
        "pay_date": run.pay_date.isoformat(), "status": run.status, "currency": run.currency,
        "kind": run.kind or "regular", "year_key": run.year_key,             # year_end = عیدی و سنوات
        "employees": len(run.lines), "total_gross": int(run.total_gross or 0), "total_tax": int(run.total_tax or 0),
        "total_social": int(run.total_social or 0), "total_deductions": int(run.total_deductions or 0),
        "total_net": int(run.total_net or 0), "employer_social": int(run.total_employer_social or 0),
    }


def find_run(ctx: ToolContext, ref: str | None, *, status: str | None = None) -> PayRun:
    """A pay run by id, by a date inside its period, or by month (YYYY-MM in the
    company's calendar); with no reference, the latest run in ``status``."""
    q = select(PayRun).where(PayRun.status.in_(LIVE))
    ref = (ref or "").strip()
    if ref:
        try:
            run = ctx.db.get(PayRun, uuid.UUID(ref))
            if run:
                return run
        except ValueError:
            pass
        try:
            if len(ref) == 7:
                from app.services.calendar_periods import key_bounds
                start, end = key_bounds(ref)
                q = q.where(PayRun.period_end >= start, PayRun.period_end <= end)
            else:
                on = _date.fromisoformat(ref[:10])
                q = q.where(PayRun.period_start <= on, PayRun.period_end >= on)
        except (ValueError, KeyError) as e:
            raise ToolError(f"{ref!r} isn't a pay run id, a date (YYYY-MM-DD) or a month (YYYY-MM).",
                            code="pay_run_not_found") from e
    if status:
        q = q.where(PayRun.status == status)
    rows = ctx.db.execute(q.order_by(PayRun.period_end.desc(), PayRun.created_at.desc())).scalars().all()
    if not rows:
        what = f" {status}" if status else ""
        raise ToolError(f"No{what} pay run" + (f" for {ref}" if ref else "") + ".", code="pay_run_not_found")
    if ref and len(rows) > 1:
        raise ToolError(f"{len(rows)} pay runs match {ref!r}: " + "; ".join(
            f"{r.period_start}–{r.period_end} ({r.status}, id {r.id})" for r in rows[:5]) + " — ask which one.",
            code="pay_run_ambiguous")
    return rows[0]


# ---------------------------------------------------------------------------
# READ
# ---------------------------------------------------------------------------

class GetPayrollInput(BaseModel):
    employee: str | None = Field(None, description="Employee name — their pay history. Omit for the recent pay runs.")
    from_date: _date | None = Field(None, description="Only pay dates on or after this (YYYY-MM-DD).")
    to_date: _date | None = Field(None, description="Only pay dates on or before this.")
    include_drafts: bool = Field(False, description="Include draft runs (not in the books yet).")
    limit: int = Field(12, ge=1, le=60)


class GetPayroll(BaseTool):
    name = "get_payroll"
    category = "read"
    description = (
        "Payroll: the recent pay runs (period, pay date, status draft/posted/paid, gross, tax, insurance, net, "
        "employer insurance) — or, with an employee, what that person was paid run by run and in total, and their "
        "pay profile. Use for 'what did we pay Sara this year?', 'how much was last month's payroll?', "
        "'is September's payroll posted?'. Draft runs are left out unless asked for. Pure read."
    )
    InputSchema = GetPayrollInput

    async def run(self, ctx: ToolContext, args: GetPayrollInput) -> dict[str, Any]:
        statuses = LIVE if args.include_drafts else ("posted", "paid")
        q = select(PayRun).where(PayRun.status.in_(statuses))
        if args.from_date:
            q = q.where(PayRun.pay_date >= args.from_date)
        if args.to_date:
            q = q.where(PayRun.pay_date <= args.to_date)
        if not args.employee:
            runs = ctx.db.execute(q.order_by(PayRun.pay_date.desc()).limit(args.limit)).scalars().all()
            drafts = ctx.db.execute(select(PayRun).where(PayRun.status == "draft")).scalars().all()
            return {"runs": [_run_row(r) for r in runs], "drafts_waiting": [_run_row(r) for r in drafts]}
        ent = _find_entity(ctx, args.employee, ("employee",))
        if ent is None:
            raise ToolError(f"No employee named {args.employee!r}", code="entity_not_found")
        rows = ctx.db.execute(
            select(PayRunLine, PayRun).join(PayRun, PayRun.id == PayRunLine.run_id)
            .where(PayRunLine.entity_id == ent.id, PayRun.id.in_(q.with_only_columns(PayRun.id)))
            .order_by(PayRun.pay_date.desc()).limit(args.limit)).all()
        history, totals = [], {}
        for ln, run in rows:
            history.append({
                "period_start": run.period_start.isoformat(), "period_end": run.period_end.isoformat(),
                "pay_date": run.pay_date.isoformat(), "status": run.status, "currency": run.currency,
                "gross": int(ln.gross or 0), "income_tax": int(ln.income_tax or 0),
                "social_security": int(ln.social_security or 0), "deductions": int(ln.pre_tax_deductions or 0),
                "net_pay": int(ln.net_pay or 0), "hours": float(ln.hours or 0), "overtime_hours": float(ln.overtime_hours or 0),
            })
            t = totals.setdefault(run.currency, {"gross": 0, "income_tax": 0, "social_security": 0, "net_pay": 0, "runs": 0})
            for k in ("gross", "income_tax", "social_security", "net_pay"):
                t[k] += history[-1][k]
            t["runs"] += 1
        prof = ctx.db.execute(select(EmployeePayProfile).where(EmployeePayProfile.entity_id == ent.id)).scalars().first()
        profile = None if prof is None else {
            "pay_type": prof.pay_type, "base_salary": int(prof.base_salary or 0), "hourly_rate": int(prof.hourly_rate or 0),
            "currency": prof.currency, "active": bool(prof.active), "tax_mode": prof.tax_mode}
        return {"employee": ent.name, "profile": profile, "history": history, "totals_by_currency": totals}


# ---------------------------------------------------------------------------
# PROPOSALS
# ---------------------------------------------------------------------------

class ProposeRunPayrollInput(BaseModel):
    period_start: _date = Field(..., description="First day of the pay period (YYYY-MM-DD).")
    period_end: _date = Field(..., description="Last day of the pay period.")
    pay_date: _date | None = Field(None, description="The day staff are paid (defaults to period_end).")
    employees: list[str] | None = Field(None, description="Only these employees, by name. Omit for everyone on payroll.")


class ProposeRunPayroll(BaseTool):
    name = "propose_run_payroll"
    category = "proposal"
    description = (
        "Calculate a DRAFT pay run for a period — every active employee on payroll, or the ones named. The draft "
        "works out gross → tax, insurance, net for each person; NOTHING is posted to the books (post it with "
        "propose_post_pay_run once the user has checked it). Use for 'run payroll for September', 'do Shahrivar's "
        "salaries'. Refuses a period someone is already paid for. Returns a confirm card."
    )
    InputSchema = ProposeRunPayrollInput

    async def run(self, ctx: ToolContext, args: ProposeRunPayrollInput) -> dict[str, Any]:
        from app.models.entity import Entity
        pay_date = args.pay_date or args.period_end
        if args.period_end < args.period_start:
            raise ToolError("The period ends before it starts.", code="bad_period")
        profiles = ctx.db.execute(select(EmployeePayProfile).where(EmployeePayProfile.active.is_(True))).scalars().all()
        by_entity = {p.entity_id: p for p in profiles}
        chosen: list[tuple[Entity, EmployeePayProfile]] = []
        if args.employees:
            for name in args.employees:
                ent = _find_entity(ctx, name, ("employee",))
                if ent is None:
                    raise ToolError(f"No employee named {name!r}", code="entity_not_found")
                prof = by_entity.get(ent.id)
                if prof is None:
                    raise ToolError(f"{ent.name} has no active pay profile — set one up on the Payroll page first.",
                                    code="no_pay_profile")
                chosen.append((ent, prof))
        else:
            for prof in profiles:
                ent = ctx.db.get(Entity, prof.entity_id)
                if ent is not None:
                    chosen.append((ent, prof))
        if not chosen:
            raise ToolError("Nobody is on payroll yet — add a pay profile on the Payroll page first.", code="no_pay_profile")
        clashes = ctx.db.execute(
            select(PayRunLine.employee_name, PayRun.period_start, PayRun.period_end, PayRun.status)
            .join(PayRun, PayRunLine.run_id == PayRun.id)
            .where(PayRunLine.entity_id.in_([e.id for e, p in chosen if (p.pay_type or "").lower() != "hourly"]),
                   PayRun.status.in_(LIVE), PayRun.kind != "year_end", PayRun.period_start <= args.period_end,
                   PayRun.period_end >= args.period_start)).all()
        if clashes:
            raise ToolError("Already in a pay run for an overlapping period: " + "; ".join(
                f"{n} ({a}–{b}, {s})" for n, a, b, s in clashes[:6]) + ". Void that run first, or use it.",
                code="pay_run_overlap")
        people = [{"entity_id": str(e.id), "name": e.name, "pay_type": p.pay_type,
                   "base_salary": int(p.base_salary or 0), "hourly_rate": int(p.hourly_rate or 0),
                   "currency": p.currency} for e, p in sorted(chosen, key=lambda ep: ep[0].name)]
        payload = {"period_start": args.period_start.isoformat(), "period_end": args.period_end.isoformat(),
                   "pay_date": pay_date.isoformat(), "entity_ids": [p["entity_id"] for p in people] if args.employees else None,
                   "employees": [p["name"] for p in people]}
        token = _register(ctx, self.name, payload)
        summary = (f"Draft pay run for {args.period_start}–{args.period_end}, paid {pay_date}, for "
                   f"{len(people)} employee(s): " + ", ".join(p["name"] for p in people)
                   + ". Gross → tax, insurance and net are calculated on confirm; nothing is posted to the books "
                   "until the run is posted.")
        return {"confirmation_token": str(token), "status": "pending", "summary": summary,
                "preview": {**payload, "people": people}}


class PayRunRefInput(BaseModel):
    run: str | None = Field(None, description="Pay run id, a date inside its period, or its month (YYYY-MM). "
                                              "Omit for the latest one waiting.")


class ProposePostPayRun(BaseTool):
    name = "propose_post_pay_run"
    category = "proposal"
    description = (
        "Post a DRAFT pay run to the books: wages expense against tax, insurance and net pay owed (and the "
        "employer's insurance). Use after propose_run_payroll, for 'post the September payroll'. Shows the "
        "totals. Returns a confirm card."
    )
    InputSchema = PayRunRefInput

    async def run(self, ctx: ToolContext, args: PayRunRefInput) -> dict[str, Any]:
        run = find_run(ctx, args.run, status=None if args.run else "draft")
        if run.status != "draft":
            raise ToolError(f"The {run.period_start}–{run.period_end} run is already {run.status}.", code="pay_run_not_draft")
        if not run.lines or int(run.total_gross or 0) <= 0:
            raise ToolError("That run has nothing to post.", code="pay_run_empty")
        r = _run_row(run)
        payload = {"run_id": r["id"], "period_start": r["period_start"], "period_end": r["period_end"],
                   "on": r["pay_date"], "amount": r["total_gross"], "currency": run.currency}
        token = _register(ctx, self.name, payload)
        summary = (f"Post payroll {r['period_start']}–{r['period_end']} on {r['pay_date']} for {r['employees']} "
                   f"employee(s): gross {r['total_gross']:,} {run.currency}; tax {r['total_tax']:,}, insurance "
                   f"{r['total_social']:,}, deductions {r['total_deductions']:,}; net pay owed {r['total_net']:,}"
                   + (f"; employer insurance {r['employer_social']:,}" if r["employer_social"] else "") + ".")
        return {"confirmation_token": str(token), "status": "pending", "summary": summary,
                "preview": {**payload, **r, "lines": [{"name": ln.employee_name, "gross": int(ln.gross or 0),
                                                       "net_pay": int(ln.net_pay or 0)} for ln in run.lines]}}


class ProposePayPayRunInput(PayRunRefInput):
    bank_account_code: str | None = Field(None, description="The bank account paying (optional; the default bank).")


class ProposePayPayRun(BaseTool):
    name = "propose_pay_pay_run"
    category = "proposal"
    description = (
        "Pay a POSTED pay run's net pay from the bank (clears net pay owed). Use for 'we paid the September "
        "salaries', 'pay the payroll'. Returns a confirm card."
    )
    InputSchema = ProposePayPayRunInput

    async def run(self, ctx: ToolContext, args: ProposePayPayRunInput) -> dict[str, Any]:
        run = find_run(ctx, args.run, status=None if args.run else "posted")
        if run.status != "posted":
            hint = " Post it first." if run.status == "draft" else ""
            raise ToolError(f"The {run.period_start}–{run.period_end} run is {run.status}.{hint}", code="pay_run_not_posted")
        if args.bank_account_code:
            from app.models.account import Account
            if not ctx.db.execute(select(Account.id).where(Account.code == args.bank_account_code)).first():
                raise ToolError(f"No account {args.bank_account_code}.", code="account_not_found")
        r = _run_row(run)
        payload = {"run_id": r["id"], "period_start": r["period_start"], "period_end": r["period_end"],
                   "on": r["pay_date"], "amount": r["total_net"], "currency": run.currency,
                   "bank_account_code": args.bank_account_code}
        token = _register(ctx, self.name, payload)
        summary = (f"Pay net salaries of {r['total_net']:,} {run.currency} for {r['period_start']}–{r['period_end']} "
                   f"on {r['pay_date']} from " + (f"account {args.bank_account_code}" if args.bank_account_code else "the bank")
                   + f" ({r['employees']} employee(s)).")
        return {"confirmation_token": str(token), "status": "pending", "summary": summary, "preview": {**payload, **r}}


def register_payroll_tools(registry) -> None:
    registry.register(GetPayroll())
    registry.register(ProposeRunPayroll())
    registry.register(ProposePostPayRun())
    registry.register(ProposePayPayRun())
