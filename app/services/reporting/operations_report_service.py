from __future__ import annotations

from collections import defaultdict
from datetime import date
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.entity import Entity, TransactionEntity
from app.models.transaction import Transaction, TransactionLine
from app.schemas.manager_report import (
    DebtorCreditorResponse,
    DebtorCreditorRow,
    PersonRunningBalanceResponse,
    PersonRunningBalanceRow,
    ReportPeriod,
)
from app.services.reporting.common import default_period
from app.services.reporting.repository import debtor_creditor_movements


def _aging_bucket(days_old: int) -> str:
    if days_old <= 30:
        return "current"
    if days_old <= 60:
        return "days_31_60"
    if days_old <= 90:
        return "days_61_90"
    return "days_90_plus"


class OperationsReportService:
    def __init__(self, db: Session):
        self.db = db

    def debtor_creditor(self, from_date: date | None, to_date: date | None) -> DebtorCreditorResponse:
        period = default_period(from_date, to_date)
        rows = debtor_creditor_movements(self.db, period.from_date, period.to_date)
        today = period.to_date

        deb: dict[UUID | None, dict] = defaultdict(
            lambda: {"name": "Unassigned", "type": "client", "current": 0, "days_31_60": 0, "days_61_90": 0, "days_90_plus": 0}
        )
        cred: dict[UUID | None, dict] = defaultdict(
            lambda: {"name": "Unassigned", "type": "supplier", "current": 0, "days_31_60": 0, "days_61_90": 0, "days_90_plus": 0}
        )

        for tx_date, role, entity_id, name, delta in rows:
            days_old = max(0, (today - tx_date).days)
            bucket = _aging_bucket(days_old)
            target = deb if role == "debtor" else cred
            target[entity_id]["name"] = name or "Unassigned"
            target[entity_id][bucket] += int(delta or 0)

        debtors: list[DebtorCreditorRow] = []
        creditors: list[DebtorCreditorRow] = []
        total_deb = total_cred = 0
        for entity_id, d in deb.items():
            total = int(d["current"] + d["days_31_60"] + d["days_61_90"] + d["days_90_plus"])
            if total <= 0:
                continue
            total_deb += total
            debtors.append(
                DebtorCreditorRow(
                    entity_id=entity_id,
                    entity_name=d["name"],
                    entity_type=d["type"],
                    current=int(d["current"]),
                    days_31_60=int(d["days_31_60"]),
                    days_61_90=int(d["days_61_90"]),
                    days_90_plus=int(d["days_90_plus"]),
                    total=total,
                )
            )
        for entity_id, d in cred.items():
            total = int(d["current"] + d["days_31_60"] + d["days_61_90"] + d["days_90_plus"])
            if total <= 0:
                continue
            total_cred += total
            creditors.append(
                DebtorCreditorRow(
                    entity_id=entity_id,
                    entity_name=d["name"],
                    entity_type=d["type"],
                    current=int(d["current"]),
                    days_31_60=int(d["days_31_60"]),
                    days_61_90=int(d["days_61_90"]),
                    days_90_plus=int(d["days_90_plus"]),
                    total=total,
                )
            )
        debtors.sort(key=lambda x: x.total, reverse=True)
        creditors.sort(key=lambda x: x.total, reverse=True)
        return DebtorCreditorResponse(
            period=ReportPeriod(from_date=period.from_date, to_date=period.to_date),
            debtors=debtors,
            creditors=creditors,
            totals={"debtors": total_deb, "creditors": total_cred},
        )

    def person_running_balance(
        self,
        entity_id: UUID,
        role: str,
        from_date: date | None,
        to_date: date | None,
    ) -> PersonRunningBalanceResponse:
        period = default_period(from_date, to_date)
        entity = self.db.get(Entity, entity_id)
        if not entity:
            raise HTTPException(status_code=404, detail="Entity not found")
        role_key = (role or "").strip().lower()
        if role_key not in ("client", "supplier", "payee"):
            raise HTTPException(status_code=400, detail="role must be client, supplier, or payee")

        q = (
            select(Transaction, TransactionLine)
            .join(TransactionEntity, TransactionEntity.transaction_id == Transaction.id)
            .join(TransactionLine, TransactionLine.transaction_id == Transaction.id)
            .join(TransactionLine.account)
            .where(
                TransactionEntity.entity_id == entity_id,
                TransactionEntity.role == role_key,
                Transaction.date >= period.from_date,
                Transaction.date <= period.to_date,
            )
            .order_by(Transaction.date, Transaction.created_at, Transaction.id)
        )
        rows = self.db.execute(q).all()
        running = 0
        out: list[PersonRunningBalanceRow] = []
        for txn, line in rows:
            code = line.account.code
            if role_key == "client":
                if code != "1112":
                    continue
                delta = int(line.debit or 0) - int(line.credit or 0)
                running += delta
                out.append(
                    PersonRunningBalanceRow(
                        date=txn.date,
                        transaction_id=txn.id,
                        reference=txn.reference,
                        description=txn.description,
                        debit_effect=max(0, delta),
                        credit_effect=max(0, -delta),
                        running_balance=running,
                    )
                )
            else:
                if not code.startswith("21"):
                    continue
                delta = int(line.credit or 0) - int(line.debit or 0)
                running += delta
                out.append(
                    PersonRunningBalanceRow(
                        date=txn.date,
                        transaction_id=txn.id,
                        reference=txn.reference,
                        description=txn.description,
                        debit_effect=max(0, -delta),
                        credit_effect=max(0, delta),
                        running_balance=running,
                    )
                )
        return PersonRunningBalanceResponse(
            period=ReportPeriod(from_date=period.from_date, to_date=period.to_date),
            entity_id=entity.id,
            entity_name=entity.name,
            role=role_key,
            rows=out,
            closing_balance=running,
        )
