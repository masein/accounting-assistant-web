"""Messenger webhooks (roadmap 2026-09 §5.7, part 2).

Not behind the session guard: Telegram and Bale post here. The path carries
the bot's secret and Telegram also sends it as a header; a wrong one gets a
plain 404. The update is handled after the response — an AI turn can take
longer than the platform waits, and a slow answer only makes it re-send.

The relay (``/bots/<platform>/relay/…``, app/services/messenger.py "Relay
mode") is for a server that can't reach the platform: its relay key, as a
bearer token, is the guard (a wrong one: the same plain 404). The relay waits
for the answer — it is the one talking to the platform — so the update is
handled within the request, and the answer is the calls to make.
"""
from __future__ import annotations

import logging

import base64
import binascii

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.services.messenger import (MAX_VOICE_BYTES, PLATFORMS, check_secret, handle_update, relay_calls,
                                    relay_config, relay_hello)

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


# --- the relay ---------------------------------------------------------------------------------------------

def _relay_cfg(platform: str, request: Request, db: Session) -> dict:
    auth = request.headers.get("Authorization") or ""
    key = auth[7:].strip() if auth.lower().startswith("bearer ") else ""
    cfg = relay_config(db, platform, key) if platform in PLATFORMS else None
    if cfg is None:
        raise HTTPException(status_code=404, detail="Not Found")
    return cfg


class RelayHello(BaseModel):
    username: str = Field(..., min_length=3, max_length=64)


class RelayUpdate(BaseModel):
    update: dict
    # a voice note's audio, base64, by file_id: the server can't fetch it from the platform
    files: dict[str, str] = Field(default_factory=dict, max_length=2)


@router.post("/{platform}/relay/hello", include_in_schema=False)
def relay_check_in(platform: str, payload: RelayHello, request: Request, db: Session = Depends(get_db)) -> dict:
    _relay_cfg(platform, request, db)
    try:
        status = relay_hello(db, platform, payload.username)
    except ValueError:
        raise HTTPException(status_code=422, detail="That is not a bot username.")
    db.commit()
    return {"ok": True, "bot": status}


@router.post("/{platform}/relay/update", include_in_schema=False)
async def relay_update(platform: str, payload: RelayUpdate, request: Request, db: Session = Depends(get_db)) -> dict:
    _relay_cfg(platform, request, db)
    files: dict[str, bytes] = {}
    for file_id, b64 in payload.files.items():
        if len(b64) > (MAX_VOICE_BYTES * 4) // 3 + 8:
            continue                                          # too large: the turn says so
        try:
            files[file_id] = base64.b64decode(b64, validate=True)
        except (binascii.Error, ValueError):
            continue
    try:
        reply = await handle_update(db, platform, payload.update, files=files, deliver=False)
    except Exception:  # noqa: BLE001 — the relay gets an answer either way
        db.rollback()
        log.exception("relayed update failed platform=%s", platform)
        return {"ok": False, "calls": []}
    return {"ok": True, "calls": relay_calls(reply)}
