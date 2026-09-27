from __future__ import annotations

from uuid import UUID

from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models.account import Account
from app.schemas.account import AccountRead

router = APIRouter(prefix="/accounts", tags=["accounts"])


@router.get("", response_model=list[AccountRead])
def list_accounts(
    db: Session = Depends(get_db),
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
    include_inactive: bool = Query(False, description="Pickers leave deactivated accounts out; the chart shows them."),
) -> list[AccountRead]:
    q = select(Account).order_by(Account.code)
    if not include_inactive:
        q = q.where(Account.is_active.is_(True))
    rows = db.execute(q.offset(skip).limit(limit)).scalars().all()
    return [AccountRead.model_validate(r) for r in rows]


# --- Chart management (roadmap §4.5) --------------------------------------------------

class AccountCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=512)
    parent_code: str | None = Field(None, max_length=16)
    code: str | None = Field(None, max_length=16)
    detail_type: str | None = Field(None, max_length=128)


class AccountUpdate(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=512)
    detail_type: str | None = Field(None, max_length=128)
    is_active: bool | None = None


class OpeningLine(BaseModel):
    account_code: str = Field(..., max_length=16)
    debit: int = Field(0, ge=0, le=10**15)
    credit: int = Field(0, ge=0, le=10**15)


class OpeningIn(BaseModel):
    on: date
    currency: str | None = Field(None, max_length=8)
    lines: list[OpeningLine] = Field(default_factory=list, max_length=2000)


def _audit(db, action, acc, detail):
    from app.services.audit_service import log_audit_event
    log_audit_event(db, action=action, entity_type="account", entity_id=str(acc.id), detail=detail)


@router.get("/tree")
def account_tree(include_inactive: bool = True, db: Session = Depends(get_db)) -> dict:
    from app.services.chart_service import tree
    return {"accounts": tree(db, include_inactive=include_inactive)}


@router.get("/suggest-code/{parent_code}")
def suggest_child_code(parent_code: str, db: Session = Depends(get_db)) -> dict:
    from app.services.chart_service import suggest_code
    parent = db.execute(select(Account).where(Account.code == parent_code.strip())).scalars().one_or_none()
    if parent is None:
        raise HTTPException(status_code=404, detail="Account not found")
    return {"parent_code": parent.code, "code": suggest_code(db, parent)}


@router.post("", response_model=AccountRead, status_code=201)
def create_account(body: AccountCreate, db: Session = Depends(get_db)) -> AccountRead:
    from app.services.chart_service import create_account as _create
    acc = _create(db, name=body.name, parent_code=body.parent_code, code=body.code, detail_type=body.detail_type)
    _audit(db, "create", acc, f"{acc.code} {acc.name}")
    db.commit()
    return AccountRead.model_validate(acc)


@router.get("/opening-balances")
def get_opening_balances(db: Session = Depends(get_db)) -> dict:
    from app.services.chart_service import get_opening
    return get_opening(db)


@router.put("/opening-balances")
def put_opening_balances(body: OpeningIn, db: Session = Depends(get_db)) -> dict:
    from app.services.audit_service import log_audit_event
    from app.services.chart_service import set_opening
    out = set_opening(db, on=body.on, lines=[ln.model_dump() for ln in body.lines], currency=body.currency)
    log_audit_event(db, action="update", entity_type="opening_balances", entity_id=out["transaction_id"] or "none",
                    detail=f"{out['lines']} line(s) on {out['date']}, adjustment {out['adjustment']}")
    db.commit()
    return out


@router.get("/by-code/{code}", response_model=AccountRead)
def get_account_by_code(
    code: str,
    db: Session = Depends(get_db),
) -> AccountRead:
    acc = db.execute(select(Account).where(Account.code == code.strip())).scalars().one_or_none()
    if not acc:
        raise HTTPException(status_code=404, detail="Account not found")
    return AccountRead.model_validate(acc)


@router.get("/{account_id}", response_model=AccountRead)
def get_account(
    account_id: UUID,
    db: Session = Depends(get_db),
) -> AccountRead:
    acc = db.get(Account, account_id)
    if not acc:
        raise HTTPException(status_code=404, detail="Account not found")
    return AccountRead.model_validate(acc)


@router.patch("/{account_id}", response_model=AccountRead)
def update_account(account_id: UUID, body: AccountUpdate, db: Session = Depends(get_db)) -> AccountRead:
    from app.services.chart_service import rename, set_active
    acc = db.get(Account, account_id)
    if not acc:
        raise HTTPException(status_code=404, detail="Account not found")
    changes = body.model_dump(exclude_unset=True)
    if "is_active" in changes and changes["is_active"] is not None and changes["is_active"] != acc.is_active:
        set_active(db, acc, changes["is_active"])          # checks first: nothing else changes on a refusal
    if "name" in changes or "detail_type" in changes:
        rename(db, acc, name=changes.get("name"), detail_type=changes.get("detail_type"),
               clear_detail_type="detail_type" in changes and not changes["detail_type"])
    _audit(db, "update", acc, f"{acc.code}: " + ", ".join(sorted(changes)))
    db.commit()
    db.refresh(acc)
    return AccountRead.model_validate(acc)


@router.delete("/{account_id}", status_code=204)
def delete_account(account_id: UUID, db: Session = Depends(get_db)) -> None:
    from app.services.chart_service import delete_account as _delete
    acc = db.get(Account, account_id)
    if not acc:
        raise HTTPException(status_code=404, detail="Account not found")
    what = f"{acc.code} {acc.name}"
    from app.services.audit_service import log_audit_event
    _delete(db, acc)                                       # refuses before anything is written
    log_audit_event(db, action="delete", entity_type="account", entity_id=str(account_id), detail=what)
    db.commit()
