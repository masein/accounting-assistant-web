"""Tests for the Iranian-standard financial statements and the reporting-locale setting."""
from __future__ import annotations

import pytest

from app.services.locale_service import (
    DEFAULT_LOCALE,
    get_reporting_locale,
    set_reporting_locale,
)


# ---------------------------------------------------------------------------
# Locale setting
# ---------------------------------------------------------------------------
class TestReportingLocaleService:
    def test_default_is_default(self, db):
        assert get_reporting_locale(db) == DEFAULT_LOCALE

    def test_set_and_get(self, db):
        set_reporting_locale(db, "ir")
        db.commit()
        assert get_reporting_locale(db) == "ir"

    def test_rejects_unsupported(self, db):
        with pytest.raises(ValueError):
            set_reporting_locale(db, "xx")

    def test_idempotent_update(self, db):
        set_reporting_locale(db, "ir")
        set_reporting_locale(db, "default")
        db.commit()
        assert get_reporting_locale(db) == "default"


class TestReportingLocaleAPI:
    def test_get_returns_default(self, auth_client):
        resp = auth_client.get("/admin/reporting-locale")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["locale"] == DEFAULT_LOCALE
        assert "ir" in body["supported"]
        assert "default" in body["supported"]

    def test_put_updates(self, auth_client):
        resp = auth_client.put("/admin/reporting-locale", json={"locale": "ir"})
        assert resp.status_code == 200, resp.text
        assert resp.json()["locale"] == "ir"
        # Read back via GET.
        assert auth_client.get("/admin/reporting-locale").json()["locale"] == "ir"

    def test_put_rejects_unsupported(self, auth_client):
        resp = auth_client.put("/admin/reporting-locale", json={"locale": "zz"})
        assert resp.status_code == 400


# ---------------------------------------------------------------------------
# Iran Income Statement
# ---------------------------------------------------------------------------
def _rows_by_key(body: dict) -> dict:
    return {r["key"]: r for r in body["rows"]}


def _create_txn(auth_client, tx_date: str, lines: list[dict], desc: str = "test") -> None:
    resp = auth_client.post("/transactions", json={
        "date": tx_date,
        "description": desc,
        "lines": lines,
    })
    assert resp.status_code == 201, resp.text


