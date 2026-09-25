"""
Jalali (Solar Hijri / Shamsi) ↔ Gregorian date conversion utilities.

Recognizes formats like:
  1404/11/27   1404-11-27   27/11/1404
  ۱۴۰۴/۱۱/۲۷  (Persian digits)
  27 بهمن 1404  (month name)
  بهمن 1404     (month only → first of month)

All conversions return standard datetime.date (Gregorian).
"""
from __future__ import annotations

import re
from datetime import date

import jdatetime

# Persian (U+06F0…) AND Arabic-Indic (U+0660…) digits: Arabic keyboards and
# many bank exports use the latter, which used to leave dates unparsed.
_PERSIAN_DIGITS = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")
DIGITS_TO_ASCII = _PERSIAN_DIGITS

_MONTH_NAMES: dict[str, int] = {
    "فروردین": 1, "farvardin": 1, "فروردين": 1,
    "اردیبهشت": 2, "ordibehesht": 2, "ارديبهشت": 2,
    "خرداد": 3, "khordad": 3,
    "تیر": 4, "tir": 4, "تير": 4,
    "مرداد": 5, "mordad": 5, "amordad": 5,
    "شهریور": 6, "shahrivar": 6, "شهريور": 6,
    "مهر": 7, "mehr": 7,
    "آبان": 8, "aban": 8,
    "آذر": 9, "azar": 9,
    "دی": 10, "dey": 10, "دي": 10,
    "بهمن": 11, "bahman": 11,
    "اسفند": 12, "esfand": 12, "espand": 12,
}

_JALALI_YEAR_RANGE = range(1300, 1500)


def _to_ascii(text: str) -> str:
    return text.translate(_PERSIAN_DIGITS)


def jalali_to_gregorian(year: int, month: int, day: int) -> date:
    """Convert a Jalali date to Gregorian. Raises ValueError on invalid input."""
    jd = jdatetime.date(year, month, day)
    gd = jd.togregorian()
    return date(gd.year, gd.month, gd.day)


def gregorian_to_jalali(d: date) -> tuple[int, int, int]:
    """Convert a Gregorian date to (jalali_year, jalali_month, jalali_day)."""
    jd = jdatetime.date.fromgregorian(date=d)
    return jd.year, jd.month, jd.day


def format_jalali(d: date) -> str:
    """Format a Gregorian date as Jalali string 'YYYY/MM/DD'."""
    y, m, day = gregorian_to_jalali(d)
    return f"{y}/{m:02d}/{day:02d}"


# Month number → Persian name (the first alias in _MONTH_NAMES is canonical).
JALALI_MONTH_NAMES_FA: list[str] = [
    "فروردین", "اردیبهشت", "خرداد", "تیر", "مرداد", "شهریور",
    "مهر", "آبان", "آذر", "دی", "بهمن", "اسفند",
]
JALALI_MONTH_NAMES_EN: list[str] = [
    "Farvardin", "Ordibehesht", "Khordad", "Tir", "Mordad", "Shahrivar",
    "Mehr", "Aban", "Azar", "Dey", "Bahman", "Esfand",
]
_ASCII_TO_PERSIAN = str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹")


def to_persian_digits(text: str | int) -> str:
    return str(text).translate(_ASCII_TO_PERSIAN)


def format_jalali_long(d: date) -> str:
    """'۲ مهر ۱۴۰۵' — the way a Persian speaker writes the date. Given to the
    model so it copies the day/month instead of deriving them (it once turned
    2026-09-24 into '۲۴ مهر')."""
    y, m, day = gregorian_to_jalali(d)
    return f"{to_persian_digits(day)} {JALALI_MONTH_NAMES_FA[m - 1]} {to_persian_digits(y)}"


