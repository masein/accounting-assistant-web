"""The date of entries already in the books, changed from the chat (scenario BB4, 2026-10-25).

The owner's nine trips paid by Amin were told as a list with no dates, so they were recorded on the day they
were told, and every weekly review counted $4,555.06 of travel "this week". The web could edit each entry's
date one by one; the chat couldn't. ``propose_change_date`` takes the entries and the day each one really was,
as one card; confirmed, each entry keeps everything but its date. The closed-period lock holds as for any edit.
Undo puts the old dates back.
"""
from __future__ import annotations

import json
import uuid
from datetime import date as _date
from typing import Any

from pydantic import BaseModel, Field
from sqlalchemy import select

from .base import BaseTool, ToolContext, ToolError
from .time_tools import _register


class EntryDate(BaseModel):
    transaction_id: str = Field(..., description="The entry's transaction_id, from query_ledger.")
    date: _date = Field(..., description="The day it really was (YYYY-MM-DD; a Jalali 1405-06-15 is converted).")


class ProposeChangeDateInput(BaseModel):
    entries: list[EntryDate] = Field(..., min_length=1, max_length=60,
                                     description="Each entry and its real date; several entries, one card.")


def _persian(s: str) -> bool:
    import re
    return bool(re.search(r"[؀-ۿ]", s or ""))


def _day(d: _date, persian: bool, jalali: bool) -> str:
    if persian and jalali:
        from app.utils.jalali import format_jalali_long
        return format_jalali_long(d)
    return d.isoformat()


class ProposeChangeDate(BaseTool):
    name = "propose_change_date"
    category = "proposal"
    description = (
        "Give entries already in the books the date they really had: a list recorded on the day it was told, a "
        "payment dated today that was last month. Each entry keeps its amounts, accounts and people; only its "
        "date moves, so reports count it in the right week, month and year. Find the entries with query_ledger "
        "(its rows carry transaction_id). Returns one confirm card for all of them."
    )
    InputSchema = ProposeChangeDateInput

    async def run(self, ctx: ToolContext, args: ProposeChangeDateInput) -> dict[str, Any]:
        from app.models.transaction import Transaction
        from app.services.calendar_periods import company_calendar
        from app.services.ledger_posting import assert_transaction_mutable
        today = _date.today()
        persian = _persian(ctx.user_message or "")
        jalali = company_calendar(ctx.db) == "jalali"
        rows, seen = [], set()
        for e in args.entries:
            try:
                tid = uuid.UUID(str(e.transaction_id).strip())
            except ValueError:
                raise ToolError(f"{e.transaction_id!r} isn't an entry id: take transaction_id from query_ledger.",
                                code="not_found") from None
            if tid in seen:
                continue
            seen.add(tid)
            t = ctx.db.get(Transaction, tid)
            if t is None or t.deleted_at is not None:
                raise ToolError(f"No entry {tid} in these books: take transaction_id from query_ledger.",
                                code="not_found")
            if e.date > today:
                raise ToolError(f"{e.date.isoformat()} is after today: an entry's real date has passed.",
                                code="future_date")
            if e.date == t.date:
                continue
            try:
                assert_transaction_mutable(ctx.db, t, new_date=e.date)
            except Exception as exc:  # the closed-period lock, said as it is
                detail = getattr(exc, "detail", None) or str(exc)
                raise ToolError(f"{t.description or tid}: {detail}", code="period_locked") from exc
            rows.append({"transaction_id": str(t.id), "description": t.description or t.reference or "",
                         "old_date": t.date.isoformat(), "new_date": e.date.isoformat(),
                         "currency": t.currency})
        if not rows:
            raise ToolError("Those entries already have those dates: nothing to change.", code="nothing_to_change")
        days = sorted({r["new_date"] for r in rows})
        one_day = len(days) == 1
        when = _day(_date.fromisoformat(days[0]), persian, jalali) if one_day else None
        if persian:
            description = (f"تاریخ {len(rows)} سند" + (f" ← {when}" if one_day else ": هر کدام روز خودش")
                           if len(rows) > 1 else f"{rows[0]['description']}: تاریخ ← {_day(_date.fromisoformat(rows[0]['new_date']), True, jalali)}")
        else:
            description = (f"New date for {len(rows)} entries" + (f": {when}" if one_day else ", each its own")
                           if len(rows) > 1 else f"{rows[0]['description']}: new date {rows[0]['new_date']}")
        payload = {"entries": rows, "description": description}
        token = _register(ctx, self.name, payload)
        summary = "; ".join(f"{r['description'] or r['transaction_id'][:8]}: {r['old_date']} → {r['new_date']}" for r in rows)
        return {"confirmation_token": str(token), "status": "pending", "preview": payload,
                "summary": f"Only the dates change — {summary}."}


