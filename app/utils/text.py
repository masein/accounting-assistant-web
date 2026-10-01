"""Arabic and Persian letterforms compared as one — for matching, never for storing.

The same name reaches the books with either ي or ی, ك or ک (an Arabic keyboard,
a bank's PDF, an Excel export): searching «علی» didn't find «علي», and a
second «شرکت پارس» was created next to «شركت پارس». Stored names stay as they
were typed (ي and ك are the right letters in Arabic); comparisons fold both
sides with ``fold_fa`` (a Python string) or ``fold_sql`` (a column).
"""
from __future__ import annotations

from sqlalchemy import func

_PAIRS = (("ي", "ی"), ("ى", "ی"), ("ك", "ک"))
_TABLE = str.maketrans({a: b for a, b in _PAIRS})


def fold_fa(text):
    """``text`` with ي/ى→ی and ك→ک; anything not a string as it is."""
    return text.translate(_TABLE) if isinstance(text, str) else text


def fold_sql(column):
    """The SQL expression of ``column`` with the same letters folded (PostgreSQL and SQLite)."""
    expr = column
    for a, b in _PAIRS:
        expr = func.replace(expr, a, b)
    return expr
