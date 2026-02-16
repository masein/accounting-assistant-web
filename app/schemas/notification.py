from __future__ import annotations

from pydantic import BaseModel


class NotificationItem(BaseModel):
    level: str
    title: str
    message: str


class NotificationCheckResponse(BaseModel):
    items: list[NotificationItem]
    delivered: list[str]
