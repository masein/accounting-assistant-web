from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel, Field, model_validator


class EntityBase(BaseModel):
    type: str = Field(..., description="client, bank, employee, supplier")
    name: str = Field(..., min_length=1)
    code: str | None = None


class EntityCreate(EntityBase):
    pass


class EntityUpdate(BaseModel):
    type: str | None = Field(None, description="client, bank, employee, supplier")
    name: str | None = Field(None, min_length=1)
    code: str | None = None


class EntityRead(EntityBase):
    id: UUID

    model_config = {"from_attributes": True}


class EntityLink(BaseModel):
    """
    Link a transaction to an entity. Provide either entity_id (existing) or name (get-or-create).
    role is used as entity type when creating: client, bank, payee, supplier.
    """
    role: str = Field(..., description="client, bank, payee, supplier")
    entity_id: UUID | None = None
    name: str | None = None

    @model_validator(mode="after")
    def require_id_or_name(self):
        if not self.entity_id and not (self.name and self.name.strip()):
            raise ValueError("Provide either entity_id or name")
        return self


class EntityMention(BaseModel):
    """Used when AI returns mentioned parties (e.g. client Innotech, bank Melli)."""
    role: str = Field(..., description="client, bank, payee, supplier")
    name: str = Field(..., min_length=1)


class EntityResolveMention(BaseModel):
    """One mention to resolve to an entity (get-or-create by type + name)."""
    role: str = Field(..., description="client, bank, payee, supplier")
    name: str = Field(..., min_length=1)


class EntityResolvedItem(BaseModel):
    """Resolved mention with entity id (for dropdowns / linking)."""
    role: str
    name: str
    entity_id: UUID


class EntityResolveRequest(BaseModel):
    """Resolve a list of mentions to entity ids (DB lookup or create)."""
    mentions: list[EntityResolveMention] = Field(..., min_length=0)


class EntityResolveResponse(BaseModel):
    """Result of resolving mentions: same order, with entity_id set."""
    resolved: list[EntityResolvedItem] = Field(default_factory=list)
