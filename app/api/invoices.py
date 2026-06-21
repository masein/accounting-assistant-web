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
from sqlalchemy import func, select
from sqlalchemy.exc import DataError
from sqlalchemy.orm import Session, object_session, selectinload

from app.db.session import get_db
from app.models.account import Account
from app.models.credit_note import CreditNote
from app.models.entity import Entity, TransactionEntity
from app.models.invoice import Invoice
from app.models.invoice_item import InvoiceItem
from app.models.payment import Payment
from app.models.transaction import Transaction, TransactionLine
from app.schemas.invoice import (
    CreditNoteCreate,
    CreditNoteRead,
    InvoiceCreate,
    InvoiceOCRResult,
    InvoiceRead,
    InvoiceTimelineEvent,
    InvoiceUpdate,
    MarkInvoicePaidRequest,
    PaymentCreate,
    PaymentRead,
)
from app.services.account_resolver import AccountResolutionError, resolve_account_code
from app.services.audit_service import log_audit_event
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
    if v not in ("draft", "issued", "partially_paid", "paid", "canceled", "voided"):
        raise HTTPException(status_code=400, detail="Invalid invoice status.")
    return v


def _line_tax(line_total: int, tax_rate, taxable: bool) -> int:
    """Tax on one line: rounded ``line_total * rate/100`` for taxable lines,
    0 for exempt lines."""
    if not taxable:
        return 0
    rate = float(tax_rate or 0)
    if rate <= 0:
        return 0
    return int(round(int(line_total or 0) * rate / 100.0))


def _tax_breakdown(inv: Invoice) -> tuple[int, int, int]:
    """(subtotal, tax_total, grand_total) for an invoice from its line items.

    Tax applies per line, only to taxable lines (mixed taxable/exempt is
    handled naturally). An invoice with no items has no tax: subtotal ==
    grand_total == its stored amount.
    """
    items = list(inv.items or [])
    if not items:
        amount = int(inv.amount or 0)
        return amount, 0, amount
    subtotal = sum(int(it.line_total or 0) for it in items)
    tax_total = sum(_line_tax(it.line_total, it.tax_rate, it.taxable) for it in items)
    return subtotal, tax_total, subtotal + tax_total


def _invoice_totals(db: Session, inv: Invoice) -> tuple[int, int, int]:
    """Return (amount_paid, credited, balance_due) for an invoice, where
    credited counts only credit notes that reduce the invoice (note_type
    'reduction'), not standalone overpayment credits. balance_due is clamped
    to >= 0 (overpayment surfaces as a separate entity credit, never a
    negative due)."""
    amount = int(inv.amount or 0)
    paid = int(
        db.execute(
            select(func.coalesce(func.sum(Payment.amount), 0)).where(Payment.invoice_id == inv.id)
        ).scalar()
        or 0
    )
    credited = int(
        db.execute(
            select(func.coalesce(func.sum(CreditNote.amount), 0)).where(
                CreditNote.invoice_id == inv.id, CreditNote.note_type == "reduction"
            )
        ).scalar()
        or 0
    )
    balance_due = max(0, amount - paid - credited)
    # Legacy reconciliation: invoices marked paid under the old flow have no
    # Payment rows, so the new calc would show a full open balance that
    # contradicts the status. Treat a 'paid' invoice as fully settled.
    if inv.status == "paid" and balance_due > 0:
        paid = amount - credited
        balance_due = 0
    # Voided/canceled invoices have no open balance — their recognition (and
    # payments) are reversed; they must not show as receivable/payable.
    if inv.status in ("voided", "canceled"):
        balance_due = 0
    return paid, credited, balance_due


def _recompute_status(inv: Invoice, paid: int, credited: int, balance_due: int) -> None:
    """issued → partially_paid → paid based on the open balance. Leaves
    draft/canceled untouched."""
    if inv.status in ("draft", "canceled"):
        return
    if int(inv.amount or 0) <= 0:
        return
    if balance_due <= 0:
        inv.status = "paid"
    elif (paid + credited) > 0:
        inv.status = "partially_paid"
    else:
        inv.status = "issued"


