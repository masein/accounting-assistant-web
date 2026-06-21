"""Execute handlers for the confirm-gated time-billing proposals.

Called from execute_service once the user clicks Confirm. Each creates the
real record(s) and writes one audit row (actor_source='ai-assistant'). A
log-time proposal also creates any bundled new worker/client/project so it
stays a single confirmed action.
"""
from __future__ import annotations

import json
from datetime import date
from uuid import UUID

from sqlalchemy.orm import Session

from app.models.ai_accountant import AIProposal
from app.models.audit_log import AuditLog
from app.models.entity import Entity
from app.models.time_billing import Project, TimeEntry
from app.services import time_billing_service as tbs
from app.services.ai_accountant.entity_create import create_entity


def _audit(db: Session, proposal: AIProposal, *, entity_type: str, entity_id: str,
           detail: dict, actor_user_id: str, actor_username: str | None,
           ip_address: str | None) -> str:
    audit = AuditLog(
        action="create", entity_type=entity_type, entity_id=entity_id,
        user_id=actor_user_id, username=actor_username, ip_address=ip_address,
        actor_source="ai-assistant", session_id=proposal.session_id,
        tool_name=proposal.tool_name, confirmation_token=proposal.confirmation_token,
        user_message=proposal.user_message, detail=json.dumps(detail, default=str),
    )
    db.add(audit)
    db.flush()
    db.refresh(audit)
    return str(audit.id)


def _get_or_create_client(db: Session, p: dict) -> Entity:
    if p.get("client_id"):
        ent = db.get(Entity, UUID(p["client_id"]))
        if ent:
            return ent
    return create_entity(db, name=p["client_name"], type_="client").entity


def execute_time_proposal(
    db: Session, proposal: AIProposal, *, actor_user_id: str,
    actor_username: str | None, ip_address: str | None,
) -> tuple[str | None, str]:
    p = dict(proposal.tool_input or {})
    name = proposal.tool_name

    if name == "propose_log_time":
        client = _get_or_create_client(db, p)
        if p.get("employee_id"):
            worker = db.get(Entity, UUID(p["employee_id"]))
        else:
            worker = create_entity(
                db, name=p["employee_name"], type_=(p.get("employee_create_type") or "employee")
            ).entity
        project = None
        if p.get("project_id"):
            project = db.get(Project, UUID(p["project_id"]))
        elif p.get("project_create") and p.get("project_name"):
            project = Project(client_id=client.id, name=p["project_name"], status="active")
            db.add(project)
            db.flush()
        entry = TimeEntry(
            employee_id=worker.id, client_id=client.id,
            project_id=(project.id if project else None),
            work_date=date.fromisoformat(p["work_date"]), hours=p["hours"],
            description=p.get("description"), billable=bool(p.get("billable", True)),
            status="unbilled", created_by=actor_username,
        )
        db.add(entry)
        db.flush()
        audit_id = _audit(db, proposal, entity_type="time_entry", entity_id=str(entry.id),
                          detail={"hours": p["hours"], "client": client.name,
                                  "worker": worker.name, "project_id": str(project.id) if project else None},
                          actor_user_id=actor_user_id, actor_username=actor_username, ip_address=ip_address)
        return None, audit_id

    if name == "propose_create_project":
        client = _get_or_create_client(db, p)
        project = Project(client_id=client.id, name=p["name"], code=p.get("code"), status="active")
        db.add(project)
        db.flush()
        audit_id = _audit(db, proposal, entity_type="project", entity_id=str(project.id),
                          detail={"name": p["name"], "client": client.name},
                          actor_user_id=actor_user_id, actor_username=actor_username, ip_address=ip_address)
        return None, audit_id

    if name == "propose_set_billable_rate":
        scope = tbs.set_billable_rate(
            db, employee_id=UUID(p["employee_id"]), rate=float(p["rate"]),
            client_id=(UUID(p["client_id"]) if p.get("client_id") else None),
            project_id=(UUID(p["project_id"]) if p.get("project_id") else None),
            currency=p.get("currency"),
        )
        audit_id = _audit(db, proposal, entity_type="billing_rate", entity_id=str(p["employee_id"]),
                          detail={"rate": p["rate"], "scope": scope},
                          actor_user_id=actor_user_id, actor_username=actor_username, ip_address=ip_address)
        return None, audit_id

    if name == "propose_create_invoice_from_time":
        inv, preview = tbs.create_invoice_from_time(
            db, client_id=UUID(p["client_id"]),
            project_id=(UUID(p["project_id"]) if p.get("project_id") else None),
            date_from=(date.fromisoformat(p["date_from"]) if p.get("date_from") else None),
            date_to=(date.fromisoformat(p["date_to"]) if p.get("date_to") else None),
            invoice_date=(date.fromisoformat(p["invoice_date"]) if p.get("invoice_date") else None),
            due_date=(date.fromisoformat(p["due_date"]) if p.get("due_date") else None),
            created_by=actor_username,
        )
        audit_id = _audit(db, proposal, entity_type="invoice", entity_id=str(inv.id),
                          detail={"number": inv.number, "amount": int(inv.amount or 0),
                                  "currency": inv.currency, "entry_count": preview["entry_count"],
                                  "pdf_url": f"/time/invoice/{inv.id}/pdf"},
                          actor_user_id=actor_user_id, actor_username=actor_username, ip_address=ip_address)
        return None, audit_id

    raise ValueError(f"Unknown time proposal tool: {name}")
