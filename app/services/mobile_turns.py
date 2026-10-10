"""A phone message answered once, however often it arrives (roadmap
ROADMAP_ANDROID_CHAT P0.5, P1.5).

The phone's outbox sends a message again until it gets an answer: after a
dropped connection, from the background sender, or while the first try is
still asking the model on another server worker. ``begin`` claims the
message's row before the turn runs. A second arrival waits for the first and
gets its reply; a turn that failed (the model was down) or was abandoned (the
worker died) is taken over by the next try, once.
"""
from __future__ import annotations

import asyncio
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Callable

from sqlalchemy import delete, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.mobile_turn import MobileTurn

ABANDONED_AFTER = timedelta(minutes=3)     # a turn runs every tool in well under this
WAIT_SECONDS = 120.0
KEEP = timedelta(days=7)


class TurnInProgress(Exception):
    """The same message is still being answered after the wait."""


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(dt: datetime | None) -> datetime | None:
    if dt is not None and dt.tzinfo is None:          # SQLite hands back naive UTC
        return dt.replace(tzinfo=timezone.utc)
    return dt


def _row(db: Session, user_id: uuid.UUID, cid: str) -> MobileTurn | None:
    return db.execute(
        select(MobileTurn).where(MobileTurn.user_id == user_id, MobileTurn.client_message_id == cid)
        .execution_options(populate_existing=True)
    ).scalars().first()


async def begin(db: Session, user_id, cid: str | None, *, waiting: Callable[[], None] | None = None,
                wait_seconds: float = WAIT_SECONDS, poll: float = 0.5) -> dict | None:
    """None when this request should run the turn (the row is now claimed);
    the earlier reply, marked ``repeat``, when the message was answered."""
    if not cid:
        return None
    uid = uuid.UUID(str(user_id))
    deadline = time.monotonic() + wait_seconds
    told = False
    while True:
        row = _row(db, uid, cid)
        if row is None:
            db.execute(delete(MobileTurn).where(MobileTurn.user_id == uid, MobileTurn.started_at < _now() - KEEP))
            db.add(MobileTurn(user_id=uid, client_message_id=cid, state="running", attempt=1, started_at=_now()))
            try:
                db.commit()
                return None
            except IntegrityError:                     # another worker claimed it first
                db.rollback()
                continue
        if row.state == "done" and row.reply is not None:
            return {**row.reply, "stop_reason": "repeat"}
        if row.state == "failed" or _aware(row.started_at) < _now() - ABANDONED_AFTER:
            taken = db.execute(
                update(MobileTurn)
                .where(MobileTurn.user_id == uid, MobileTurn.client_message_id == cid, MobileTurn.attempt == row.attempt)
                .values(state="running", attempt=row.attempt + 1, started_at=_now(), finished_at=None)
            ).rowcount
            db.commit()
            if taken == 1:
                return None
            continue
        if time.monotonic() >= deadline:
            raise TurnInProgress(cid)
        if waiting is not None and not told:
            waiting()
            told = True
        await asyncio.sleep(poll)


def finish(db: Session, user_id, cid: str | None, reply: dict) -> None:
    """The turn answered: keep the reply for any repeat."""
    if not cid:
        return
    uid = uuid.UUID(str(user_id))
    db.execute(update(MobileTurn).where(MobileTurn.user_id == uid, MobileTurn.client_message_id == cid)
               .values(state="done", thread_id=str(reply.get("thread_id") or "") or None, reply=reply,
                       finished_at=_now()))
    db.commit()


def fail(db: Session, user_id, cid: str | None) -> None:
    """The turn failed without an answer: the next try may run it again."""
    if not cid:
        return
    db.rollback()
    uid = uuid.UUID(str(user_id))
    db.execute(update(MobileTurn).where(MobileTurn.user_id == uid, MobileTurn.client_message_id == cid)
               .values(state="failed", finished_at=_now()))
    db.commit()
