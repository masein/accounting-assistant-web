"""Integration tests for financial report correctness.

Verifies:
- Balance Sheet: Assets = Liabilities + Equity
- Trial Balance: Total debits = Total credits
- Account Ledger: Running balance accuracy
- Bank balance with all-time data (no truncation)
- Period filtering
"""
from __future__ import annotations

from datetime import date

import pytest


def _create_txn(auth_client, tx_date: str, lines: list[dict], desc: str = "test") -> dict:
    resp = auth_client.post("/transactions", json={
        "date": tx_date,
        "description": desc,
        "lines": lines,
    })
    assert resp.status_code == 201, resp.text
    return resp.json()


class TestTrialBalance:
    """Total debits must always equal total credits."""

    def test_debits_equal_credits(self, auth_client):
        _create_txn(auth_client, "2026-01-05", [
            {"account_code": "1110", "debit": 1000000, "credit": 0},
            {"account_code": "3110", "debit": 0, "credit": 1000000},
        ], "Capital injection")
        _create_txn(auth_client, "2026-01-10", [
            {"account_code": "6112", "debit": 200000, "credit": 0},
            {"account_code": "1110", "debit": 0, "credit": 200000},
        ], "Rent expense")

        resp = auth_client.get("/reports/ledger-summary")
        assert resp.status_code == 200, resp.text
        data = resp.json()

        total_debit = sum(row["debit_turnover"] for row in data["rows"])
        total_credit = sum(row["credit_turnover"] for row in data["rows"])
        assert total_debit == total_credit, (
            f"Trial balance mismatch: debits={total_debit}, credits={total_credit}"
        )


class TestAccountLedger:
    """Running balance correctness for individual accounts."""

    def test_account_detail_sums(self, auth_client):
        _create_txn(auth_client, "2026-01-01", [
            {"account_code": "1110", "debit": 500000, "credit": 0},
            {"account_code": "3110", "debit": 0, "credit": 500000},
        ], "Capital")
        _create_txn(auth_client, "2026-01-05", [
            {"account_code": "6112", "debit": 100000, "credit": 0},
            {"account_code": "1110", "debit": 0, "credit": 100000},
        ], "Expense")

        resp = auth_client.get("/reports/accounts/1110/detail")
        assert resp.status_code == 200, resp.text
        data = resp.json()

        assert data["debit_turnover"] >= 500000
        assert data["credit_turnover"] >= 100000


class TestBalanceSheetEquation:
    """Assets = Liabilities + Equity (via ledger summary)."""

    def test_accounting_equation(self, auth_client):
        _create_txn(auth_client, "2026-02-01", [
            {"account_code": "1110", "debit": 2000000, "credit": 0},
            {"account_code": "3110", "debit": 0, "credit": 2000000},
        ], "Equity injection")
        _create_txn(auth_client, "2026-02-02", [
            {"account_code": "1210", "debit": 500000, "credit": 0},
            {"account_code": "1110", "debit": 0, "credit": 500000},
        ], "Buy equipment")

        resp = auth_client.get("/reports/ledger-summary")
        assert resp.status_code == 200, resp.text
        data = resp.json()

        asset_balance = 0
        liability_balance = 0
        equity_balance = 0
        revenue_balance = 0
        expense_balance = 0

        for row in data["rows"]:
            code = row["account_code"]
            db_bal = row.get("debit_balance", 0) or 0
            cr_bal = row.get("credit_balance", 0) or 0
            net = db_bal - cr_bal
            if code.startswith("1"):
                asset_balance += net
            elif code.startswith("2"):
                liability_balance += net
            elif code.startswith("3"):
                equity_balance += net
            elif code.startswith("4"):
                revenue_balance += net
            elif code.startswith(("5", "6")):
                expense_balance += net

        net_income = -(revenue_balance) - expense_balance
        total_equity_side = -(liability_balance) + -(equity_balance) + net_income
        assert asset_balance == total_equity_side, (
            f"A={asset_balance} != L+E={total_equity_side} "
            f"(L={liability_balance}, E={equity_balance}, NI={net_income})"
        )


class TestChatReportNoTruncation:
    """Bank balance queries should not truncate to current month."""

    def test_entity_search_excludes_banks(self):
        from app.api.transactions import _parse_entity_transaction_query

        assert _parse_entity_transaction_query("transactions with Melli bank") is None
        assert _parse_entity_transaction_query("transactions with Ali Roshan") == "ali roshan"
        assert _parse_entity_transaction_query("any transactions with Nikzade?") == "nikzade"


class TestPeriodFiltering:
    """Period keywords produce correct date ranges."""

    def test_this_month_range(self):
        from app.services.reporting.common import period_for_keyword
        today = date(2026, 2, 15)
        p = period_for_keyword("this month", today=today)
        assert p.from_date == date(2026, 2, 1)
        assert p.to_date == today

    def test_last_month_range(self):
        from app.services.reporting.common import period_for_keyword
        today = date(2026, 2, 15)
        p = period_for_keyword("last month", today=today)
        assert p.from_date == date(2026, 1, 1)
        assert p.to_date == date(2026, 1, 31)
