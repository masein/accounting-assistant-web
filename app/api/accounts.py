from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
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
) -> list[AccountRead]:
    rows = db.execute(
        select(Account).order_by(Account.code).offset(skip).limit(limit)
    ).scalars().all()
    return [AccountRead.model_validate(r) for r in rows]


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
