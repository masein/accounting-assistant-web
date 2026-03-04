"""Tests for account classification and period keyword parsing."""
from __future__ import annotations

from datetime import date

import pytest

from app.services.reporting.common import (
    ASSET,
    EQUITY,
    EXPENSE,
    LIABILITY,
    OTHER,
    REVENUE,
    balance_from_turnovers,
    classify_account_code,
    period_for_keyword,
)


class TestClassifyAccountCode:
    @pytest.mark.parametrize(
        "code,expected",
        [
            ("1110", ASSET),
            ("1112", ASSET),
            ("1210", ASSET),
            ("1510", ASSET),
            ("2110", LIABILITY),
            ("2210", LIABILITY),
            ("3110", EQUITY),
            ("3210", EQUITY),
            ("4110", REVENUE),
            ("4210", REVENUE),
            ("5110", EXPENSE),
            ("6110", EXPENSE),
            ("6210", EXPENSE),
            ("9100", OTHER),
            ("", OTHER),
        ],
    )
    def test_classification(self, code, expected):
        assert classify_account_code(code) == expected


class TestBalanceFromTurnovers:
    def test_asset_debit_nature(self):
        assert balance_from_turnovers(ASSET, 1000, 300) == 700

    def test_liability_credit_nature(self):
        assert balance_from_turnovers(LIABILITY, 1000, 300) == -700

    def test_equity_credit_nature(self):
        assert balance_from_turnovers(EQUITY, 100, 500) == 400

    def test_revenue_credit_nature(self):
        assert balance_from_turnovers(REVENUE, 100, 450) == 350

    def test_expense_debit_nature(self):
        assert balance_from_turnovers(EXPENSE, 500, 100) == 400

    def test_zero_turnovers(self):
        assert balance_from_turnovers(ASSET, 0, 0) == 0


class TestPeriodForKeyword:
    _today = date(2026, 2, 25)

    @pytest.mark.parametrize(
        "keyword",
        ["today", "امروز", "yesterday", "دیروز", "this month", "این ماه",
         "last month", "ماه قبل", "this year", "امسال", "last week", "هفته قبل"],
    )
    def test_known_keywords_return_range(self, keyword):
        result = period_for_keyword(keyword, today=self._today)
        assert result is not None, f"No result for {keyword!r}"
        assert result.from_date <= result.to_date

    def test_today_single_day(self):
        result = period_for_keyword("today", today=self._today)
        assert result.from_date == result.to_date == self._today

    def test_yesterday(self):
        result = period_for_keyword("yesterday", today=self._today)
        assert result.from_date == date(2026, 2, 24)

    def test_this_month(self):
        result = period_for_keyword("this month", today=self._today)
        assert result.from_date == date(2026, 2, 1)
        assert result.to_date == self._today

    def test_unknown_keyword_returns_none(self):
        assert period_for_keyword("next century") is None
