"""Content-Disposition for any file name (RFC 6266, RFC 5987).

HTTP headers are latin-1. A download named from the data — a payslip for
"سارا احمدی", a statement for a Persian client, invoice "۱۴۰۵-۰۱۲", an uploaded
"رسید.jpg" — used to raise while the response was sent, so the download came
back as a 500. ``filename`` now carries an ASCII fallback and ``filename*`` the
real name, UTF-8 and percent-encoded; every browser in use reads the second.
"""
from __future__ import annotations

import re
import unicodedata
from urllib.parse import quote

_DIGITS = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")
_UNSAFE = re.compile(r"[^A-Za-z0-9._-]+")


def ascii_filename(name: str, default: str = "download") -> str:
    """The name in plain ASCII: Persian/Arabic digits as 0-9, accents dropped,
    anything else a single underscore."""
    text = unicodedata.normalize("NFKD", (name or "").translate(_DIGITS))
    text = text.encode("ascii", "ignore").decode("ascii")
    text = _UNSAFE.sub("_", text).strip("._") or default
    return text[:150]


def content_disposition(filename: str, *, inline: bool = False) -> str:
    name = re.sub(r"[\r\n\t\"\\\\]+", " ", filename or "").strip() or "download"
    kind = "inline" if inline else "attachment"
    fallback = ascii_filename(name)
    if fallback == name:
        return f'{kind}; filename="{name}"'
    return f"{kind}; filename=\"{fallback}\"; filename*=UTF-8''{quote(name, safe='')}"
