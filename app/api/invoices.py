from __future__ import annotations

import io
import uuid
from datetime import date, timedelta
from pathlib import Path
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import Response
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas
from sqlalchemy import select
from sqlalchemy.exc import DataError
from sqlalchemy.orm import Session, selectinload

from app.db.session import get_db
from app.models.account import Account
from app.models.entity import Entity, TransactionEntity
from app.models.invoice import Invoice
from app.models.invoice_item import InvoiceItem
from app.models.transaction import Transaction, TransactionLine
from app.schemas.invoice import (
    InvoiceCreate,
    InvoiceOCRResult,
    InvoiceRead,
    InvoiceTimelineEvent,
    InvoiceUpdate,
    MarkInvoicePaidRequest,
)
from app.services.ocr_extract import OCRExtractError, extract_from_attachment

router = APIRouter(prefix="/invoices", tags=["invoices"])
OCR_UPLOAD_DIR = Path(__file__).resolve().parents[1] / "uploads" / "invoice_imports"
MAX_IMPORT_SIZE_BYTES = 10 * 1024 * 1024
ALLOWED_IMPORT_TYPES = {"image/jpeg", "image/png", "image/webp", "application/pdf"}


def _validate_kind(kind: str) -> str:
    v = (kind or "").strip().lower()
    if v not in ("sales", "purchase"):
        raise HTTPException(status_code=400, detail="Invoice kind must be 'sales' or 'purchase'.")
    return v


def _validate_status(status: str) -> str:
    v = (status or "").strip().lower()
    if v not in ("draft", "issued", "paid", "canceled"):
        raise HTTPException(status_code=400, detail="Invalid invoice status.")
    return v


def _to_read(row: Invoice) -> InvoiceRead:
    data = InvoiceRead.model_validate(row)
    data.pdf_url = f"/invoices/{row.id}/pdf"
    return data


def _build_invoice_items(payload_items: list, invoice_id: UUID) -> tuple[list[InvoiceItem], int]:
    rows: list[InvoiceItem] = []
    total = 0
    for raw in payload_items or []:
        qty = float(raw.quantity or 0)
        if qty <= 0:
            continue
        unit_price = int(raw.unit_price or 0)
        line_total = int(raw.line_total if raw.line_total is not None else round(qty * unit_price))
        total += max(0, line_total)
        rows.append(
            InvoiceItem(
                invoice_id=invoice_id,
                product_name=raw.product_name.strip(),
                quantity=qty,
                unit_price=max(0, unit_price),
                unit_cost=(int(raw.unit_cost) if raw.unit_cost is not None else None),
                line_total=max(0, line_total),
                description=(raw.description or "").strip() or None,
                inventory_item_id=raw.inventory_item_id,
            )
        )
    return rows, total


def _safe_invoice_number(raw: str | None) -> str:
    text = (raw or "").strip().upper()
    text = "".join(ch for ch in text if ch.isalnum() or ch in ("-", "_", "/"))
    if len(text) >= 3 and any(ch.isdigit() for ch in text):
        return text[:120]
    return f"INV-OCR-{date.today().strftime('%Y%m%d')}-{uuid.uuid4().hex[:6].upper()}"


def _parse_yyyy_mm_dd(raw: str | None) -> date | None:
    if not raw:
        return None
    t = str(raw).strip().replace("/", "-")
    try:
        return date.fromisoformat(t[:10])
    except ValueError:
        return None


def _guess_kind_from_text(raw_text: str, default_kind: str) -> str:
    t = (raw_text or "").lower()
    if any(k in t for k in ("bill", "purchase", "supplier", "expense", "payable")):
        return "purchase"
    if any(k in t for k in ("invoice", "sales", "revenue", "receivable", "customer")):
        return "sales"
    return default_kind


def _find_or_create_party(db: Session, kind: str, name: str | None) -> UUID | None:
    clean = (name or "").strip()
    if not clean:
        return None
    typ = "client" if kind == "sales" else "supplier"
    existing = db.execute(select(Entity).where(Entity.type == typ, Entity.name.ilike(clean))).scalars().first()
    if existing:
        return existing.id
    row = Entity(type=typ, name=clean)
    db.add(row)
    db.flush()
    return row.id


@router.get("", response_model=list[InvoiceRead])
def list_invoices(
    db: Session = Depends(get_db),
    status: str | None = Query(None),
) -> list[InvoiceRead]:
    q = select(Invoice).options(selectinload(Invoice.items)).order_by(Invoice.issue_date.desc(), Invoice.created_at.desc())
    if status:
        q = q.where(Invoice.status == status.strip().lower())
    rows = db.execute(q).scalars().all()
    return [_to_read(r) for r in rows]