def _to_read(row: Invoice) -> InvoiceRead:
    data = InvoiceRead.model_validate(row)
    data.pdf_url = f"/invoices/{row.id}/pdf"
    subtotal, tax_total, grand_total = _tax_breakdown(row)
    data.subtotal = subtotal
    data.tax_total = tax_total
    data.grand_total = grand_total
    db = object_session(row)
    if db is not None:
        paid, credited, balance_due = _invoice_totals(db, row)
        data.amount_paid = paid
        data.credited = credited
        data.balance_due = balance_due
    return data


def _post_entry(
    db: Session,
    *,
    on: date,
    reference: str | None,
    description: str | None,
    currency: str,
    lines: list[tuple[str, int, int, str | None]],
    entity_links: list[tuple[UUID, str]] | None = None,
    audit_detail: str | None = None,
) -> Transaction:
    """Post one balanced journal entry (lines = [(account_code, debit, credit,
    line_description)]) with entity links and an audit-log row. Validates the
    entry balances and that every account exists."""
    total_dr = sum(d for _, d, _c, _ld in lines)
    total_cr = sum(c for _, _d, c, _ld in lines)
    if total_dr != total_cr:
        raise HTTPException(status_code=400, detail=f"Entry not balanced: DR {total_dr} != CR {total_cr}.")
    if total_dr <= 0:
        raise HTTPException(status_code=400, detail="Entry has zero amount.")

    # Block posting into a closed period (invoice recognition, payments,
    # credit notes all route through here).
    from app.services.period_service import assert_period_open
    assert_period_open(db, on)

    txn = Transaction(
        date=on,
        reference=(reference or None),
        description=(description or None),
        currency=(currency or "IRR").strip().upper(),
    )
    db.add(txn)
    db.flush()
    for code, debit, credit, line_desc in lines:
        acc = db.execute(select(Account).where(Account.code == code)).scalars().one_or_none()
        if not acc:
            raise HTTPException(status_code=400, detail=f"Account not found: {code}")
        db.add(TransactionLine(
            transaction_id=txn.id, account_id=acc.id,
            debit=int(debit), credit=int(credit), line_description=line_desc,
        ))
    for ent_id, role in (entity_links or []):
        if ent_id:
            db.add(TransactionEntity(transaction_id=txn.id, entity_id=ent_id, role=role))
    db.flush()
    log_audit_event(
        db, action="create", entity_type="transaction", entity_id=str(txn.id),
        detail=audit_detail,
    )
    return txn


def _recognize_invoice(db: Session, inv: Invoice) -> None:
    """Post the AR/AP recognition entry for an issued invoice, once. Sales:
    DR trade debtors / CR revenue. Purchase: DR expense / CR trade creditors.
    Links the recognition transaction to the invoice."""
    if inv.transaction_id is not None or inv.status in ("draft", "canceled"):
        return
    amount = int(inv.amount or 0)
    if amount <= 0:
        return
    # grand_total drives the AR/AP leg; the subtotal/tax split feeds the
    # revenue/expense and VAT legs. amount == grand_total (kept in sync on
    # create/update), so net + tax always reconciles to amount.
    subtotal, tax_total, grand_total = _tax_breakdown(inv)
    if inv.kind == "sales":
        ar = resolve_account_code(db, "ar")
        rev = resolve_account_code(db, "revenue")
        lines = [
            (ar, grand_total, 0, f"Invoice {inv.number} — receivable"),
            (rev, 0, subtotal, f"Invoice {inv.number} — revenue"),
        ]
        if tax_total > 0:
            lines.append((resolve_account_code(db, "vat_output"), 0, tax_total, f"Invoice {inv.number} — output VAT"))
        role = "client"
    else:
        exp = resolve_account_code(db, "expense")
        ap = resolve_account_code(db, "ap")
        lines = [
            (exp, subtotal, 0, f"Bill {inv.number} — expense"),
            (ap, 0, grand_total, f"Bill {inv.number} — payable"),
        ]
        if tax_total > 0:
            lines.append((resolve_account_code(db, "vat_input"), tax_total, 0, f"Bill {inv.number} — input VAT"))
        role = "supplier"
    txn = _post_entry(
        db, on=inv.issue_date, reference=inv.number,
        description=(inv.description or f"Invoice {inv.number} issued"),
        currency=inv.currency,
        lines=lines,
        entity_links=[(inv.entity_id, role)] if inv.entity_id else [],
        audit_detail=f"AR/AP recognition for invoice {inv.number}",
    )
    inv.transaction_id = txn.id


