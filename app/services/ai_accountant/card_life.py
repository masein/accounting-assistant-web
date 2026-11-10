"""How long a card can be confirmed, said once.

A card used to lapse after 10 minutes. On the phone a card made at night is confirmed in the morning, and a
card read an hour later had to be typed again (production chats, 2026-10-22 and 10-24, scenario BP1). A card
now waits a day. A card the user moved past («ولش», "never mind") is set aside by the chat itself, and a late
confirm of a card a later one already recorded is refused (execute_service).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import or_

from app.models.ai_accountant import AIProposal

PROPOSAL_TTL = timedelta(hours=24)


def _utc(dt: datetime | None) -> datetime | None:
    if dt is not None and dt.tzinfo is None:          # SQLite gives them back without their zone
        return dt.replace(tzinfo=timezone.utc)
    return dt


def lapsed(created_at: datetime | None, approval_status: str | None = None, now: datetime | None = None) -> bool:
    """Past its time. A card waiting for a second person keeps the approval's own clock (guardrails)."""
    made = _utc(created_at)
    if made is None or approval_status == "requested":
        return False
    return made + PROPOSAL_TTL < (now or datetime.now(timezone.utc))


def waiting_clause(now: datetime | None = None):
    """SQL: a card still waiting for its confirm — pending and not past its time."""
    cutoff = (now or datetime.now(timezone.utc)) - PROPOSAL_TTL
    return (AIProposal.status == "pending") & or_(AIProposal.created_at >= cutoff,
                                                   AIProposal.approval_status == "requested")
