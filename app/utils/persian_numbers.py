"""
Parse Persian number words (e.g. پنج میلیون) to integers,
and handle Toman-to-Rial conversion.
"""
from __future__ import annotations

import re

_ONES = {
    "صفر": 0, "یک": 1, "دو": 2, "سه": 3, "چهار": 4,
    "پنج": 5, "شش": 6, "شیش": 6, "هفت": 7, "هشت": 8, "نه": 9,
    "ده": 10, "یازده": 11, "دوازده": 12, "سیزده": 13, "چهارده": 14,
    "پانزده": 15, "پونزده": 15, "شانزده": 16, "هفده": 17, "هجده": 18, "نوزده": 19,
    "بیست": 20, "سی": 30, "چهل": 40, "پنجاه": 50,
    "شصت": 60, "هفتاد": 70, "هشتاد": 80, "نود": 90,
    "صد": 100, "یکصد": 100, "دویست": 200, "سیصد": 300,
    "چهارصد": 400, "پانصد": 500, "ششصد": 600,
    "هفتصد": 700, "هشتصد": 800, "نهصد": 900,
    "نیم": 0,  # handled specially via _HALF
}

_MULTIPLIERS = {
    "هزار": 1_000,
    "میلیون": 1_000_000,
    "ملیون": 1_000_000,
    "میلیارد": 1_000_000_000,
}

_HALF_WORDS = {"نیم", "نصف"}


def _persian_to_ascii(text: str) -> str:
    mapping = str.maketrans("۰۱۲۳۴۵۶۷۸۹", "0123456789")
    return text.translate(mapping)


def parse_persian_number(text: str) -> int | None:
    """
    Parse Persian number words to integer.
    Examples:
      "پنج میلیون" -> 5_000_000
      "سه هزار و پانصد" -> 3_500
      "دو و نیم میلیون" -> 2_500_000
      "صد و بیست هزار" -> 120_000
    Returns None if no Persian number pattern found.
    """
    if not text:
        return None
    t = text.strip()
    t = t.replace("\u200c", " ")  # ZWNJ -> space
    t = re.sub(r"\s+", " ", t).strip()

    tokens = re.split(r"\s+و\s+|\s+", t)
    tokens = [tok.strip() for tok in tokens if tok.strip()]

    if not tokens:
        return None

    has_any_persian_word = any(tok in _ONES or tok in _MULTIPLIERS or tok in _HALF_WORDS for tok in tokens)
    if not has_any_persian_word:
        return None

    result = 0
    current = 0
    has_half = False

    for tok in tokens:
        if tok in _HALF_WORDS:
            has_half = True
            continue
        if tok in _ONES:
            current += _ONES[tok]
        elif tok in _MULTIPLIERS:
            mult = _MULTIPLIERS[tok]
            if current == 0:
                current = 1
            result += current * mult
            if has_half:
                result += mult // 2
                has_half = False
            current = 0
        else:
            continue

    result += current
    return result if result > 0 else None


_TOMAN_PATTERNS = re.compile(
    r"(?:toman[sa]?|تومان|تومن|tomans?)\b",
    re.IGNORECASE,
)


def is_toman_amount(text: str) -> bool:
    """Check if the text explicitly mentions Toman currency."""
    return bool(_TOMAN_PATTERNS.search(text or ""))


def toman_to_rial(amount: int) -> int:
    return amount * 10


def parse_amount_with_currency(text: str) -> tuple[int, bool]:
    """
    Parse an amount from text, detecting Toman suffix.
    Returns (amount_in_rials, was_toman).
    If toman detected, the returned amount is already converted to rials.
    """
    is_toman = is_toman_amount(text)

    cleaned = _TOMAN_PATTERNS.sub("", text).strip()
    cleaned = re.sub(r"(?:irr|rial[sa]?|ریال)\b", "", cleaned, flags=re.IGNORECASE).strip()

    persian_amount = parse_persian_number(cleaned)
    if persian_amount is not None:
        return (toman_to_rial(persian_amount) if is_toman else persian_amount, is_toman)

    ascii_text = _persian_to_ascii(cleaned)
    ascii_text = ascii_text.replace(",", "").replace("_", "")

    m = re.search(r"(\d+(?:\.\d+)?)\s*([mkb])\b", ascii_text, re.IGNORECASE)
    if m:
        n = float(m.group(1))
        unit = m.group(2).lower()
        mult = {"k": 1_000, "m": 1_000_000, "b": 1_000_000_000}[unit]
        val = int(n * mult)
        return (toman_to_rial(val) if is_toman else val, is_toman)

    m = re.search(r"(\d+(?:\.\d+)?)\s*million", ascii_text, re.IGNORECASE)
    if m:
        val = int(float(m.group(1)) * 1_000_000)
        return (toman_to_rial(val) if is_toman else val, is_toman)

    m = re.search(r"\d+", ascii_text)
    if m:
        val = int(m.group(0))
        return (toman_to_rial(val) if is_toman else val, is_toman)

    return (0, False)
