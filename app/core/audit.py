"""Audit logging helper — writes to the audit_logs table."""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from fastapi import Request
from sqlalchemy.orm import Session

from app.models.audit_log import AuditLog

_logger = logging.getLogger("app.audit")


def get_client_ip(request: Request) -> str | None:
    """Extract client IP, respecting X-Forwarded-For from reverse proxies."""
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else None


def audit_log(
    db: Session,
    *,
    action: str,
    entity_type: str,
    entity_id: str | None = None,
    user_id: str | None = None,
    username: str | None = None,
    detail: str | None = None,
    ip_address: str | None = None,
) -> AuditLog:
    """Create an audit log entry. Caller is responsible for committing."""
    entry = AuditLog(
        timestamp=datetime.now(timezone.utc),
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        user_id=user_id,
        username=username,
        detail=detail,
        ip_address=ip_address,
    )
    db.add(entry)
    db.flush()
    _logger.info(
        "audit action=%s type=%s id=%s user=%s",
        action, entity_type, entity_id or "-", username or "-",
    )
    return entry
