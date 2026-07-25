from __future__ import annotations

from datetime import date, datetime
from typing import Any, Optional
from uuid import UUID

from pydantic import BaseModel, Field


class MigrationPreviewResponse(BaseModel):
    token: str
    batch_id: UUID
    summary: dict[str, Any]
    default_opening_date: date
    already_applied: bool = False


class MigrationConfirmRequest(BaseModel):
    token: str = Field(..., max_length=80)
    opening_date: Optional[date] = None


class MigrationConfirmResponse(BaseModel):
    batch_id: UUID
    status: str
    idempotent: bool = False
    result: dict[str, Any]


class MigrationBatchRead(BaseModel):
    id: UUID
    token: str
    status: str
    summary: dict[str, Any]
    result: Optional[dict[str, Any]] = None
    opening_date: Optional[date] = None
    applied_at: Optional[datetime] = None
    created_at: datetime

    model_config = {"from_attributes": True}


class MigrationPendingRead(BaseModel):
    id: UUID
    batch_id: UUID
    entity_id: UUID
    entity_type: str
    entity_name: str
    source_code: Optional[str] = None
    missing_fields: list[str]
    review_flags: list[str]
    status: str
    created_at: datetime


class MigrationPendingResolveResponse(BaseModel):
    id: UUID
    status: str
    missing_fields: list[str]
