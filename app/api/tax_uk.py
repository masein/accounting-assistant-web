"""UK Making Tax Digital (roadmap 2026-09 §3.6): company MTD settings and the
VAT return boxes 1–9 (app/services/uk_mtd/)."""
from __future__ import annotations

import json
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.services.audit_service import log_audit_event
from app.services.uk_mtd import periods as P
from app.services.uk_mtd import settings as S
from app.services.uk_mtd.vat import vat_return

router = APIRouter(prefix="/tax/uk", tags=["tax"])


def _base_currency(db: Session) -> str:
    from app.services.fx_service import _current_company_row
    row = _current_company_row(db)
    return ((row.base_currency if row is not None else None) or "GBP").upper()


class MtdSettingsPayload(BaseModel):
    income_source: str | None = None
    period_basis: str | None = None
    vat_registered: bool | None = None
    vat_stagger: str | None = None
    vrn: str | None = None
    category_overrides: dict[str, str] | None = None


@router.get("/settings")
def get_settings(db: Session = Depends(get_db)) -> dict:
    return S.get_settings(db)


@router.put("/settings")
def put_settings(payload: MtdSettingsPayload, db: Session = Depends(get_db)) -> dict:
    changes = payload.model_dump(exclude_unset=True)
    try:
        cur = S.save_settings(db, **changes)
    except S.MtdSettingsError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    log_audit_event(db, action="update", entity_type="uk_mtd_settings", entity_id="company",
                    detail=json.dumps({k: v for k, v in changes.items() if k != "category_overrides"}
                                      | ({"category_overrides": len(changes["category_overrides"] or {})}
                                         if "category_overrides" in changes else {}), default=str))
    db.commit()
    return cur


@router.get("/vat/periods")
def vat_periods(db: Session = Depends(get_db)) -> dict:
    conf = S.get_settings(db)
    today = date.today()
    rows = [p.as_dict() | {"days_left": (p.deadline - today).days, "open": p.end >= today}
            for p in P.vat_periods_before(today, conf["vat_stagger"], count=6)]
    return {"vat_registered": conf["vat_registered"], "stagger": conf["vat_stagger"], "periods": rows}


def _period(db: Session, period_end: date) -> P.VatPeriod:
    try:
        return P.vat_period_ending(period_end, S.get_settings(db)["vat_stagger"])
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e


@router.get("/vat/return")
def get_vat_return(period_end: date = Query(...), db: Session = Depends(get_db)) -> dict:
    conf = S.get_settings(db)
    out = vat_return(db, _period(db, period_end), currency=_base_currency(db))
    return out | {"vrn": conf["vrn"], "vat_registered": conf["vat_registered"]}


_BOX_LABELS = {
    "1": "VAT due on sales and other outputs",
    "2": "VAT due on acquisitions from the EU (Northern Ireland)",
    "3": "Total VAT due (box 1 + box 2)",
    "4": "VAT reclaimed on purchases and other inputs",
    "5": "Net VAT to pay to HMRC or reclaim",
    "6": "Total value of sales and other outputs, excluding VAT",
    "7": "Total value of purchases and other inputs, excluding VAT",
    "8": "Total value of dispatches of goods to the EU (Northern Ireland), excluding VAT",
    "9": "Total value of acquisitions of goods from the EU (Northern Ireland), excluding VAT",
}


@router.get("/vat/return/export")
def export_vat_return(period_end: date = Query(...), db: Session = Depends(get_db)) -> Response:
    """The nine boxes as CSV, for bridging software or the records."""
    import csv
    import io
    conf = S.get_settings(db)
    out = vat_return(db, _period(db, period_end), currency=_base_currency(db))
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["VRN", conf["vrn"] or ""])
    w.writerow(["Period", out["period"]["start"], out["period"]["end"]])
    w.writerow(["Due", out["period"]["deadline"]])
    w.writerow([])
    w.writerow(["Box", "Description", f"Amount ({out['currency']})"])
    for n in map(str, range(1, 10)):
        v = out["boxes"][n]
        w.writerow([n, _BOX_LABELS[n], f"{v:.2f}" if int(n) <= 5 else str(int(v))])
    return Response(content=buf.getvalue(), media_type="text/csv",
                    headers={"Content-Disposition": f'attachment; filename="vat-return-{out["period"]["end"]}.csv"'})
