from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models.entity import Entity
from app.schemas.entity import (
    EntityCreate,
    EntityRead,
    EntityResolveRequest,
    EntityResolveResponse,
    EntityResolvedItem,
    EntityUpdate,
)

router = APIRouter(prefix="/entities", tags=["entities"])


def _get_or_create_entity(db: Session, role: str, name: str) -> Entity:
    """Find entity by type and name (case-insensitive), or create. role 'payee' -> type 'employee'."""
    name = name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="Entity name is empty")
    role = role.strip().lower()
    entity_type = role if role in ("client", "bank", "employee", "supplier") else "employee"
    existing = (
        db.execute(
            select(Entity).where(
                Entity.type == entity_type,
                Entity.name.ilike(name),
            )
        )
        .scalars().first()
    )
    if existing:
        return existing
    entity = Entity(type=entity_type, name=name)
    db.add(entity)
    db.flush()
    return entity


@router.get("", response_model=list[EntityRead])
def list_entities(
    type: str | None = Query(None, description="Filter by type: client, bank, employee, supplier"),
    search: str | None = Query(None, description="Filter by name (substring, case-insensitive)"),
    db: Session = Depends(get_db),
) -> list[EntityRead]:
    q = select(Entity).order_by(Entity.type, Entity.name)
    if type:
        q = q.where(Entity.type == type.strip().lower())
    if search and search.strip():
        q = q.where(Entity.name.ilike(f"%{search.strip()}%"))
    entities = db.execute(q).scalars().all()
    return [EntityRead.model_validate(e) for e in entities]


@router.post("/resolve", response_model=EntityResolveResponse)
def resolve_entities(
    payload: EntityResolveRequest,
    db: Session = Depends(get_db),
) -> EntityResolveResponse:
    """
    Resolve mentions to entity ids (get-or-create by type + name).
    Use when you have free-text mentions and need entity_ids for linking or dropdowns.
    """
    resolved: list[EntityResolvedItem] = []
    for m in payload.mentions:
        role = (m.role or "").strip().lower()
        name = (m.name or "").strip()
        if not role or not name:
            continue
        if role not in ("client", "bank", "payee", "supplier"):
            continue
        entity = _get_or_create_entity(db, role, name)
        resolved.append(EntityResolvedItem(role=role, name=name, entity_id=entity.id))
    db.commit()
    return EntityResolveResponse(resolved=resolved)


# client = customer; supplier = vendor/contractor; employee = staff;
# shareholder = equity holder (سهامدار — profit distribution / capital, NOT
# payroll); bank = a bank counterparty (the company's own bank ACCOUNT gets its
# own GL account — see the bank auto-link below).
VALID_ENTITY_TYPES = ("client", "bank", "employee", "supplier", "shareholder")


@router.post("", response_model=EntityRead, status_code=201)
def create_entity(
    payload: EntityCreate,
    db: Session = Depends(get_db),
) -> EntityRead:
    name = payload.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="Entity name is empty")
    typ = payload.type.strip().lower()
    if typ not in VALID_ENTITY_TYPES:
        raise HTTPException(status_code=400, detail="Invalid entity type")
    code = payload.code.strip() if payload.code else None
    if typ == "bank":
        # The company's bank ACCOUNT is a ledger account, not just a person:
        # link (or create) its own GL cash account (سرفصل) so payments can
        # post against it — same as the AI create path.
        from app.services.ai_accountant.entity_create import (
            EntityCreateError, _resolve_bank_account,
        )
        try:
            code, _created = _resolve_bank_account(db, name, code, None)
        except EntityCreateError as e:
            raise HTTPException(status_code=422, detail=str(e)) from e
    entity = Entity(
        type=typ,
        name=name,
        code=code,
    )
    for f in ("legal_name", "address", "email", "phone", "website", "tax_id",
              "contact_person", "payment_terms", "currency", "notes"):
        setattr(entity, f, getattr(payload, f, None))
    db.add(entity)
    db.commit()
    db.refresh(entity)
    return EntityRead.model_validate(entity)