@router.post("/ocr-import", response_model=InvoiceOCRResult)
async def ocr_import_invoice(
    file: UploadFile = File(...),
    kind: str = Form("sales"),
    create: bool = Form(False),
    issue_date: date | None = Form(None),
    due_date: date | None = Form(None),
    entity_id: UUID | None = Form(None),
    description: str | None = Form(None),
    db: Session = Depends(get_db),
) -> InvoiceOCRResult:
    k = _validate_kind(kind)
    content_type = (file.content_type or "").strip().lower()
    if content_type not in ALLOWED_IMPORT_TYPES:
        raise HTTPException(status_code=400, detail="Unsupported file type. Use JPG, PNG, WEBP, or PDF.")
    raw = await file.read()
    if not raw:
        raise HTTPException(status_code=400, detail="File is empty.")
    if len(raw) > MAX_IMPORT_SIZE_BYTES:
        raise HTTPException(status_code=400, detail="File too large. Max size is 10 MB.")

    OCR_UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    ext = Path(file.filename or "invoice").suffix or ".bin"
    path = OCR_UPLOAD_DIR / f"{uuid.uuid4().hex}{ext}"
    path.write_bytes(raw)

    try:
        extracted = await extract_from_attachment(str(path), content_type)
    except OCRExtractError as e:
        raise HTTPException(status_code=400, detail=f"OCR failed: {str(e)}") from e
    finally:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass

    parsed_date = issue_date or _parse_yyyy_mm_dd(extracted.get("date")) or date.today()
    parsed_due = due_date or (parsed_date + timedelta(days=14))
    parsed_kind = _guess_kind_from_text(str(extracted.get("raw_text") or ""), k)
    parsed_amount = int(extracted.get("amount") or 0)
    parsed_currency = str(extracted.get("currency") or "IRR").upper()[:8]
    parsed_ref = _safe_invoice_number(extracted.get("invoice_or_receipt_no"))
    parsed_desc = (description or "").strip() or (
        f"OCR import - {extracted.get('vendor_name')}" if extracted.get("vendor_name") else "OCR imported invoice"
    )

    resolved_entity_id = entity_id
    if resolved_entity_id is None:
        resolved_entity_id = _find_or_create_party(db, parsed_kind, extracted.get("vendor_name"))

    suggested = InvoiceCreate(
        number=parsed_ref,
        kind=parsed_kind,
        issue_date=parsed_date,
        due_date=parsed_due,
        amount=max(0, parsed_amount),
        currency=parsed_currency or "IRR",
        description=parsed_desc,
        entity_id=resolved_entity_id,
        status="issued",
    )

    created_invoice: InvoiceRead | None = None
    if create:
        row = Invoice(
            number=suggested.number.strip(),
            kind=suggested.kind,
            status=suggested.status,
            issue_date=suggested.issue_date,
            due_date=suggested.due_date,
            amount=suggested.amount,
            currency=suggested.currency,
            description=suggested.description,
            entity_id=suggested.entity_id,
        )
        db.add(row)
        db.flush()
        item_rows, items_total = _build_invoice_items(suggested.items, row.id)
        for item in item_rows:
            db.add(item)
        if item_rows:
            row.amount = items_total
        db.commit()
        db.refresh(row)
        created_invoice = _to_read(row)

    return InvoiceOCRResult(
        vendor_name=extracted.get("vendor_name"),
        invoice_or_receipt_no=extracted.get("invoice_or_receipt_no"),
        date=extracted.get("date"),
        amount=extracted.get("amount"),
        currency=extracted.get("currency"),
        confidence=extracted.get("confidence"),
        raw_text=extracted.get("raw_text"),
        suggested=suggested,
        created_invoice=created_invoice,
    )


@router.post("", response_model=InvoiceRead, status_code=201)
def create_invoice(payload: InvoiceCreate, db: Session = Depends(get_db)) -> InvoiceRead:
    kind = _validate_kind(payload.kind)
    row = Invoice(
        number=payload.number.strip(),
        kind=kind,
        status=_validate_status(payload.status),
        issue_date=payload.issue_date,
        due_date=payload.due_date,
        amount=int(payload.amount or 0),
        currency=(payload.currency or "IRR").strip().upper(),
        description=(payload.description or "").strip() or None,
        entity_id=payload.entity_id,
    )
    db.add(row)
    try:
        db.flush()
        item_rows, items_total = _build_invoice_items(payload.items, row.id)
        for item in item_rows:
            db.add(item)
        if item_rows:
            row.amount = items_total
        db.commit()
    except DataError as e:
        db.rollback()
        raise HTTPException(status_code=400, detail="Invoice amount is too large for current database schema.") from e
    db.refresh(row)
    return _to_read(row)


