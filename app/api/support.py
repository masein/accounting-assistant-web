"""Support: a person's chat with the support desk, and the desk's door (the owner, 2026-11-28, DA1–DA8).

* ``/support`` (a signed-in person, any role) and ``/api/mobile/v1/support`` (the phones): the conversation, a
  message with screenshots and what the app knew, a screenshot's upload, the replies marked seen.
* ``/desk`` — not behind the session guard: the support desk on the owner's Mac (Nightshift) calls it with its
  desk key as a bearer token. ``hello`` (online), ``pending`` (the waiting messages, marked taken), ``reply``,
  ``attachments/{id}`` (a screenshot's bytes). A wrong key gets a plain 404, as the bots' relay does.
* ``/admin/support-desk`` (the platform admin): whether a desk is connected and online, a new desk key — shown
  once, only its hash kept — or support turned off.

The server never runs the desk's AI and holds no credentials for it (app/services/support_desk.py).
"""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.mobile import PREFIX, check_app_version
from app.core.auth import SessionUser, get_current_user
from app.db.session import get_db
from app.services import support_desk as S

router = APIRouter(prefix="/support", tags=["support"])
mobile_router = APIRouter(prefix=f"{PREFIX}/support", tags=["mobile"], dependencies=[Depends(check_app_version)])
desk_router = APIRouter(prefix="/desk", tags=["support"], include_in_schema=False)
admin_router = APIRouter(prefix="/admin/support-desk", tags=["admin"])

# what a person may attach to a support message: a screenshot or a document
_SUPPORT_TYPES = ("image/png", "image/jpeg", "image/webp", "application/pdf")


class SupportPost(BaseModel):
    text: str = Field("", max_length=S.MAX_TEXT)
    attachment_ids: list[str] = Field(default_factory=list, max_length=5)
    # what the app knew: page, app_version, language, user_agent, screen, platform, errors [{method, path,
    # status, request_id, at}]
    context: dict = Field(default_factory=dict)


def _after(value: str | None) -> datetime | None:
    if not value:
        return None
    # a "+00:00" sent unencoded arrives as " 00:00" (a query string's + is a space): read it as meant
    value = value.strip().replace(" ", "+")
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise HTTPException(status_code=422, detail="after must be an ISO date and time")
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _post(db: Session, user: SessionUser, payload: SupportPost, channel: str) -> dict:
    try:
        msg = S.post(db, user_id=user.user_id, text=payload.text, attachment_ids=payload.attachment_ids,
                     context=payload.context, channel=channel)
    except S.SupportLimit as e:
        db.rollback()
        raise HTTPException(status_code=429, detail=str(e), headers={"X-Error-Code": "support_limit"})
    except S.SupportError as e:
        db.rollback()
        raise HTTPException(status_code=422, detail=str(e))
    db.commit()
    return {"message": S.message_dict(msg), "desk": {"online": S.status(db)["online"]}}


def _upload(db: Session, file: UploadFile) -> dict:
    from app.api.transactions import attachment_type, store_attachment
    try:
        content_type = attachment_type(file.content_type, file.filename)
    except HTTPException:
        content_type = ""
    if content_type not in _SUPPORT_TYPES:
        raise HTTPException(status_code=422, detail="Attach a picture or a PDF.")
    row = store_attachment(db, file.file.read(), file.filename, content_type)
    db.commit()
    return {"id": str(row.id), "file_name": row.file_name, "content_type": row.content_type,
            "size_bytes": row.size_bytes}


# --- the person, on the web ---------------------------------------------------------------------------------------

@router.get("")
def support_conversation(after: str | None = Query(None, max_length=40), db: Session = Depends(get_db),
                         user: SessionUser = Depends(get_current_user)) -> dict:
    """The conversation with the support desk (the newest 100, or those after ``after``), the unread count, and
    whether the desk is online."""
    return S.conversation(db, user.user_id, after=_after(after))


@router.post("/messages", status_code=201)
def support_message(payload: SupportPost, db: Session = Depends(get_db),
                    user: SessionUser = Depends(get_current_user)) -> dict:
    return _post(db, user, payload, "web")


@router.post("/attachments", status_code=201)
def support_attachment(file: UploadFile = File(...), db: Session = Depends(get_db)) -> dict:
    """A screenshot or a PDF for the next message (20 MB at most, its type checked by its bytes)."""
    return _upload(db, file)