def execute_change_date(db, proposal, *, actor_user_id: str, actor_username: str | None,
                        ip_address: str | None) -> tuple[str | None, str]:
    """Each entry takes its new date, through the same lock and audit as an edit on the web."""
    from app.models.transaction import Transaction
    from app.services.ai_accountant.time_execute import _audit
    from app.services.ledger_posting import assert_transaction_mutable
    from app.api.transactions import _log_transaction_audit
    p = dict(proposal.tool_input or {})
    done = []
    for r in p.get("entries") or []:
        t = db.get(Transaction, uuid.UUID(r["transaction_id"]))
        if t is None or t.deleted_at is not None:
            continue
        new = _date.fromisoformat(r["new_date"])
        assert_transaction_mutable(db, t, new_date=new)
        done.append({"transaction_id": str(t.id), "old_date": t.date.isoformat(), "new_date": new.isoformat()})
        t.date = new
        db.flush()
        db.expire(t)
        _log_transaction_audit(db, "update", t)
    if not done:
        raise ValueError("None of these entries is in the books any more.")
    audit_id = _audit(db, proposal, entity_type="transaction_dates", entity_id=done[0]["transaction_id"],
                      detail={"entries": done}, actor_user_id=actor_user_id, actor_username=actor_username,
                      ip_address=ip_address)
    _invalidate(db)
    return done[0]["transaction_id"], audit_id


def undo_change_date(db, audit, *, actor_user_id: str, actor_username: str | None, ip_address: str | None,
                     tool_name: str):
    """The old dates back, where the entries can still be changed."""
    from app.models.audit_log import AuditLog
    from app.models.transaction import Transaction
    from app.services.ai_accountant.execute_service import UndoNotApplicable, UndoResult
    from app.services.ledger_posting import assert_transaction_mutable
    from app.api.transactions import _log_transaction_audit
    entries = (json.loads(audit.detail or "{}") or {}).get("entries") or []
    if db.execute(select(AuditLog.id).where(AuditLog.action == "undo", AuditLog.detail.contains(str(audit.id)))).first():
        raise UndoNotApplicable("These dates have already been put back.")
    back = []
    for r in entries:
        t = db.get(Transaction, uuid.UUID(r["transaction_id"]))
        if t is None or t.deleted_at is not None:
            continue
        old = _date.fromisoformat(r["old_date"])
        assert_transaction_mutable(db, t, new_date=old)
        t.date = old
        db.flush()
        db.expire(t)
        _log_transaction_audit(db, "update", t)
        back.append(str(t.id))
    if not back:
        raise UndoNotApplicable("None of these entries is in the books any more.")
    db.add(AuditLog(action="undo", entity_type="transaction_dates", entity_id=back[0], user_id=actor_user_id,
                    username=actor_username, ip_address=ip_address, actor_source="ai-assistant",
                    session_id=audit.session_id, tool_name=tool_name, confirmation_token=audit.confirmation_token,
                    user_message=audit.user_message, detail=json.dumps({"transaction_dates": str(audit.id)})))
    db.commit()
    _invalidate(db)
    return UndoResult(original_transaction_id=back[0], reversal_transaction_id=None, audit_log_id=str(audit.id),
                      mode="restored")


def _invalidate(db) -> None:
    """Reports and the dashboard read the new dates at once."""
    try:
        from app.api.reports import invalidate_dashboard_cache
        invalidate_dashboard_cache()
        from app.services.insight_service import invalidate_insights_cache
        invalidate_insights_cache()
    except Exception:  # noqa: BLE001 — a cache is never worth an error
        pass


def register_date_tools(registry) -> None:
    registry.register(ProposeChangeDate())
