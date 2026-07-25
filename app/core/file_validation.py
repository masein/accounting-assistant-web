"""Validate file uploads by checking magic bytes (file signatures)."""
from __future__ import annotations

from fastapi import HTTPException

# Map MIME types to their expected magic byte signatures
_MAGIC_SIGNATURES: dict[str, list[tuple[bytes, int]]] = {
    # (signature_bytes, offset)
    "image/jpeg": [(b"\xff\xd8\xff", 0)],
    "image/png": [(b"\x89PNG\r\n\x1a\n", 0)],
    "image/webp": [(b"RIFF", 0), (b"WEBP", 8)],  # Must match BOTH
    "application/pdf": [(b"%PDF", 0)],
    # .xlsx is a ZIP container; legacy binary .xls is an OLE2 compound file.
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": [(b"PK", 0)],
}

# Spreadsheet types that are TEXT on disk (no reliable magic): CSV/TSV, and the
# SpreadsheetML 2003 ".xls" XML exports Iranian accounting systems produce.
# Validated as "printable text, no NUL bytes" instead of a byte signature —
# a binary payload claiming these types is rejected.
_TEXT_SPREADSHEET_TYPES = {
    "text/csv",
    "text/tab-separated-values",
    "application/csv",
    "application/vnd.ms-excel",  # browsers send this for both .xls and .csv
}

ALLOWED_TYPES = set(_MAGIC_SIGNATURES.keys()) | set(_TEXT_SPREADSHEET_TYPES)

# Real legacy binary .xls (OLE2) signature — accepted under the
# application/vnd.ms-excel claim alongside the XML/CSV text forms.
_OLE2_SIG = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"


def _looks_like_text(data: bytes) -> bool:
    sample = data[:4096]
    return bool(sample) and b"\x00" not in sample


def validate_file_magic(data: bytes, claimed_content_type: str) -> None:
    """
    Validate that file content matches its claimed MIME type.
    Raises HTTPException 400 if the content type is unsupported or
    the file signature doesn't match.
    """
    ct = claimed_content_type.strip().lower()
    if ct in _TEXT_SPREADSHEET_TYPES:
        if data[:8] == _OLE2_SIG or data[:2] == b"PK":
            return  # genuine binary .xls / mislabeled .xlsx — both fine
        if not _looks_like_text(data):
            raise HTTPException(
                status_code=400,
                detail="File content does not match declared type (possible MIME spoofing)",
            )
        return
    if ct not in _MAGIC_SIGNATURES:
        raise HTTPException(status_code=400, detail=f"Unsupported file type: {ct}")

    sigs = _MAGIC_SIGNATURES[ct]
    for sig_bytes, offset in sigs:
        if len(data) < offset + len(sig_bytes):
            raise HTTPException(
                status_code=400,
                detail="File too small to validate signature",
            )
        if data[offset : offset + len(sig_bytes)] != sig_bytes:
            raise HTTPException(
                status_code=400,
                detail="File content does not match declared type (possible MIME spoofing)",
            )
