"""Time-based billing AI tools: read (unbilled time, summary) + proposal
(log time, create project, set billable rate, invoice from time).

Each proposal is ONE registered proposal → ONE confirm card. A log-time/rate
proposal can bundle the creation of a missing worker / client / project, so the
"new party + action" stays a single card (no orchestrator merge needed here).
Confirm-gated + audited via the shared execute path.
"""
from __future__ import annotations

import uuid
from datetime import date as _date
from typing import Any

from pydantic import BaseModel, Field
from sqlalchemy import select

from app.models.ai_accountant import AIProposal
from app.models.entity import Entity
from app.models.time_billing import Project

from .base import BaseTool, ToolContext, ToolError
from .date_resolver import resolve_entry_date
from .entity_create import classify_entity_type

# ---------------------------------------------------------------------------
# Name resolution helpers
# ---------------------------------------------------------------------------


def _find_entity(ctx: ToolContext, name: str, types: tuple[str, ...]) -> Entity | None:
    if not name:
        return None
    rows = ctx.db.execute(
        select(Entity).where(Entity.type.in_(list(types)), Entity.name.ilike(name.strip()))
    ).scalars().all()
    if rows:
        return rows[0]
    rows = ctx.db.execute(
        select(Entity).where(Entity.type.in_(list(types)), Entity.name.ilike(f"%{name.strip()}%"))
    ).scalars().all()
    return rows[0] if len(rows) == 1 else None


def _find_project(ctx: ToolContext, client_id, name: str) -> Project | None:
    if not name or client_id is None:
        return None
    rows = ctx.db.execute(
        select(Project).where(Project.client_id == client_id, Project.name.ilike(name.strip()))
    ).scalars().all()
    return rows[0] if rows else None


def _register(ctx: ToolContext, tool_name: str, payload: dict) -> uuid.UUID:
    token = uuid.uuid4()
    ctx.db.add(AIProposal(
        confirmation_token=token, user_id=ctx.user_id, session_id=ctx.chat_session_id,
        tool_name=tool_name, tool_input=payload, user_message=ctx.user_message, status="pending",
    ))
    ctx.db.commit()
    return token


# ---------------------------------------------------------------------------
# READ — list_unbilled_time
# ---------------------------------------------------------------------------


class ListUnbilledTimeInput(BaseModel):
    client: str | None = Field(None, description="Client name to filter by (optional).")
    project: str | None = Field(None, description="Project name to filter by (optional).")


class ListUnbilledTime(BaseTool):
    name = "list_unbilled_time"
    category = "read"
    description = (
        "List UNBILLED billable time, grouped by client → project → employee, with "
        "hours and value. Use to answer 'what unbilled time do we have for Acme?' or "
        "before invoicing so the user sees exactly what would be billed."
    )
    InputSchema = ListUnbilledTimeInput

    async def run(self, ctx: ToolContext, args: ListUnbilledTimeInput) -> dict[str, Any]:
        from app.services import time_billing_service as tbs
        client = _find_entity(ctx, args.client, ("client",)) if args.client else None
        project = _find_project(ctx, client.id if client else None, args.project) if args.project else None
        entries = tbs._unbilled_query(
            ctx.db, client_id=(client.id if client else None),
            project_id=(project.id if project else None),
        )
        clients: dict[str, dict] = {}
        for e in entries:
            rate = tbs.resolve_billable_rate(ctx.db, e.employee_id, e.client_id, e.project_id)
            value = tbs._round(float(e.hours or 0) * rate["rate"]) if rate else 0
            cobj = ctx.db.get(Entity, e.client_id)
            ck = str(e.client_id)
            c = clients.setdefault(ck, {"client": cobj.name if cobj else "—", "hours": 0.0, "value": 0})
            c["hours"] += float(e.hours or 0)
            c["value"] += value
        for c in clients.values():
            c["hours"] = round(c["hours"], 2)
        return {"unbilled": list(clients.values()), "count": len(entries)}


# ---------------------------------------------------------------------------
# READ — get_time_summary
# ---------------------------------------------------------------------------


class GetTimeSummaryInput(BaseModel):
    client: str | None = Field(None, description="Optional client name to scope the totals.")


class GetTimeSummary(BaseTool):
    name = "get_time_summary"
    category = "read"
    description = (
        "Totals of unbilled time (hours + value) for a client, or overall. Answers "
        "'how many unbilled hours do we have for Acme?'."
    )
    InputSchema = GetTimeSummaryInput

    async def run(self, ctx: ToolContext, args: GetTimeSummaryInput) -> dict[str, Any]:
        from app.services import time_billing_service as tbs
        client = _find_entity(ctx, args.client, ("client",)) if args.client else None
        entries = tbs._unbilled_query(ctx.db, client_id=(client.id if client else None))
        hours = round(sum(float(e.hours or 0) for e in entries), 2)
        value = 0
        for e in entries:
            r = tbs.resolve_billable_rate(ctx.db, e.employee_id, e.client_id, e.project_id)
            if r:
                value += tbs._round(float(e.hours or 0) * r["rate"])
        return {
            "client": (client.name if client else None), "unbilled_hours": hours,
            "unbilled_value": value, "currency": tbs.company_currency(ctx.db),
            "entry_count": len(entries),
        }


