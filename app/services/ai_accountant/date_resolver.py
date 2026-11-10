"""Deterministic entry-date resolution for chat-driven proposals.

The conversational model is unreliable at resolving relative dates: it dated
a "…today" expense 2023-10-18, and mid-session copied an earlier invoice's
date instead of today. ``get_company_defaults`` already returns the correct
``date.today()``, but the model ignores it.

So we anchor the date server-side from the user's own words instead of
trusting the model:

* a relative term ("today", "yesterday", "last tuesday", "3 days ago", and
  the fa/es/ar equivalents) → computed from the server's current date;
* no date mentioned at all → today;
* an explicit absolute date in the message ("on 3 March", "2026-02-10") →
  keep the model's date (it parsed something concrete);
* a model date >1 year from today with no explicit absolute date in the
  message → re-anchor to today (catches the 2023-10-18 hallucination).

Document/OCR turns are left untouched (``has_attachment``): the invoice's own
date is authoritative there.
"""
from __future__ import annotations

import re
from datetime import date, timedelta

# Persian/Arabic-Indic digits → ASCII, so "۳ روز پیش" / "٣ أيام" parse.
_DIGIT_TABLE = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")

# Phrase → day offset from today. Lowercased substring match.
_TODAY_TERMS = ("today", "tonight", "right now", "امروز", "الان", "امشب", "hoy", "ahora", "اليوم", "الآن")
# «دیشب» is last night: «شام دیشب» was dated today (chat review 2026-11-10, BO1)
_YESTERDAY_TERMS = ("yesterday", "last night", "دیروز", "دیشب", "ayer", "anoche", "أمس", "امس", "البارحة")
_TOMORROW_TERMS = ("tomorrow", "فردا", "mañana", "manana", "غدا", "غدًا", "بكرة")
_DAY_BEFORE_YESTERDAY = ("day before yesterday", "پریروز", "پریشب", "anteayer", "أول أمس", "اول امس")

_WEEKDAYS = {
    "monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3,
    "friday": 4, "saturday": 5, "sunday": 6,
}

# "<n> days ago" across languages (digits already normalized to ASCII).
_DAYS_AGO_RES = (
    re.compile(r"\b(\d{1,4})\s*days?\s*ago\b", re.IGNORECASE),
    re.compile(r"\bhace\s*(\d{1,4})\s*d[ií]as?\b", re.IGNORECASE),
    re.compile(r"(\d{1,4})\s*روز\s*(?:پیش|قبل)"),
    re.compile(r"قبل\s*(\d{1,4})\s*(?:يوم|أيام|ايام)"),
)
_WEEKS_AGO_RE = re.compile(r"\b(\d{1,3})\s*weeks?\s*ago\b", re.IGNORECASE)

# Explicit absolute-date shapes. ISO, numeric d/m/y or y/m/d (incl. Jalali),
# and a day-number adjacent to an English month name.
_MONTHS = (
    "january|february|march|april|may|june|july|august|september|october|"
    "november|december|jan|feb|mar|apr|jun|jul|aug|sep|sept|oct|nov|dec"
)
_ABSOLUTE_RES = (
    re.compile(r"\b\d{4}[-/]\d{1,2}[-/]\d{1,2}\b"),       # ISO / Jalali y/m/d
    re.compile(r"\b\d{1,2}[-/]\d{1,2}[-/]\d{2,4}\b"),      # d/m/y or m/d/y
    re.compile(rf"\b\d{{1,2}}(?:st|nd|rd|th)?\s+(?:{_MONTHS})\b", re.IGNORECASE),
    re.compile(rf"\b(?:{_MONTHS})\s+\d{{1,2}}(?:st|nd|rd|th)?\b", re.IGNORECASE),
)


# Spelled-out small numbers → digits, so "two days ago" parses like "2 days
# ago". English + a few Spanish forms (digits already handle fa/ar numerals).
_NUMBER_WORDS = {
    "a": "1", "an": "1", "one": "1", "two": "2", "three": "3", "four": "4",
    "five": "5", "six": "6", "seven": "7", "eight": "8", "nine": "9", "ten": "10",
    "eleven": "11", "twelve": "12", "thirteen": "13", "fourteen": "14",
    "fifteen": "15", "twenty": "20", "thirty": "30",
    "un": "1", "una": "1", "dos": "2", "tres": "3", "cuatro": "4", "cinco": "5",
    "seis": "6", "siete": "7", "ocho": "8", "nueve": "9", "diez": "10",
    "couple": "2",
}
_NUMBER_WORD_RE = re.compile(
    r"\b(" + "|".join(sorted(_NUMBER_WORDS, key=len, reverse=True)) + r")\b(?=\s+(?:days?|weeks?|d[ií]as?|روز|semanas?|weeks?))",
    re.IGNORECASE,
)