def _resolve_bank_code(db: Session, code: str | None) -> str:
    """The bank/cash account for a payment: an explicit valid code, else the
    locale's bank account."""
    c = (code or "").strip()
    if c and db.execute(select(Account.id).where(Account.code == c)).first():
        return c
    return resolve_account_code(db, "bank")


def _build_invoice_items(
    payload_items: list, invoice_id: UUID,
    db: Session | None = None, on_date: date | None = None,
) -> tuple[list[InvoiceItem], int]:
    """Build InvoiceItem rows. Returns (rows, grand_total) where grand_total =
    Σ line_total + Σ per-line tax (only ``standard``-treatment lines charge tax),
    so the invoice's ``amount`` stays equal to the tax-inclusive grand total.

    Tax rate resolution (§7.6): if a line gives no explicit ``tax_rate`` but has
    a ``tax_code``, derive the rate in effect on ``on_date`` (the invoice date).
    Treatment (§7.3): zero_rated / exempt / reverse_charge charge no output tax;
    the resolved rate is still stored (for reverse-charge notional reporting)."""
    from app.services.tax_rate_service import TREATMENTS, tax_rate_for

    rows: list[InvoiceItem] = []
    subtotal = 0
    tax_total = 0
    for raw in payload_items or []:
        qty = float(raw.quantity or 0)
        if qty <= 0:
            continue
        unit_price = int(raw.unit_price or 0)
        line_total = max(0, int(raw.line_total if raw.line_total is not None else round(qty * unit_price)))

        treatment = (getattr(raw, "tax_treatment", "standard") or "standard").strip().lower()
        if treatment not in TREATMENTS:
            treatment = "standard"
        tax_code = (getattr(raw, "tax_code", None) or None)
        tax_rate = float(getattr(raw, "tax_rate", 0) or 0)
        # Derive the rate from the code as of the invoice date when not given.
        if tax_rate <= 0 and tax_code and db is not None and on_date is not None:
            derived = tax_rate_for(db, tax_code, on_date)
            if derived is not None:
                tax_rate = float(derived)
        # Only a standard-treatment line actually charges output tax. The
        # explicit `taxable=False` flag still forces a line exempt.
        explicit_taxable = bool(getattr(raw, "taxable", True))
        charges_tax = explicit_taxable and treatment == "standard"

        subtotal += line_total
        tax_total += _line_tax(line_total, tax_rate, charges_tax)
        rows.append(
            InvoiceItem(
                invoice_id=invoice_id,
                product_name=raw.product_name.strip(),
                quantity=qty,
                unit_price=max(0, unit_price),
                unit_cost=(int(raw.unit_cost) if raw.unit_cost is not None else None),
                line_total=line_total,
                tax_rate=tax_rate,
                taxable=charges_tax,
                tax_code=tax_code,
                tax_treatment=treatment,
                description=(raw.description or "").strip() or None,
                inventory_item_id=raw.inventory_item_id,
            )
        )
    return rows, subtotal + tax_total


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
    kind: str | None = Query(None, description="Filter by kind: sales | purchase"),
) -> list[InvoiceRead]:
    q = select(Invoice).options(selectinload(Invoice.items)).order_by(Invoice.issue_date.desc(), Invoice.created_at.desc())
    if status:
        q = q.where(Invoice.status == status.strip().lower())
    if kind:
        q = q.where(Invoice.kind == kind.strip().lower())
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
        item_rows, items_total = _build_invoice_items(suggested.items, row.id, db, row.issue_date)
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
        item_rows, items_total = _build_invoice_items(payload.items, row.id, db, row.issue_date)
        for item in item_rows:
            row.items.append(item)  # populate the relationship so tax breakdown sees them
        if item_rows:
            row.amount = items_total
        db.flush()
        # Recognise AR/AP at issue: DR debtors / CR revenue (sales) or
        # DR expense / CR creditors (purchase). Posts once, links the txn.
        if row.status in ("issued", "partially_paid", "paid"):
            _recognize_invoice(db, row)
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
    if payload.scheduled_payment_date is not None:
        row.scheduled_payment_date = payload.scheduled_payment_date
    if payload.items is not None:
        for item in list(row.items or []):
            db.delete(item)
        row.items.clear()
        item_rows, items_total = _build_invoice_items(payload.items, row.id, db, row.issue_date)
        for item in item_rows:
            row.items.append(item)
        if item_rows:
            row.amount = items_total
        db.flush()
    # If this update brings the invoice into an issued state and it hasn't
    # been recognised yet, post the AR/AP recognition entry now.
    if row.status in ("issued", "partially_paid", "paid"):
        _recognize_invoice(db, row)
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


