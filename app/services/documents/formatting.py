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
# The hundredth of each, for an amount with pence (docs/PLAN_PENCE.md).
_MINOR_WORD_EN = {"GBP": "pence", "USD": "cents", "EUR": "cents", "AED": "fils", "TRY": "kuruş"}
_MINOR_WORD_FA = {"GBP": "پنس", "USD": "سنت", "EUR": "سنت"}


def fmt_amount(amount, currency: str | None, locale: str | None = None) -> str:
    """A grouped amount with its currency's places, no symbol: «1,234.56»,
    «۲,۵۰۰,۰۰۰» (rials have none). It was int(round()): pence went."""
    from app.core.money import exponent, quantize, to_decimal
    try:
        v = to_decimal(amount or 0)
    except ValueError:
        v = to_decimal(0)
    places = exponent(currency) if currency else 0
    q = quantize(v, currency) if currency else int(round(float(v)))
    text = f"{q:,.{places}f}" if places else f"{int(q):,}"
    if is_rtl(locale):
        # the grouping comma as Persian documents have always printed it; the
        # Persian decimal mark only where there are pence
        text = to_persian_digits(text.replace(".", "٫"))
    return text


def is_rtl(locale: str | None) -> bool:
    return (locale or "").lower() == "ir"


def to_persian_digits(text: str) -> str:
    return str(text).translate(_PERSIAN_DIGITS)


def fmt_digits(text: str, locale: str | None) -> str:
    return to_persian_digits(text) if is_rtl(locale) else str(text)


def fmt_money(amount, currency: str | None, locale: str | None) -> str:
    """Thousands-grouped amount with the currency symbol and the currency's
    places («£1,234.56»); Persian digits for ir."""
    ccy = (currency or "").upper()
    grouped = fmt_amount(amount, ccy or None, locale)
    symbol = CURRENCY_SYMBOL.get(ccy, ccy)
    if is_rtl(locale):
        # symbol trails the number in RTL
        return f"{grouped} {symbol}".strip()
    # symbol leads for £/$/€, trails for ISO codes
    if symbol and symbol != ccy:
        neg = grouped.startswith("-")
        return ("-" if neg else "") + f"{symbol}{grouped.lstrip('-')}"
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
    """«One hundred and twenty pounds and fifty pence»: the pence in words
    too (they were rounded away), none for a currency without places."""
    from app.core.money import exponent, quantize, to_decimal
    ccy = (currency or "").upper()
    try:
        v = quantize(to_decimal(amount or 0), ccy) if ccy else to_decimal(int(round(float(amount or 0))))
    except (TypeError, ValueError):
        v = to_decimal(0)
    n = int(v)
    places = exponent(ccy) if ccy else 0
    minor = int(round((to_decimal(v) - n) * (10 ** places))) if places else 0
    if is_rtl(locale):
        try:
            from num2fawords import words
            w = words(n)
            unit = _CCY_WORD_FA.get(ccy, ccy)
            out = f"{w} {unit}".strip()
            if minor:
                out += f" و {words(minor)} {_MINOR_WORD_FA.get(ccy, '')}".rstrip()
            return out
        except Exception:
            return to_persian_digits(f"{n:,}")
    try:
        from num2words import num2words
        w = num2words(n, lang="en")
        unit = _CCY_WORD_EN.get(ccy, ccy.lower())
        out = f"{w} {unit}".strip()
        if minor:
            out += f" and {num2words(minor, lang='en')} {_MINOR_WORD_EN.get(ccy, 'cents')}"
        return out.capitalize()
    except Exception:
        return f"{n:,}"
