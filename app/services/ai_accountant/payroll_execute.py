"""Execute handlers for the confirm-gated payroll and budget proposals.
Each goes through the route code the Payroll and Budgets pages use and writes
one audit row (actor_source='ai-assistant')."""
from __future__ import annotations

from datetime import date
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.ai_accountant import AIProposal
from app.services.ai_accountant.time_execute import _audit


def execute_payroll_proposal(
    db: Session, proposal: AIProposal, *, actor_user_id: str,
    actor_username: str | None, ip_address: str | None,
) -> tuple[str | None, str]:
    p = dict(proposal.tool_input or {})
    name = proposal.tool_name
    common = dict(actor_user_id=actor_user_id, actor_username=actor_username, ip_address=ip_address)
    # The routes' own refusals (409 already posted, 422 nothing to pay…) reach
    # the confirm card as they are.
    if name == "propose_run_payroll":
        from app.api.payroll import PayRunCreate, PayRunEmployeeInput, create_run
        employees = [PayRunEmployeeInput(entity_id=UUID(i)) for i in p["entity_ids"]] if p.get("entity_ids") else None
        run = create_run(PayRunCreate(period_start=date.fromisoformat(p["period_start"]),
                                      period_end=date.fromisoformat(p["period_end"]),
                                      pay_date=date.fromisoformat(p["pay_date"]), employees=employees), db)
        audit_id = _audit(db, proposal, entity_type="pay_run", entity_id=run["id"],
                          detail={"action": "create", "period": [p["period_start"], p["period_end"]],
                                  "gross": run["total_gross"], "net": run["total_net"],
                                  "employees": [ln["employee_name"] for ln in run["lines"]]}, **common)
        return None, audit_id

    if name in ("propose_post_pay_run", "propose_pay_pay_run"):
        from app.api.payroll import pay_run, post_run
        if name == "propose_post_pay_run":
            run = post_run(UUID(p["run_id"]), db)
            txn_id = run.get("post_transaction_id")
        else:
            run = pay_run(UUID(p["run_id"]), bank_account_code=p.get("bank_account_code"), db=db)
            txn_id = run.get("pay_transaction_id")
        audit_id = _audit(db, proposal, entity_type="pay_run", entity_id=run["id"],
                          detail={"action": "post" if name == "propose_post_pay_run" else "pay",
                                  "period": [run["period_start"], run["period_end"]], "gross": run["total_gross"],
                                  "net": run["total_net"], "transaction_id": txn_id}, **common)
        return txn_id, audit_id

    if name == "propose_set_budget":
        from app.models.budget import BudgetLimit
        ids = []
        for key in p["months"]:
            row = db.execute(select(BudgetLimit).where(BudgetLimit.month == key,
                                                       BudgetLimit.category.ilike(p["category"]))).scalars().first()
            if row is None:
                row = BudgetLimit(month=key, category=p["category"], limit_amount=int(p["amount"]))
                db.add(row)
            else:
                row.limit_amount = int(p["amount"])
            db.flush()
            ids.append(str(row.id))
        audit_id = _audit(db, proposal, entity_type="budget", entity_id=ids[0],
                          detail={"category": p["category"], "amount": p["amount"], "months": p["months"]}, **common)
        return None, audit_id

    raise ValueError(f"no payroll/budget executor for {name}")
