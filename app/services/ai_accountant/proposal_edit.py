"""Change a draft voucher from its card (roadmap ROADMAP_ANDROID_CHAT P0.4).

The phone's Edit changes the date, the description or, for a two-line
voucher, the amount. The draft is proposed again with the changes through
the same tool and the same checks as the accountant's own proposal (the
amount guard, the future-date and closed-period guards, the approval
threshold), and the old card is withdrawn: one token, one voucher.
"""
from __future__ import annotations

import json
import uuid
from typing import Any

from sqlalchemy.orm import Session

from app.models.ai_accountant import AIProposal
from app.services.ai_accountant.execute_service import (
    PermissionDenied,
    ProposalCancelled,
    _resolve_proposal,
)

EDITABLE = {"propose_create_transaction"}


class EditRefused(Exception):
    """The change can't be made here: why, in a sentence for the user."""


async def edit_proposal(db: Session, *, token: str, user, date: str | None = None,
                        description: str | None = None, amount: int | None = None) -> dict[str, Any]:
    """The new draft (as a proposal tool returns it, with the approval note);
    the old one is cancelled. Raises EditRefused, PermissionDenied,
    ProposalCancelled or ProposalNotFound."""
    from app.services.ai_accountant import guardrails
    from app.services.ai_accountant.base import ToolContext, ToolError
    from app.services.ai_accountant.proposal_tools import ProposeCreateTransaction, ProposeCreateTransactionInput
    from app.services.audit_service import log_audit_event
    from app.services.journal_import import parse_date

    old = _resolve_proposal(db, token)
    if str(old.user_id) != str(user.user_id):
        raise PermissionDenied("This proposal belongs to a different user.")
    if old.status != "pending":
        raise ProposalCancelled("This proposal was already confirmed or cancelled.")
    if old.tool_name not in EDITABLE:
        raise EditRefused("This card can't be changed here. Tell the accountant what to change.")

    payload = json.loads(json.dumps(old.tool_input or {}))
    changes: dict[str, Any] = {}
    if date is not None and date.strip():
        on = parse_date(date)
        if on is None:
            raise EditRefused("That date couldn't be read. Write it like 1405/07/18 or 2026-10-10.")
        payload["date"] = on.isoformat()
        changes["date"] = on.isoformat()
    if description is not None:
        text = description.strip()
        if not text:
            raise EditRefused("The description can't be empty.")
        payload["description"] = text[:1024]
        changes["description"] = payload["description"]
    lines = payload.get("lines") or []
    if amount is not None:
        debits = [ln for ln in lines if int(ln.get("debit") or 0) > 0]
        credits = [ln for ln in lines if int(ln.get("credit") or 0) > 0]
        if len(debits) != 1 or len(credits) != 1:
            raise EditRefused("This voucher has several lines. Tell the accountant the new amounts.")
        debits[0]["debit"] = int(amount)
        credits[0]["credit"] = int(amount)
        changes["amount"] = int(amount)
    if not changes:
        raise EditRefused("Nothing was changed.")

    args = ProposeCreateTransactionInput.model_validate(payload)
    total = sum(ln.debit for ln in args.lines)
    ctx = ToolContext(
        db=db, user_id=str(user.user_id), username=user.username, is_admin=bool(getattr(user, "is_admin", False)),
        chat_session_id=str(old.session_id) if old.session_id else None,
        # an explicit date, so the entry-date resolver keeps it; no payment or
        # receipt words, so the direction guards leave the lines as they are
        user_message=f"Changed on the card: {args.date.isoformat()}",
        source_amounts=[total],
    )
    try:
        result = await ProposeCreateTransaction().run(ctx, args)
        result.update(guardrails.review(db, result))
    except ToolError as e:
        raise EditRefused(str(e)) from e
    except guardrails.ProposalRefused as e:
        raise EditRefused(str(e)) from e

    new = db.query(AIProposal).filter(AIProposal.confirmation_token == uuid.UUID(result["confirmation_token"])).one()
    new.user_message = old.user_message                     # the audit keeps what was first asked
    old.status = "cancelled"
    if old.approval_status == "requested":
        old.approval_status = "withdrawn"
    log_audit_event(db, action="update", entity_type="ai_proposal", entity_id=str(old.id),
                    detail=json.dumps({"tool": old.tool_name, "replaced_by": result["confirmation_token"],
                                       "changes": changes}, ensure_ascii=False),
                    user_id=str(user.user_id), username=user.username)
    db.commit()
    return result
