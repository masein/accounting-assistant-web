"""Fixed-asset register (roadmap 2026-09 §4.3): asset cards, the month-end
depreciation run, disposal and the register report."""
from __future__ import annotations

from datetime import date
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models.fixed_asset import FixedAsset
from app.services import fixed_assets as svc
from app.services.audit_service import log_audit_event

router = APIRouter(prefix="/fixed-assets", tags=["fixed-assets"])

_MAX = 10**15


class AssetCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=256)
    category: str = Field("other", max_length=48)
    cost: int = Field(..., gt=0, le=_MAX)
    residual: int = Field(0, ge=0, le=_MAX)
    acquired_on: date
    in_service_on: date | None = None
    depreciation_start: date | None = None
    method: str | None = Field(None, pattern="^(straight_line|declining_balance)$")
    life_months: int | None = Field(None, ge=1, le=1200)
    rate_bps: int | None = Field(None, ge=1, le=10_000)
    opening_accumulated: int = Field(0, ge=0, le=_MAX)
    opening_date: date | None = None
    currency: str | None = Field(None, max_length=8)
    entity_id: UUID | None = None
    serial_number: str | None = Field(None, max_length=128)
    location: str | None = Field(None, max_length=128)
    description: str | None = Field(None, max_length=2000)
    asset_account_code: str | None = Field(None, max_length=16)
    accumulated_account_code: str | None = Field(None, max_length=16)
    expense_account_code: str | None = Field(None, max_length=16)
    acquisition: str = Field("none", pattern="^(none|bank|payable)$")
    bank_account_code: str | None = Field(None, max_length=16)


class AssetUpdate(BaseModel):
    """Details can always change; the depreciation terms only until a month
    has been posted."""
    name: str | None = Field(None, min_length=1, max_length=256)
    serial_number: str | None = Field(None, max_length=128)
    location: str | None = Field(None, max_length=128)
    description: str | None = Field(None, max_length=2000)
    entity_id: UUID | None = None
    residual: int | None = Field(None, ge=0, le=_MAX)
    method: str | None = Field(None, pattern="^(straight_line|declining_balance)$")
    life_months: int | None = Field(None, ge=1, le=1200)
    rate_bps: int | None = Field(None, ge=1, le=10_000)
    depreciation_start: date | None = None


class RunRequest(BaseModel):
    through: date | None = None


class DisposeRequest(BaseModel):
    on: date
    proceeds: int = Field(0, ge=0, le=_MAX)
    bank_account_code: str | None = Field(None, max_length=16)


def _get(db: Session, asset_id: UUID) -> FixedAsset:
    a = db.get(FixedAsset, asset_id)
    if a is None:
        raise HTTPException(status_code=404, detail="Asset not found")
    return a


def _read(db: Session, a: FixedAsset) -> dict:
    cal = svc.calendar_for(svc._locale(db))
    row = svc.asset_row(db, a, cal, as_of=date.today())
    row["schedule"] = svc.schedule_rows(db, a)
    row["has_postings"] = svc.has_postings(db, a)
    return row


@router.get("")
def list_assets(as_of: date | None = Query(None), include_disposed: bool = True,
                db: Session = Depends(get_db)) -> dict:
    return svc.register(db, as_of=as_of, include_disposed=include_disposed)


@router.get("/categories")
def list_categories(db: Session = Depends(get_db)) -> dict:
    locale = svc._locale(db)
    return {"locale": locale, "calendar": svc.calendar_for(locale),
            "start_rule": "next_month" if locale == "ir" else "same_month",
            "accounts": svc.DEFAULT_ACCOUNTS[locale],
            "categories": [{"key": c.key, "label": c.label, "method": c.method, "life_months": c.life_months,
                            "rate_bps": c.rate_bps, "statutory": c.statutory,
                            "asset_account": c.asset_account or svc.DEFAULT_ACCOUNTS[locale]["asset"],
                            "accumulated_account": c.accumulated_account or svc.DEFAULT_ACCOUNTS[locale]["accumulated"]}
                           for c in svc.categories(locale)]}


