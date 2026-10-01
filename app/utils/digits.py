"""Persian (۰–۹) and Arabic-Indic (٠–٩) digits as 0–9.

An identifier typed on a Persian keyboard — a national ID, an economic code,
a postal code, a phone number, an IBAN — arrives in those digits; stored as
they came, it failed the IBAN check ("IBAN must be 2 letters…") and reached
the Moadian export and bank files in a form neither accepts.
"""
from __future__ import annotations

_TABLE = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")


def ascii_digits(value):
    """``value`` with its Persian/Arabic-Indic digits as 0–9; anything not a string as it is."""
    return value.translate(_TABLE) if isinstance(value, str) else value
