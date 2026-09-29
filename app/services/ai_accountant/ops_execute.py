"""Execute handlers for the confirm-gated period-lock and recurring-rule
proposals (roadmap §5.1), through the same code the Settings and Recurring
pages use, with one audit row (actor_source='ai-assistant')."""
from __future__ import annotations

from datetime import date
from uuid import UUID

from sqlalchemy.orm import Session

from app.models.ai_accountant import AIProposal
from app.services.ai_accountant.time_execute import _audit


def execute_ops_proposal(
    db: Session, proposal: AIProposal, *, actor_user_id: str,
    actor_username: str | None, ip_address: str | None,
) -> tuple[str | None, str]:
    from fastapi import HTTPException

    from app.core.permissions import Perm, role_can
    from app.services.ai_accountant.ops_tools import user_role
    p = dict(proposal.tool_input or {})
    name = proposal.tool_name
    common = dict(actor_user_id=actor_user_id, actor_username=actor_username, ip_address=ip_address)

    if name == "propose_lock_period":
        from app.services.period_service import get_closed_period, set_closed_period
        if not role_can(user_role(db, actor_user_id), Perm.SETTINGS_WRITE):       # checked again at confirm
            raise HTTPException(status_code=403, detail="Only the owner can lock the books.")
        through = date.fromisoformat(p["through"])
        current = get_closed_period(db)
        if current and through <= current:
            raise HTTPException(status_code=409, detail=f"The books are already locked through {current.isoformat()}.")
        set_closed_period(db, through)
        audit_id = _audit(db, proposal, entity_type="closed_period", entity_id=p["through"],
                          detail={"through": p["through"], "was": current.isoformat() if current else None}, **common)
        return None, audit_id

    if name == "propose_create_recurring_rule":
        from app.api.recurring import create_rule
        from app.schemas.recurring import RecurringRuleCreate
        start = date.fromisoformat(p["start_date"])
        rule = create_rule(RecurringRuleCreate(
            name=p["name"], direction=p["direction"], frequency=p["frequency"], amount=int(p["amount"]),
            start_date=start, next_run_date=start, end_date=date.fromisoformat(p["end_date"]) if p.get("end_date") else None,
            entity_id=UUID(p["entity_id"]) if p.get("entity_id") else None, bank_account_code=p.get("bank_account_code"),
            counter_account_code=p["counter_account_code"], auto_post=True, note="Set up from the chat",
        ), db)
        audit_id = _audit(db, proposal, entity_type="recurring_rule", entity_id=str(rule.id),
                          detail={"name": rule.name, "frequency": rule.frequency, "amount": rule.amount,
                                  "start": p["start_date"]}, **common)
        return None, audit_id

    raise ValueError(f"no ops executor for {name}")
