"""Tests for Jalali (Solar Hijri) date conversion utilities."""
from __future__ import annotations

from datetime import date

import pytest

from app.utils.jalali import (
    find_and_replace_jalali_dates,
    format_jalali,
    gregorian_to_jalali,
    jalali_to_gregorian,
    try_parse_jalali,
)


# ---------------------------------------------------------------------------
# jalali_to_gregorian / gregorian_to_jalali round-trips
# ---------------------------------------------------------------------------
class TestRoundTrip:
    @pytest.mark.parametrize(
        "jy,jm,jd,gy,gm,gd",
        [
            (1404, 11, 27, 2026, 2, 16),
            (1404, 12, 2, 2026, 2, 21),
            (1404, 12, 4, 2026, 2, 23),
            (1404, 1, 1, 2025, 3, 21),
            (1403, 12, 30, 2025, 3, 20),
            (1400, 1, 1, 2021, 3, 21),
        ],
    )
    def test_jalali_to_gregorian(self, jy, jm, jd, gy, gm, gd):
        assert jalali_to_gregorian(jy, jm, jd) == date(gy, gm, gd)

    @pytest.mark.parametrize(
        "gy,gm,gd,jy,jm,jd",
        [
            (2026, 2, 16, 1404, 11, 27),
            (2026, 2, 23, 1404, 12, 4),
            (2025, 3, 21, 1404, 1, 1),
        ],
    )
    def test_gregorian_to_jalali(self, gy, gm, gd, jy, jm, jd):
        assert gregorian_to_jalali(date(gy, gm, gd)) == (jy, jm, jd)

    def test_full_round_trip(self):
        for d in [date(2026, 1, 1), date(2026, 2, 16), date(2025, 3, 21)]:
            y, m, day = gregorian_to_jalali(d)
            assert jalali_to_gregorian(y, m, day) == d


class TestFormatJalali:
    def test_basic(self):
        assert format_jalali(date(2026, 2, 16)) == "1404/11/27"

    def test_zero_padded(self):
        result = format_jalali(date(2025, 3, 21))
        assert result == "1404/01/01"


# ---------------------------------------------------------------------------
# try_parse_jalali — numeric formats
# ---------------------------------------------------------------------------
class TestParseNumeric:
    @pytest.mark.parametrize(
        "text,expected",
        [
            ("1404/11/27", date(2026, 2, 16)),
            ("1404-11-27", date(2026, 2, 16)),
            ("1404/12/05", date(2026, 2, 24)),
            ("1404/1/1", date(2025, 3, 21)),
        ],
    )
    def test_yyyy_mm_dd(self, text, expected):
        assert try_parse_jalali(text) == expected

    def test_day_first(self):
        assert try_parse_jalali("27/11/1404") == date(2026, 2, 16)

    def test_persian_digits(self):
        assert try_parse_jalali("۱۴۰۴/۱۱/۲۷") == date(2026, 2, 16)

    @pytest.mark.parametrize("bad", ["1404/13/01", "1404/00/01", "1404/01/32"])
    def test_invalid_dates_return_none(self, bad):
        assert try_parse_jalali(bad) is None

    def test_non_jalali_year_ignored(self):
        assert try_parse_jalali("2026/02/16") is None

    def test_empty_and_none(self):
        assert try_parse_jalali("") is None
        assert try_parse_jalali(None) is None


# ---------------------------------------------------------------------------
# try_parse_jalali — month names
# ---------------------------------------------------------------------------
class TestParseMonthNames:
    def test_day_month_year_persian(self):
        assert try_parse_jalali("27 بهمن 1404") == date(2026, 2, 16)

    def test_day_month_year_english(self):
        assert try_parse_jalali("4 Esfand 1404") == date(2026, 2, 23)

    def test_month_year_only(self):
        result = try_parse_jalali("بهمن 1404")
        assert result == date(2026, 1, 21)  # 1st of Bahman

    def test_ordinal_of_month_no_year(self):
        result = try_parse_jalali("4th of Esfand")
        assert result is not None
        assert result == jalali_to_gregorian(1404, 12, 4)

    def test_ordinals(self):
        for ordinal in ["1st", "2nd", "3rd", "21st"]:
            day = int("".join(c for c in ordinal if c.isdigit()))
            result = try_parse_jalali(f"{ordinal} of Bahman")
            expected = jalali_to_gregorian(1404, 11, day)
            assert result == expected, f"Failed for {ordinal}"

    def test_month_day_no_year(self):
        result = try_parse_jalali("Esfand 4")
        assert result is not None

    def test_persian_month_day_no_year(self):
        result = try_parse_jalali("27 بهمن")
        assert result == jalali_to_gregorian(1404, 11, 27)

    def test_in_sentence(self):
        result = try_parse_jalali("the date was 4th of Esfand")
        assert result is not None


# ---------------------------------------------------------------------------
# find_and_replace_jalali_dates
# ---------------------------------------------------------------------------
class TestFindAndReplace:
    def test_numeric_in_sentence(self):
        text = "paid 5M on 1404/11/27 from melli bank"
        new_text, replaced = find_and_replace_jalali_dates(text)
        assert "2026-02-16" in new_text
        assert "1404/11/27" not in new_text
        assert len(replaced) == 1

    def test_multiple_dates(self):
        text = "from 1404/11/01 to 1404/11/30"
        new_text, replaced = find_and_replace_jalali_dates(text)
        assert len(replaced) == 2

    def test_no_jalali_unchanged(self):
        text = "paid 5M yesterday"
        new_text, replaced = find_and_replace_jalali_dates(text)
        assert new_text == text
        assert replaced == []

    def test_month_name_without_year(self):
        text = "the date was 4th of Esfand"
        new_text, replaced = find_and_replace_jalali_dates(text)
        assert len(replaced) >= 1
        assert "Esfand" not in new_text or "202" in new_text


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------
class TestEdgeCases:
    def test_jalali_to_gregorian_invalid_raises(self):
        with pytest.raises(Exception):
            jalali_to_gregorian(1404, 13, 1)

    def test_leap_year_boundary(self):
        result = try_parse_jalali("1403/12/30")
        assert result is not None  # 1403 is a leap year
