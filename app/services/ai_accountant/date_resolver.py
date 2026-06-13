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
_YESTERDAY_TERMS = ("yesterday", "دیروز", "ayer", "أمس", "امس", "البارحة")
_TOMORROW_TERMS = ("tomorrow", "فردا", "mañana", "manana", "غدا", "غدًا", "بكرة")
_DAY_BEFORE_YESTERDAY = ("day before yesterday", "پریروز", "anteayer", "أول أمس", "اول امس")

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


def _norm(text: str | None) -> str:
    return (text or "").translate(_DIGIT_TABLE).lower()


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


def resolve_entry_date(
    message: str | None,
    model_date: date,
    *,
    today: date | None = None,
    has_attachment: bool = False,
) -> date:
    """Anchor a chat proposal's entry date to reality (see module docstring).

    ``model_date`` is the date the model put on the proposal; ``message`` is
    the user's original text. Document/OCR turns (``has_attachment``) keep the
    model's (document-derived) date unchanged.
    """
    today = today or date.today()
    if has_attachment:
        return model_date

    rel = relative_offset_date(message, today)
    if rel is not None:
        return rel

    # An explicit absolute date in the message → trust the model's parse of it
    # (even if far in the past, e.g. backdating an old entry).
    if has_explicit_absolute_date(message):
        return model_date

    # No date stated at all → today (don't trust the model's guess). The same
    # branch re-anchors a model date that drifted >1 year off with no basis.
    return today
