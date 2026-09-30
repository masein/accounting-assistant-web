"""Jalali date phrases the way people correct a date: 'the date was 4th of
Esfand', 'date is 1404/12/04'. (The old voucher chat's endpoint that used
them is gone; the parser is still what the AI accountant's date resolver
reads.)"""
from __future__ import annotations

import pytest

from app.utils.jalali import try_parse_jalali, jalali_to_gregorian


class TestPostVoucherDateParsing:
    """Verify the date parsing that feeds into the chat date-correction handler."""

    def test_jalali_numeric(self):
        result = try_parse_jalali("1404/12/04")
        assert result == jalali_to_gregorian(1404, 12, 4)

    def test_english_ordinal_month_name(self):
        result = try_parse_jalali("4th of Esfand")
        assert result is not None

    def test_persian_month_day(self):
        result = try_parse_jalali("4 اسفند")
        assert result is not None

    def test_iso_date(self):
        from datetime import date

        result = try_parse_jalali("2026-02-23")
        assert result is None  # ISO dates should not match Jalali parser

    def test_non_date_text(self):
        result = try_parse_jalali("the amount was 5M")
        assert result is None

    def test_date_in_sentence(self):
        result = try_parse_jalali("the date was 4th of Esfand")
        assert result is not None
