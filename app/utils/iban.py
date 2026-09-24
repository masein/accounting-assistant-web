"""IBAN normalisation + validation (ISO 13616 structure and mod-97 check).

Entities used to accept any string as an IBAN ("not-an-iban" → 201); a wrong
IBAN on an employee or supplier means a failed payment run later.
"""
from __future__ import annotations

import re

_IBAN_RE = re.compile(r"^[A-Z]{2}\d{2}[A-Z0-9]{11,30}$")
# Country-specific fixed lengths for the markets we serve; other countries
# are accepted on structure + checksum alone.
_LENGTHS = {"IR": 26, "GB": 22, "DE": 22, "FR": 27, "TR": 26, "AE": 23, "ES": 24, "NL": 18, "IT": 27}


def normalize_iban(value: str | None) -> str | None:
    """Return the canonical IBAN (upper-case, no separators) or None for an
    empty value. Raises ValueError with a plain-language reason otherwise."""
    if value is None:
        return None
    iban = re.sub(r"[\s\-]", "", str(value)).upper()
    if not iban:
        return None
    if not _IBAN_RE.match(iban):
        raise ValueError("IBAN must be 2 letters, 2 check digits and 11–30 letters/digits (e.g. IR82 0540 1026 8002 0817 9090 02).")
    expected = _LENGTHS.get(iban[:2])
    if expected and len(iban) != expected:
        raise ValueError(f"A {iban[:2]} IBAN is {expected} characters long; this one has {len(iban)}.")
    rearranged = iban[4:] + iban[:4]
    numeric = "".join(str(int(ch, 36)) for ch in rearranged)
    if int(numeric) % 97 != 1:
        raise ValueError("IBAN check digits do not match — please re-check the number.")
    return iban