def try_parse_jalali(text: str) -> date | None:
    """
    Try to parse a Jalali date from free text. Returns Gregorian date or None.
    Handles: 1404/11/27, 1404-11-27, ۱۴۰۴/۱۱/۲۷, 27 بهمن 1404, etc.
    """
    if not text:
        return None
    t = _to_ascii(text.strip())

    # 1404/11/27 or 1404-11-27
    m = re.search(r"\b(1[34]\d{2})[/\-](0?[1-9]|1[0-2])[/\-](0?[1-9]|[12]\d|3[01])\b", t)
    if m:
        try:
            return jalali_to_gregorian(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            pass

    # 27/11/1404 (day first if year > 31)
    m = re.search(r"\b(0?[1-9]|[12]\d|3[01])[/\-](0?[1-9]|1[0-2])[/\-](1[34]\d{2})\b", t)
    if m:
        try:
            return jalali_to_gregorian(int(m.group(3)), int(m.group(2)), int(m.group(1)))
        except ValueError:
            pass

    today_jalali = _today_jalali()
    current_jalali_year = today_jalali.year

    # "27 بهمن 1404" or "بهمن 27 1404" or "بهمن 1404" (with explicit year)
    for name, month_num in _MONTH_NAMES.items():
        pattern = re.compile(
            r"(?:(\d{1,2})\s+" + re.escape(name) + r"\s+(\d{4}))|"
            r"(?:" + re.escape(name) + r"\s+(\d{1,2})\s+(\d{4}))|"
            r"(?:" + re.escape(name) + r"\s+(\d{4}))",
            re.IGNORECASE,
        )
        match = pattern.search(_to_ascii(text))
        if match:
            groups = match.groups()
            if groups[0] and groups[1]:
                day_val, year_val = int(groups[0]), int(groups[1])
            elif groups[2] and groups[3]:
                day_val, year_val = int(groups[2]), int(groups[3])
            elif groups[4]:
                day_val, year_val = 1, int(groups[4])
            else:
                continue
            if year_val in _JALALI_YEAR_RANGE:
                try:
                    return jalali_to_gregorian(year_val, month_num, day_val)
                except ValueError:
                    pass

    # Month name WITHOUT year: "4th of Esfand", "4 Esfand", "Esfand 4", "بهمن ۲۷"
    # Infer the most likely Jalali year: if the resulting date would be more than
    # 6 months in the future, assume the user meant the previous year.
    ascii_text = _to_ascii(text)
    for name, month_num in _MONTH_NAMES.items():
        # "4th of Esfand", "4 of Esfand", "4 Esfand", "27 بهمن"
        pat_day_month = re.compile(
            r"(\d{1,2})(?:st|nd|rd|th)?\s+(?:of\s+)?" + re.escape(name) + r"(?:\s|$|[.,;!?])",
            re.IGNORECASE,
        )
        match = pat_day_month.search(ascii_text)
        if match:
            day_val = int(match.group(1))
            result = _resolve_jalali_no_year(current_jalali_year, today_jalali, month_num, day_val)
            if result is not None:
                return result

        # "Esfand 4", "Esfand 4th", "بهمن 27"
        pat_month_day = re.compile(
            re.escape(name) + r"\s+(\d{1,2})(?:st|nd|rd|th)?(?:\s|$|[.,;!?])",
            re.IGNORECASE,
        )
        match = pat_month_day.search(ascii_text)
        if match:
            day_val = int(match.group(1))
            result = _resolve_jalali_no_year(current_jalali_year, today_jalali, month_num, day_val)
            if result is not None:
                return result

    return None


def _today_jalali() -> jdatetime.date:
    """Today on the Tehran clock (UTC+3:30), not the server's: between 20:30
    and midnight UTC the server's date is a day behind Iran's."""
    from datetime import datetime, timedelta, timezone
    now = datetime.now(timezone(timedelta(hours=3, minutes=30)))
    return jdatetime.date.fromgregorian(date=now.date())


def nearest_jalali_date(month: int, day: int, today_jalali: jdatetime.date | None = None) -> date | None:
    """The Gregorian date of Jalali month/day in the year that puts it
    closest to today — last, this or next year. Around Nowruz this is what
    people mean: "5 فروردین" typed on 28 Esfand is next week, and "25 اسفند"
    typed on 5 Farvardin was ten days ago. (It used to try only this year and
    last year, so early-Farvardin dates typed in Esfand landed a year back.)
    A day that doesn't exist in a year (30 Esfand in a common year) skips it."""
    today_jalali = today_jalali or _today_jalali()
    best: tuple[int, jdatetime.date] | None = None
    for year in (today_jalali.year - 1, today_jalali.year, today_jalali.year + 1):
        try:
            candidate = jdatetime.date(year, month, day)
        except ValueError:
            continue
        distance = abs((candidate - today_jalali).days)
        if best is None or distance < best[0]:
            best = (distance, candidate)
    if best is None:
        return None
    gd = best[1].togregorian()
    return date(gd.year, gd.month, gd.day)


def _resolve_jalali_no_year(
    current_year: int,
    today_jalali: jdatetime.date,
    month: int,
    day: int,
) -> date | None:
    """Kept for callers; see ``nearest_jalali_date``."""
    return nearest_jalali_date(month, day, today_jalali)


def find_and_replace_jalali_dates(text: str) -> tuple[str, list[tuple[str, date]]]:
    """
    Find all Jalali dates in text, replace them with YYYY-MM-DD Gregorian.
    Returns (new_text, [(original_match, gregorian_date), ...]).
    """
    if not text:
        return text, []

    replacements: list[tuple[str, date]] = []
    result = text

    ascii_text = _to_ascii(text)

    # Pattern: YYYY/MM/DD or YYYY-MM-DD (Jalali year range)
    for m in re.finditer(r"\b(1[34]\d{2})[/\-](0?[1-9]|1[0-2])[/\-](0?[1-9]|[12]\d|3[01])\b", ascii_text):
        try:
            gd = jalali_to_gregorian(int(m.group(1)), int(m.group(2)), int(m.group(3)))
            original = text[m.start():m.end()]
            replacements.append((original, gd))
            result = result.replace(original, gd.isoformat(), 1)
        except ValueError:
            pass

    # Pattern: DD/MM/YYYY (day-first Jalali)
    for m in re.finditer(r"\b(0?[1-9]|[12]\d|3[01])[/\-](0?[1-9]|1[0-2])[/\-](1[34]\d{2})\b", ascii_text):
        y, mo, d = int(m.group(3)), int(m.group(2)), int(m.group(1))
        try:
            gd = jalali_to_gregorian(y, mo, d)
            original = text[m.start():m.end()]
            if original not in [r[0] for r in replacements]:
                replacements.append((original, gd))
                result = result.replace(original, gd.isoformat(), 1)
        except ValueError:
            pass

    # Month name patterns (with year)
    for name, month_num in _MONTH_NAMES.items():
        pattern = re.compile(
            r"(\d{1,2})\s+" + re.escape(name) + r"\s+(\d{4})",
            re.IGNORECASE,
        )
        for match in pattern.finditer(_to_ascii(text)):
            d_val, y_val = int(match.group(1)), int(match.group(2))
            if y_val in _JALALI_YEAR_RANGE:
                try:
                    gd = jalali_to_gregorian(y_val, month_num, d_val)
                    original = text[match.start():match.end()]
                    if original not in [r[0] for r in replacements]:
                        replacements.append((original, gd))
                        result = result.replace(original, gd.isoformat(), 1)
                except ValueError:
                    pass

    # Month name patterns WITHOUT year: "4th of Esfand", "4 Esfand", "بهمن 27"
    today_jalali = jdatetime.date.today()
    current_jalali_year = today_jalali.year
    for name, month_num in _MONTH_NAMES.items():
        for pat in [
            re.compile(r"(\d{1,2})(?:st|nd|rd|th)?\s+(?:of\s+)?" + re.escape(name) + r"(?:\s|$|[.,;!?])", re.IGNORECASE),
            re.compile(re.escape(name) + r"\s+(\d{1,2})(?:st|nd|rd|th)?(?:\s|$|[.,;!?])", re.IGNORECASE),
        ]:
            for match in pat.finditer(ascii_text):
                d_val = int(match.group(1))
                gd = _resolve_jalali_no_year(current_jalali_year, today_jalali, month_num, d_val)
                if gd is not None:
                    original = text[match.start():match.end()].rstrip(" .,;!?")
                    if original not in [r[0] for r in replacements]:
                        replacements.append((original, gd))
                        result = result.replace(original, gd.isoformat(), 1)

    return result, replacements
