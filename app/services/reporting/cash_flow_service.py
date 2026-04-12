from __future__ import annotations

from collections import defaultdict
from datetime import date

from sqlalchemy.orm import Session

from app.schemas.manager_report import CashFlowResponse
from app.services.reporting.common import default_period
from app.services.reporting.financial_statement_service import build_cash_flow_statement


class CashFlowService:
    def __init__(self, db: Session):
        self.db = db

    def statement(self, from_date: date | None = None, to_date: date | None = None, currency: str | None = None) -> CashFlowResponse:
        return build_cash_flow_statement(self.db, from_date=from_date, to_date=to_date, currency=currency)

    def cash_flow_periods(self, from_date: date | None = None, to_date: date | None = None, granularity: str = "monthly", currency: str | None = None) -> dict:
        """Return cash inflows and outflows grouped by period."""
        from app.services.reporting.repository import transactions_with_lines_between

        period = default_period(from_date, to_date)
        txns = transactions_with_lines_between(self.db, period.from_date, period.to_date, currency=currency)

        inflows: dict[str, int] = defaultdict(int)
        outflows: dict[str, int] = defaultdict(int)
        details: dict[str, list[dict]] = defaultdict(list)

        for txn in txns:
            cash_lines = [ln for ln in txn.lines if (ln.account.code or "").startswith("1110")]
            if not cash_lines:
                continue
            cash_delta = sum((ln.debit or 0) - (ln.credit or 0) for ln in cash_lines)

            if granularity == "weekly":
                key = txn.date.strftime("%Y-W%W")
            elif granularity == "quarterly":
                q = (txn.date.month - 1) // 3 + 1
                key = f"{txn.date.year}-Q{q}"
            elif granularity == "seasonal":
                month = txn.date.month
                if month in (3, 4, 5):
                    key = f"{txn.date.year}-Spring"
                elif month in (6, 7, 8):
                    key = f"{txn.date.year}-Summer"
                elif month in (9, 10, 11):
                    key = f"{txn.date.year}-Autumn"
                else:
                    key = f"{txn.date.year}-Winter"
            else:  # monthly
                key = txn.date.strftime("%Y-%m")

            if cash_delta > 0:
                inflows[key] += int(cash_delta)
            else:
                outflows[key] += int(abs(cash_delta))

            # Collect counterpart account names for description
            counterpart = ", ".join(
                sorted({ln.account.name for ln in txn.lines if not (ln.account.code or "").startswith("1110") and ln.account.name})
            ) or "—"
            details[key].append({
                "date": txn.date.isoformat(),
                "description": txn.description or counterpart,
                "counterpart_accounts": counterpart,
                "amount": int(cash_delta),
                "type": "inflow" if cash_delta > 0 else "outflow",
            })

        all_keys = sorted(set(list(inflows.keys()) + list(outflows.keys())))
        periods = []
        for k in all_keys:
            periods.append({
                "period": k,
                "inflow": inflows.get(k, 0),
                "outflow": outflows.get(k, 0),
                "net": inflows.get(k, 0) - outflows.get(k, 0),
                "transactions": details.get(k, []),
            })

        return {
            "report_type": "cash_flow_periods",
            "granularity": granularity,
            "period": {"from_date": period.from_date.isoformat(), "to_date": period.to_date.isoformat()},
            "periods": periods,
            "totals": {
                "total_inflow": sum(p["inflow"] for p in periods),
                "total_outflow": sum(p["outflow"] for p in periods),
                "net": sum(p["net"] for p in periods),
            },
        }
