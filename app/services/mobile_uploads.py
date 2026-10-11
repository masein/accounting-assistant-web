"""Uploads that survive a poor line (roadmap ROADMAP_ANDROID_CHAT P0.7).

The phone used to send a file in one request, and a statement on mobile data
in Iran often never got there: the connection dropped halfway and the next
try started from nothing. Now the phone asks to begin with the file's hash
and size, sends 256 KB pieces at the offset the server gives it, and after a
drop simply begins again: the same hash finds the same upload and the reply
says how much is already here.

- A piece that isn't at the expected offset (a repeat of one that landed but
  whose reply was lost, or one after a gap) writes nothing; the reply says
  where to go on, so the phone never needs to know what happened.
- The last piece is checked: the bytes must match the hash, then they go
  through the same checks as any attachment (type by content, 8 MB) and
  become one. A mismatch starts the upload over.
- The same file sent again, while its attachment isn't part of a voucher yet,
  is attached at once without its bytes.
- Each upload is its user's: nobody else can resume or add to it.
- Unfinished uploads are forgotten after a day; finished rows after a month.
"""
from __future__ import annotations

import hashlib
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.transactions import MAX_ATTACHMENT_SIZE_BYTES, UPLOADS_DIR, attachment_type, store_attachment
from app.models.mobile_upload import MobileUpload
from app.models.transaction import TransactionAttachment

CHUNK_SIZE = 256 * 1024          # one piece: about ten seconds on EDGE, so a drop costs little
MAX_CHUNK = 1024 * 1024
UNFINISHED_FOR = timedelta(days=1)
FINISHED_FOR = timedelta(days=30)
PARTS_DIR = UPLOADS_DIR.parent / "partial"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _part(row: MobileUpload) -> Path:
    return PARTS_DIR / f"{row.id.hex}.part"


def _unlink(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass


def _sweep(db: Session) -> None:
    """Forget what nobody finished in a day, and finished rows after a month."""
    now = _now()
    stale = db.execute(select(MobileUpload).where(
        ((MobileUpload.attachment_id.is_(None)) & (MobileUpload.updated_at < now - UNFINISHED_FOR))
        | (MobileUpload.updated_at < now - FINISHED_FOR))).scalars().all()
    for row in stale:
        _unlink(_part(row))
        db.delete(row)


def begin(db: Session, user_id: str | uuid.UUID, *, file_name: str, content_type: str | None,
          size_bytes: int, sha256: str) -> MobileUpload:
    """Begin an upload, or find the one this file already has: finished (and
    not yet part of a voucher), or as far as it got."""
    uid = uuid.UUID(str(user_id))
    kind = attachment_type(content_type, file_name)
    if size_bytes > MAX_ATTACHMENT_SIZE_BYTES:
        raise HTTPException(status_code=400, detail="Attachment too large. Max size is 8 MB.")
    _sweep(db)
    done = db.execute(
        select(MobileUpload)
        .join(TransactionAttachment, TransactionAttachment.id == MobileUpload.attachment_id)
        .where(MobileUpload.user_id == uid, MobileUpload.sha256 == sha256,
               TransactionAttachment.transaction_id.is_(None))
        .order_by(MobileUpload.updated_at.desc()).limit(1)).scalar_one_or_none()
    if done is not None:
        return done
    going = db.execute(
        select(MobileUpload)
        .where(MobileUpload.user_id == uid, MobileUpload.sha256 == sha256, MobileUpload.attachment_id.is_(None),
               MobileUpload.size_bytes == size_bytes, MobileUpload.received < MobileUpload.size_bytes)
        .order_by(MobileUpload.updated_at.desc()).limit(1)).scalar_one_or_none()
    if going is not None:
        db.commit()
        return going
    row = MobileUpload(user_id=uid, sha256=sha256, file_name=file_name.strip()[:256], content_type=kind,
                       size_bytes=size_bytes, received=0, created_at=_now(), updated_at=_now())
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def add_piece(db: Session, user_id: str | uuid.UUID, upload_id: str, offset: int, data: bytes) -> MobileUpload:
    """Write a piece where the upload stands; at the end, check it and make it an attachment."""
    try:
        key = uuid.UUID(str(upload_id))
    except ValueError:
        key = None
    row = None if key is None else db.execute(
        select(MobileUpload).where(MobileUpload.id == key, MobileUpload.user_id == uuid.UUID(str(user_id)))
        .with_for_update()).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="Upload expired or not found. Please re-upload the file.")
    if row.attachment_id is not None or offset != row.received or not data:
        db.commit()                                   # nothing to write: the reply says where it stands
        return row
    if row.received + len(data) > row.size_bytes:
        db.commit()
        raise HTTPException(status_code=400, detail="More bytes than the file has.")
    PARTS_DIR.mkdir(parents=True, exist_ok=True)
    part = _part(row)
    # from what the row says arrived: a write that crashed before its commit leaves nothing behind
    with open(part, "r+b" if part.exists() else "wb") as f:
        f.seek(row.received)
        f.truncate()
        f.write(data)
    row.received += len(data)
    row.updated_at = _now()
    if row.received == row.size_bytes:
        _finish(db, row, part)
    db.commit()
    db.refresh(row)
    return row


def _finish(db: Session, row: MobileUpload, part: Path) -> None:
    raw = part.read_bytes()
    if hashlib.sha256(raw).hexdigest() != row.sha256:
        _unlink(part)
        row.received = 0
        db.commit()
        raise HTTPException(status_code=422, detail="The file arrived damaged. It will be sent again.",
                            headers={"X-Error-Code": "upload_corrupt"})
    try:
        attachment = store_attachment(db, raw, row.file_name, row.content_type)
    except HTTPException:
        _unlink(part)
        db.delete(row)
        db.commit()
        raise
    row.attachment_id = attachment.id
    _unlink(part)


def attachment_of(db: Session, row: MobileUpload) -> TransactionAttachment | None:
    return db.get(TransactionAttachment, row.attachment_id) if row.attachment_id else None
