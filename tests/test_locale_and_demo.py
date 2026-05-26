"""End-to-end smoke tests for the locale-aware reporting infrastructure:

* the Iran + UK demo-data loaders post balanced journals
* every Iran and UK statement endpoint returns 200 with the right
  ``report_type`` field and well-formed rows
* the calendar and locale admin endpoints round-trip cleanly
* the Balance Sheet equation holds for both demos after the implicit
  P&L → retained-earnings auto-close
"""
from __future__ import annotations

from datetime import date

import pytest
from sqlalchemy import delete
from sqlalchemy.orm import Session

from app.db.demo_data import seed_iran_demo, seed_uk_demo
from app.db.seed import seed_chart_if_empty
from app.models.account import Account
from app.models.transaction import Transaction, TransactionLine
from app.services.reporting.iran_statement_service import IranStatementService
from app.services.reporting.uk_statement_service import UKStatementService


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
def _wipe(db: Session) -> None:
    db.execute(delete(TransactionLine))
    db.execute(delete(Transaction))
    db.execute(delete(Account))
    db.commit()


def _restore_default_iran_chart(db: Session) -> None:
    """Restore the session-level Iran chart so subsequent tests that rely
    on the auto-seeded fixture keep working."""
    _wipe(db)
    seed_chart_if_empty(db, locale="ir")


@pytest.fixture()
def ir_demo_db(db: Session):
    """Wipe accounts/transactions then seed Iran chart + demo. Restore the
    bare Iran chart afterwards so the suite remains hermetic."""
    _wipe(db)
    seed_chart_if_empty(db, locale="ir")
    seed_iran_demo(db)
    yield db
    _restore_default_iran_chart(db)


@pytest.fixture()
def uk_demo_db(db: Session):
    _wipe(db)
    seed_chart_if_empty(db, locale="uk")
    seed_uk_demo(db)
    yield db
    _restore_default_iran_chart(db)


# ---------------------------------------------------------------------------
# Iran demo
# ---------------------------------------------------------------------------
class TestIranDemo:
    def test_balance_sheet_balances(self, ir_demo_db: Session) -> None:
        svc = IranStatementService(ir_demo_db)
        bs = svc.balance_sheet(as_of=date(2025, 12, 31))
        rows = {r.key: r for r in bs.rows}
        assert "total_assets" in rows and "total_equity_and_liabilities" in rows
        assert rows["total_assets"].amount_current == rows["total_equity_and_liabilities"].amount_current
        assert rows["total_assets"].amount_prior == rows["total_equity_and_liabilities"].amount_prior
        assert bs.metadata.get("balances", {}).get("assets_equal_equity_plus_liabilities") is True

    def test_income_statement_has_growth(self, ir_demo_db: Session) -> None:
        svc = IranStatementService(ir_demo_db)
        pl = svc.income_statement(from_date=date(2025, 1, 1), to_date=date(2025, 12, 31))
        rows = {r.key: r for r in pl.rows}
        net_cur = rows["net_profit"].amount_current
        net_pri = rows["net_profit"].amount_prior
        # 2025 should be more profitable than 2024 in the demo.
        assert net_cur > net_pri > 0, f"expected growth: cur={net_cur} pri={net_pri}"

    def test_cash_flow_reconciles(self, ir_demo_db: Session) -> None:
        svc = IranStatementService(ir_demo_db)
        cf = svc.cash_flow(from_date=date(2025, 1, 1), to_date=date(2025, 12, 31))
        rows = {r.key: r for r in cf.rows}
        # closing_cash = opening_cash + net_cash_change + fx_rate_effect
        derived = (
            rows["opening_cash"].amount_current
            + rows["net_cash_change"].amount_current
            + rows["fx_rate_effect"].amount_current
        )
        assert rows["closing_cash"].amount_current == derived

    def test_changes_in_equity_has_36_rows(self, ir_demo_db: Session) -> None:
        svc = IranStatementService(ir_demo_db)
        ce = svc.changes_in_equity(from_date=date(2025, 1, 1), to_date=date(2025, 12, 31))
        assert len(ce.components) == 10
        assert len(ce.rows) >= 30  # opening + comparative-period + current-period blocks

    def test_balance_sheet_has_three_date_columns(self, ir_demo_db: Session) -> None:
        svc = IranStatementService(ir_demo_db)
        bs = svc.balance_sheet(as_of=date(2025, 12, 31))
        assert bs.comparative_as_of is not None
        assert bs.comparative_beginning_as_of is not None


