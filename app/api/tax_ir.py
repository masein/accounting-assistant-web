"""Iranian seasonal tax filings (roadmap 2026-09 §3.2): the quarterly
transactions report for TTMS (ماده ۱۶۹ مکرر) and the VAT return figures.
Computed by app/services/tax_ir.py."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.services import tax_ir

router = APIRouter(prefix="/tax/ir", tags=["tax"])


def _season(year: int, season: int) -> tax_ir.Season:
    try:
        return tax_ir.parse_season(year, season)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e


@router.get("/seasons")
def seasons(db: Session = Depends(get_db)) -> list[dict]:
    """The last six Jalali seasons with their filing deadlines and activity."""
    return tax_ir.seasons_overview(db)


@router.get("/quarterly")
def quarterly(year: int = Query(...), season: int = Query(..., ge=1, le=4), include_moadian: bool = False,
              db: Session = Depends(get_db)) -> dict:
    return tax_ir.quarterly_report(db, _season(year, season), include_moadian=include_moadian)


@router.get("/quarterly/export")
def quarterly_export(year: int = Query(...), season: int = Query(..., ge=1, le=4), include_moadian: bool = False,
                     db: Session = Depends(get_db)) -> Response:
    s = _season(year, season)
    body = tax_ir.export_xlsx(db, s, include_moadian=include_moadian)
    return Response(
        content=body,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="ttms-{s.year}-{s.season}.xlsx"'},
    )


@router.get("/vat-return")
def vat_return(year: int = Query(...), season: int = Query(..., ge=1, le=4), db: Session = Depends(get_db)) -> dict:
    s = _season(year, season)
    return {**tax_ir.vat_return(db, s), "reconciliation": tax_ir.reconciliation(db, s)}
