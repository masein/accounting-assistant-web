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
    # what it is, as the app's own code says it, when its code is the company's own (docs/PLAN_OWN_CODES.md):
    # the pages read an account's kind from this, never from its digits
    class_code: str | None = None

    model_config = {"from_attributes": True}
