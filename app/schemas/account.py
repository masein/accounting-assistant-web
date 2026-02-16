from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel

from app.models.account import AccountLevel


class AccountRead(BaseModel):
    id: UUID
    code: str
    name: str
    level: AccountLevel
    parent_id: UUID | None
    detail_type: str | None

    model_config = {"from_attributes": True}
