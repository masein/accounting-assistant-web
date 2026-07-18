"""Execute confirmed shareholder-equity proposals — posts the balanced GL
entries via ``equity_service`` and writes an ai-assistant audit row.

Single-transaction ops (contribution, capital increase, current account) get a
``transaction``-type audit so the standard undo/reverse path can compensate
them. A dividend declaration posts one voucher per shareholder; its audit
references the first voucher and records the full set in ``detail``.
"""
from __future__ import annotations

import json
import uuid
from datetime import date as _date

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models.ai_accountant import AIProposal
from app.models.audit_log import AuditLog
from app.services import equity_service as eq


def _parse_date(raw) -> _date:
    try:
        return _date.fromisoformat(str(raw))
    except (ValueError, TypeError):
        return _date.today()


def execute_equity_proposal(
    db: Session,
    proposal: AIProposal,
    *,
    actor_user_id: str,
    actor_username: str | None,
    ip_address: str | None,
) -> tuple[str | None, str]:
    """Dispatch a confirmed equity proposal to the posting service. Returns
    (transaction_id, audit_log_id)."""
    p = dict(proposal.tool_input)
    name = proposal.tool_name
    txn_date = _parse_date(p.get("date"))

    try:
        if name == "propose_shareholder_contribution":
            res = eq.contribution(
                db, entity_id=uuid.UUID(p["entity_id"]), amount=int(p["amount"]),
                txn_date=txn_date, to_capital=bool(p.get("to_capital", True)),
            )
        elif name == "propose_capital_increase":
            res = eq.capital_increase(
                db, amount=int(p["amount"]), txn_date=txn_date,
                source=p.get("source", "retained_earnings"),
            )
        elif name == "propose_declare_dividend":
            allocations = None
            if p.get("allocations"):
                allocations = [(uuid.UUID(a["entity_id"]), int(a["amount"])) for a in p["allocations"]]
            res = eq.declare_dividend(
                db, total_amount=int(p["total_amount"]), txn_date=txn_date, allocations=allocations,
            )
        elif name == "propose_shareholder_current_account":
            res = eq.shareholder_current_account(
                db, entity_id=uuid.UUID(p["entity_id"]), amount=int(p["amount"]),
                txn_date=txn_date, direction=p.get("direction", "out"),
            )
        else:  # pragma: no cover - dispatch guarded by caller
            raise HTTPException(status_code=400, detail=f"Unknown equity tool {name!r}")
    except eq.EquityError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e

    first_txn = res.transaction_ids[0] if res.transaction_ids else None
    audit = AuditLog(
        action="create",
        entity_type="transaction" if first_txn else "equity_event",
        entity_id=first_txn,
        user_id=actor_user_id,
        username=actor_username,
        ip_address=ip_address,
        actor_source="ai-assistant",
        session_id=proposal.session_id,
        tool_name=proposal.tool_name,
        confirmation_token=proposal.confirmation_token,
        user_message=proposal.user_message,
        detail=json.dumps(
            {
                "equity_event": name,
                "transaction_ids": res.transaction_ids,
                "event_ids": res.event_ids,
                "summary_lines": res.summary_lines,
                "allocations": res.allocations,
                "registered_capital": res.registered_capital,
                "tool_input": p,
            },
            default=str,
        ),
    )
    db.add(audit)
    db.flush()
    db.refresh(audit)
    return first_txn, str(audit.id)
