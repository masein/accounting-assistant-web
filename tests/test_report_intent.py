"""Tests for report intent parsing from natural language."""
from __future__ import annotations

from datetime import date

import pytest

from app.services.reporting.report_intent import parse_report_intent


_TODAY = date(2026, 2, 25)


# ---------------------------------------------------------------------------
# Financial statement reports
# ---------------------------------------------------------------------------
class TestFinancialStatements:
    @pytest.mark.parametrize(
        "query,expected_key",
        [
            ("balance sheet", "balance_sheet"),
            ("show me balance sheet this month", "balance_sheet"),
            ("balance sheet last month", "balance_sheet"),
            ("ترازنامه", "balance_sheet"),
            ("income statement last month", "income_statement"),
            ("profit and loss", "income_statement"),
            ("صورت سود و زیان", "income_statement"),
            ("cash flow", "cash_flow"),
            ("جریان وجوه نقد", "cash_flow"),
            ("trial balance", "trial_balance"),
            ("تراز آزمایشی", "trial_balance"),
            ("general ledger", "general_ledger"),
            ("دفتر کل", "general_ledger"),
            ("general journal", "general_journal"),
        ],
    )
    def test_report_type_detection(self, query, expected_key):
        intent = parse_report_intent(query, today=_TODAY)
        assert intent is not None, f"Failed to parse: {query!r}"
        assert intent.key == expected_key


class TestBalanceQueries:
    @pytest.mark.parametrize(
        "query",
        [
            "what is the current balance",
            "whats the balance",
            "current balance",
            "show me the balance",
            "balance",
            "how much do i have",
            "how much money do i have",
            "how much in the bank",
            "whats my cash",
            "total money",
            "total cash",
        ],
    )
    def test_bare_balance_queries(self, query):
        intent = parse_report_intent(query, today=_TODAY)
        assert intent is not None, f"Failed: {query!r}"
        assert intent.key == "account_ledger"

    @pytest.mark.parametrize(
        "query",
        [
            "balance of mellat bank",
            "what is the balance of melli bank",
            "current balance mellat bank",
            "show me melli bank balance",
            "balance melli bank",
            "melli bank balance",
        ],
    )
    def test_balance_with_bank(self, query):
        intent = parse_report_intent(query, today=_TODAY)
        assert intent is not None, f"Failed: {query!r}"
        assert intent.key == "account_ledger"
        assert intent.bank_name is not None


class TestInformalQueries:
    def test_who_owes_me(self):
        intent = parse_report_intent("who owes me money", today=_TODAY)
        assert intent is not None
        assert intent.key == "debtor_creditor"

    def test_how_much_i_owe(self):
        intent = parse_report_intent("how much i owe", today=_TODAY)
        assert intent is not None
        assert intent.key == "debtor_creditor"

    def test_expenses(self):
        intent = parse_report_intent("show me my expenses this month", today=_TODAY)
        assert intent is not None
        assert intent.key == "income_statement"

    def test_revenue(self):
        intent = parse_report_intent("how much did i earn this month", today=_TODAY)
        assert intent is not None
        assert intent.key == "income_statement"


# ---------------------------------------------------------------------------
# Bank transactions
# ---------------------------------------------------------------------------
class TestBankTransactions:
    def test_latest_transactions_bank(self):
        intent = parse_report_intent("last 10 transactions melli bank", today=_TODAY)
        assert intent is not None
        assert intent.key == "account_ledger"
        assert intent.bank_name is not None

    def test_bank_statement(self):
        intent = parse_report_intent("bank statement melli", today=_TODAY)
        assert intent is not None
        assert intent.key == "account_ledger"
        assert intent.bank_name is not None


# ---------------------------------------------------------------------------
# Jalali date ranges in reports
# ---------------------------------------------------------------------------
class TestJalaliDates:
    def test_two_jalali_dates(self):
        intent = parse_report_intent("balance sheet 1404/11/01 to 1404/11/30", today=_TODAY)
        assert intent is not None
        assert intent.key == "balance_sheet"
        assert intent.from_date is not None
        assert intent.to_date is not None

    def test_single_jalali_date(self):
        intent = parse_report_intent("transactions on 1404/11/27", today=_TODAY)
        assert intent is not None
        assert intent.to_date is not None

    def test_persian_digits(self):
        intent = parse_report_intent("balance sheet ۱۴۰۴/۱۱/۰۱ to ۱۴۰۴/۱۱/۳۰", today=_TODAY)
        assert intent is not None
        assert intent.from_date is not None

    def test_bank_statement_jalali(self):
        intent = parse_report_intent("bank statement melli from 1404/12/01 to 1404/12/05", today=_TODAY)
        assert intent is not None
        assert intent.bank_name is not None
        assert intent.from_date is not None


# ---------------------------------------------------------------------------
# Inventory / Sales / AR-AP
# ---------------------------------------------------------------------------
class TestOtherReports:
    @pytest.mark.parametrize(
        "query,expected_key",
        [
            ("inventory balance", "inventory_balance"),
            ("inventory movement", "inventory_movement"),
            ("sales by product", "sales_by_product"),
            ("purchase by invoice", "purchase_by_invoice"),
            ("accounts receivable", "debtor_creditor"),
            ("accounts payable", "debtor_creditor"),
        ],
    )
    def test_other_report_types(self, query, expected_key):
        intent = parse_report_intent(query, today=_TODAY)
        assert intent is not None, f"Failed: {query!r}"
        assert intent.key == expected_key


# ---------------------------------------------------------------------------
# Negative cases — should NOT match reports
# ---------------------------------------------------------------------------
class TestNegativeCases:
    @pytest.mark.parametrize(
        "query",
        [
            "i paid 5M from melli bank",
            "received 2M from client",
            "pay rent 1M",
            "i payed ali roshan 8M from melli bank",
        ],
    )
    def test_payment_phrases_not_reports(self, query):
        intent = parse_report_intent(query, today=_TODAY)
        assert intent is None, f"Unexpectedly matched: {query!r} -> {intent}"


# ---------------------------------------------------------------------------
# Persian queries
# ---------------------------------------------------------------------------
class TestPersianQueries:
    def test_persian_bank_statement(self):
        intent = parse_report_intent("گردش بانک ملی هم میخوام", today=_TODAY)
        assert intent is not None
        assert intent.key == "account_ledger"
        assert intent.bank_name == "ملی"

    def test_persian_balance_sheet(self):
        intent = parse_report_intent("ترازنامه ماه قبل", today=_TODAY)
        assert intent is not None
        assert intent.key == "balance_sheet"