def _apply_payment(
    db: Session,
    inv: Invoice,
    *,
    amount: int,
    on: date,
    method: str,
    bank_code: str | None,
    reference: str | None,
    description: str | None,
) -> tuple[Payment, CreditNote | None]:
    """Post a payment against an invoice and update its open balance.

    Sales receipt: DR bank / CR trade debtors (and CR customer-credit for any
    overpayment). Bill payment: DR trade creditors / CR bank (and DR
    supplier-advance for overpayment). The excess is surfaced as a standalone
    entity credit — the invoice never goes to a negative balance.
    """
    if amount <= 0:
        raise HTTPException(status_code=400, detail="Payment amount must be greater than zero.")
    _recognize_invoice(db, inv)
    db.flush()
    _paid, _credited, balance_due = _invoice_totals(db, inv)
    applied = min(amount, balance_due)
    excess = amount - applied
    bank = _resolve_bank_code(db, bank_code)

    if inv.kind == "sales":
        ar = resolve_account_code(db, "ar")
        lines: list[tuple[str, int, int, str | None]] = [(bank, amount, 0, f"Invoice {inv.number} receipt")]
        if applied > 0:
            lines.append((ar, 0, applied, f"Invoice {inv.number} — settle receivable"))
        if excess > 0:
            lines.append((resolve_account_code(db, "customer_credit"), 0, excess, f"Overpayment credit — {inv.number}"))
        direction, role = "in", "client"
    else:
        ap = resolve_account_code(db, "ap")
        lines = []
        if applied > 0:
            lines.append((ap, applied, 0, f"Bill {inv.number} — settle payable"))
        if excess > 0:
            lines.append((resolve_account_code(db, "supplier_advance"), excess, 0, f"Overpayment advance — {inv.number}"))
        lines.append((bank, 0, amount, f"Bill {inv.number} payment"))
        direction, role = "out", "supplier"

    txn = _post_entry(
        db, on=on, reference=(reference or inv.number),
        description=(description or f"Payment for invoice {inv.number}"),
        currency=inv.currency, lines=lines,
        entity_links=[(inv.entity_id, role)] if inv.entity_id else [],
        audit_detail=f"Payment {amount} {inv.currency} on invoice {inv.number}",
    )
    payment = Payment(
        invoice_id=inv.id, date=on, amount=int(amount), currency=inv.currency,
        method=(method or "bank"), direction=direction, transaction_id=txn.id,
    )
    db.add(payment)
    credit: CreditNote | None = None
    if excess > 0:
        credit = CreditNote(
            invoice_id=None, entity_id=inv.entity_id, kind=inv.kind, date=on,
            amount=int(excess), currency=inv.currency,
            reason=f"Overpayment on invoice {inv.number}", note_type="credit",
            transaction_id=txn.id,
        )
        db.add(credit)
    db.flush()
    paid2, credited2, balance2 = _invoice_totals(db, inv)
    _recompute_status(inv, paid2, credited2, balance2)
    return payment, credit