# ---------------------------------------------------------------------------
# PROPOSAL — propose_log_time
# ---------------------------------------------------------------------------


class ProposeLogTimeInput(BaseModel):
    employee: str = Field(..., description="Worker name (employee or contractor/supplier).")
    client: str = Field(..., description="Client the work was for.")
    hours: float = Field(..., gt=0)
    project: str | None = Field(None, description="Project/matter name (optional).")
    date: _date | None = Field(None, description="Work date (ISO). Defaults to today; relative dates resolved.")
    description: str | None = None
    billable: bool = True


class ProposeLogTime(BaseTool):
    name = "propose_log_time"
    category = "proposal"
    description = (
        "Register a pending TIME ENTRY for the user to confirm. Resolves the worker, "
        "client and (optional) project by name; if any are new and clearly named, the "
        "SAME proposal creates them on Confirm (one card, not several). Resolves the "
        "billable rate and shows it. Relative/worded dates are handled."
    )
    InputSchema = ProposeLogTimeInput

    async def run(self, ctx: ToolContext, args: ProposeLogTimeInput) -> dict[str, Any]:
        from app.services import time_billing_service as tbs

        work_date = resolve_entry_date(ctx.user_message, args.date or _date.today(), has_attachment=False, scheduled=False)
        worker = _find_entity(ctx, args.employee, ("employee", "supplier"))
        client = _find_entity(ctx, args.client, ("client",))
        project = _find_project(ctx, client.id if client else None, args.project) if (client and args.project) else None

        create_notes: list[str] = []
        worker_type = None
        if worker is None:
            worker_type = classify_entity_type("employee", text=ctx.user_message or "")
            create_notes.append(f"{worker_type} {args.employee}")
        if client is None:
            create_notes.append(f"client {args.client}")
        if args.project and project is None:
            create_notes.append(f"project {args.project}")

        rate = None
        if worker is not None and client is not None:
            rate = tbs.resolve_billable_rate(ctx.db, worker.id, client.id, project.id if project else None)

        payload = {
            "employee_id": str(worker.id) if worker else None, "employee_name": args.employee,
            "employee_create_type": worker_type,
            "client_id": str(client.id) if client else None, "client_name": args.client,
            "project_id": str(project.id) if project else None,
            "project_name": args.project, "project_create": bool(args.project and project is None),
            "work_date": work_date.isoformat(), "hours": float(args.hours),
            "description": args.description, "billable": bool(args.billable),
        }
        token = _register(ctx, self.name, payload)

        rate_txt = (f" @ {rate['rate']:g} {rate['currency']}/h ({rate['source']})" if rate
                    else " (rate to be set)")
        where = args.client + (f" / {args.project}" if args.project else "")
        summary = (f"Log {args.hours:g}h for {args.employee} on {where} — {work_date.isoformat()}"
                   f"{rate_txt}")
        if create_notes:
            summary += "\n  Will create: " + ", ".join(create_notes)
        new_entities = []
        if worker is None:
            new_entities.append({"name": args.employee, "type": worker_type, "role": worker_type})
        if client is None:
            new_entities.append({"name": args.client, "type": "client", "role": "client"})
        return {
            "confirmation_token": str(token), "status": "pending", "summary": summary,
            "tool_name": self.name, "preview": payload, "new_entities": new_entities,
            "new_project": (args.project if (args.project and project is None) else None),
        }


# ---------------------------------------------------------------------------
# PROPOSAL — propose_create_project
# ---------------------------------------------------------------------------


class ProposeCreateProjectInput(BaseModel):
    client: str = Field(..., description="The client the project belongs to.")
    name: str = Field(..., min_length=1)
    code: str | None = None


class ProposeCreateProject(BaseTool):
    name = "propose_create_project"
    category = "proposal"
    description = "Register a pending PROJECT/matter for a client, for the user to confirm."
    InputSchema = ProposeCreateProjectInput

    async def run(self, ctx: ToolContext, args: ProposeCreateProjectInput) -> dict[str, Any]:
        client = _find_entity(ctx, args.client, ("client",))
        payload = {
            "client_id": str(client.id) if client else None, "client_name": args.client,
            "name": args.name.strip(), "code": (args.code or None),
        }
        token = _register(ctx, self.name, payload)
        summary = f"Create project '{args.name}' for {args.client}"
        if client is None:
            summary += f"\n  Will create client {args.client}"
        return {
            "confirmation_token": str(token), "status": "pending", "summary": summary,
            "tool_name": self.name, "preview": payload,
            "new_entities": ([{"name": args.client, "type": "client", "role": "client"}] if client is None else []),
        }


# ---------------------------------------------------------------------------
# PROPOSAL — propose_set_billable_rate
# ---------------------------------------------------------------------------


class ProposeSetBillableRateInput(BaseModel):
    employee: str = Field(..., description="Worker name (employee or contractor).")
    rate: float = Field(..., ge=0, description="Billable rate per hour in major currency units.")
    client: str | None = Field(None, description="Scope to this client (client override).")
    project: str | None = Field(None, description="Scope to this project (project override).")
    currency: str | None = None


