"""Jalali calendar edges (roadmap 2026-09 §6, suite 9): leap Esfand, month
ends, day-month dates with no year around Nowruz (frozen clock), Arabic-Indic
as well as Persian digits, and Excel day codes."""
from __future__ import annotations

from datetime import date

import jdatetime
import pytest

from app.utils import jalali as J


def g(y, m, d) -> date:
    """Gregorian date of a Jalali y/m/d."""
    x = jdatetime.date(y, m, d).togregorian()
    return date(x.year, x.month, x.day)


# ─── leap years and month ends ─────────────────────────────────────────

def test_esfand_30_exists_only_in_leap_years():
    assert jdatetime.date(1403, 1, 1).isleap() and not jdatetime.date(1404, 1, 1).isleap()
    assert J.try_parse_jalali("1403/12/30") == g(1403, 12, 30) == date(2025, 3, 20)
    assert J.try_parse_jalali("1404/12/30") is None
    assert J.try_parse_jalali("30 اسفند 1404") is None
    with pytest.raises(ValueError):
        J.jalali_to_gregorian(1404, 12, 30)


def test_month_ends_follow_the_calendar():
    from app.services.ai_accountant.spending_tools import _j_last
    assert _j_last(1403, 12) == g(1403, 12, 30)
    assert _j_last(1404, 12) == g(1404, 12, 29)
    assert _j_last(1404, 6) == g(1404, 6, 31)
    assert _j_last(1404, 7) == g(1404, 7, 30)


def test_last_month_from_farvardin_is_esfand_of_the_previous_year():
    from app.services.ai_accountant.spending_tools import resolve_period
    p = resolve_period("last_month", g(1404, 1, 10), "jalali")
    assert (p.from_date, p.to_date) == (g(1403, 12, 1), g(1403, 12, 30))


# ─── no-year dates around Nowruz (frozen clock) ────────────────────────

@pytest.fixture()
def frozen(monkeypatch):
    def _freeze(y, m, d):
        monkeypatch.setattr(J, "_today_jalali", lambda: jdatetime.date(y, m, d))
    return _freeze


def test_early_farvardin_typed_at_the_end_of_esfand_is_next_year(frozen):
    frozen(1404, 12, 28)
    assert J.try_parse_jalali("5 فروردین") == g(1405, 1, 5)          # 8 days ahead, not a year ago


def test_late_esfand_typed_in_farvardin_is_last_year(frozen):
    frozen(1405, 1, 5)
    assert J.try_parse_jalali("25 اسفند") == g(1404, 12, 25)
    assert J.try_parse_jalali("اسفند 25") == g(1404, 12, 25)


def test_mid_year_picks_the_nearest(frozen):
    frozen(1404, 7, 10)
    assert J.try_parse_jalali("4 مهر") == g(1404, 7, 4)
    assert J.try_parse_jalali("20 مهر") == g(1404, 7, 20)


def test_esfand_30_without_a_year_picks_the_leap_year(frozen):
    frozen(1404, 1, 15)                                                 # 1403 was leap, 1404 is not
    assert J.try_parse_jalali("30 اسفند") == g(1403, 12, 30)


def test_chat_named_dates_use_the_same_rule():
    from app.services.ai_accountant.date_resolver import jalali_named_date
    assert jalali_named_date("۲۵ اسفند", g(1405, 1, 5)) == g(1404, 12, 25)
    assert jalali_named_date("۵ فروردین", g(1404, 12, 28)) == g(1405, 1, 5)
    assert jalali_named_date("۴ مرداد ۱۴۰۵", g(1404, 12, 28)) == g(1405, 5, 4)   # explicit year wins


# ─── digits ────────────────────────────────────────────────────────────

@pytest.mark.parametrize("text", ["١٤٠٤/١١/٢٧", "۱۴۰۴/۱۱/۲۷", "1404-11-27", "٢٧/١١/١٤٠٤", "٢٧ بهمن ١٤٠٤"])
def test_persian_and_arabic_indic_digits(text):
    assert J.try_parse_jalali(text) == g(1404, 11, 27)


def test_find_and_replace_with_arabic_digits():
    out, found = J.find_and_replace_jalali_dates("پرداخت ١٤٠٤/٠١/٠١ انجام شد")
    assert found and found[0][1] == g(1404, 1, 1) and g(1404, 1, 1).isoformat() in out


def test_number_words_with_arabic_digits():
    from app.utils.persian_numbers import _persian_to_ascii
    assert _persian_to_ascii("٥ ميليون و ۲") == "5 ميليون و 2"


def test_bank_statement_dates_in_arabic_digits():
    from app.services.bank_statement_parser import _parse_date
    assert _parse_date("١٤٠٣/١٢/٣٠") == g(1403, 12, 30)
    assert _parse_date("1404/12/30") is None


# ─── Excel day codes ───────────────────────────────────────────────────

@pytest.mark.parametrize("code,year,expected", [
    (419, 1403, (1403, 4, 19)),
    (1018, 1403, (1403, 10, 18)),
    (1230, 1403, (1403, 12, 30)),
    (1230, 1404, None),                         # Esfand 30 in a common year
    (14021229, 1403, (1402, 12, 29)),           # a full code keeps ITS year
    (14050101, 1404, (1405, 1, 1)),
    (99, 1403, None), (1332, 1403, None), ("x", 1403, None), (None, 1403, None),
])
def test_excel_day_codes(code, year, expected):
    from app.services.excel_journal_parser import _jalali_day_to_gregorian
    got = _jalali_day_to_gregorian(code, year)
    assert got == (g(*expected) if expected else None)
