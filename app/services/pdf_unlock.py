"""Password-protected PDF statements (roadmap 2026-09 §4.1).

Several banks lock the PDF statements they e-mail, often with the customer's
national ID. Neither the page renderer nor the text reader can open a locked
file, so the statement import unlocks it first:

* a PDF that isn't locked, or only restricts printing/copying (an owner
  password with an empty user password), opens as it is;
* otherwise the password is tried as typed and with Persian or Arabic digits
  turned into ASCII (a national ID typed on a Persian keyboard);
* a locked PDF without the right password raises ``PasswordNeeded``, and the
  import answers ``needs_password`` instead of failing.

The unlocked copy only lives in memory and in the parser's temporary file;
the password is never stored with the statement.
"""
from __future__ import annotations

import io

MAX_PASSWORD = 128
_DIGITS = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")


class PasswordNeeded(Exception):
    """The PDF is locked and no password (``wrong=False``) or a wrong one opened it."""

    def __init__(self, *, wrong: bool) -> None:
        super().__init__("the PDF password isn't right" if wrong else "the PDF is password-protected")
        self.wrong = wrong


def _candidates(password: str | None) -> list[str]:
    out = [""]
    if password:
        for p in (password, password.strip(), password.strip().translate(_DIGITS)):
            if p and p not in out:
                out.append(p)
    return out


def is_locked(data: bytes) -> bool:
    """A PDF that can't be read without a password."""
    try:
        unlock(data, None)
    except PasswordNeeded:
        return True
    return False


def unlock(data: bytes, password: str | None) -> bytes:
    """The PDF's bytes, unlocked. Anything that isn't a readable PDF comes back
    unchanged (the parser reports it); a locked one needs its password."""
    from pypdf import PdfReader, PdfWriter
    try:
        reader = PdfReader(io.BytesIO(data))
        encrypted = reader.is_encrypted
    except Exception:  # noqa: BLE001 — not a PDF we can read: leave it to the parser
        return data
    if not encrypted:
        return data
    for candidate in _candidates(password):
        attempt = PdfReader(io.BytesIO(data))
        try:
            opened = attempt.decrypt(candidate)
        except Exception:  # noqa: BLE001 — an unsupported scheme is the same as a wrong password
            opened = 0
        if opened:
            out = io.BytesIO()
            PdfWriter(clone_from=attempt).write(out)
            return out.getvalue()
    raise PasswordNeeded(wrong=bool(password))
