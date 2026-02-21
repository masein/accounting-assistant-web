from __future__ import annotations

from datetime import date

from sqlalchemy.orm import Session

from app.schemas.manager_report import CashFlowResponse
from app.services.reporting.financial_statement_service import build_cash_flow_statement


class CashFlowService:
    def __init__(self, db: Session):
        self.db = db

    def statement(self, from_date: date | None = None, to_date: date | None = None) -> CashFlowResponse:
        return build_cash_flow_statement(self.db, from_date=from_date, to_date=to_date)
