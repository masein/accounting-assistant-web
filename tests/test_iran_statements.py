"""Tests for the Iranian-standard financial statements and the reporting-locale setting."""
from __future__ import annotations

import pytest

from app.services.locale_service import (
    DEFAULT_LOCALE,
    get_reporting_locale,
    set_reporting_locale,
)


def _purge_transactions(db):
    """Wipe Transaction + TransactionLine + TransactionEntity so balance-sheet
    absolute-amount assertions stay deterministic under the shared-engine test
    DB (other tests' commits persist across fixture rollbacks).
    """
    from sqlalchemy import delete
    from app.models.transaction import Transaction, TransactionLine
    from app.models.entity import TransactionEntity

    db.execute(delete(TransactionEntity))
    db.execute(delete(TransactionLine))
    db.execute(delete(Transaction))
    db.commit()


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


# ---------------------------------------------------------------------------
# Iran Balance Sheet
# ---------------------------------------------------------------------------
class TestIranBalanceSheet:
    def test_empty_bs_has_all_rows(self, auth_client):
        resp = auth_client.get(
            "/manager-reports/financial/iran/balance-sheet",
            params={"as_of": "2024-10-31"},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["report_type"] == "iran_balance_sheet"
        assert body["locale"] == "ir"
        rows = _rows_by_key(body)
        # Core section headers and totals.
        for key in (
            "assets_section",
            "nca_section",
            "ca_section",
            "total_nca",
            "total_ca",
            "total_assets",
            "eq_liab_section",
            "equity_section",
            "total_equity",
            "ncl_section",
            "cl_section",
            "total_cl",
            "total_liabilities",
            "total_equity_and_liabilities",
        ):
            assert key in rows, f"missing row: {key}"
        # Specific prescribed line items.
        for key in (
            "nca_ppe", "nca_intangibles", "nca_lt_investments",
            "ca_cash", "ca_trade_receivables", "ca_inventory",
            "eq_capital", "eq_legal_reserve", "eq_retained_earnings",
            "cl_trade_payables", "cl_tax_payable",
        ):
            assert key in rows, f"missing line: {key}"
        assert rows["total_assets"]["row_type"] == "total"
        assert rows["total_equity_and_liabilities"]["row_type"] == "total"

    def test_bs_balances(self, auth_client, db):
        # Absolute-amount assertions (cash = 3M, PP&E = 2M, …) need a clean DB.
        _purge_transactions(db)
        # Capital injection: cash 5M DR / capital 5M CR
        _create_txn(auth_client, "2024-11-01", [
            {"account_code": "1110", "debit": 5_000_000, "credit": 0},
            {"account_code": "3110", "debit": 0, "credit": 5_000_000},
        ], "Iran BS: capital")
        # Buy PP&E for cash: PP&E 2M DR / cash 2M CR
        _create_txn(auth_client, "2024-11-02", [
            {"account_code": "1210", "debit": 2_000_000, "credit": 0},
            {"account_code": "1110", "debit": 0, "credit": 2_000_000},
        ], "Iran BS: buy PPE")
        # Borrow from supplier (AP): AR 1M DR / AP 1M CR  → creates a receivable and payable
        _create_txn(auth_client, "2024-11-03", [
            {"account_code": "1112", "debit": 1_000_000, "credit": 0},
            {"account_code": "2110", "debit": 0, "credit": 1_000_000},
        ], "Iran BS: AR+AP")

        resp = auth_client.get(
            "/manager-reports/financial/iran/balance-sheet",
            params={"as_of": "2024-11-30"},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        rows = _rows_by_key(body)
        # cash 5M - 2M = 3M (code 1110 → ca_cash bucket via prefix 111)
        assert rows["ca_cash"]["amount_current"] == 3_000_000
        # trade receivable 1M (code 1112 → ca_trade_receivables via prefix 112)
        assert rows["ca_trade_receivables"]["amount_current"] == 1_000_000
        # PP&E 2M (code 1210 → nca_ppe via prefix 121)
        assert rows["nca_ppe"]["amount_current"] == 2_000_000
        # capital 5M (code 3110 → eq_capital via prefix 311)
        assert rows["eq_capital"]["amount_current"] == 5_000_000
        # trade payable 1M (code 2110 → cl_trade_payables via prefix 211)
        assert rows["cl_trade_payables"]["amount_current"] == 1_000_000
        # Fundamental accounting equation: total assets = total equity + total liabilities
        assert rows["total_assets"]["amount_current"] == rows["total_equity_and_liabilities"]["amount_current"]
        assert body["metadata"]["balances"]["assets_equal_equity_plus_liabilities"] is True

    def test_bs_comparative_defaults_to_prior_year(self, auth_client, db):
        _purge_transactions(db)
        # Use an isolated year pair (2020/2021) that no other test touches,
        # so as-of queries see only this test's postings.
        _create_txn(auth_client, "2020-10-01", [
            {"account_code": "1110", "debit": 1_000_000, "credit": 0},
            {"account_code": "3110", "debit": 0, "credit": 1_000_000},
        ], "Iran BS: prior year capital")
        _create_txn(auth_client, "2021-10-01", [
            {"account_code": "1110", "debit": 2_000_000, "credit": 0},
            {"account_code": "3110", "debit": 0, "credit": 2_000_000},
        ], "Iran BS: current year capital")

        resp = auth_client.get(
            "/manager-reports/financial/iran/balance-sheet",
            params={"as_of": "2021-12-15"},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["as_of"] == "2021-12-15"
        assert body["comparative_as_of"] == "2020-12-15"
        rows = _rows_by_key(body)
        # Current cash = 3M (cumulative)
        assert rows["ca_cash"]["amount_current"] == 3_000_000
        # Prior-year cash at 2020-12-15 = 1M (only the 2020 txn had posted)
        assert rows["ca_cash"]["amount_prior"] == 1_000_000
        # (3M - 1M) / 1M * 100 = 200%
        assert rows["ca_cash"]["change_pct"] == 200.0

    def test_bs_bucket_prefix_routing(self):
        from app.services.reporting.iran_statement_service import _bs_bucket_for_code

        cases = {
            "1110": ("current_assets", "ca_cash"),
            "1112": ("current_assets", "ca_trade_receivables"),
            "1140": ("current_assets", "ca_inventory"),
            "1180": ("current_assets", "ca_other"),           # 118x falls through to ca_other
            "1210": ("non_current_assets", "nca_ppe"),
            "1230": ("non_current_assets", "nca_intangibles"),
            "1290": ("non_current_assets", "nca_other"),
            "2110": ("current_liabilities", "cl_trade_payables"),
            "2150": ("current_liabilities", "cl_st_loans"),
            "2190": ("current_liabilities", "cl_other"),
            "2220": ("non_current_liabilities", "ncl_lt_loans"),
            "2270": ("non_current_liabilities", "ncl_employee_benefits"),
            "3110": ("equity", "eq_capital"),
            "3210": ("equity", "eq_legal_reserve"),
            "3300": ("equity", "eq_retained_earnings"),
            "3400": ("equity", "eq_treasury_stock"),
            "4110": None,  # income statement
            "6110": None,
        }
        for code, expected in cases.items():
            assert _bs_bucket_for_code(code) == expected, f"{code} → expected {expected}"


# ---------------------------------------------------------------------------
# Statement of Changes in Equity
# ---------------------------------------------------------------------------
class TestIranChangesInEquity:
    def test_empty_period_has_standard_rows(self, auth_client):
        resp = auth_client.get(
            "/manager-reports/financial/iran/changes-in-equity",
            params={"from_date": "2024-01-01", "to_date": "2024-01-31"},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["report_type"] == "iran_changes_in_equity"
        assert body["locale"] == "ir"
        # Components are always the 10 prescribed Iranian equity columns.
        component_keys = [c["key"] for c in body["components"]]
        for key in (
            "eq_capital",
            "eq_legal_reserve",
            "eq_retained_earnings",
            "eq_treasury_stock",
        ):
            assert key in component_keys
        row_keys = {r["key"]: r for r in body["rows"]}
        for key in (
            "opening_balance",
            "restated_opening",
            "net_profit_reported",
            "total_comprehensive_income",
            "approved_dividends",
            "capital_increase",
            "closing_balance",
        ):
            assert key in row_keys
        assert row_keys["closing_balance"]["row_type"] == "total"
        assert row_keys["restated_opening"]["row_type"] == "subtotal"

    def test_net_profit_flows_into_retained_earnings(self, auth_client):
        # Period activity: 10M revenue, 4M wages → 6M net profit.
        _create_txn(auth_client, "2024-12-01", [
            {"account_code": "1110", "debit": 10_000_000, "credit": 0},
            {"account_code": "4110", "debit": 0, "credit": 10_000_000},
        ], "Iran CE: sale")
        _create_txn(auth_client, "2024-12-05", [
            {"account_code": "6110", "debit": 4_000_000, "credit": 0},
            {"account_code": "1110", "debit": 0, "credit": 4_000_000},
        ], "Iran CE: wages")

        resp = auth_client.get(
            "/manager-reports/financial/iran/changes-in-equity",
            params={"from_date": "2024-12-01", "to_date": "2024-12-31"},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        rows = {r["key"]: r for r in body["rows"]}
        # net profit row: retained_earnings cell == 6M, other cells == 0
        np_row = rows["net_profit_reported"]
        cells = {c["component"]: c["amount"] for c in np_row["cells"]}
        assert cells["eq_retained_earnings"] == 6_000_000
        assert cells["eq_capital"] == 0
        assert np_row["total"] == 6_000_000

    def test_opening_and_closing_capture_equity_balances(self, auth_client, db):
        _purge_transactions(db)
        # Post-purge, the opening/closing snapshot reflects only this test's
        # own capital injection — no need to fight cross-test date overlap.
        _create_txn(auth_client, "2024-03-01", [
            {"account_code": "1110", "debit": 7_000_000, "credit": 0},
            {"account_code": "3110", "debit": 0, "credit": 7_000_000},
        ], "Iran CE: prior capital")

        resp = auth_client.get(
            "/manager-reports/financial/iran/changes-in-equity",
            params={"from_date": "2024-05-01", "to_date": "2024-05-31"},
        )
        body = resp.json()
        rows = {r["key"]: r for r in body["rows"]}
        opening_cells = {c["component"]: c["amount"] for c in rows["opening_balance"]["cells"]}
        closing_cells = {c["component"]: c["amount"] for c in rows["closing_balance"]["cells"]}
        assert opening_cells["eq_capital"] == 7_000_000
        assert closing_cells["eq_capital"] == 7_000_000