@router.patch("/{invoice_id}", response_model=InvoiceRead)
def update_invoice(invoice_id: UUID, payload: InvoiceUpdate, db: Session = Depends(get_db)) -> InvoiceRead:
    row = db.execute(select(Invoice).where(Invoice.id == invoice_id).options(selectinload(Invoice.items))).scalars().one_or_none()
    if not row:
        raise HTTPException(status_code=404, detail="Invoice not found")
    if payload.number is not None:
        row.number = payload.number.strip()
    if payload.kind is not None:
        row.kind = _validate_kind(payload.kind)
    if payload.status is not None:
        row.status = _validate_status(payload.status)
    if payload.issue_date is not None:
        row.issue_date = payload.issue_date
    if payload.due_date is not None:
        row.due_date = payload.due_date
    if payload.amount is not None:
        row.amount = payload.amount
    if payload.currency is not None:
        row.currency = payload.currency.strip().upper() or "IRR"
    if payload.description is not None:
        row.description = payload.description.strip() or None
    if payload.entity_id is not None:
        row.entity_id = payload.entity_id
    if payload.items is not None:
        for item in list(row.items or []):
            db.delete(item)
        item_rows, items_total = _build_invoice_items(payload.items, row.id)
        for item in item_rows:
            db.add(item)
        if item_rows:
            row.amount = items_total
    db.commit()
    db.refresh(row)
    return _to_read(row)


@router.delete("/{invoice_id}", status_code=204)
def delete_invoice(invoice_id: UUID, db: Session = Depends(get_db)) -> None:
    row = db.get(Invoice, invoice_id)
    if not row:
        raise HTTPException(status_code=404, detail="Invoice not found")
    db.delete(row)
    db.commit()


@router.post("/{invoice_id}/mark-paid", response_model=InvoiceRead)
def mark_invoice_paid(
    invoice_id: UUID,
    payload: MarkInvoicePaidRequest,
    db: Session = Depends(get_db),
) -> InvoiceRead:
    inv = db.get(Invoice, invoice_id)
    if not inv:
        raise HTTPException(status_code=404, detail="Invoice not found")
    if inv.status == "paid" and inv.transaction_id:
        db.refresh(inv)
        return _to_read(inv)

    bank_entity: Entity | None = None
    bank_code = (payload.bank_account_code or "").strip() or "1110"
    if payload.bank_entity_id:
        bank_entity = db.get(Entity, payload.bank_entity_id)
        if not bank_entity or (bank_entity.type or "").strip().lower() != "bank":
            raise HTTPException(status_code=400, detail="Selected bank entity not found.")
        entity_code = (bank_entity.code or "").strip()
        if entity_code:
            acc_from_entity = db.execute(select(Account).where(Account.code == entity_code)).scalars().one_or_none()
            if acc_from_entity:
                bank_code = entity_code

    bank_acc = db.execute(select(Account).where(Account.code == bank_code)).scalars().one_or_none()
    if not bank_acc:
        raise HTTPException(status_code=400, detail=f"Bank account not found: {bank_code}")
    opposite_code = "4110" if inv.kind == "sales" else "6112"
    opposite_acc = db.execute(select(Account).where(Account.code == opposite_code)).scalars().one_or_none()
    if not opposite_acc:
        raise HTTPException(status_code=400, detail=f"Required account not found: {opposite_code}")

    txn = Transaction(
        date=payload.payment_date,
        reference=(payload.reference or inv.number).strip() or None,
        description=(payload.description or inv.description or f"Invoice {inv.number} paid").strip() or None,
    )
    db.add(txn)
    db.flush()
    if inv.kind == "sales":
        lines = [
            TransactionLine(transaction_id=txn.id, account_id=bank_acc.id, debit=inv.amount, credit=0, line_description=f"Invoice {inv.number} receipt"),
            TransactionLine(transaction_id=txn.id, account_id=opposite_acc.id, debit=0, credit=inv.amount, line_description=f"Revenue from invoice {inv.number}"),
        ]
    else:
        lines = [
            TransactionLine(transaction_id=txn.id, account_id=opposite_acc.id, debit=inv.amount, credit=0, line_description=f"Expense from invoice {inv.number}"),
            TransactionLine(transaction_id=txn.id, account_id=bank_acc.id, debit=0, credit=inv.amount, line_description=f"Invoice {inv.number} payment"),
        ]
    for ln in lines:
        db.add(ln)
    if bank_entity:
        db.add(TransactionEntity(transaction_id=txn.id, entity_id=bank_entity.id, role="bank"))
    if inv.entity_id:
        inv_role = "client" if inv.kind == "sales" else "supplier"
        db.add(TransactionEntity(transaction_id=txn.id, entity_id=inv.entity_id, role=inv_role))
    inv.status = "paid"
    inv.transaction_id = txn.id
    db.commit()
    db.refresh(inv)
    return _to_read(inv)


