from __future__ import annotations

import re
from datetime import date
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models.entity import Entity
from app.models.recurring import RecurringRule
from app.schemas.recurring import (
    RecurringFromTextRequest,
    RecurringRuleCreate,
    RecurringRuleRead,
    RecurringRuleUpdate,
)

router = APIRouter(prefix="/recurring", tags=["recurring"])


def _parse_amount(text: str) -> int | None:
    t = (text or "").lower().replace(",", "")
    unit_matches = re.findall(r"(\d+(?:\.\d+)?)\s*([mk])\b", t)
    if unit_matches:
        total = 0
        for n_str, unit in unit_matches:
            n = float(n_str)
            total += int(n * (1_000 if unit == "k" else 1_000_000))
        return total
    m = re.search(r"\b(\d+)\b", t)
    if m:
        return int(m.group(1))
    return None


def _normalize_frequency(text: str) -> str:
    t = (text or "").lower()
    if "year" in t or "annual" in t:
        return "yearly"
    return "monthly"


def _direction(text: str) -> str:
    t = (text or "").lower()
    if any(k in t for k in ("receive", "received", "collect")):
        return "receipt"
    return "payment"


def _find_or_create_entity(db: Session, name: str | None, direction: str) -> UUID | None:
    if not name:
        return None
    clean = name.strip()
    if not clean:
        return None
    typ = "client" if direction == "receipt" else "supplier"
    row = db.execute(select(Entity).where(Entity.type == typ, Entity.name.ilike(clean))).scalars().first()
    if row:
        return row.id
    row = Entity(type=typ, name=clean)
    db.add(row)
    db.flush()
    return row.id


@router.get("", response_model=list[RecurringRuleRead])
def list_rules(db: Session = Depends(get_db)) -> list[RecurringRuleRead]:
    rows = db.execute(select(RecurringRule).order_by(RecurringRule.next_run_date, RecurringRule.created_at.desc())).scalars().all()
    return [RecurringRuleRead.model_validate(r) for r in rows]


@router.post("", response_model=RecurringRuleRead, status_code=201)
def create_rule(payload: RecurringRuleCreate, db: Session = Depends(get_db)) -> RecurringRuleRead:
    row = RecurringRule(
        name=payload.name.strip(),
        direction=payload.direction.strip().lower(),
        frequency=payload.frequency.strip().lower(),
        amount=payload.amount,
        start_date=payload.start_date,
        next_run_date=payload.next_run_date,
        entity_id=payload.entity_id,
        bank_name=(payload.bank_name or "").strip() or None,
        reference_prefix=(payload.reference_prefix or "").strip() or None,
        note=(payload.note or "").strip() or None,
        status=payload.status.strip().lower(),
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return RecurringRuleRead.model_validate(row)


@router.post("/from-text", response_model=RecurringRuleRead, status_code=201)
def create_rule_from_text(payload: RecurringFromTextRequest, db: Session = Depends(get_db)) -> RecurringRuleRead:
    text = payload.text.strip()
    if not text:
        raise HTTPException(status_code=400, detail="Text is empty")
    freq = _normalize_frequency(text)
    direction = _direction(text)
    amount = _parse_amount(text)
    bank = None
    m_bank = re.search(r"\bfrom\s+([A-Za-z][A-Za-z0-9\s]{1,20})\s+bank\b", text, re.IGNORECASE)
    if m_bank:
        bank = m_bank.group(1).strip().title()
    m_name = re.search(r"(?:pay|paid|receive|received)\s+([A-Za-z][A-Za-z0-9\s]{1,30})\s", text, re.IGNORECASE)
    party = m_name.group(1).strip().title() if m_name else None
    start = payload.start_date or date.today()
    entity_id = _find_or_create_entity(db, party, direction)
    name = text[:120]
    row = RecurringRule(
        name=name,
        direction=direction,
        frequency=freq,
        amount=amount,
        start_date=start,
        next_run_date=start,
        entity_id=entity_id,
        bank_name=bank,
        note=text,
        status="active",
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return RecurringRuleRead.model_validate(row)


@router.patch("/{rule_id}", response_model=RecurringRuleRead)
def update_rule(rule_id: UUID, payload: RecurringRuleUpdate, db: Session = Depends(get_db)) -> RecurringRuleRead:
    row = db.get(RecurringRule, rule_id)
    if not row:
        raise HTTPException(status_code=404, detail="Recurring rule not found")
    for field in (
        "name", "direction", "frequency", "amount", "start_date", "next_run_date",
        "entity_id", "bank_name", "reference_prefix", "note", "status"
    ):
        val = getattr(payload, field)
        if val is not None:
            setattr(row, field, val)
    db.commit()
    db.refresh(row)
    return RecurringRuleRead.model_validate(row)


@router.delete("/{rule_id}", status_code=204)
def delete_rule(rule_id: UUID, db: Session = Depends(get_db)) -> None:
    row = db.get(RecurringRule, rule_id)
    if not row:
        raise HTTPException(status_code=404, detail="Recurring rule not found")
    db.delete(row)
    db.commit()
