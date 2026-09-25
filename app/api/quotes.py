"""Sales quotes (پیش‌فاکتور) → invoice (roadmap §4.2).

A quote carries the same lines as an invoice but never posts to the ledger.
Lifecycle: draft → sent → accepted | declined, then ``convert`` creates the
sales invoice in one commit and locks the quote as ``converted``. "Expired"
is shown when ``valid_until`` has passed on a draft/sent quote; it is
informational (a late acceptance can still be recorded and invoiced).
"""
from __future__ import annotations

import re
from datetime import date, datetime, timedelta, timezone
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.exc import DataError
from sqlalchemy.orm import Session, selectinload

from app.api.invoices import (
    _build_invoice_items,
    _tax_breakdown,
    _to_read,
    insert_invoice,
    invoice_number_prefix,
    suggest_number,
)
from app.db.session import get_db
from app.models.company_profile import CompanyProfile
from app.models.entity import Entity
from app.models.invoice import Invoice
from app.models.quote import Quote, QuoteItem
from app.schemas.invoice import InvoiceCreate, InvoiceItemCreate, InvoiceRead
from app.schemas.quote import QuoteConvert, QuoteCreate, QuoteRead, QuoteUpdate
from app.services.audit_service import log_audit_event

router = APIRouter(prefix="/quotes", tags=["quotes"])

QUOTE_PREFIX = "Q-"
_EDITABLE = ("draft", "sent")
_SETTABLE = ("draft", "sent", "accepted", "declined")
DEFAULT_TERMS_DAYS = 30


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _effective_status(q: Quote, today: date | None = None) -> str:
    today = today or date.today()
    if q.status in _EDITABLE and q.valid_until and q.valid_until < today:
        return "expired"
    return q.status


def _read(db: Session, q: Quote) -> QuoteRead:
    subtotal, tax_total, _grand = _tax_breakdown(q)
    party = db.get(Entity, q.entity_id) if q.entity_id else None
    inv = db.get(Invoice, q.converted_invoice_id) if q.converted_invoice_id else None
    return QuoteRead(
        id=q.id, number=q.number, status=q.status, effective_status=_effective_status(q),
        issue_date=q.issue_date, valid_until=q.valid_until, amount=int(q.amount or 0),
        subtotal=subtotal, tax_total=tax_total, currency=q.currency, description=q.description,
        entity_id=q.entity_id, entity_name=(party.name if party else None),
        converted_invoice_id=q.converted_invoice_id,
        converted_invoice_number=(inv.number if inv else None),
        sent_at=q.sent_at, decided_at=q.decided_at, pdf_url=f"/quotes/{q.id}/pdf",
        items=[{
            "id": it.id, "position": it.position, "product_name": it.product_name,
            "quantity": float(it.quantity or 0), "unit_price": int(it.unit_price or 0),
            "unit_cost": it.unit_cost, "line_total": int(it.line_total or 0),
            "tax_rate": float(it.tax_rate or 0), "taxable": bool(it.taxable),
            "tax_code": it.tax_code, "tax_treatment": it.tax_treatment or "standard",
            "description": it.description, "inventory_item_id": it.inventory_item_id,
        } for it in (q.items or [])],
        created_at=q.created_at, updated_at=q.updated_at,
    )


def _load(db: Session, quote_id: UUID) -> Quote:
    q = db.execute(
        select(Quote).where(Quote.id == quote_id).options(selectinload(Quote.items))
    ).scalars().one_or_none()
    if q is None:
        raise HTTPException(status_code=404, detail="Quote not found.")
    return q


def _assert_number_free(db: Session, number: str, *, exclude_id=None) -> None:
    q = select(Quote.id).where(Quote.number == number)
    if exclude_id is not None:
        q = q.where(Quote.id != exclude_id)
    if db.execute(q).first():
        raise HTTPException(status_code=409, detail=f"Quote number '{number}' already exists.")


def _assert_entity(db: Session, entity_id) -> None:
    if entity_id is not None and db.get(Entity, entity_id) is None:
        raise HTTPException(status_code=422, detail="Customer entity not found.")


def _assert_dates(issue: date, valid_until: date) -> None:
    if valid_until < issue:
        raise HTTPException(status_code=422, detail="valid_until is before the issue date.")


def _set_items(db: Session, q: Quote, items: list[InvoiceItemCreate]) -> None:
    """Replace the lines using the invoice builder, so tax codes, treatments
    and rounding resolve exactly as they will on the invoice."""
    for old in list(q.items or []):
        db.delete(old)
    q.items.clear()
    built, grand_total = _build_invoice_items(items, None, db, q.issue_date)
    for pos, b in enumerate(built):
        q.items.append(QuoteItem(
            position=pos, product_name=b.product_name, quantity=b.quantity, unit_price=b.unit_price,
            unit_cost=b.unit_cost, line_total=b.line_total, tax_rate=b.tax_rate, taxable=b.taxable,
            tax_code=b.tax_code, tax_treatment=b.tax_treatment, description=b.description,
            inventory_item_id=b.inventory_item_id,
        ))
    if built:
        q.amount = grand_total