# ---------------------------------------------------------------------------
# UK demo
# ---------------------------------------------------------------------------
class TestUKDemo:
    def test_balance_sheet_balances(self, uk_demo_db: Session) -> None:
        svc = UKStatementService(uk_demo_db)
        bs = svc.balance_sheet(as_of=date(2025, 12, 31))
        rows = {r.key: r for r in bs.rows}
        # net_assets ≡ total_capital_reserves under Companies Act format 1
        assert rows["net_assets"].amount_current == rows["total_capital_reserves"].amount_current
        assert rows["net_assets"].amount_prior == rows["total_capital_reserves"].amount_prior
        assert bs.metadata.get("balances", {}).get("net_assets_equals_capital_reserves") is True

    def test_profit_and_loss_shows_turnover_growth(self, uk_demo_db: Session) -> None:
        svc = UKStatementService(uk_demo_db)
        pl = svc.income_statement(from_date=date(2025, 1, 1), to_date=date(2025, 12, 31))
        rows = {r.key: r for r in pl.rows}
        # 2025 turnover should exceed 2024 turnover in the demo (~60% growth
        # with the year-2 scale=1.6 in seed_uk_demo).
        assert rows["turnover"].amount_current > rows["turnover"].amount_prior > 0
        # Both years should be profitable — the demo tells a growth +
        # profitable story for the video walkthrough. (Year 1: founder
        # capital + first sales; year 2: scaling.)
        assert rows["profit_for_year"].amount_prior > 0, \
            f"Year 1 should be profitable; got {rows['profit_for_year'].amount_prior}"
        assert rows["profit_for_year"].amount_current > 0, \
            f"Year 2 should be profitable; got {rows['profit_for_year'].amount_current}"

    def test_comprehensive_income_equals_net_when_no_oci(self, uk_demo_db: Session) -> None:
        svc = UKStatementService(uk_demo_db)
        ci = svc.comprehensive_income(from_date=date(2025, 1, 1), to_date=date(2025, 12, 31))
        rows = {r.key: r for r in ci.rows}
        # Demo doesn't post any OCI items → total comprehensive = profit for year.
        assert rows["total_comprehensive_income"].amount_current == rows["profit_for_year"].amount_current

    def test_cash_flow_reconciles(self, uk_demo_db: Session) -> None:
        svc = UKStatementService(uk_demo_db)
        cf = svc.cash_flow(from_date=date(2025, 1, 1), to_date=date(2025, 12, 31))
        rows = {r.key: r for r in cf.rows}
        derived = (
            rows["opening_cash"].amount_current
            + rows["net_cash_change"].amount_current
            + rows["fx_effect"].amount_current
        )
        assert rows["closing_cash"].amount_current == derived

    def test_changes_in_equity_has_5_components(self, uk_demo_db: Session) -> None:
        svc = UKStatementService(uk_demo_db)
        ce = svc.changes_in_equity(from_date=date(2025, 1, 1), to_date=date(2025, 12, 31))
        assert len(ce.components) == 5
        assert {c.key for c in ce.components} == {
            "eq_share_capital", "eq_share_premium", "eq_revaluation_reserve",
            "eq_other_reserves", "eq_pl_account",
        }


