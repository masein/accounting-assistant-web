from __future__ import annotations

import csv
import io
import json
from datetime import date
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

from fastapi import APIRouter, Depends
from fastapi.responses import Response
from openpyxl import Workbook
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.db.session import get_db
from app.models.entity import Entity
from app.models.invoice import Invoice
from app.models.transaction import Transaction, TransactionLine

router = APIRouter(prefix="/exports", tags=["exports"])

SNAPSHOT_DIR = Path(__file__).resolve().parents[1] / "uploads" / "snapshots"
SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)


def _rows(db: Session) -> list[list[str]]:
    txns = db.execute(
        select(Transaction).options(selectinload(Transaction.lines).selectinload(TransactionLine.account))
    ).scalars().all()
    rows: list[list[str]] = []
    for t in txns:
        for ln in t.lines:
            rows.append([
                str(t.id),
                t.date.isoformat(),
                t.reference or "",
                t.description or "",
                ln.account.code,
                ln.account.name,
                str(ln.debit),
                str(ln.credit),
                ln.line_description or "",
            ])
    return rows


@router.get("/transactions.csv")
def export_transactions_csv(db: Session = Depends(get_db)) -> Response:
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(["transaction_id", "date", "reference", "description", "account_code", "account_name", "debit", "credit", "line_description"])
    for r in _rows(db):
        w.writerow(r)
    csv_bytes = out.getvalue().encode("utf-8")
    headers = {"Content-Disposition": f'attachment; filename="transactions-{date.today().isoformat()}.csv"'}
    return Response(content=csv_bytes, media_type="text/csv", headers=headers)


@router.get("/transactions.xlsx")
def export_transactions_xlsx(db: Session = Depends(get_db)) -> Response:
    wb = Workbook()
    ws = wb.active
    ws.title = "Transactions"
    ws.append(["transaction_id", "date", "reference", "description", "account_code", "account_name", "debit", "credit", "line_description"])
    for r in _rows(db):
        ws.append(r)
    bio = io.BytesIO()
    wb.save(bio)
    headers = {"Content-Disposition": f'attachment; filename="transactions-{date.today().isoformat()}.xlsx"'}
    return Response(
        content=bio.getvalue(),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers=headers,
    )


@router.post("/monthly-snapshot")
def create_monthly_snapshot(db: Session = Depends(get_db)) -> dict:
    month = f"{date.today().year:04d}-{date.today().month:02d}"
    path = SNAPSHOT_DIR / f"snapshot-{month}.zip"
    txns = db.execute(select(Transaction).options(selectinload(Transaction.lines).selectinload(TransactionLine.account))).scalars().all()
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
    return {"ok": True, "snapshot_file": f"/uploads/snapshots/{path.name}"}
