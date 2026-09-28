"""Messenger webhooks (roadmap 2026-09 §5.7, part 2).

Not behind the session guard: Telegram and Bale post here. The path carries
the bot's secret and Telegram also sends it as a header; a wrong one gets a
plain 404. The update is handled after the response — an AI turn can take
longer than the platform waits, and a slow answer only makes it re-send.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.services.messenger import PLATFORMS, check_secret, handle_update

log = logging.getLogger("app.messenger")
router = APIRouter(prefix="/bots", tags=["bots"])


def _session_factory():
    from app.db.session import SessionLocal
    return SessionLocal


async def process_update(platform: str, update: dict) -> None:
    db = _session_factory()()
    try:
        await handle_update(db, platform, update)
    except Exception:  # noqa: BLE001 — the platform must still get its 200
        db.rollback()
        log.exception("messenger update failed platform=%s", platform)
    finally:
        db.close()


@router.post("/{platform}/webhook/{secret}", include_in_schema=False)
async def webhook(platform: str, secret: str, request: Request, background: BackgroundTasks,
                  db: Session = Depends(get_db)) -> dict:
    if platform not in PLATFORMS or not check_secret(
            db, platform, secret, request.headers.get("X-Telegram-Bot-Api-Secret-Token")):
        raise HTTPException(status_code=404, detail="Not Found")
    try:
        update = await request.json()
    except ValueError:
        raise HTTPException(status_code=400, detail="Not JSON")
    if not isinstance(update, dict):
        raise HTTPException(status_code=400, detail="Not an update")
    background.add_task(process_update, platform, update)
    return {"ok": True}