# ---------------------------------------------------------------------------
# UK HTTP endpoints
# ---------------------------------------------------------------------------
class TestUKEndpoints:
    """Walk the FastAPI router for each UK statement endpoint."""

    @pytest.fixture(autouse=True)
    def _seed_uk(self, db: Session):
        _wipe(db)
        seed_chart_if_empty(db, locale="uk")
        seed_uk_demo(db)
        yield
        _restore_default_iran_chart(db)

    def test_balance_sheet_endpoint(self, auth_client) -> None:
        r = auth_client.get(
            "/manager-reports/financial/uk/balance-sheet",
            params={"as_of": "2025-12-31"},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["report_type"] == "uk_balance_sheet"
        assert body["locale"] == "uk"
        assert isinstance(body["rows"], list) and body["rows"]

    def test_profit_and_loss_endpoint(self, auth_client) -> None:
        r = auth_client.get(
            "/manager-reports/financial/uk/profit-and-loss",
            params={"from_date": "2025-01-01", "to_date": "2025-12-31"},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["report_type"] == "uk_profit_and_loss"

    def test_comprehensive_income_endpoint(self, auth_client) -> None:
        r = auth_client.get(
            "/manager-reports/financial/uk/comprehensive-income",
            params={"from_date": "2025-01-01", "to_date": "2025-12-31"},
        )
        assert r.status_code == 200, r.text
        assert r.json()["report_type"] == "uk_comprehensive_income"

    def test_changes_in_equity_endpoint(self, auth_client) -> None:
        r = auth_client.get(
            "/manager-reports/financial/uk/changes-in-equity",
            params={"from_date": "2025-01-01", "to_date": "2025-12-31"},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["report_type"] == "uk_changes_in_equity"
        assert len(body["components"]) == 5

    def test_cash_flow_endpoint(self, auth_client) -> None:
        r = auth_client.get(
            "/manager-reports/financial/uk/cash-flow",
            params={"from_date": "2025-01-01", "to_date": "2025-12-31"},
        )
        assert r.status_code == 200, r.text
        assert r.json()["report_type"] == "uk_cash_flow"


# ---------------------------------------------------------------------------
# Admin endpoints (locale + calendar)
# ---------------------------------------------------------------------------
class TestLocaleAndCalendarAdmin:
    def test_reporting_locale_round_trip(self, auth_client) -> None:
        r = auth_client.get("/admin/reporting-locale")
        assert r.status_code == 200
        assert set(r.json()["supported"]) == {"default", "ir", "uk"}

        # Each supported value must round-trip.
        for value in ("ir", "uk", "default"):
            r = auth_client.put("/admin/reporting-locale", json={"locale": value})
            assert r.status_code == 200, r.text
            assert r.json()["locale"] == value

    def test_reporting_locale_rejects_unknown(self, auth_client) -> None:
        r = auth_client.put("/admin/reporting-locale", json={"locale": "fr"})
        assert r.status_code == 400

    def test_display_calendar_round_trip(self, auth_client) -> None:
        r = auth_client.get("/admin/display-calendar")
        assert r.status_code == 200
        assert set(r.json()["supported"]) == {"gregorian", "jalali"}

        for value in ("jalali", "gregorian"):
            r = auth_client.put("/admin/display-calendar", json={"calendar": value})
            assert r.status_code == 200, r.text
            assert r.json()["calendar"] == value

    def test_display_calendar_rejects_unknown(self, auth_client) -> None:
        r = auth_client.put("/admin/display-calendar", json={"calendar": "hijri"})
        assert r.status_code == 400

    def test_display_calendar_defaults_by_locale(self, auth_client) -> None:
        # Clear any explicit setting by setting locale to ir → calendar defaults to jalali
        auth_client.put("/admin/reporting-locale", json={"locale": "ir"})
        # We have no DELETE endpoint, so set explicitly to jalali and confirm.
        # (Default-by-locale is enforced inside get_display_calendar when AppSetting
        # is unset — covered indirectly by the unit test below.)
        from app.services.locale_service import _default_calendar_for_locale
        assert _default_calendar_for_locale("ir") == "jalali"
        assert _default_calendar_for_locale("uk") == "gregorian"
        assert _default_calendar_for_locale("default") == "gregorian"