@router.post("/{invoice_id}/payments", response_model=PaymentRead, status_code=201)
def add_payment(invoice_id: UUID, payload: PaymentCreate, db: Session = Depends(get_db)) -> PaymentRead:
    inv = db.get(Invoice, invoice_id)
    if not inv:
        raise HTTPException(status_code=404, detail="Invoice not found")
    if inv.status == "canceled":
        raise HTTPException(status_code=400, detail="Cannot pay a canceled invoice.")
    if payload.currency and payload.currency.strip().upper() != (inv.currency or "").upper():
        raise HTTPException(status_code=400, detail=f"Payment currency must match the invoice ({inv.currency}).")
    try:
        payment, _credit = _apply_payment(
            db, inv,
            amount=int(payload.amount),
            on=payload.date or date.today(),
            method=payload.method,
            bank_code=payload.bank_account_code,
            reference=payload.reference,
            description=payload.description,
        )
    except AccountResolutionError as e:
        db.rollback()
        raise HTTPException(status_code=422, detail=f"Could not post the payment — {e}") from e
    db.commit()
    db.refresh(payment)
    return PaymentRead.model_validate(payment)


@router.get("/{invoice_id}/payments", response_model=list[PaymentRead])
def list_payments(invoice_id: UUID, db: Session = Depends(get_db)) -> list[PaymentRead]:
    if not db.get(Invoice, invoice_id):
        raise HTTPException(status_code=404, detail="Invoice not found")
    rows = db.execute(
        select(Payment).where(Payment.invoice_id == invoice_id).order_by(Payment.date, Payment.created_at)
    ).scalars().all()
    return [PaymentRead.model_validate(r) for r in rows]


@router.post("/{invoice_id}/credit-notes", response_model=CreditNoteRead, status_code=201)
def add_credit_note(invoice_id: UUID, payload: CreditNoteCreate, db: Session = Depends(get_db)) -> CreditNoteRead:
    inv = db.get(Invoice, invoice_id)
    if not inv:
        raise HTTPException(status_code=404, detail="Invoice not found")
    if payload.currency and payload.currency.strip().upper() != (inv.currency or "").upper():
        raise HTTPException(status_code=400, detail=f"Credit note currency must match the invoice ({inv.currency}).")
    amount = int(payload.amount)
    _recognize_invoice(db, inv)
    db.flush()
    _paid, _credited, balance_due = _invoice_totals(db, inv)
    if amount > balance_due:
        raise HTTPException(
            status_code=400,
            detail=f"Credit note ({amount}) exceeds the open balance ({balance_due}).",
        )
    on = payload.date or date.today()
    # Sales credit note: DR sales returns (contra-revenue) / CR trade debtors.
    # Purchase credit note: DR trade creditors / CR purchases (expense).
    try:
        if inv.kind == "sales":
            lines = [
                (resolve_account_code(db, "sales_returns"), amount, 0, f"Credit note — {inv.number}"),
                (resolve_account_code(db, "ar"), 0, amount, f"Credit note reduces receivable — {inv.number}"),
            ]
            role = "client"
        else:
            lines = [
                (resolve_account_code(db, "ap"), amount, 0, f"Credit note reduces payable — {inv.number}"),
                (resolve_account_code(db, "expense"), 0, amount, f"Credit note — {inv.number}"),
            ]
            role = "supplier"
        txn = _post_entry(
            db, on=on, reference=inv.number,
            description=(payload.reason or f"Credit note against invoice {inv.number}"),
            currency=inv.currency, lines=lines,
            entity_links=[(inv.entity_id, role)] if inv.entity_id else [],
            audit_detail=f"Credit note {amount} {inv.currency} against invoice {inv.number}",
        )
    except AccountResolutionError as e:
        db.rollback()
        raise HTTPException(status_code=422, detail=f"Could not post the credit note — {e}") from e
    note = CreditNote(
        invoice_id=inv.id, entity_id=inv.entity_id, kind=inv.kind, date=on,
        amount=amount, currency=inv.currency, reason=payload.reason,
        note_type="reduction", transaction_id=txn.id,
    )
    db.add(note)
    db.flush()
    paid2, credited2, balance2 = _invoice_totals(db, inv)
    _recompute_status(inv, paid2, credited2, balance2)
    db.commit()
    db.refresh(note)
    return CreditNoteRead.model_validate(note)


