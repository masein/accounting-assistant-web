"""Invoice e-mail and reminder routes (roadmap §4.2). Mounted under
/invoices next to the invoice CRUD; the logic lives in
app/services/invoice_mail.py."""
from __future__ import annotations

from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session, selectinload
from sqlalchemy import select

from app.db.session import get_db
from app.models.invoice import Invoice
from app.services import invoice_mail
from app.services.audit_service import log_audit_event
from app.services.invoice_mail import InvoiceMailError

router = APIRouter(prefix="/invoices", tags=["invoices"])


class SendInvoiceRequest(BaseModel):
    to: str | None = Field(None, max_length=512, description="Comma-separated; blank → the customer's e-mail")
    message: str | None = Field(None, max_length=2000, description="Optional note above the payment details")


class ReminderSettingsUpdate(BaseModel):
    reminders_enabled: bool | None = None
    reminder_days: list[int] | None = Field(None, description="Day offsets from the due date; negative = before due")
    payment_instructions: str | None = Field(None, max_length=2000)
    payment_link: str | None = Field(None, max_length=500)


class InvoiceEmailRead(BaseModel):
    id: UUID
    invoice_id: UUID
    kind: str
    stage: int | None = None
    to_address: str
    subject: str
    status: str
    error: str | None = None
    actor: str | None = None
    created_at: datetime

    model_config = {"from_attributes": True}


def _actor_name() -> str | None:
    from app.core.request_context import get_current_actor
    a = get_current_actor()
    return getattr(a, "username", None) if a else None


def _settings_read(db: Session) -> dict:
    from app.services.mail_service import mail_configured
    return {**invoice_mail.get_settings(db), "mail_configured": mail_configured()}


@router.get("/reminder-settings")
def get_reminder_settings(db: Session = Depends(get_db)) -> dict:
    return _settings_read(db)


@router.put("/reminder-settings")
def put_reminder_settings(payload: ReminderSettingsUpdate, db: Session = Depends(get_db)) -> dict:
    """Automatic reminders e-mail customers without anyone clicking, so the
    switch is owner-level (SETTINGS_WRITE) and audited."""
    try:
        saved = invoice_mail.save_settings(db, **payload.model_dump(exclude_unset=True))
    except InvoiceMailError as e:
        db.rollback()
        raise HTTPException(status_code=e.status_code, detail=str(e)) from e
    log_audit_event(db, action="update", entity_type="invoice_reminder_settings", entity_id=invoice_mail.SETTINGS_KEY,
                    detail=f"Reminders {'on' if saved['reminders_enabled'] else 'off'} at days {saved['reminder_days']}")
    db.commit()
    return _settings_read(db)


@router.post("/{invoice_id}/send", response_model=InvoiceEmailRead, status_code=201)
def send_invoice(invoice_id: UUID, payload: SendInvoiceRequest | None = None,
                 db: Session = Depends(get_db)) -> InvoiceEmailRead:
    """E-mail the invoice PDF to the customer. A delivery failure is returned
    as a logged attempt with status ``failed`` (502), never lost."""
    payload = payload or SendInvoiceRequest()
    inv = db.execute(select(Invoice).where(Invoice.id == invoice_id).options(selectinload(Invoice.items))).scalars().one_or_none()
    if inv is None:
        raise HTTPException(status_code=404, detail="Invoice not found")
    try:
        row = invoice_mail.send_invoice_email(db, inv, to=payload.to, message=payload.message,
                                              actor=_actor_name())
    except InvoiceMailError as e:
        db.rollback()
        raise HTTPException(status_code=e.status_code, detail=str(e)) from e
    log_audit_event(db, action="email", entity_type="invoice", entity_id=str(inv.id),
                    detail=f"Invoice {inv.number} e-mailed to {row.to_address}: {row.status}")
    db.commit()
    db.refresh(row)
    if row.status != "sent":
        raise HTTPException(status_code=502, detail=f"The mail server did not accept the message: {row.error}")
    return InvoiceEmailRead.model_validate(row)


@router.get("/{invoice_id}/emails", response_model=list[InvoiceEmailRead])
def list_invoice_emails(invoice_id: UUID, db: Session = Depends(get_db)) -> list[InvoiceEmailRead]:
    if db.get(Invoice, invoice_id) is None:
        raise HTTPException(status_code=404, detail="Invoice not found")
    return [InvoiceEmailRead.model_validate(r) for r in invoice_mail.email_log(db, invoice_id)]