@router.post("", status_code=201)
def create_asset(body: AssetCreate, db: Session = Depends(get_db)) -> dict:
    a = svc.create_asset(db, body.model_dump())
    log_audit_event(db, action="create", entity_type="fixed_asset", entity_id=str(a.id),
                    detail=f"{a.number} {a.name}")
    db.commit()
    return _read(db, a)


@router.get("/depreciation-run")
def preview_run(through: date | None = Query(None), db: Session = Depends(get_db)) -> dict:
    return svc.run(db, through, preview=True)


@router.post("/depreciation-run")
def post_run(body: RunRequest, db: Session = Depends(get_db)) -> dict:
    out = svc.run(db, body.through)
    if out["journals"]:
        log_audit_event(db, action="create", entity_type="depreciation_run", entity_id=out["through"],
                        detail=f"{out['months']} asset-month(s), {len(out['journals'])} journal(s)")
    db.commit()
    return out


@router.get("/{asset_id}")
def get_asset(asset_id: UUID, db: Session = Depends(get_db)) -> dict:
    return _read(db, _get(db, asset_id))


@router.patch("/{asset_id}")
def update_asset(asset_id: UUID, body: AssetUpdate, db: Session = Depends(get_db)) -> dict:
    a = _get(db, asset_id)
    changes = body.model_dump(exclude_unset=True)
    terms = {"residual", "method", "life_months", "rate_bps", "depreciation_start"} & set(changes)
    if terms and (a.status != "active" or svc.has_postings(db, a)):
        raise HTTPException(status_code=409, detail="Depreciation has been posted for this asset — its terms "
                                                    "can't change. Dispose of it and register it again instead.")
    method = changes.get("method", a.method)
    life = changes.get("life_months", a.life_months)
    rate = changes.get("rate_bps", a.rate_bps)
    if terms:
        if method == "straight_line":
            rate = None
        else:
            life = None
        # validate before touching the row: the session is shared on a 4xx
        svc.validate_terms(method, life, rate, int(a.cost), int(changes.get("residual", a.residual) or 0),
                           int(a.opening_accumulated or 0))
    if "name" in changes and not (changes["name"] or "").strip():
        raise HTTPException(status_code=422, detail="An asset needs a name.")
    for key in ("name", "serial_number", "location", "description", "entity_id"):
        if key in changes:
            setattr(a, key, changes[key].strip() if key == "name" else changes[key])
    if terms:
        a.method, a.life_months, a.rate_bps = method, life, rate
        if "residual" in changes:
            a.residual = changes["residual"]
        if changes.get("depreciation_start"):
            a.depreciation_start = svc.month_start(changes["depreciation_start"], svc.calendar_for(svc._locale(db)))
    log_audit_event(db, action="update", entity_type="fixed_asset", entity_id=str(a.id), detail=a.number)
    db.commit()
    return _read(db, a)


@router.delete("/{asset_id}", status_code=204)
def delete_asset(asset_id: UUID, db: Session = Depends(get_db)) -> None:
    """Only an asset registered by mistake: nothing posted for it at all."""
    a = _get(db, asset_id)
    if svc.has_postings(db, a) or a.acquisition_transaction_id or a.disposal_transaction_id:
        raise HTTPException(status_code=409, detail="This asset has entries in the books — dispose of it instead.")
    log_audit_event(db, action="delete", entity_type="fixed_asset", entity_id=str(a.id), detail=a.number)
    db.delete(a)
    db.commit()


@router.post("/{asset_id}/dispose/preview")
def preview_dispose(asset_id: UUID, body: DisposeRequest, db: Session = Depends(get_db)) -> dict:
    return svc.dispose(db, _get(db, asset_id), on=body.on, proceeds=body.proceeds,
                       bank_account_code=body.bank_account_code, preview=True)


@router.post("/{asset_id}/dispose")
def dispose_asset(asset_id: UUID, body: DisposeRequest, db: Session = Depends(get_db)) -> dict:
    a = _get(db, asset_id)
    out = svc.dispose(db, a, on=body.on, proceeds=body.proceeds, bank_account_code=body.bank_account_code)
    log_audit_event(db, action="update", entity_type="fixed_asset", entity_id=str(a.id),
                    detail=f"{a.number} disposed on {body.on.isoformat()}")
    db.commit()
    out["asset"] = _read(db, a)
    return out
