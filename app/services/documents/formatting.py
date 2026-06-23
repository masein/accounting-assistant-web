"""Locale-aware formatting for documents: money, dates, digits, amount-in-words.

`ir` → Persian digits, Jalali dates, Persian words. `uk`/default → Latin
digits, Gregorian dates, English words. Currency follows the company.
"""
from __future__ import annotations

from datetime import date, datetime

_PERSIAN_DIGITS = str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹")

CURRENCY_SYMBOL = {
    "GBP": "£", "USD": "$", "EUR": "€", "IRR": "﷼", "AED": "د.إ", "TRY": "₺",
}
# Currency words for English amount-in-words.
_CCY_WORD_EN = {
    "GBP": "pounds", "USD": "dollars", "EUR": "euros", "IRR": "rials",
    "AED": "dirhams", "TRY": "lira",
}
# Currency words for Persian amount-in-words.
_CCY_WORD_FA = {"IRR": "ریال", "USD": "دلار", "EUR": "یورو", "GBP": "پوند"}


def is_rtl(locale: str | None) -> bool:
    return (locale or "").lower() == "ir"


def to_persian_digits(text: str) -> str:
    return str(text).translate(_PERSIAN_DIGITS)


def fmt_digits(text: str, locale: str | None) -> str:
    return to_persian_digits(text) if is_rtl(locale) else str(text)


def fmt_money(amount, currency: str | None, locale: str | None) -> str:
    """Thousands-grouped amount with the currency symbol; Persian digits for ir."""
    try:
        n = int(round(float(amount or 0)))
    except (TypeError, ValueError):
        n = 0
    ccy = (currency or "").upper()
    grouped = f"{n:,}"
    symbol = CURRENCY_SYMBOL.get(ccy, ccy)
    if is_rtl(locale):
        grouped = to_persian_digits(grouped)
        # symbol trails the number in RTL
        return f"{grouped} {symbol}".strip()
    # symbol leads for £/$/€, trails for ISO codes
    if symbol and symbol != ccy:
        return f"{symbol}{grouped}"
    return f"{grouped} {ccy}".strip()


def _to_date(d) -> date | None:
    if isinstance(d, datetime):
        return d.date()
    if isinstance(d, date):
        return d
    return None


def fmt_date(d, locale: str | None) -> str:
    dd = _to_date(d)
    if dd is None:
        return "-"
    if is_rtl(locale):
        try:
            import jdatetime
            j = jdatetime.date.fromgregorian(date=dd)
            return to_persian_digits(f"{j.year:04d}/{j.month:02d}/{j.day:02d}")
        except Exception:
            return to_persian_digits(dd.isoformat())
    return dd.isoformat()


def amount_in_words(amount, currency: str | None, locale: str | None) -> str:
    try:
        n = int(round(float(amount or 0)))
    except (TypeError, ValueError):
        n = 0
    ccy = (currency or "").upper()
    if is_rtl(locale):
        try:
            from num2fawords import words
            w = words(n)
        except Exception:
            return to_persian_digits(f"{n:,}")
        unit = _CCY_WORD_FA.get(ccy, ccy)
        return f"{w} {unit}".strip()
    try:
        from num2words import num2words
        w = num2words(n, lang="en")
    except Exception:
        return f"{n:,}"
    unit = _CCY_WORD_EN.get(ccy, ccy.lower())
    return f"{w} {unit}".strip().capitalize()
