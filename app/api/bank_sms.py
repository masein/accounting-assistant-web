"""Paste bank SMS into the books (roadmap 2026-09 §4.1).

One or many messages at once; each becomes a row of the bank's SMS-feed
statement (app/services/bank_sms.py), reviewed and approved like any other
statement on the Bank statements page."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.services.audit_service import log_audit_event
from app.services.bank_sms import ingest, parse_sms

router = APIRouter(prefix="/bank-sms", tags=["bank-statements"])


class BankSmsPaste(BaseModel):
    text: str = Field(..., min_length=1, max_length=20_000)


@router.post("")
def paste_bank_sms(payload: BankSmsPaste, db: Session = Depends(get_db)) -> dict:
    try:
        out = ingest(db, payload.text)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    log_audit_event(db, "bank_sms_paste", "bank_statement", entity_id=None,
                    detail=f"{out['added']} added, {out['duplicates']} already on file, {len(out['unparsed'])} unread")
    db.commit()
    return out


@router.post("/preview")
def preview_bank_sms(payload: BankSmsPaste) -> dict:
    """What would be read from the text, without filing anything."""
    from app.services.bank_sms import split_messages
    return {"messages": [parse_sms(t).as_dict() | {"text": t[:300]} for t in split_messages(payload.text)[:200]]}
