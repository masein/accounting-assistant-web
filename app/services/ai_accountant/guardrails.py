"""Guardrails for what the assistant proposes (roadmap 2026-09 §5.6).

* **Closed periods** — a proposal that would post into a closed period is
  refused when it is made, not after the user presses Confirm. The single
  posting path (``ledger_posting``) still refuses at execution, for a period
  closed in between.
* **Two-person approval** — a company can set a threshold (app_settings
  ``ai_guardrails``, owner): a proposal moving that much or more (in the base
  currency) is not executed by the requester's Confirm. It waits for someone
  else who can approve (``approvals:write`` — owner, CFO, manager), who
  executes or rejects it. An amount that can't be converted to the base
  currency counts as over the threshold.
* **Tool budget** — at most ``tool_calls_per_message`` tool calls and
  ``proposals_per_message`` proposals per chat message (platform
  ``ai_limits``); past them the model is told to answer with what it has.

``review`` runs in the orchestrator right after a proposal tool returns — every
proposal passes through there, whichever tool made it.
"""
from __future__ import annotations

import json
import uuid
from datetime import date, datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.ai_accountant import AIProposal
from app.models.app_setting import AppSetting

SETTINGS_KEY = "ai_guardrails"
APPROVAL_TTL = timedelta(days=7)

# Proposals that change the books, and the payload key holding the posting date
# (None → it posts today).
_POSTING_DATE_KEYS = ("date", "txn_date", "issue_date", "invoice_date", "on")
_NON_POSTING = frozenset({
    "propose_create_entity", "propose_update_entity", "propose_create_project",
    "propose_set_billable_rate", "propose_log_time", "propose_remember_preference",
})


class ProposalRefused(Exception):
    """The proposal breaks a guardrail; the message is for the user."""


# --- settings -------------------------------------------------------------------------------------------------

def _row(db: Session) -> AppSetting | None:
    return db.execute(select(AppSetting).where(AppSetting.key == SETTINGS_KEY)).scalars().first()


def get_settings(db: Session) -> dict:
    out = {"approval_threshold": None}
    row = _row(db)
    if row and row.value:
        try:
            saved = json.loads(row.value) or {}
        except ValueError:
            saved = {}
        t = saved.get("approval_threshold")
        out["approval_threshold"] = int(t) if isinstance(t, (int, float)) and t > 0 else None
    return out


def save_settings(db: Session, *, approval_threshold: int | None) -> dict:
    value = int(approval_threshold) if approval_threshold else None
    if value is not None and value < 0:
        raise ValueError("The threshold can't be negative.")
    row = _row(db)
    data = json.dumps({"approval_threshold": value})
    if row is None:
        db.add(AppSetting(key=SETTINGS_KEY, value=data))
    else:
        row.value = data
    db.flush()
    return get_settings(db)


def approvers(db: Session, *, exclude_user_id: str | None = None) -> list[dict]:
    """The company's active users who can approve."""
    from app.core.permissions import Perm, role_can
    from app.db.tenant import get_current_company, tenant_bypass
    from app.models.user import User
    cid = get_current_company()
    if not cid:
        return []
    with tenant_bypass():
        users = db.execute(select(User).where(User.company_id == uuid.UUID(str(cid)),
                                              User.is_active.is_(True))).scalars().all()
    return [{"id": str(u.id), "username": u.username, "role": u.role} for u in users
            if role_can(u.role, Perm.APPROVALS_WRITE) and str(u.id) != str(exclude_user_id)]


# --- what a proposal does -------------------------------------------------------------------------------------

def posting_date(tool_name: str, payload: dict) -> date | None:
    if tool_name in _NON_POSTING:
        return None
    for key in _POSTING_DATE_KEYS:
        v = payload.get(key)
        if v:
            try:
                return date.fromisoformat(str(v)[:10])
            except ValueError:
                continue
    return date.today()


def _to_base(db: Session, amount: int, currency: str | None, on: date | None) -> int | None:
    from app.services.fx_base import base_currency
    base = base_currency(db)
    ccy = (currency or base).upper()
    if ccy == base.upper():
        return int(amount)
    from app.services.fx_base import rate_to_base
    rate = rate_to_base(db, ccy, on or date.today(), base)
    return int(round(amount * float(rate))) if rate else None


