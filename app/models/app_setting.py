"""Simple key-value store for application settings persisted in the database."""
from __future__ import annotations

from sqlalchemy import Column, String, Text

from app.db.base import Base
from app.db.tenant import TenantMixin


class AppSetting(Base, TenantMixin):
    __tablename__ = "app_settings"

    key = Column(String(128), primary_key=True)
    value = Column(Text, nullable=False, default="")