def _mark_status(q: Quote, status: str) -> None:
    if status == q.status:
        return
    q.status = status
    if status == "sent" and q.sent_at is None:
        q.sent_at = _now()
    if status in ("accepted", "declined"):
        q.decided_at = _now()
    if status in ("draft", "sent"):
        q.decided_at = None


def _terms_days(db: Session, entity_id) -> int:
    """Invoice terms for a converted quote: the customer's payment terms,
    else the company default, else 30 days (first number in the text)."""
    texts: list[str | None] = []
    if entity_id is not None:
        ent = db.get(Entity, entity_id)
        texts.append(ent.payment_terms if ent else None)
    prof = db.execute(select(CompanyProfile)).scalars().first()
    texts.append(prof.default_payment_terms if prof else None)
    for t in texts:
        m = re.search(r"\d+", t or "")
        if m:
            return min(int(m.group(0)), 3650)
    return DEFAULT_TERMS_DAYS


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.get("", response_model=list[QuoteRead])
def list_quotes(
    status: str | None = Query(None, description="draft|sent|accepted|declined|converted|expired"),
    entity_id: UUID | None = None,
    db: Session = Depends(get_db),
) -> list[QuoteRead]:
    q = select(Quote).options(selectinload(Quote.items)).order_by(Quote.issue_date.desc(), Quote.created_at.desc())
    if entity_id is not None:
        q = q.where(Quote.entity_id == entity_id)
    if status and status != "expired":
        q = q.where(Quote.status == status)
    rows = [_read(db, r) for r in db.execute(q).scalars().all()]
    if status == "expired":
        rows = [r for r in rows if r.effective_status == "expired"]
    return rows


@router.get("/next-number")
def next_numbers(db: Session = Depends(get_db)) -> dict:
    """Suggested numbers for a new quote and for the invoice a conversion
    would create, so the form can pre-fill them."""
    prefix = invoice_number_prefix(db)
    return {
        "quote_number": suggest_number(db, Quote, QUOTE_PREFIX),
        "invoice_number": suggest_number(db, Invoice, prefix, kind="sales"),
    }


@router.post("", response_model=QuoteRead, status_code=201)
def create_quote(payload: QuoteCreate, db: Session = Depends(get_db)) -> QuoteRead:
    status = (payload.status or "draft").strip().lower()
    if status not in _EDITABLE:
        raise HTTPException(status_code=422, detail="A new quote is 'draft' or 'sent'.")
    _assert_dates(payload.issue_date, payload.valid_until)
    _assert_entity(db, payload.entity_id)
    number = (payload.number or "").strip() or suggest_number(db, Quote, QUOTE_PREFIX)
    _assert_number_free(db, number)
    q = Quote(
        number=number, status="draft", issue_date=payload.issue_date, valid_until=payload.valid_until,
        amount=int(payload.amount or 0), currency=(payload.currency or "IRR").strip().upper(),
        description=(payload.description or "").strip() or None, entity_id=payload.entity_id,
    )
    db.add(q)
    try:
        db.flush()
        _set_items(db, q, payload.items)
        if int(q.amount or 0) <= 0:
            raise HTTPException(status_code=422, detail="A quote needs a positive amount or at least one priced line.")
        _mark_status(q, status)
        db.flush()
        log_audit_event(db, action="create", entity_type="quote", entity_id=str(q.id),
                        detail=f"Quote {q.number} created ({q.amount:,} {q.currency})")
        db.commit()
    except DataError as e:
        db.rollback()
        raise HTTPException(status_code=400, detail="Quote amount is too large.") from e
    except HTTPException:
        db.rollback()
        raise
    return _read(db, _load(db, q.id))


@router.get("/{quote_id}", response_model=QuoteRead)
def get_quote(quote_id: UUID, db: Session = Depends(get_db)) -> QuoteRead:
    return _read(db, _load(db, quote_id))