class ProposeSetBillableRate(BaseTool):
    name = "propose_set_billable_rate"
    category = "proposal"
    description = (
        "Register a pending BILLABLE-RATE change for the user to confirm. Scope: a "
        "project override, a client override, or (no scope) the worker's default. "
        "Precedence project > client > default."
    )
    InputSchema = ProposeSetBillableRateInput

    async def run(self, ctx: ToolContext, args: ProposeSetBillableRateInput) -> dict[str, Any]:
        worker = _find_entity(ctx, args.employee, ("employee", "supplier"))
        if worker is None:
            raise ToolError(f"I couldn't find a worker named {args.employee!r}. Add them first.",
                            code="worker_not_found")
        client = _find_entity(ctx, args.client, ("client",)) if args.client else None
        project = _find_project(ctx, client.id if client else None, args.project) if (client and args.project) else None
        payload = {
            "employee_id": str(worker.id), "rate": float(args.rate),
            "client_id": str(client.id) if client else None,
            "project_id": str(project.id) if project else None,
            "currency": (args.currency or None),
        }
        token = _register(ctx, self.name, payload)
        scope = ("project " + args.project if project else
                 "client " + args.client if client else "default")
        summary = f"Set {args.employee}'s billable rate to {args.rate:g}/h ({scope})"
        return {"confirmation_token": str(token), "status": "pending", "summary": summary,
                "tool_name": self.name, "preview": payload}


# ---------------------------------------------------------------------------
# PROPOSAL — propose_create_invoice_from_time
# ---------------------------------------------------------------------------


class ProposeInvoiceFromTimeInput(BaseModel):
    client: str = Field(..., description="Client to invoice.")
    project: str | None = Field(None, description="Limit to one project (optional).")
    date_from: _date | None = Field(None, description="Only include time on/after this date.")
    date_to: _date | None = Field(None, description="Only include time on/before this date.")
    invoice_date: _date | None = None
    due_date: _date | None = None


class ProposeCreateInvoiceFromTime(BaseTool):
    name = "propose_create_invoice_from_time"
    category = "proposal"
    description = (
        "Aggregate a client's UNBILLED time into a draft sales invoice (grouped by "
        "project, then employee, at each worker's billable rate) and register it for "
        "the user to confirm. The card shows the exact entries/hours/value, subtotal, "
        "VAT and total. On Confirm it posts to AR (DR trade debtors / CR sales / CR "
        "VAT) and marks the entries invoiced. Never includes already-invoiced time."
    )
    InputSchema = ProposeInvoiceFromTimeInput

    async def run(self, ctx: ToolContext, args: ProposeInvoiceFromTimeInput) -> dict[str, Any]:
        from app.services import time_billing_service as tbs
        client = _find_entity(ctx, args.client, ("client",))
        if client is None:
            raise ToolError(f"I couldn't find a client named {args.client!r}.", code="client_not_found")
        project = _find_project(ctx, client.id, args.project) if args.project else None
        try:
            preview = tbs.build_preview(
                ctx.db, client_id=client.id, project_id=(project.id if project else None),
                date_from=args.date_from, date_to=args.date_to,
                invoice_date=args.invoice_date, due_date=args.due_date,
            )
        except tbs.TimeBillingError as e:
            raise ToolError(str(e), code="time_billing_error") from e
        if preview["empty"]:
            return {
                "status": "no_op",
                "summary": f"No unbilled time to invoice for {args.client}.",
                "tool_name": self.name,
            }

        payload = {
            "client_id": str(client.id), "project_id": (str(project.id) if project else None),
            "date_from": (args.date_from.isoformat() if args.date_from else None),
            "date_to": (args.date_to.isoformat() if args.date_to else None),
            "invoice_date": preview["invoice_date"], "due_date": preview["due_date"],
        }
        token = _register(ctx, self.name, payload)
        cur = preview["currency"]
        lines = []
        for b in preview["groups"]:
            lines.append(f"  {b['project_name']}:")
            for ln in b["lines"]:
                lines.append(f"    {ln['employee_name']} — {ln['hours']:g}h × {ln['rate']:g} = {ln['amount']:,} {cur}")
        summary = (
            f"Draft invoice for {preview['client_name']} "
            f"({preview['period_from']} → {preview['period_to']}):\n"
            + "\n".join(lines)
            + f"\n  Subtotal {preview['subtotal']:,} + VAT {preview['tax']:,} = "
              f"Total {preview['total']:,} {cur}"
            + f"\n  Includes {preview['entry_count']} time entries ({preview['total_hours']:g} hrs)."
        )
        return {"confirmation_token": str(token), "status": "pending", "summary": summary,
                "tool_name": self.name, "preview": payload, "invoice_preview": preview}


def register_time_tools(registry) -> None:
    registry.register(ListUnbilledTime())
    registry.register(GetTimeSummary())
    registry.register(ProposeLogTime())
    registry.register(ProposeCreateProject())
    registry.register(ProposeSetBillableRate())
    registry.register(ProposeCreateInvoiceFromTime())