@router.get("/{invoice_id}/credit-notes", response_model=list[CreditNoteRead])
def list_credit_notes(invoice_id: UUID, db: Session = Depends(get_db)) -> list[CreditNoteRead]:
    if not db.get(Invoice, invoice_id):
        raise HTTPException(status_code=404, detail="Invoice not found")
    rows = db.execute(
        select(CreditNote).where(CreditNote.invoice_id == invoice_id).order_by(CreditNote.date, CreditNote.created_at)
    ).scalars().all()
    return [CreditNoteRead.model_validate(r) for r in rows]


def _reverse_txn(db: Session, transaction_id, *, reference: str, description: str) -> None:
    """Reverse a transaction via the shared ledger machinery (opposite-sign
    lines), skipping anything already reversed."""
    from app.services.ai_accountant.execute_service import _already_reversed
    from app.services.reporting.ledger_service import LedgerService

    if transaction_id is None:
        return
    if _already_reversed(db, str(transaction_id)):
        return
    LedgerService(db).reverse_journal_entry(
        transaction_id=transaction_id, reverse_date=date.today(),
        reference=reference, description=description,
    )
    # Mark the reversal in the audit trail so it isn't double-reversed.
    log_audit_event(
        db, action="undo", entity_type="transaction", entity_id=str(transaction_id),
        detail=description,
    )


@router.post("/{invoice_id}/void", response_model=InvoiceRead)
def void_invoice(invoice_id: UUID, db: Session = Depends(get_db)) -> InvoiceRead:
    """Void an invoice: reverse its recognition entry and every payment via
    the reversal machinery, set status 'voided', and keep the audit trail
    (rows are never hard-deleted)."""
    inv = db.get(Invoice, invoice_id)
    if not inv:
        raise HTTPException(status_code=404, detail="Invoice not found")
    if inv.status == "voided":
        return _to_read(inv)

    # Reverse each payment first (reopen cash), then the recognition entry.
    payments = db.execute(select(Payment).where(Payment.invoice_id == inv.id)).scalars().all()
    for p in payments:
        _reverse_txn(db, p.transaction_id, reference=f"VOID-PAY-{inv.number}",
                     description=f"Void payment on invoice {inv.number}")
    _reverse_txn(db, inv.transaction_id, reference=f"VOID-{inv.number}",
                 description=f"Void invoice {inv.number}")
    inv.status = "voided"
    # Un-bill any time entries billed on this invoice so they can be re-invoiced
    # (atomic with the reversal) — never orphan or double-bill.
    from app.services.time_billing_service import unbill_for_invoice
    reverted = unbill_for_invoice(db, inv.id)
    detail = f"Invoice {inv.number} voided"
    if reverted:
        detail += f"; {reverted} time entr{'y' if reverted == 1 else 'ies'} returned to unbilled"
    log_audit_event(db, action="update", entity_type="invoice", entity_id=str(inv.id),
                    detail=detail)
    db.commit()
    db.refresh(inv)
    return _to_read(inv)