def _words_to_digits(t: str) -> str:
    return _NUMBER_WORD_RE.sub(lambda m: _NUMBER_WORDS[m.group(1).lower()], t)


def _norm(text: str | None) -> str:
    return _words_to_digits((text or "").translate(_DIGIT_TABLE).lower())


def has_explicit_absolute_date(message: str | None) -> bool:
    """True if the message names a concrete calendar date (not a relative
    term). Conservative: a month word alone (e.g. "the March invoice") does
    not count — a day number must sit next to it."""
    t = _norm(message)
    return any(rx.search(t) for rx in _ABSOLUTE_RES)


def relative_offset_date(message: str | None, today: date) -> date | None:
    """Return the date implied by a relative term in ``message``, or None if
    none is present. Checks most-specific phrases first."""
    t = _norm(message)
    if not t:
        return None

    if any(term in t for term in _DAY_BEFORE_YESTERDAY):
        return today - timedelta(days=2)
    if any(term in t for term in _YESTERDAY_TERMS):
        return today - timedelta(days=1)
    if any(term in t for term in _TOMORROW_TERMS):
        return today + timedelta(days=1)

    for rx in _DAYS_AGO_RES:
        m = rx.search(t)
        if m:
            return today - timedelta(days=int(m.group(1)))
    m = _WEEKS_AGO_RE.search(t)
    if m:
        return today - timedelta(weeks=int(m.group(1)))

    # last/next/this/on <weekday> (English). Requires a qualifier so a stray
    # weekday word in a name/description isn't misread as a date.
    for name, idx in _WEEKDAYS.items():
        if re.search(rf"\bnext\s+{name}\b", t):
            delta = (idx - today.weekday()) % 7
            return today + timedelta(days=delta or 7)
        if re.search(rf"\blast\s+{name}\b", t):
            delta = (today.weekday() - idx) % 7
            return today - timedelta(days=delta or 7)
        if re.search(rf"\b(?:this|on|past)\s+{name}\b", t):
            delta = (today.weekday() - idx) % 7
            return today - timedelta(days=delta)

    if any(term in t for term in _TODAY_TERMS) or re.search(r"\bnow\b", t):
        return today

    return None


# Jalali month names (Persian + Arabic letterforms). A DAY NUMBER must
# precede the month («۴ مرداد», «۱۲ دی ماه») — the bare word دی is also a
# bank name, so a month word alone never counts as a date.
_JALALI_MONTHS = {
    "فروردین": 1, "فروردين": 1, "اردیبهشت": 2, "ارديبهشت": 2, "خرداد": 3,
    "تیر": 4, "تير": 4, "مرداد": 5, "امرداد": 5, "شهریور": 6, "شهريور": 6,
    "مهر": 7, "آبان": 8, "ابان": 8, "آذر": 9, "اذر": 9, "دی": 10, "دي": 10,
    "بهمن": 11, "اسفند": 12,
}
_JALALI_DAY_MONTH_RE = re.compile(
    r"(?<![\d/])(\d{1,2})(?:ام|م)?\s*(" + "|".join(_JALALI_MONTHS) + r")(?:\s*ماه)?(?:\s*(1[34]\d{2}))?"
)
# …and in Latin letters, «4th of Mordad», "Mordad 4" (BO5). «Dey» only: "day" is an English word.
_JALALI_LATIN = {
    "farvardin": 1, "ordibehesht": 2, "khordad": 3, "tir": 4, "mordad": 5, "amordad": 5, "shahrivar": 6,
    "mehr": 7, "aban": 8, "azar": 9, "dey": 10, "bahman": 11, "esfand": 12,
}
_LATIN_NAMES = "|".join(sorted(_JALALI_LATIN, key=len, reverse=True))
_JALALI_LATIN_RES = (
    re.compile(rf"\b(\d{{1,2}})(?:st|nd|rd|th)?\s+(?:of\s+)?({_LATIN_NAMES})\b(?:\s*,?\s*(1[34]\d{{2}}))?", re.IGNORECASE),
    re.compile(rf"\b({_LATIN_NAMES})\s+(\d{{1,2}})(?:st|nd|rd|th)?\b(?:\s*,?\s*(1[34]\d{{2}}))?", re.IGNORECASE),
)


def _jalali(day: int, month: int, year: str | None, today: date) -> date | None:
    if not 1 <= day <= 31:
        return None
    try:
        from app.utils.jalali import jalali_to_gregorian

        if year:
            return jalali_to_gregorian(int(year), month, day)
        # No year: the nearest one to today (around Nowruz the current Jalali
        # year is often the wrong one; see nearest_jalali_date).
        import jdatetime
        from app.utils.jalali import nearest_jalali_date
        return nearest_jalali_date(month, day, jdatetime.date.fromgregorian(date=today))
    except Exception:
        return None


