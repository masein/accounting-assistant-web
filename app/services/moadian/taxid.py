"""The 22-character unique tax number (شماره منحصربه‌فرد مالیاتی).

Layout, as in the tax organisation's technical instructions and both
public SDKs (github.com/Jooyeshgar/moadian, github.com/arjavand/moadian):

    memory id (6)  +  hex(days since 1970-01-01) (5)  +  hex(serial) (10)  +  check digit (1)

The check digit is Verhoeff over the DECIMAL form of the same data: the
memory id with each letter replaced by its ASCII code, the day number
zero-padded to 6 digits and the serial zero-padded to 12.
"""
from __future__ import annotations

import re
from datetime import date

_D = (
    (0, 1, 2, 3, 4, 5, 6, 7, 8, 9), (1, 2, 3, 4, 0, 6, 7, 8, 9, 5), (2, 3, 4, 0, 1, 7, 8, 9, 5, 6),
    (3, 4, 0, 1, 2, 8, 9, 5, 6, 7), (4, 0, 1, 2, 3, 9, 5, 6, 7, 8), (5, 9, 8, 7, 6, 0, 4, 3, 2, 1),
    (6, 5, 9, 8, 7, 1, 0, 4, 3, 2), (7, 6, 5, 9, 8, 2, 1, 0, 4, 3), (8, 7, 6, 5, 9, 3, 2, 1, 0, 4),
    (9, 8, 7, 6, 5, 4, 3, 2, 1, 0),
)
_P = (
    (0, 1, 2, 3, 4, 5, 6, 7, 8, 9), (1, 5, 7, 6, 2, 8, 3, 0, 9, 4), (5, 8, 0, 3, 7, 9, 6, 1, 4, 2),
    (8, 9, 1, 6, 0, 4, 3, 5, 2, 7), (9, 4, 5, 3, 1, 2, 6, 8, 7, 0), (4, 2, 8, 6, 5, 7, 3, 9, 0, 1),
    (2, 7, 9, 3, 8, 0, 6, 4, 1, 5), (7, 0, 4, 6, 9, 1, 3, 2, 5, 8),
)
_INV = (0, 4, 3, 2, 1, 5, 6, 7, 8, 9)

MEMORY_ID_RE = re.compile(r"^[A-Z0-9]{6}$")
MAX_SERIAL = 16 ** 10 - 1
EPOCH = date(1970, 1, 1)


def verhoeff_digit(number: str) -> int:
    c = 0
    for i, ch in enumerate(reversed(number)):
        c = _D[c][_P[(i + 1) % 8][int(ch)]]
    return _INV[c]


def verhoeff_valid(number: str) -> bool:
    c = 0
    for i, ch in enumerate(reversed(number)):
        c = _D[c][_P[i % 8][int(ch)]]
    return c == 0


def normalize_memory_id(value: str | None) -> str:
    return (value or "").strip().upper()


def valid_memory_id(value: str | None) -> bool:
    return bool(MEMORY_ID_RE.match(normalize_memory_id(value)))


def _decimal_memory_id(memory_id: str) -> str:
    return "".join(ch if ch.isdigit() else str(ord(ch)) for ch in memory_id)


def generate_taxid(memory_id: str, issue_date: date, serial: int) -> str:
    mid = normalize_memory_id(memory_id)
    if not MEMORY_ID_RE.match(mid):
        raise ValueError("The tax memory id (شناسه یکتای حافظه مالیاتی) must be 6 letters or digits.")
    if not (0 < int(serial) <= MAX_SERIAL):
        raise ValueError("Serial out of range.")
    days = (issue_date - EPOCH).days
    decimal = _decimal_memory_id(mid) + f"{days:06d}" + f"{int(serial):012d}"
    return f"{mid}{days:05X}{int(serial):010X}{verhoeff_digit(decimal)}".upper()


def taxid_is_valid(taxid: str) -> bool:
    """Structural check + check digit (used by the tests and on import)."""
    t = (taxid or "").strip().upper()
    if len(t) != 22 or not MEMORY_ID_RE.match(t[:6]):
        return False
    try:
        days, serial, check = int(t[6:11], 16), int(t[11:21], 16), t[21]
    except ValueError:
        return False
    if not check.isdigit():
        return False
    decimal = _decimal_memory_id(t[:6]) + f"{days:06d}" + f"{serial:012d}"
    return verhoeff_valid(decimal + check)