@router.get("/{entity_id}", response_model=EntityRead)
def get_entity(
    entity_id: UUID,
    db: Session = Depends(get_db),
) -> EntityRead:
    entity = db.get(Entity, entity_id)
    if not entity:
        raise HTTPException(status_code=404, detail="Entity not found")
    return EntityRead.model_validate(entity)


@router.get("/{entity_id}/statement.pdf")
def entity_statement_pdf(
    entity_id: UUID,
    date_from: str | None = Query(None),
    date_to: str | None = Query(None),
    db: Session = Depends(get_db),
):
    """Branded account statement for a client: their invoices (debit) and
    payments (credit) with a running balance. Tenant-scoped → 404 cross-company."""
    from datetime import date
    from fastapi.responses import Response
    from app.models.invoice import Invoice
    from app.models.payment import Payment
    from app.services.documents import render_statement_pdf

    entity = db.get(Entity, entity_id)
    if not entity:
        raise HTTPException(status_code=404, detail="Entity not found")

    def _parse(s, default):
        try:
            return date.fromisoformat(s) if s else default
        except ValueError:
            return default

    lo = _parse(date_from, date(date.today().year, 1, 1))
    hi = _parse(date_to, date.today())

    invoices = db.execute(
        select(Invoice).where(Invoice.entity_id == entity_id).order_by(Invoice.issue_date)
    ).scalars().all()
    events: list[dict] = []
    ccy = entity.currency
    for inv in invoices:
        if inv.issue_date and lo <= inv.issue_date <= hi and (inv.status or "") not in ("voided", "canceled"):
            events.append({"date": inv.issue_date, "description": f"Invoice {inv.number}",
                           "debit": int(inv.amount or 0), "credit": 0})
            ccy = ccy or inv.currency
        for pay in db.execute(select(Payment).where(Payment.invoice_id == inv.id)).scalars().all():
            if pay.date and lo <= pay.date <= hi and pay.direction == "in":
                events.append({"date": pay.date, "description": f"Payment — {inv.number}",
                               "debit": 0, "credit": int(pay.amount or 0)})
    events.sort(key=lambda e: e["date"])
    pdf = render_statement_pdf(db, entity, events, (lo, hi), ccy or "")
    return Response(
        content=pdf, media_type="application/pdf",
        headers={"Content-Disposition": f'inline; filename="statement-{entity.name.replace(" ", "_")}.pdf"'},
    )


@router.patch("/{entity_id}", response_model=EntityRead)
def update_entity(
    entity_id: UUID,
    payload: EntityUpdate,
    db: Session = Depends(get_db),
) -> EntityRead:
    entity = db.get(Entity, entity_id)
    if not entity:
        raise HTTPException(status_code=404, detail="Entity not found")
    if payload.type is not None:
        typ = payload.type.strip().lower()
        if typ not in VALID_ENTITY_TYPES:
            raise HTTPException(status_code=400, detail="Invalid entity type")
        entity.type = typ
    if payload.name is not None:
        name = payload.name.strip()
        if not name:
            raise HTTPException(status_code=400, detail="Entity name is empty")
        entity.name = name
    if payload.code is not None:
        entity.code = payload.code.strip() or None
    for f in ("legal_name", "address", "email", "phone", "website", "tax_id",
              "contact_person", "payment_terms", "currency", "notes"):
        val = getattr(payload, f, None)
        if val is not None:
            setattr(entity, f, (val.strip() or None) if isinstance(val, str) else val)
    db.commit()
    db.refresh(entity)
    return EntityRead.model_validate(entity)


@router.delete("/{entity_id}", status_code=204)
def delete_entity(
    entity_id: UUID,
    db: Session = Depends(get_db),
) -> None:
    entity = db.get(Entity, entity_id)
    if not entity:
        raise HTTPException(status_code=404, detail="Entity not found")
    if entity.transaction_links:
        raise HTTPException(
            status_code=400,
            detail="Cannot delete this entity because it is linked to transactions.",
        )
    db.delete(entity)
    db.commit()
