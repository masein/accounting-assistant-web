from __future__ import annotations

import io
import re
import uuid
import json
from datetime import date
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse, Response
from openpyxl import Workbook
from openpyxl.styles import Font
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.core.http_headers import content_disposition
from app.core.spreadsheet import CSV_MEDIA_TYPE, csv_bytes, csv_writer, no_formulas
from app.db.session import get_db
from app.models.entity import Entity
from app.models.invoice import Invoice
from app.models.transaction import Transaction, TransactionLine

router = APIRouter(prefix="/exports", tags=["exports"])

# Snapshots hold a company's whole books, so they live OUTSIDE the public
# /uploads mount, in a per-company folder, and are only served through the
# authenticated download route below (security review 2026-09-24, C1: the old
# uploads/snapshots/snapshot-YYYY-MM.zip was world-readable and shared by every
# tenant).
SNAPSHOT_DIR = Path(__file__).resolve().parents[1] / "private_uploads" / "snapshots"
SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
_SNAPSHOT_NAME = re.compile(r"^snapshot-\d{4}-\d{2}-[0-9a-f]{8}\.zip$")


def _snapshot_folder() -> Path:
    from app.db.tenant import get_current_company
    folder = SNAPSHOT_DIR / str(get_current_company() or "platform")
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def _rows(db: Session, currency: str | None = None) -> list[list[str]]:
    q = (
        select(Transaction)
        .where(Transaction.deleted_at.is_(None))  # undone/replaced journals are not books
        .options(selectinload(Transaction.lines).selectinload(TransactionLine.account))
    )
    if currency:
        q = q.where(Transaction.currency == currency)
    txns = db.execute(q).scalars().all()
    # typed values: the workbook gets real dates and numbers, the CSV writer prints them as before
    rows: list[list] = []
    for t in txns:
        for ln in t.lines:
            rows.append([
                str(t.id),
                t.date,
                t.reference or "",
                t.description or "",
                ln.account.code,
                ln.account.name,
                int(ln.debit or 0),
                int(ln.credit or 0),
                ln.line_description or "",
                getattr(t, "currency", "IRR"),
            ])
    return rows


@router.get("/transactions.csv")
def export_transactions_csv(
    currency: str | None = Query(None, description="Filter by currency (IRR, USD, etc.)"),
    db: Session = Depends(get_db),
) -> Response:
    out = io.StringIO()
    w = csv_writer(out)
    w.writerow(["transaction_id", "date", "reference", "description", "account_code", "account_name", "debit", "credit", "line_description", "currency"])
    for r in _rows(db, currency):
        w.writerow(r)
    headers = {"Content-Disposition": content_disposition(f'transactions-{date.today().isoformat()}.csv')}
    return Response(content=csv_bytes(out), media_type=CSV_MEDIA_TYPE, headers=headers)


@router.get("/transactions.xlsx")
def export_transactions_xlsx(
    currency: str | None = Query(None, description="Filter by currency (IRR, USD, etc.)"),
    db: Session = Depends(get_db),
) -> Response:
    wb = Workbook()
    ws = wb.active
    ws.title = "Transactions"
    ws.append(["transaction_id", "date", "reference", "description", "account_code", "account_name", "debit", "credit", "line_description", "currency"])
    for c in ws[1]:
        c.font = Font(bold=True)
    for r in _rows(db, currency):
        ws.append(r)
        ws.cell(ws.max_row, 2).number_format = "yyyy-mm-dd"
        for col in (7, 8):
            ws.cell(ws.max_row, col).number_format = "#,##0"
    ws.freeze_panes = "A2"
    for col, width in zip("ABCDEFGHIJ", (38, 12, 16, 40, 12, 28, 16, 16, 32, 10)):
        ws.column_dimensions[col].width = width
    bio = io.BytesIO()
    no_formulas(wb).save(bio)
    headers = {"Content-Disposition": content_disposition(f'transactions-{date.today().isoformat()}.xlsx')}
    return Response(
        content=bio.getvalue(),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers=headers,
    )


@router.post("/monthly-snapshot")
def create_monthly_snapshot(db: Session = Depends(get_db)) -> dict:
    month = f"{date.today().year:04d}-{date.today().month:02d}"
    path = _snapshot_folder() / f"snapshot-{month}-{uuid.uuid4().hex[:8]}.zip"
    txns = db.execute(
        select(Transaction).where(Transaction.deleted_at.is_(None))
        .options(selectinload(Transaction.lines).selectinload(TransactionLine.account))
    ).scalars().all()
    entities = db.execute(select(Entity)).scalars().all()
    invoices = db.execute(select(Invoice)).scalars().all()
    with ZipFile(path, "w", compression=ZIP_DEFLATED) as z:
        z.writestr("transactions.json", json.dumps([
            {
                "id": str(t.id),
                "date": t.date.isoformat(),
                "reference": t.reference,
                "description": t.description,
                "lines": [
                    {"account_code": ln.account.code, "debit": ln.debit, "credit": ln.credit, "line_description": ln.line_description}
                    for ln in t.lines
                ],
            }
            for t in txns
        ], ensure_ascii=False, indent=2))
        z.writestr("entities.json", json.dumps([
            {"id": str(e.id), "type": e.type, "name": e.name, "code": e.code}
            for e in entities
        ], ensure_ascii=False, indent=2))
        z.writestr("invoices.json", json.dumps([
            {"id": str(i.id), "number": i.number, "kind": i.kind, "status": i.status, "issue_date": i.issue_date.isoformat(), "due_date": i.due_date.isoformat(), "amount": i.amount}
            for i in invoices
        ], ensure_ascii=False, indent=2))
    return {"ok": True, "snapshot_file": f"/exports/monthly-snapshot/{path.name}"}


@router.get("/monthly-snapshot/{name}")
def download_monthly_snapshot(name: str) -> FileResponse:
    """Serve a snapshot of the CALLER'S company only (session-authenticated)."""
    if not _SNAPSHOT_NAME.match(name):
        raise HTTPException(status_code=404, detail="Snapshot not found")
    path = (_snapshot_folder() / name).resolve()
    if path.parent != _snapshot_folder().resolve() or not path.is_file():
        raise HTTPException(status_code=404, detail="Snapshot not found")
    return FileResponse(path, media_type="application/zip", filename=name)