def jalali_named_dates(message: str | None, today: date) -> list[date]:
    """Every «۴ مرداد [ماه] [۱۴۰۵]» (or «4th of Mordad») in the message, in its order, as Gregorian dates."""
    t = (message or "").translate(_DIGIT_TABLE)
    found: list[tuple[int, date]] = []
    for m in _JALALI_DAY_MONTH_RE.finditer(t):
        d = _jalali(int(m.group(1)), _JALALI_MONTHS[m.group(2)], m.group(3), today)
        if d:
            found.append((m.start(), d))
    for i, rx in enumerate(_JALALI_LATIN_RES):
        for m in rx.finditer(t):
            day, name = (m.group(1), m.group(2)) if i == 0 else (m.group(2), m.group(1))
            d = _jalali(int(day), _JALALI_LATIN[name.lower()], m.group(3), today)
            if d:
                found.append((m.start(), d))
    return [d for _at, d in sorted(found, key=lambda x: x[0])]


def jalali_named_date(message: str | None, today: date) -> date | None:
    """The first «۴ مرداد [ماه] [۱۴۰۵]» → the Gregorian date, resolved server-side (the
    model reliably ignores Jalali dates and stamps today instead). Year
    defaults to the nearest Jalali year."""
    found = jalali_named_dates(message, today)
    return found[0] if found else None


def mentioned_dates(message: str | None, today: date) -> set[date]:
    """Every day the message names: each relative word and each Jalali date. «دیشب … امروز …» names two (BO2)."""
    t = _norm(message)
    if not t:
        return set()
    out: set[date] = set(jalali_named_dates(message, today))
    for terms, offset in ((_TODAY_TERMS, 0), (_YESTERDAY_TERMS, -1), (_DAY_BEFORE_YESTERDAY, -2), (_TOMORROW_TERMS, 1)):
        if any(term in t for term in terms):
            out.add(today + timedelta(days=offset))
    for rx in _DAYS_AGO_RES:
        out |= {today - timedelta(days=int(m.group(1))) for m in rx.finditer(t)}
    out |= {today - timedelta(weeks=int(m.group(1))) for m in _WEEKS_AGO_RE.finditer(t)}
    rel = relative_offset_date(message, today)
    if rel is not None:
        out.add(rel)
    return out


def resolve_entry_date(
    message: str | None,
    model_date: date,
    *,
    today: date | None = None,
    has_attachment: bool = False,
    scheduled: bool = False,
    earlier: list[str] | tuple[str, ...] = (),
    replaced_date: date | None = None,
) -> date:
    """Anchor a chat proposal's entry date to reality (see module docstring).

    ``model_date`` is the date the model put on the proposal; ``message`` is
    the user's original text. Document/OCR turns (``has_attachment``) keep the
    model's (document-derived) date unchanged. ``scheduled`` keeps the model's
    future date when the user explicitly intends a scheduled entry (e.g.
    "schedule it for next month"), which our relative parser doesn't pin to a
    specific day.

    ``earlier`` are the user's messages just before this one: a follow-up that
    names no day («proceed», «رقم همینه») keeps a day said there (BO3).
    ``replaced_date`` is the date of the card this one corrects (BO4).
    """
    today = today or date.today()
    if has_attachment:
        return model_date

    # Several days in one message («دیشب … امروز …»): each card keeps the one of them the model gave it (BO2).
    said_now = mentioned_dates(message, today)
    if model_date in said_now:
        return model_date

    rel = relative_offset_date(message, today)
    if rel is not None:
        return rel

    # An explicit Jalali date («۴ مرداد») → resolve it ourselves; the model
    # reliably ignores these and stamps today.
    jal = jalali_named_date(message, today)
    if jal is not None:
        return jal

    # An explicit absolute date in the message → trust the model's parse of it
    # (even if far in the past, e.g. backdating an old entry).
    if has_explicit_absolute_date(message):
        return model_date

    # Explicitly-scheduled future entry with only a vague term ("next month")
    # → keep the model's future date rather than collapsing it to today.
    if scheduled and model_date > today:
        return model_date

    # No day in this message: a correction keeps the day of the card it corrects (BO4), and a follow-up
    # the day the user gave just before (BO3).
    if replaced_date is not None:
        return replaced_date
    if any(model_date in mentioned_dates(m, today) for m in earlier):
        return model_date

    # No date stated at all → today (don't trust the model's guess). The same
    # branch re-anchors a model date that drifted >1 year off with no basis.
    return today
