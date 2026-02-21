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


@router.post("", response_model=EntityRead, status_code=201)
def create_entity(
    payload: EntityCreate,
    db: Session = Depends(get_db),
) -> EntityRead:
    code = payload.code.strip() if payload.code else None
    entity = Entity(
        type=payload.type.strip().lower(),
        name=payload.name.strip(),
        code=code,
    )
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
        if typ not in ("client", "bank", "employee", "supplier"):
            raise HTTPException(status_code=400, detail="Invalid entity type")
        entity.type = typ
    if payload.name is not None:
        name = payload.name.strip()
        if not name:
            raise HTTPException(status_code=400, detail="Entity name is empty")
        entity.name = name
    if payload.code is not None:
        entity.code = payload.code.strip() or None
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
