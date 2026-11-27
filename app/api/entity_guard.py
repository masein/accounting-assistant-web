"""Shared check for a request's ``entity_id`` (#14, #29)."""
from __future__ import annotations

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models.entity import Entity


def assert_entity(db: Session, entity_id) -> None:
    """The party must be one of the caller's company's entities: ``db.get`` is
    tenant-scoped, so another company's id (or an unknown one) comes back None.
    Without this a record stored a cross-company reference — and an unknown id
    failed the foreign key at commit (500), telling the two apart."""
    if entity_id is not None and db.get(Entity, entity_id) is None:
        raise HTTPException(status_code=422, detail="Entity not found.")