@router.get("/{invoice_id}/timeline", response_model=list[InvoiceTimelineEvent])
def invoice_timeline(invoice_id: UUID, db: Session = Depends(get_db)) -> list[InvoiceTimelineEvent]:
    inv = db.get(Invoice, invoice_id)
    if not inv:
        raise HTTPException(status_code=404, detail="Invoice not found")
    events = [
        InvoiceTimelineEvent(at=inv.created_at, event="created", detail=f"Invoice {inv.number} created with status {inv.status}."),
    ]
    if inv.updated_at and inv.updated_at != inv.created_at:
        events.append(InvoiceTimelineEvent(at=inv.updated_at, event="updated", detail=f"Invoice status: {inv.status}."))
    if inv.transaction_id:
        txn = db.get(Transaction, inv.transaction_id)
        if txn:
            events.append(InvoiceTimelineEvent(at=txn.created_at, event="paid", detail=f"Paid via transaction {txn.id}."))
    events.sort(key=lambda e: e.at)
    return events


@router.get("/{invoice_id}/pdf")
def invoice_pdf(invoice_id: UUID, db: Session = Depends(get_db)) -> Response:
    inv = db.execute(select(Invoice).where(Invoice.id == invoice_id).options(selectinload(Invoice.items))).scalars().one_or_none()
    if not inv:
        raise HTTPException(status_code=404, detail="Invoice not found")
    party = db.get(Entity, inv.entity_id) if inv.entity_id else None

    amount = int(inv.amount or 0)
    issue = inv.issue_date.isoformat() if inv.issue_date else "-"
    due = inv.due_date.isoformat() if inv.due_date else "-"
    status = (inv.status or "issued").upper()

    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    page_w, page_h = A4
    margin = 16 * mm
    primary = colors.HexColor("#0f766e")
    ink = colors.HexColor("#0f172a")
    muted = colors.HexColor("#475569")
    soft = colors.HexColor("#e2e8f0")

    # Header band
    c.setFillColor(primary)
    c.roundRect(margin, page_h - 50 * mm, page_w - (2 * margin), 34 * mm, 7, fill=1, stroke=0)
    c.setFillColor(colors.white)
    c.setFont("Helvetica-Bold", 22)
    c.drawString(margin + 8 * mm, page_h - 31 * mm, "INVOICE")
    c.setFont("Helvetica", 10)
    c.drawString(margin + 8 * mm, page_h - 37 * mm, "Accounting Assistant")

    c.setFont("Helvetica-Bold", 12)
    c.drawRightString(page_w - margin - 8 * mm, page_h - 28 * mm, f"No. {inv.number}")
    c.setFont("Helvetica", 10)
    c.drawRightString(page_w - margin - 8 * mm, page_h - 34 * mm, f"Status: {status}")
    c.drawRightString(page_w - margin - 8 * mm, page_h - 40 * mm, f"Type: {(inv.kind or '').title()}")

    # Meta cards
    card_y = page_h - 90 * mm
    card_h = 30 * mm
    card_w = (page_w - (2 * margin) - 8 * mm) / 2
    c.setFillColor(colors.white)
    c.setStrokeColor(soft)
    c.roundRect(margin, card_y, card_w, card_h, 6, fill=1, stroke=1)
    c.roundRect(margin + card_w + 8 * mm, card_y, card_w, card_h, 6, fill=1, stroke=1)

    c.setFillColor(muted)
    c.setFont("Helvetica-Bold", 10)
    c.drawString(margin + 5 * mm, card_y + card_h - 8 * mm, "Bill To")
    c.setFillColor(ink)
    c.setFont("Helvetica", 11)
    c.drawString(margin + 5 * mm, card_y + card_h - 15 * mm, (party.name if party else "Unspecified party")[:60])
    c.setFont("Helvetica", 9)
    c.setFillColor(muted)
    c.drawString(margin + 5 * mm, card_y + card_h - 21 * mm, f"Entity type: {(party.type if party else '-')}")

    rx = margin + card_w + 8 * mm
    c.setFillColor(muted)
    c.setFont("Helvetica-Bold", 10)
    c.drawString(rx + 5 * mm, card_y + card_h - 8 * mm, "Invoice Dates")
    c.setFillColor(ink)
    c.setFont("Helvetica", 10)
    c.drawString(rx + 5 * mm, card_y + card_h - 15 * mm, f"Issue: {issue}")
    c.drawString(rx + 5 * mm, card_y + card_h - 21 * mm, f"Due: {due}")

    item_rows = list(inv.items or [])
    if not item_rows:
        item_rows = [
            InvoiceItem(
                invoice_id=inv.id,
                product_name=(inv.description or "Service / Product"),
                quantity=1,
                unit_price=amount,
                line_total=amount,
            )
        ]
    visible_rows = item_rows[:8]

    # Line item table
    table_y = card_y - 62 * mm
    row_h = 8 * mm
    table_h = (10 * mm) + (len(visible_rows) * row_h) + (4 * mm)
    c.setStrokeColor(soft)
    c.roundRect(margin, table_y, page_w - (2 * margin), table_h, 6, fill=0, stroke=1)
    c.setFillColor(colors.HexColor("#f8fafc"))
    c.roundRect(margin, table_y + table_h - 10 * mm, page_w - (2 * margin), 10 * mm, 6, fill=1, stroke=0)
    table_left = margin + 5 * mm
    table_right = page_w - margin - 5 * mm
    qty_left = margin + 100 * mm
    qty_right = margin + 120 * mm
    unit_right = margin + 154 * mm
    # Keep a clear gutter between Unit Price and Line Total to avoid overlap with large amounts.
    line_total_right = table_right
    c.setFillColor(ink)
    c.setFont("Helvetica-Bold", 10)
    c.drawString(table_left, table_y + table_h - 6.8 * mm, "Description")
    c.drawString(qty_left, table_y + table_h - 6.8 * mm, "Qty")
    c.drawRightString(unit_right, table_y + table_h - 6.8 * mm, "Unit Price")
    c.drawRightString(line_total_right, table_y + table_h - 6.8 * mm, "Line Total")

    c.setFont("Helvetica", 9.5)
    c.setFillColor(ink)
    y = table_y + table_h - 15 * mm
    for row in visible_rows:
        qty_text = f"{float(row.quantity):.2f}".rstrip("0").rstrip(".")
        c.drawString(table_left, y, (row.product_name or "Item")[:68])
        c.drawRightString(qty_right, y, qty_text or "1")
        c.drawRightString(unit_right, y, f"{int(row.unit_price or 0):,}")
        c.drawRightString(line_total_right, y, f"{int(row.line_total or 0):,} {inv.currency}")
        y -= row_h
    if len(item_rows) > len(visible_rows):
        c.setFont("Helvetica-Oblique", 8.5)
        c.setFillColor(muted)
        c.drawString(table_left, table_y + 2.5 * mm, f"+ {len(item_rows) - len(visible_rows)} more lines")

    # Total box
    total_y = table_y - 22 * mm
    total_w = 64 * mm
    total_x = page_w - margin - total_w
    c.setFillColor(colors.HexColor("#f0fdfa"))
    c.setStrokeColor(soft)
    c.roundRect(total_x, total_y, total_w, 18 * mm, 6, fill=1, stroke=1)
    c.setFillColor(muted)
    c.setFont("Helvetica-Bold", 10)
    c.drawString(total_x + 5 * mm, total_y + 12 * mm, "TOTAL DUE")
    c.setFillColor(primary)
    c.setFont("Helvetica-Bold", 14)
    c.drawRightString(total_x + total_w - 5 * mm, total_y + 5.5 * mm, f"{amount:,} {inv.currency}")

    # Footer
    c.setFillColor(muted)
    c.setFont("Helvetica", 8.5)
    c.drawString(margin, 15 * mm, "Generated by Accounting Assistant")
    c.drawRightString(page_w - margin, 15 * mm, f"Invoice ID: {inv.id}")
    c.showPage()
    c.save()
    headers = {"Content-Disposition": f'inline; filename="invoice-{inv.number}.pdf"'}
    return Response(content=buf.getvalue(), media_type="application/pdf", headers=headers)