@router.post("/seen")
def support_seen(db: Session = Depends(get_db), user: SessionUser = Depends(get_current_user)) -> dict:
    n = S.mark_seen(db, user.user_id)
    db.commit()
    return {"seen": n}


# --- the person, on a phone -----------------------------------------------------------------------------------------

def _phone_channel(db: Session, user: SessionUser) -> str:
    import uuid

    from app.models.mobile_device import MobileDevice
    try:
        device = db.get(MobileDevice, uuid.UUID(str(user.device_id))) if getattr(user, "device_id", None) else None
    except ValueError:
        device = None
    platform = (getattr(device, "platform", None) or "android").lower()
    return "android" if platform == "android" else "ios"


@mobile_router.get("")
def mobile_support_conversation(after: str | None = Query(None, max_length=40), db: Session = Depends(get_db),
                                user: SessionUser = Depends(get_current_user)) -> dict:
    return S.conversation(db, user.user_id, after=_after(after))


@mobile_router.post("/messages", status_code=201)
def mobile_support_message(payload: SupportPost, db: Session = Depends(get_db),
                           user: SessionUser = Depends(get_current_user)) -> dict:
    return _post(db, user, payload, _phone_channel(db, user))


@mobile_router.post("/attachments", status_code=201)
def mobile_support_attachment(file: UploadFile = File(...), db: Session = Depends(get_db)) -> dict:
    return _upload(db, file)


@mobile_router.post("/seen")
def mobile_support_seen(db: Session = Depends(get_db), user: SessionUser = Depends(get_current_user)) -> dict:
    n = S.mark_seen(db, user.user_id)
    db.commit()
    return {"seen": n}


# --- the desk (the owner's Mac) -------------------------------------------------------------------------------------

def _desk(request: Request, db: Session = Depends(get_db)) -> Session:
    auth = request.headers.get("Authorization") or ""
    key = auth[7:].strip() if auth.lower().startswith("bearer ") else ""
    if not S.desk_key_ok(db, key):
        raise HTTPException(status_code=404, detail="Not Found")
    return db


class DeskReply(BaseModel):
    thread_id: str = Field(..., max_length=64)
    text: str = Field(..., min_length=1, max_length=S.MAX_TEXT)


@desk_router.post("/hello")
def desk_hello(db: Session = Depends(_desk)) -> dict:
    """The desk is online (it says so every minute); the support button shows it."""
    S.hello(db)
    db.commit()
    return {"ok": True}


@desk_router.post("/pending")
def desk_pending(limit: int = Query(20, ge=1, le=50), db: Session = Depends(_desk)) -> dict:
    """The people's messages waiting for the desk, each with who wrote it and the conversation so far."""
    S.hello(db)
    items = S.pending(db, limit=limit)
    db.commit()
    return {"items": items}


@desk_router.post("/reply", status_code=201)
def desk_reply(payload: DeskReply, db: Session = Depends(_desk)) -> dict:
    try:
        msg = S.reply(db, thread_id=payload.thread_id, text=payload.text)
    except S.SupportError as e:
        db.rollback()
        raise HTTPException(status_code=422, detail=str(e))
    return {"message": S.message_dict(msg)}


@desk_router.get("/attachments/{attachment_id}")
def desk_attachment(attachment_id: str, db: Session = Depends(_desk)):
    row = S.attachment(db, attachment_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Not Found")
    return FileResponse(row.file_path, media_type=row.content_type, filename=row.file_name or "attachment")


# --- the platform admin ----------------------------------------------------------------------------------------------

@admin_router.get("")
def support_desk_status(db: Session = Depends(get_db)) -> dict:
    return S.status(db)


@admin_router.post("/key")
def support_desk_key(db: Session = Depends(get_db)) -> dict:
    """A new desk key for the support desk on the owner's Mac, shown this once (DEPLOY.md §16). Replaces the old
    one: the desk stops until it has the new key."""
    from app.core.audit import audit_log
    key = S.new_desk_key(db)
    audit_log(db, action="create", entity_type="support_desk", detail="New support desk key")
    db.commit()
    return {**S.status(db), "desk_key": key}


@admin_router.delete("", status_code=204)
def support_desk_off(db: Session = Depends(get_db)) -> None:
    from app.core.audit import audit_log
    S.turn_off(db)
    audit_log(db, action="delete", entity_type="support_desk", detail="Support desk turned off")
    db.commit()
