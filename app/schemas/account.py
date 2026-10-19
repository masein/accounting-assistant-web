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
    is_active: bool = True
    source_codes: str | None = None          # its codes in the books it came from (scenario AH7)

    model_config = {"from_attributes": True}