@router.patch("/{quote_id}", response_model=QuoteRead)
def update_quote(quote_id: UUID, payload: QuoteUpdate, db: Session = Depends(get_db)) -> QuoteRead:
    q = _load(db, quote_id)
    if q.status == "converted":
        raise HTTPException(status_code=409, detail="This quote was converted to an invoice and is locked.")
    content = payload.model_dump(exclude_unset=True, exclude={"status"})
    if content and q.status not in _EDITABLE:
        raise HTTPException(
            status_code=409,
            detail=f"A {q.status} quote can't be edited; set it back to draft first.",
        )
    new_status = None
    if payload.status is not None:
        new_status = payload.status.strip().lower()
        if new_status not in _SETTABLE:
            raise HTTPException(status_code=422, detail=f"status must be one of {', '.join(_SETTABLE)}; use convert to invoice it.")
    # Validate everything before touching the row (the session is shared with
    # the next request in tests and a half-applied edit must not leak).
    issue = payload.issue_date or q.issue_date
    valid_until = payload.valid_until or q.valid_until
    _assert_dates(issue, valid_until)
    if "entity_id" in content:
        _assert_entity(db, payload.entity_id)
    if payload.number is not None:
        number = payload.number.strip()
        if not number:
            raise HTTPException(status_code=422, detail="Quote number is empty.")
        _assert_number_free(db, number, exclude_id=q.id)
        q.number = number
    q.issue_date, q.valid_until = issue, valid_until
    if payload.currency is not None:
        q.currency = payload.currency.strip().upper() or q.currency
    if payload.description is not None:
        q.description = payload.description.strip() or None
    if "entity_id" in content:
        q.entity_id = payload.entity_id
    if payload.amount is not None and not q.items:
        q.amount = int(payload.amount)
    if payload.items is not None:
        _set_items(db, q, payload.items)
        if not q.items and payload.amount is not None:
            q.amount = int(payload.amount)
    if int(q.amount or 0) <= 0:
        db.rollback()
        raise HTTPException(status_code=422, detail="A quote needs a positive amount or at least one priced line.")
    if new_status is not None:
        _mark_status(q, new_status)
    log_audit_event(db, action="update", entity_type="quote", entity_id=str(q.id),
                    detail=f"Quote {q.number} updated" + (f" → {new_status}" if new_status else ""))
    db.commit()
    return _read(db, _load(db, q.id))


@router.delete("/{quote_id}", status_code=204)
def delete_quote(quote_id: UUID, db: Session = Depends(get_db)) -> None:
    q = _load(db, quote_id)
    if q.status == "converted":
        raise HTTPException(status_code=409, detail="A converted quote is part of the invoice's history; it can't be deleted.")
    log_audit_event(db, action="delete", entity_type="quote", entity_id=str(q.id),
                    detail=f"Quote {q.number} deleted")
    db.delete(q)
    db.commit()


@router.post("/{quote_id}/convert", status_code=201)
def convert_quote(quote_id: UUID, payload: QuoteConvert | None = None, db: Session = Depends(get_db)) -> dict:
    """Create the sales invoice from the quote's lines and lock the quote.
    One commit: either both change or neither does."""
    payload = payload or QuoteConvert()
    q = _load(db, quote_id)
    if q.status == "converted":
        raise HTTPException(status_code=409, detail="This quote was already converted.")
    if q.status == "declined":
        raise HTTPException(status_code=409, detail="A declined quote can't be invoiced; set it back to draft first.")
    status = (payload.status or "issued").strip().lower()
    if status not in ("issued", "draft"):
        raise HTTPException(status_code=422, detail="The invoice status must be 'issued' or 'draft'.")
    issue = payload.issue_date or date.today()
    due = payload.due_date or (issue + timedelta(days=_terms_days(db, q.entity_id)))
    if due < issue:
        raise HTTPException(status_code=422, detail="due_date is before the invoice's issue date.")
    number = (payload.number or "").strip() or suggest_number(db, Invoice, invoice_number_prefix(db), kind="sales")

    items = [InvoiceItemCreate(
        product_name=it.product_name, quantity=float(it.quantity or 0), unit_price=int(it.unit_price or 0),
        unit_cost=it.unit_cost, line_total=int(it.line_total or 0), tax_rate=float(it.tax_rate or 0),
        taxable=bool(it.taxable), tax_code=it.tax_code, tax_treatment=it.tax_treatment or "standard",
        description=it.description, inventory_item_id=it.inventory_item_id,
    ) for it in (q.items or [])]
    try:
        inv = insert_invoice(db, InvoiceCreate(
            number=number, kind="sales", status=status, issue_date=issue, due_date=due,
            amount=int(q.amount or 0), currency=q.currency,
            description=q.description or f"Quote {q.number}", entity_id=q.entity_id, items=items,
        ))
        q.converted_invoice_id = inv.id
        if q.decided_at is None:
            q.decided_at = _now()
        q.status = "converted"
        log_audit_event(db, action="convert", entity_type="quote", entity_id=str(q.id),
                        detail=f"Quote {q.number} converted to invoice {inv.number}")
        db.commit()
    except HTTPException:
        db.rollback()
        raise
    except DataError as e:
        db.rollback()
        raise HTTPException(status_code=400, detail="Invoice amount is too large.") from e
    db.refresh(inv)
    invoice: InvoiceRead = _to_read(inv)
    return {"quote": _read(db, _load(db, q.id)).model_dump(mode="json"),
            "invoice": invoice.model_dump(mode="json")}


@router.get("/{quote_id}/pdf")
def quote_pdf(quote_id: UUID, db: Session = Depends(get_db)) -> Response:
    from app.services.documents.render import render_quote_pdf
    q = _load(db, quote_id)
    party = db.get(Entity, q.entity_id) if q.entity_id else None
    pdf = render_quote_pdf(db, q, party)
    return Response(content=pdf, media_type="application/pdf",
                    headers={"Content-Disposition": f'inline; filename="quote-{q.number}.pdf"'})