class TestIranIncomeStatement:
    """Uses the seeded chart: 1110 cash, 3110 capital, 4110 sales,
    6110 wages, 6112 other operating, 6210 financial expenses."""

    def test_empty_period_has_all_rows(self, auth_client):
        # No transactions — every row should still be present with 0 amounts.
        resp = auth_client.get(
            "/manager-reports/financial/iran/income-statement",
            params={"from_date": "2026-01-01", "to_date": "2026-12-31"},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["report_type"] == "iran_income_statement"
        assert body["locale"] == "ir"
        rows = _rows_by_key(body)
        # Key structural rows must exist in Iranian order.
        for key in (
            "continuing_ops",
            "revenue_operating",
            "cogs",
            "gross_profit",
            "opex_sga",
            "operating_profit",
            "financial_expenses",
            "profit_before_tax",
            "continuing_net",
            "net_profit",
            "eps_basic",
        ):
            assert key in rows, f"missing row: {key}"
        assert rows["gross_profit"]["row_type"] == "subtotal"
        assert rows["net_profit"]["row_type"] == "total"
        assert rows["continuing_ops"]["row_type"] == "header"

    def test_revenue_and_expenses_populate_buckets(self, auth_client):
        # Use an isolated date window (2030-07) that other test files don't touch,
        # so shared-engine state across tests doesn't pollute our expected totals.
        _create_txn(auth_client, "2024-07-01", [
            {"account_code": "1110", "debit": 10_000_000, "credit": 0},
            {"account_code": "4110", "debit": 0, "credit": 10_000_000},
        ], "Iran IS: sale")
        _create_txn(auth_client, "2024-07-05", [
            {"account_code": "6110", "debit": 3_000_000, "credit": 0},
            {"account_code": "1110", "debit": 0, "credit": 3_000_000},
        ], "Iran IS: wages")
        _create_txn(auth_client, "2024-07-10", [
            {"account_code": "6210", "debit": 500_000, "credit": 0},
            {"account_code": "1110", "debit": 0, "credit": 500_000},
        ], "Iran IS: interest")

        resp = auth_client.get(
            "/manager-reports/financial/iran/income-statement",
            params={"from_date": "2024-07-01", "to_date": "2024-07-31"},
        )
        assert resp.status_code == 200, resp.text
        rows = _rows_by_key(resp.json())

        assert rows["revenue_operating"]["amount_current"] == 10_000_000
        # COGS is 0 (no 51xx accounts in this chart).
        assert rows["cogs"]["amount_current"] == 0
        # Gross profit = revenue - 0 = 10M.
        assert rows["gross_profit"]["amount_current"] == 10_000_000
        # SG&A is negative-presentation, so stored as -3M.
        assert rows["opex_sga"]["amount_current"] == -3_000_000
        assert rows["opex_sga"]["is_negative_presentation"] is True
        # Operating profit = 10M - 3M = 7M.
        assert rows["operating_profit"]["amount_current"] == 7_000_000
        # Financial expenses row is negative-presentation, -500K.
        assert rows["financial_expenses"]["amount_current"] == -500_000
        # Profit before tax = 7M - 500K = 6.5M (no non-operating rows in this chart).
        assert rows["profit_before_tax"]["amount_current"] == 6_500_000
        # No tax rows populated → net continuing = before tax.
        assert rows["continuing_net"]["amount_current"] == 6_500_000
        # Net profit = continuing net (no discontinued ops).
        assert rows["net_profit"]["amount_current"] == 6_500_000

    def test_comparative_period_defaults_to_prior_year(self, auth_client):
        # Isolated date windows to avoid pollution from other test files.
        _create_txn(auth_client, "2023-08-01", [
            {"account_code": "1110", "debit": 2_000_000, "credit": 0},
            {"account_code": "4110", "debit": 0, "credit": 2_000_000},
        ], "Iran IS: prior sale")
        _create_txn(auth_client, "2024-08-01", [
            {"account_code": "1110", "debit": 4_000_000, "credit": 0},
            {"account_code": "4110", "debit": 0, "credit": 4_000_000},
        ], "Iran IS: current sale")

        resp = auth_client.get(
            "/manager-reports/financial/iran/income-statement",
            params={"from_date": "2024-08-01", "to_date": "2024-08-31"},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        # Default comparative = one year earlier.
        assert body["comparative_period"]["from"] == "2023-08-01"
        assert body["comparative_period"]["to"] == "2023-08-31"
        rows = _rows_by_key(body)
        assert rows["revenue_operating"]["amount_current"] == 4_000_000
        assert rows["revenue_operating"]["amount_prior"] == 2_000_000
        # (4M - 2M) / 2M * 100 = 100%
        assert rows["revenue_operating"]["change_pct"] == 100.0

    def test_change_pct_is_null_when_prior_is_zero(self, auth_client):
        # Isolated window where no prior-year activity exists.
        _create_txn(auth_client, "2024-09-01", [
            {"account_code": "1110", "debit": 5_000_000, "credit": 0},
            {"account_code": "4110", "debit": 0, "credit": 5_000_000},
        ], "Iran IS: only current")

        resp = auth_client.get(
            "/manager-reports/financial/iran/income-statement",
            params={"from_date": "2024-09-01", "to_date": "2024-09-30"},
        )
        rows = _rows_by_key(resp.json())
        assert rows["revenue_operating"]["amount_prior"] == 0
        assert rows["revenue_operating"]["change_pct"] is None

    def test_eps_rows_are_null(self, auth_client):
        resp = auth_client.get(
            "/manager-reports/financial/iran/income-statement",
            params={"from_date": "2026-01-01", "to_date": "2026-01-31"},
        )
        rows = _rows_by_key(resp.json())
        for key in ("eps_basic", "eps_operating", "eps_non_operating", "eps_net_per_share"):
            assert rows[key]["amount_current"] is None
            assert rows[key]["amount_prior"] is None

    def test_bucket_mapping_unit(self):
        """Unit test the prefix routing without touching the DB."""
        from app.services.reporting.iran_statement_service import _bucket_for_code

        cases = {
            "4110": "revenue_operating",
            "4210": "revenue_operating",
            "4310": "other_operating_income",
            "5110": "cogs",
            "5210": "cogs",
            "6110": "opex_sga",
            "6115": "impairment_receivables",  # more-specific prefix must win over 61
            "6210": "financial_expenses",      # 621 must win over 62
            "6220": "other_operating_expenses",
            "6310": "non_operating_net",
            "6410": "tax_current_year",
            "6420": "tax_prior_years",
            "6810": "discontinued_ops",
            "1110": None,  # balance-sheet account
            "3110": None,  # equity
        }
        for code, expected in cases.items():
            assert _bucket_for_code(code) == expected, f"{code} -> expected {expected}"
