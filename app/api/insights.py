"""Proactive insights for the dashboards and the chat briefing.

Read-only: ``GET /insights`` returns the ranked, localized list computed by
``app/services/insight_service.py``. The bell gets the same rows through
``refresh_notifications`` (kind ``insight``) and the assistant through the
``get_insights`` tool, so all three surfaces agree.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.auth import SessionUser, get_current_user
from app.db.session import get_db

router = APIRouter(prefix="/insights", tags=["insights"])


def _user_language(db: Session, user: SessionUser) -> str:
    from app.models.user import User
    from app.services.insight_service import SUPPORTED_LANGUAGES

    try:
        row = db.get(User, user.user_id)
        lang = (row.preferred_language or "en") if row else "en"
    except Exception:
        lang = "en"
    lang = (lang or "en").strip().lower()
    return lang if lang in SUPPORTED_LANGUAGES else "en"


@router.get("")
def list_insights(
    db: Session = Depends(get_db),
    user: SessionUser = Depends(get_current_user),
) -> dict:
    """Ranked proactive insights in the user's language."""
    from app.services.insight_service import insights_payload

    return insights_payload(db, _user_language(db, user))