def proposal_amount(db: Session, tool_name: str, payload: dict, result: dict | None = None) -> int | None:
    """What the proposal moves, in the base currency; None when it moves no
    money — or when its currency has no rate (then treated as over any
    threshold)."""
    if tool_name in _NON_POSTING:
        return None
    on = posting_date(tool_name, payload)
    ccy = payload.get("currency")
    if payload.get("lines") and tool_name in ("propose_create_transaction",):
        gross = sum(int(ln.get("debit") or 0) for ln in payload["lines"])
    elif payload.get("lines") and tool_name == "propose_create_invoice":
        gross = sum(int(round(float(ln.get("quantity") or 0) * float(ln.get("unit_price") or 0)
                              * (1 + float(ln.get("tax_rate") or 0) / 100))) for ln in payload["lines"])
    elif payload.get("amount") is not None:
        gross = int(payload["amount"])
    elif payload.get("total_amount") is not None:
        gross = int(payload["total_amount"])
    else:
        preview = (result or {}).get("preview") or {}
        total = preview.get("total", preview.get("amount"))
        if total is None:
            return None
        gross = int(total)
        ccy = ccy or preview.get("currency")
    return _to_base(db, gross, ccy, on)


def needs_approval(db: Session, proposal: AIProposal) -> bool:
    threshold = get_settings(db)["approval_threshold"]
    if not threshold or proposal.tool_name in _NON_POSTING:
        return False
    amount = proposal.amount
    if amount is None:
        amount = proposal_amount(db, proposal.tool_name, dict(proposal.tool_input or {}))
    return amount is None or amount >= threshold


# --- the orchestrator's check ------------------------------------------------------------------------------------

def review(db: Session, result: dict) -> dict:
    """Check a proposal the moment a tool made it. Raises ProposalRefused
    (the proposal is cancelled); otherwise returns what the card should say
    about approval."""
    from app.services.period_service import get_closed_period
    try:
        token = uuid.UUID(str(result.get("confirmation_token")))
    except ValueError:
        return {}
    proposal = db.execute(select(AIProposal).where(AIProposal.confirmation_token == token)).scalars().first()
    if proposal is None:
        return {}
    payload = dict(proposal.tool_input or {})
    when = posting_date(proposal.tool_name, payload)
    locked = get_closed_period(db)
    if when is not None and locked is not None and when <= locked:
        proposal.status = "cancelled"
        db.commit()
        raise ProposalRefused(
            f"The books are closed through {locked.isoformat()}, so this can't be recorded on "
            f"{when.isoformat()}. Ask an admin to reopen the period, or use a later date.")
    proposal.summary = (str(result.get("summary") or "")[:4000]) or proposal.summary
    proposal.amount = proposal_amount(db, proposal.tool_name, payload, result)
    db.commit()
    threshold = get_settings(db)["approval_threshold"]
    if needs_approval(db, proposal):
        return {"needs_approval": True, "approval_threshold": threshold, "amount_in_base": proposal.amount,
                "approval_note": ("Above the company's approval limit: confirming sends it to someone who "
                                  "can approve (owner, CFO or manager) — it is not recorded until they do.")}
    return {}


# --- approval steps -------------------------------------------------------------------------------------------------

def request_approval(db: Session, proposal: AIProposal) -> dict:
    if proposal.approval_status != "requested":
        proposal.approval_status = "requested"
        proposal.approval_requested_at = datetime.now(timezone.utc)
        db.flush()
    others = approvers(db, exclude_user_id=proposal.user_id)
    return {"status": "awaiting_approval", "approvers": len(others),
            "detail": ("Sent for approval — it is recorded once someone who can approve confirms it."
                       if others else
                       "Sent for approval, but no one else in this company can approve it yet: an owner, "
                       "CFO or manager other than you has to confirm it.")}


def approval_expired(proposal: AIProposal) -> bool:
    at = proposal.approval_requested_at
    if at is None:
        return False
    if at.tzinfo is None:
        at = at.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc) - at > APPROVAL_TTL


def waiting(db: Session) -> list[AIProposal]:
    rows = db.execute(select(AIProposal).where(AIProposal.approval_status == "requested",
                                               AIProposal.status == "pending")
                      .order_by(AIProposal.approval_requested_at)).scalars().all()
    return [r for r in rows if not approval_expired(r)]


def describe(db: Session, proposal: AIProposal) -> dict[str, Any]:
    from app.db.tenant import tenant_bypass
    from app.models.user import User
    requester = None
    try:
        with tenant_bypass():
            u = db.get(User, uuid.UUID(str(proposal.user_id)))
        requester = getattr(u, "username", None)
    except (ValueError, TypeError):
        pass
    return {
        "confirmation_token": str(proposal.confirmation_token),
        "tool_name": proposal.tool_name,
        "summary": proposal.summary or proposal.tool_name,
        "amount": proposal.amount,
        "requested_by": requester or proposal.user_id,
        "requested_by_id": proposal.user_id,
        "requested_at": proposal.approval_requested_at.isoformat() if proposal.approval_requested_at else None,
        "user_message": proposal.user_message,
    }
