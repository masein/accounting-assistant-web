"""Bank statements by e-mail (roadmap 2026-09 §4.1) — app/services/statement_mailbox.py.

* ``GET /bank-mailbox`` — the saved mailbox (never the password), the last
  check, and whether the caller may change it (bank:read).
* ``PUT /bank-mailbox`` / ``POST /bank-mailbox/test`` — set it up, try the
  login (bank_mail:manage: the owner, or a personal user for their books).
* ``POST /bank-mailbox/check`` — read it now (books:write, like an upload).
* ``GET /bank-mailbox/messages`` — what the checks read and what came of it.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.services import statement_mailbox as mb
from app.services.audit_service import log_audit_event

router = APIRouter(prefix="/bank-mailbox", tags=["bank-statements"])


class SenderRule(BaseModel):
    match: str = Field(..., max_length=254)
    bank_name: str = Field("", max_length=60)


class MailboxSettings(BaseModel):
    enabled: bool = False
    host: str = Field("", max_length=253)
    port: int = 993
    username: str = Field("", max_length=254)
    password: str | None = Field(None, max_length=256, description="Blank keeps the saved password.")
    folder: str = Field("INBOX", max_length=100)
    senders: list[SenderRule] | str = Field(default_factory=list)
    pdf_password: str | None = Field(None, max_length=128, description="For PDF statements the bank locks; blank keeps it.")
    clear_pdf_password: bool = False


class MailboxTest(BaseModel):
    host: str | None = Field(None, max_length=253)
    port: int | None = None
    username: str | None = Field(None, max_length=254)
    password: str | None = Field(None, max_length=256)
    folder: str | None = Field(None, max_length=100)


def _view(db: Session) -> dict:
    from app.core.permissions import Perm, role_can
    from app.core.request_context import get_current_actor
    actor = get_current_actor()
    cfg = mb.load_settings(db)
    can_manage = actor is None or getattr(actor, "is_superadmin", False) or \
        role_can(getattr(actor, "role", None), Perm.BANK_MAIL)
    return {**cfg, "can_manage": bool(can_manage), "status": mb.load_status(db)}


@router.get("")
def read_mailbox(db: Session = Depends(get_db)) -> dict:
    return _view(db)


@router.put("")
def save_mailbox(payload: MailboxSettings, db: Session = Depends(get_db)) -> dict:
    data = payload.model_dump()
    if not isinstance(payload.senders, str):
        data["senders"] = [s.model_dump() for s in payload.senders]
    try:
        mb.save_settings(db, data)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    log_audit_event(db, "update", "bank_mailbox", entity_id=None,
                    detail=f"{'on' if payload.enabled else 'off'} · {payload.host or '—'} · "
                           f"{len(data['senders']) if isinstance(data['senders'], list) else 'senders'}"
                           f"{' · password changed' if payload.password else ''}"
                           f"{' · PDF password changed' if payload.pdf_password or payload.clear_pdf_password else ''}")
    db.commit()
    return _view(db)


@router.post("/test")
def test_mailbox(payload: MailboxTest, db: Session = Depends(get_db)) -> dict:
    return mb.test_connection(db, payload.model_dump(exclude_none=True))


@router.post("/check")
def check_mailbox(db: Session = Depends(get_db)) -> dict:
    """Read the mailbox now. A sync route on purpose: the import runs its own
    event loop for the PDF reader, in the worker thread."""
    try:
        result = mb.check_mailbox(db)
    except mb.MailboxError as exc:
        raise HTTPException(status_code=409, detail="Set up the mailbox first.") from exc
    if result.get("imported"):
        log_audit_event(db, "bank_mailbox_check", "bank_statement", entity_id=None,
                        detail=f"{result['found']} new messages, {result['imported']} statements filed")
        db.commit()
    return result


@router.get("/messages")
def mailbox_messages(db: Session = Depends(get_db)) -> dict:
    return {"messages": mb.recent_messages(db)}