@router.post("/{invoice_id}/payments/{payment_id}/reverse", response_model=InvoiceRead)
def reverse_payment(invoice_id: UUID, payment_id: UUID, db: Session = Depends(get_db)) -> InvoiceRead:
    """Reverse a single payment (chargeback / bounced payment): reverse its
    journal entry and remove it from the invoice so the open balance reopens.
    The reversal transaction stays in the ledger/audit trail."""
    inv = db.get(Invoice, invoice_id)
    if not inv:
        raise HTTPException(status_code=404, detail="Invoice not found")
    payment = db.get(Payment, payment_id)
    if not payment or payment.invoice_id != inv.id:
        raise HTTPException(status_code=404, detail="Payment not found on this invoice")
    _reverse_txn(db, payment.transaction_id, reference=f"CHGBK-{inv.number}",
                 description=f"Reversed payment on invoice {inv.number}")
    db.delete(payment)
    db.flush()
    if inv.status not in ("voided", "canceled"):
        # Drop the 'paid' latch first so _invoice_totals' legacy-paid override
        # doesn't mask the reopened balance; recompute from the real rows.
        inv.status = "issued"
        paid, credited, balance_due = _invoice_totals(db, inv)
        _recompute_status(inv, paid, credited, balance_due)
    log_audit_event(db, action="update", entity_type="invoice", entity_id=str(inv.id),
                    detail=f"Payment reversed on invoice {inv.number}")
    db.commit()
    db.refresh(inv)
    return _to_read(inv)


@router.post("/{invoice_id}/mark-paid", response_model=InvoiceRead)
def mark_invoice_paid(
    invoice_id: UUID,
    payload: MarkInvoicePaidRequest,
    db: Session = Depends(get_db),
) -> InvoiceRead:
    """Thin wrapper over the payment path: settle the full open balance in one
    payment (kept for the existing UI button)."""
    inv = db.get(Invoice, invoice_id)
    if not inv:
        raise HTTPException(status_code=404, detail="Invoice not found")

    # Resolve a bank-entity override into a code, preserving prior behaviour.
    bank_code = (payload.bank_account_code or "").strip() or None
    if payload.bank_entity_id:
        bank_entity = db.get(Entity, payload.bank_entity_id)
        if not bank_entity or (bank_entity.type or "").strip().lower() != "bank":
            raise HTTPException(status_code=400, detail="Selected bank entity not found.")
        entity_code = (bank_entity.code or "").strip()
        if entity_code and db.execute(select(Account.id).where(Account.code == entity_code)).first():
            bank_code = entity_code

    try:
        _recognize_invoice(db, inv)
        db.flush()
        _paid, _credited, balance_due = _invoice_totals(db, inv)
        if balance_due > 0:
            _apply_payment(
                db, inv, amount=balance_due, on=payload.payment_date,
                method=payload.method, bank_code=bank_code,
                reference=payload.reference, description=payload.description,
            )
        else:
            _recompute_status(inv, _paid, _credited, balance_due)
    except AccountResolutionError as e:
        db.rollback()
        raise HTTPException(status_code=422, detail=f"Could not post the payment — {e}") from e
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
    if inv.transaction_id:
        txn = db.get(Transaction, inv.transaction_id)
        if txn:
            events.append(InvoiceTimelineEvent(at=txn.created_at, event="issued", detail=f"AR/AP recognised via transaction {txn.id}."))
    for p in db.execute(select(Payment).where(Payment.invoice_id == invoice_id)).scalars().all():
        events.append(InvoiceTimelineEvent(
            at=p.created_at, event="payment",
            detail=f"{p.amount:,} {p.currency} {'received' if p.direction == 'in' else 'paid'} ({p.method}).",
        ))
    for n in db.execute(select(CreditNote).where(CreditNote.invoice_id == invoice_id)).scalars().all():
        events.append(InvoiceTimelineEvent(
            at=n.created_at, event="credit_note",
            detail=f"Credit note {n.amount:,} {n.currency}" + (f": {n.reason}" if n.reason else "."),
        ))
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
